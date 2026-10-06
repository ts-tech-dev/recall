"""Split normalized Markdown into heading-scoped chunks that remember their images."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CUSTOM_ID_RE = re.compile(r"\s*\{#([\w-]+)\}\s*$")  # "## Title {#my-id}" sets the heading's anchor
IMAGE_SIZE_RE = re.compile(r"\s*\|\s*\d+(?:\s*x\s*\d+)?\s*$")  # "![alt|300](…)": size, not part of the alt text
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
HTML_IMG_RE = re.compile(r'<img\b[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\'][^>]*>', re.I)
HTML_ALT_RE = re.compile(r'\balt\s*=\s*["\']([^"\']*)["\']', re.I)

TARGET_WORDS = 300
MAX_WORDS = 450


@dataclass
class Chunk:
    heading: str  # breadcrumb, e.g. "Networking > VLANs"
    anchor: str  # slug of the nearest heading, matches the previewer's heading ids
    text: str  # searchable text: images replaced by their alt text
    images: list[dict] = field(default_factory=list)  # [{"url", "alt"}]


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    return re.sub(r"[\s]+", "-", s)


def _images(md: str) -> list[dict]:
    out = [{"alt": IMAGE_SIZE_RE.sub("", a).strip(), "url": u} for a, u in IMAGE_RE.findall(md)]
    for m in HTML_IMG_RE.finditer(md):
        alt = HTML_ALT_RE.search(m.group(0))
        out.append({"alt": alt.group(1) if alt else "", "url": m.group(1)})
    return [i for i in out if i["url"].startswith(("/api/", "http://", "https://"))]


def _plain(md: str) -> str:
    md = IMAGE_RE.sub(lambda m: f"[image: {IMAGE_SIZE_RE.sub('', m.group(1))}]" if m.group(1) else "", md)
    md = HTML_IMG_RE.sub("", md)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)  # links → their text
    return md.strip()


def split_sections(md: str, title: str) -> list[tuple[list[str], str]]:
    """Return [(heading_path, body_markdown)], ignoring '#' lines inside fenced code.

    Text before the first heading belongs to a section named after the document title
    and is dropped when empty; empty sections under a real heading are kept.
    """
    sections: list[tuple[list[str], str]] = []
    path: list[tuple[int, str]] = []
    body: list[str] = []
    in_code = False

    def flush():
        text = "\n".join(body).strip()
        if text or path:
            sections.append(([h for _, h in path] or [title], text))
        body.clear()

    for line in md.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        m = None if in_code else HEADING_RE.match(line)
        if not m:
            body.append(line)
            continue
        flush()
        level, text = len(m.group(1)), m.group(2).strip()
        path = [(lv, h) for lv, h in path if lv < level] + [(level, text)]
    flush()
    return sections


def chunk_markdown(md: str, title: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading_path, body in split_sections(md, title):
        custom = CUSTOM_ID_RE.search(heading_path[-1])
        anchor = custom.group(1) if custom else slugify(heading_path[-1])
        heading = " > ".join(CUSTOM_ID_RE.sub("", h) for h in heading_path)
        paras = [piece for p in re.split(r"\n\s*\n", body) if p.strip() for piece in _split_long(p)]
        if not paras:
            # Heading-only sections still matter for search (e.g. a slide title).
            chunks.append(Chunk(heading, anchor, "", []))
            continue
        cur: list[str] = []
        words = 0
        for p in paras:
            n = len(p.split())
            if cur and words + n > MAX_WORDS:
                chunks.append(_make(heading, anchor, cur))
                # carry the last paragraph over for context if it's short
                cur = [cur[-1]] if len(cur[-1].split()) < TARGET_WORDS // 3 else []
                words = sum(len(x.split()) for x in cur)
            cur.append(p)
            words += n
        if cur:
            chunks.append(_make(heading, anchor, cur))
    return chunks


def _split_long(p: str) -> list[str]:
    """Break an oversized paragraph (a big table, an unbroken PDF block) into pieces."""
    if len(p.split()) <= MAX_WORDS:
        return [p]
    lines = p.splitlines()
    if len(lines) > 1:
        header = lines[:2] if len(lines) > 2 and lines[0].startswith("|") and set(lines[1]) <= set("|- :") else []
        body = lines[len(header):]
        out, cur, words = [], [], 0
        for line in body:
            n = len(line.split())
            if cur and words + n > TARGET_WORDS:
                out.append("\n".join(header + cur))
                cur, words = [], 0
            cur.append(line)
            words += n
        if cur:
            out.append("\n".join(header + cur))
        return out
    words = p.split()
    return [" ".join(words[i : i + TARGET_WORDS]) for i in range(0, len(words), TARGET_WORDS)]


def _make(heading: str, anchor: str, paras: list[str]) -> Chunk:
    md = "\n\n".join(paras)
    return Chunk(heading=heading, anchor=anchor, text=_plain(md), images=_images(md))
