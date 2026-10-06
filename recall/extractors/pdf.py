"""PDF: text per page (OCR for scanned pages) and embedded figures."""

from __future__ import annotations

import re
from pathlib import Path

from .base import MIN_IMAGE_PX, Context, ExtractedDoc, cache_image, to_browser_image

SCANNED_PAGE_CHARS = 25  # a PDF page with less extractable text than this is treated as a scan
OCR_DPI = 150


def extract_pdf(path: Path, ctx: Context) -> ExtractedDoc:
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        title = (doc.metadata or {}).get("title") or path.stem
        parts: list[str] = []
        seen: set[int] = set()
        for pno, page in enumerate(doc, 1):
            parts.append(f"## Page {pno}\n")
            blocks = [" ".join(b[4].split()) for b in page.get_text("blocks", sort=True) if b[6] == 0]
            blocks = [b for b in blocks if b]
            scanned = sum(len(b) for b in blocks) < SCANNED_PAGE_CHARS and ctx.ocr is not None and page.get_images()
            if scanned:
                # No text layer: the page is (mostly) a picture, so read it with OCR instead.
                text = ctx.ocr(page.get_pixmap(dpi=OCR_DPI).tobytes("png"))
                if text:
                    parts.append("_Text recognized by OCR:_\n")
                    blocks = [line for line in text.splitlines() if line.strip()]
            for text in blocks:
                parts.append(re.sub(r"^([#>*+-]|\d+\.)", r"\\\1", text) + "\n")
            if scanned:
                continue  # the page scan itself isn't a useful figure; the PDF viewer shows it
            for info in page.get_images(full=True):
                xref, w, h = info[0], info[2], info[3]
                if xref in seen or w < MIN_IMAGE_PX or h < MIN_IMAGE_PX:
                    continue
                seen.add(xref)
                try:
                    img = doc.extract_image(xref)
                    conv = to_browser_image(img["image"], img["ext"])
                    if not conv:
                        pix = fitz.Pixmap(doc, xref)
                        if pix.n - pix.alpha >= 4:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        conv = (pix.tobytes("png"), "png")
                except Exception:
                    continue
                url = cache_image(ctx, path, f"xref{xref}", *conv)
                parts.append(f"![Figure on page {pno}]({url})\n")
        return ExtractedDoc(title=title, markdown="\n".join(parts))
    finally:
        doc.close()
