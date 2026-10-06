"""Shared pieces for extractors: the result type, the per-run context, URLs and image/table helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

# File extension -> kind of note. Each kind has an extractor, registered in extractors/__init__.py.
NOTE_TYPES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".rst": "text",
    ".org": "text",
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".html": "html",
    ".htm": "html",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
BROWSER_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
MIN_IMAGE_PX = 64  # skip icons, bullets, spacer images embedded in documents


@dataclass
class ExtractedDoc:
    title: str
    markdown: str
    kind: str = "markdown"  # how the previewer should render `markdown`: markdown | text
    tags: list[str] = field(default_factory=list)


@dataclass
class Context:
    """Where a file lives and where to put derived assets."""

    root: Path
    cache_dir: Path
    ocr: Callable[[bytes], str] | None = None  # used for scanned PDF pages
    _names: dict[str, list[str]] | None = None

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root.resolve()).as_posix()

    def find_by_name(self, name: str) -> str | None:
        """Obsidian-style lookup: resolve a bare file name anywhere in the vault."""
        if self._names is None:
            self._names = {}
            for p in self.root.rglob("*"):
                if p.is_file() and not any(part.startswith(".") for part in p.relative_to(self.root).parts):
                    rel = p.relative_to(self.root).as_posix()
                    self._names.setdefault(p.name.lower(), []).append(rel)
                    self._names.setdefault(p.stem.lower(), []).append(rel)
        hits = self._names.get(name.lower()) or self._names.get(Path(name).name.lower())
        return sorted(hits, key=len)[0] if hits else None


def file_url(rel: str) -> str:
    return "/api/file?path=" + quote(rel)


def doc_link(rel: str) -> str:
    return "#doc=" + quote(rel)


def cache_image(ctx: Context, src: Path, key: str, data: bytes, ext: str) -> str:
    ext = ext.lower().lstrip(".")
    st = src.stat()
    name = hashlib.sha1(f"{src}:{st.st_mtime_ns}:{key}".encode()).hexdigest()[:20] + "." + ext
    out = ctx.cache_dir / name
    if not out.exists():
        ctx.cache_dir.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    return "/api/cache-image/" + name


def image_size_ok(data: bytes) -> bool:
    try:
        import pymupdf as fitz

        pix = fitz.Pixmap(data)
        return pix.width >= MIN_IMAGE_PX and pix.height >= MIN_IMAGE_PX
    except Exception:
        return True  # unknown format (e.g. EMF) — keep and let the caller decide


def to_browser_image(data: bytes, ext: str) -> tuple[bytes, str] | None:
    """Convert formats browsers can't show (tiff, jpx, emf…) to PNG when possible."""
    ext = ext.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext in BROWSER_IMAGE_EXTS:
        return data, ext
    try:
        import pymupdf as fitz

        pix = fitz.Pixmap(data)
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.tobytes("png"), "png"
    except Exception:
        return None


def md_escape_cell(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def md_table(rows: list[list[str]]) -> str:
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [[md_escape_cell(c) for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)
