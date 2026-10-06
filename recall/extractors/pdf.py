"""PDF: text in reading order with the document's own section structure, tables, OCR for scans, and figures.

Sections come from the PDF's bookmarks (outline) when it has them, otherwise from lines set in a larger font
than the body text. A section heading is anchored to the page it starts on ({#page-N}), and every later page
of the section gets a "Page N" heading under it, so search results point at a page (the PDF viewer jumps
there) while the section titles make the text findable by topic. Running headers/footers repeated on most pages are dropped, and ruled tables
become Markdown tables.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .base import MIN_IMAGE_PX, Context, ExtractedDoc, cache_image, md_table, to_browser_image

SCANNED_PAGE_CHARS = 25  # a PDF page with less extractable text than this is treated as a scan
OCR_DPI = 150
MAX_SECTION_LEVELS = 3  # deeper bookmark levels are folded into their parent section
HEADING_SIZE_RATIO = 1.15  # a line this much larger than the body text may be a heading
MARGIN_RATIO = 0.08  # top/bottom part of the page where running headers and footers live
RUNNING_MIN_SHARE = 0.05  # share of pages a margin line must repeat on to count as a header/footer…
RUNNING_MAX_REPEATS = 10  # …or this many pages, whichever is fewer (long manuals with per-chapter footers)


@dataclass
class Block:
    """A paragraph, heading or table on a page, in reading order."""

    y: float
    text: str
    kind: str = "text"  # text | heading | table
    size: float = 0.0  # font size (for heading detection)
    level: int = 0  # heading level (1 = top) once known
    margin: bool = False  # in the top/bottom margin (candidate running header/footer)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _line_text(spans: list[dict]) -> str:
    """Join a line's spans: a visible gap between two spans is a space (some PDFs place words without one),
    but touching spans are one word (small caps are often set as separate spans)."""
    out = spans[0]["text"]
    for prev, cur in zip(spans, spans[1:]):
        gap = cur["bbox"][0] - prev["bbox"][2]
        if gap > 0.15 * min(prev["size"], cur["size"]) and not out.endswith(" ") and not cur["text"].startswith(" "):
            out += " "
        out += cur["text"]
    return " ".join(out.split())


def _page_blocks(page) -> list[Block]:
    """Paragraphs and tables of one page, top to bottom. Table cells are not repeated as paragraphs."""
    tables = []
    try:
        for t in page.find_tables().tables:
            # Built from the cells only: PyMuPDF may take a line of text above the table as its header.
            rows = [[" ".join((c or "").split()) for c in row] for row in t.extract()]
            md = md_table(rows)
            if md and len(rows) >= 2:
                words = Counter(w for row in rows for c in row for w in _norm(c).split())
                tables.append((t.bbox, md, words))
    except Exception:
        pass

    def in_table(block) -> bool:
        """Inside a table whose cells already hold this text. Table detection can misread a table and miss
        cells, so text that isn't in the extracted cells is kept as a paragraph rather than lost."""
        x0, y0, x1, y1 = block["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        words = _norm(" ".join(s["text"] for line in block["lines"] for s in line["spans"])).split()
        for (tx0, ty0, tx1, ty1), _, cells in tables:
            if tx0 <= cx <= tx1 and ty0 <= cy <= ty1:
                return sum(1 for w in words if cells[w]) >= 0.9 * len(words)
        return False

    h = page.rect.height
    out = [Block(y=bbox[1], text=md, kind="table") for bbox, md, _ in tables]
    for b in page.get_text("dict", sort=True)["blocks"]:
        if b.get("type") != 0 or in_table(b):
            continue
        # Split a block where the font size changes, so a heading set above its paragraph stands alone.
        group: list[str] = []
        size = None
        y = b["bbox"][1]
        for line in b["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = _line_text(spans)
            lsize = round(max(s["size"] for s in spans), 1)
            if group and lsize != size:
                out.append(Block(y=y, text=" ".join(group), size=size))
                group, y = [], line["bbox"][1]
            group.append(text)
            size = lsize
        if group:
            out.append(Block(y=y, text=" ".join(group), size=size or 0))
    for blk in out:
        blk.margin = blk.kind == "text" and (blk.y < h * MARGIN_RATIO or blk.y > h * (1 - MARGIN_RATIO))
    out.sort(key=lambda blk: blk.y)
    return out


def _drop_running_lines(pages: list[list[Block]]) -> None:
    """Remove headers/footers (page numbers, document titles) that repeat in the margins of most pages."""
    if len(pages) < 3:
        return
    key = lambda blk: re.sub(r"\d+", "#", _norm(blk.text))  # "Page 3 of 9" ~ "Page 4 of 9"
    counts = Counter(k for blocks in pages for k in {key(b) for b in blocks if b.margin})
    # Repeating in the margins of even a few pages (a chapter's footer) marks a running line, not content.
    need = max(3, min(len(pages) * RUNNING_MIN_SHARE, RUNNING_MAX_REPEATS))
    running = {k for k, n in counts.items() if n >= need}
    for blocks in pages:
        blocks[:] = [b for b in blocks if not (b.margin and key(b) in running)]


def _mark_outline_headings(doc, pages: list[list[Block]]) -> bool:
    """Turn bookmark titles into headings on their pages. Returns False if the PDF has no bookmarks."""
    toc = [(lvl, title.strip(), pno) for lvl, title, pno in doc.get_toc(simple=True) if title.strip() and pno >= 1]
    if not toc:
        return False
    for lvl, title, pno in toc:
        blocks = pages[pno - 1] if pno <= len(pages) else None
        if blocks is None:
            continue
        want = _norm(title)
        hit = next((b for b in blocks if b.kind == "text" and (_norm(b.text) == want or
                    (_norm(b.text).startswith(want) and len(b.text) < len(title) + 40))), None)
        level = min(lvl, MAX_SECTION_LEVELS)
        if hit is not None and _norm(hit.text) == want:
            hit.kind, hit.level = "heading", level
        else:  # title not found as its own line: start the section at the top of the page
            y = hit.y - 0.1 if hit is not None else -1.0
            blocks.insert(blocks.index(hit) if hit is not None else 0, Block(y=y, text=title, kind="heading", level=level))
    return True


def _mark_font_headings(pages: list[list[Block]]) -> None:
    """Without bookmarks: short lines in a clearly larger font than the body text become headings."""
    chars = Counter()
    for blocks in pages:
        for b in blocks:
            if b.kind == "text" and b.size:
                chars[b.size] += len(b.text)
    if not chars:
        return
    body = chars.most_common(1)[0][0]
    cands = [b for blocks in pages for b in blocks
             if b.kind == "text" and b.size >= body * HEADING_SIZE_RATIO and 2 <= len(b.text) <= 120
             and re.search(r"[^\W\d_]", b.text) and not b.text.rstrip().endswith((".", ",", ";", ":"))]
    total = sum(1 for blocks in pages for b in blocks if b.kind == "text")
    if not cands or len(cands) > max(4, total * 0.4):  # too many "headings": the size isn't a structure signal
        return
    sizes = sorted({b.size for b in cands}, reverse=True)[:MAX_SECTION_LEVELS]
    for b in cands:
        if b.size in sizes:
            b.kind, b.level = "heading", sizes.index(b.size) + 1


def _escape(text: str) -> str:
    """Keep body text from being read as Markdown structure (a line starting with '#', '-', '1.' …)."""
    return re.sub(r"^([#>*+-]|\d+\.)", r"\\\1", text)


def extract_pdf(path: Path, ctx: Context) -> ExtractedDoc:
    import pymupdf as fitz

    fitz.no_recommend_layout()  # its optional layout package isn't used; don't print the suggestion
    doc = fitz.open(path)
    try:
        title = (doc.metadata or {}).get("title") or path.stem
        pages = [_page_blocks(page) for page in doc]
        _drop_running_lines(pages)
        if not _mark_outline_headings(doc, pages):
            _mark_font_headings(pages)
        levels = sorted({b.level for blocks in pages for b in blocks if b.kind == "heading"})
        # Section headings start at "##"; the page heading sits one level below the deepest section.
        hashes = {lvl: "#" * (i + 2) for i, lvl in enumerate(levels)}
        page_hashes = "#" * min(len(levels) + 2, 6)

        parts: list[str] = []
        seen: set[int] = set()
        for pno, (page, blocks) in enumerate(zip(doc, pages), 1):
            page_heading = f"{page_hashes} Page {pno}\n"
            text_chars = sum(len(b.text) for b in blocks if b.kind != "heading")
            scanned = text_chars < SCANNED_PAGE_CHARS and ctx.ocr is not None and page.get_images()
            if scanned:
                # No text layer: the page is (mostly) a picture, so read it with OCR instead.
                text = ctx.ocr(page.get_pixmap(dpi=OCR_DPI).tobytes("png"))
                blocks = [b for b in blocks if b.kind == "heading"]
                if text:
                    blocks += [Block(y=0, text="_Text recognized by OCR:_")]
                    blocks += [Block(y=0, text=line) for line in text.splitlines() if line.strip()]
            if not blocks or blocks[0].kind != "heading":
                parts.append(page_heading)
            for b in blocks:
                if b.kind == "heading":
                    # {#page-N}: the section's own anchor is its page, so the PDF viewer can jump there
                    parts.append(f"{hashes[b.level]} {b.text.strip()} {{#page-{pno}}}\n")
                elif b.kind == "table":
                    parts.append(b.text + "\n")
                elif b.text.startswith("_Text recognized by OCR:_"):
                    parts.append(b.text + "\n")
                else:
                    parts.append(_escape(" ".join(b.text.split())) + "\n")
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
