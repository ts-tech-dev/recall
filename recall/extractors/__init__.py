"""Turn every supported file type into normalized Markdown.

One module per format; to support a new one, write `extract_<kind>(path, ctx) -> ExtractedDoc`, add its
extensions to NOTE_TYPES (base.py) and register it in EXTRACTORS below.

Embedded images (PDF/DOCX/PPTX) are written to a cache directory and referenced as
``/api/cache-image/<name>``; images referenced from Markdown/HTML notes are rewritten to
``/api/file?path=<path relative to the notes root>``. Links between notes become ``#doc=<path>``.
"""

from __future__ import annotations

from pathlib import Path

from .base import IMAGE_EXTS, NOTE_TYPES, Context, ExtractedDoc, doc_link, file_url
from .docx import extract_docx
from .html import extract_html
from .markdown import (EXTERNAL_RE, HTML_IMG_RE, MD_IMAGE_RE, MD_LINK_RE, extract_markdown, extract_text,
                       rewrite_markdown_links, split_code)
from .pdf import extract_pdf
from .pptx import extract_pptx
from .sheets import extract_csv, extract_xlsx

EXTRACTORS = {
    "markdown": extract_markdown,
    "text": extract_text,
    "pdf": extract_pdf,
    "docx": extract_docx,
    "pptx": extract_pptx,
    "xlsx": extract_xlsx,
    "csv": extract_csv,
    "html": extract_html,
}

__all__ = ["IMAGE_EXTS", "NOTE_TYPES", "EXTRACTORS", "Context", "ExtractedDoc", "doc_link", "file_url", "extract",
           "is_note", "rewrite_markdown_links", "split_code", "EXTERNAL_RE", "HTML_IMG_RE", "MD_IMAGE_RE",
           "MD_LINK_RE"]


def is_note(path: Path) -> bool:
    return path.suffix.lower() in NOTE_TYPES


def extract(path: Path, ctx: Context) -> ExtractedDoc:
    kind = NOTE_TYPES.get(path.suffix.lower())
    if not kind:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    return EXTRACTORS[kind](path, ctx)
