"""Image text: OCR (local, RapidOCR) and optional AI captions, cached by image content hash."""

from __future__ import annotations

import hashlib
import logging
import threading
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("recall.images")

MIN_OCR_PX = 100  # skip icons and thumbnails
MAX_OCR_BYTES = 15_000_000
MIN_CONFIDENCE = 0.6

_ocr_engine = None
_ocr_lock = threading.Lock()
_ocr_failed = False


def ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def ocr_bytes(data: bytes) -> str:
    """Recognize text in an image. Returns '' for tiny/unsupported images or if OCR is unavailable."""
    global _ocr_engine, _ocr_failed
    if _ocr_failed or len(data) > MAX_OCR_BYTES:
        return ""
    try:
        import pymupdf

        pix = pymupdf.Pixmap(data)
        if pix.width < MIN_OCR_PX or pix.height < MIN_OCR_PX:
            return ""
        if pix.colorspace is None or pix.colorspace.n not in (1, 3):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        data = pix.tobytes("png")
    except Exception:
        return ""  # not an image pymupdf can decode (svg, emf…)
    with _ocr_lock:
        if _ocr_engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR

                _ocr_engine = RapidOCR()
            except Exception as e:
                log.warning("OCR unavailable: %s", e)
                _ocr_failed = True
                return ""
        try:
            result, _ = _ocr_engine(data)
        except Exception as e:
            log.warning("OCR failed: %s", e)
            return ""
    lines = [r[1].strip() for r in (result or []) if r[1].strip() and float(r[2]) >= MIN_CONFIDENCE]
    return "\n".join(lines)


def content_key(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class ImageText:
    """OCR + caption lookups backed by the index's `image_meta` table."""

    def __init__(self, conn_factory, ocr_enabled: bool = True):
        self._conn = conn_factory
        self.ocr_enabled = ocr_enabled and ocr_available()

    def get(self, path: Path, conn=None) -> dict:
        """Return {"key", "ocr", "caption"} for an image file, running OCR if not cached.

        Pass `conn` when called inside an open write transaction (a second connection would block).
        """
        try:
            data = path.read_bytes()
        except OSError:
            return {"key": "", "ocr": "", "caption": ""}
        key = content_key(data)
        with self._use(conn) as c:
            row = c.execute("SELECT ocr, caption, ocr_done FROM image_meta WHERE key=?", (key,)).fetchone()
        if row and (row["ocr_done"] or not self.ocr_enabled):
            return {"key": key, "ocr": row["ocr"] or "", "caption": row["caption"] or ""}
        text = ocr_bytes(data) if self.ocr_enabled else ""
        with self._use(conn) as c:
            c.execute(
                "INSERT INTO image_meta(key, ocr, ocr_done) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET ocr=excluded.ocr, ocr_done=excluded.ocr_done",
                (key, text, 1 if self.ocr_enabled else 0),
            )
        return {"key": key, "ocr": text, "caption": row["caption"] if row else ""}

    @contextmanager
    def _use(self, conn):
        if conn is not None:
            yield conn
        else:
            with self._conn() as c:
                yield c

    def set_caption(self, key: str, caption: str, model: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO image_meta(key, caption, caption_model) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET caption=excluded.caption, caption_model=excluded.caption_model",
                (key, caption, model),
            )
