"""Markdown and plain-text notes: front matter, tags, and rewriting links/images for the previewer."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from .base import IMAGE_EXTS, NOTE_TYPES, Context, ExtractedDoc, doc_link, file_url

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n(?:---|\.\.\.)\s*\n", re.S)
MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+["\'][^"\']*["\'])?\s*\)')
WIKI_EMBED_RE = re.compile(r"!\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
WIKI_LINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?\s*\)")
HTML_IMG_RE = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(.*?)\2', re.I)
FENCE_RE = re.compile(r"^(```|~~~)", re.M)
EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.I)


def resolve_local(ctx: Context, base: Path, target: str) -> str | None:
    """Resolve a link target written in a note to a path relative to the notes root."""
    target = unquote(target.split("#")[0].split("?")[0])
    if not target:
        return None
    root = ctx.root.resolve()
    cand = (root / target.lstrip("/")) if target.startswith("/") else (base.parent / target)
    try:
        cand = cand.resolve()
        cand.relative_to(root)
    except (ValueError, OSError):
        return None
    if cand.is_file():
        return cand.relative_to(root).as_posix()
    return ctx.find_by_name(Path(target).name)


def split_code(md: str) -> list[tuple[bool, str]]:
    """Split Markdown into (is_code, text) segments so link rewriting skips fenced code."""
    out, pos, in_code, fence = [], 0, False, ""
    for m in FENCE_RE.finditer(md):
        line_end = md.find("\n", m.start())
        line_end = len(md) if line_end == -1 else line_end + 1
        if not in_code:
            out.append((False, md[pos : m.start()]))
            pos, in_code, fence = m.start(), True, m.group(1)
        elif m.group(1) == fence:
            out.append((True, md[pos:line_end]))
            pos, in_code = line_end, False
    out.append((in_code, md[pos:]))
    return out


def rewrite_markdown_links(md: str, ctx: Context, path: Path) -> str:
    def img(m: re.Match) -> str:
        alt, target = m.group(1), m.group(2)
        if EXTERNAL_RE.match(target):
            return m.group(0)
        rel = resolve_local(ctx, path, target)
        return f"![{alt}]({file_url(rel)})" if rel else m.group(0)

    def wiki_embed(m: re.Match) -> str:
        target, alias = m.group(1).strip(), (m.group(2) or "").strip()
        rel = resolve_local(ctx, path, target) or ctx.find_by_name(target)
        if not rel:
            return m.group(0)
        if Path(rel).suffix.lower() in IMAGE_EXTS:
            if re.fullmatch(r"\d+(?:x\d+)?", alias):  # ![[pic.png|300]]: a size, kept for the previewer as alt|300
                return f"![{Path(rel).stem}|{alias}]({file_url(rel)})"
            return f"![{alias or Path(rel).stem}]({file_url(rel)})"
        return f"[{alias or Path(rel).stem}]({doc_link(rel)})"

    def wiki_link(m: re.Match) -> str:
        target, alias = m.group(1).strip(), (m.group(2) or "").strip()
        rel = ctx.find_by_name(target) or ctx.find_by_name(target + ".md")
        return f"[{alias or target}]({doc_link(rel)})" if rel else (alias or target)

    def link(m: re.Match) -> str:
        text, target = m.group(1), m.group(2)
        if EXTERNAL_RE.match(target):
            return m.group(0)
        rel = resolve_local(ctx, path, target)
        if not rel:
            return m.group(0)
        suffix = Path(rel).suffix.lower()
        if suffix in NOTE_TYPES:
            return f"[{text}]({doc_link(rel)})"
        return f"[{text}]({file_url(rel)})"

    def html_img(m: re.Match) -> str:
        target = m.group(3)
        if EXTERNAL_RE.match(target):
            return m.group(0)
        rel = resolve_local(ctx, path, target)
        return f"{m.group(1)}{m.group(2)}{file_url(rel)}{m.group(2)}" if rel else m.group(0)

    parts = []
    for is_code, seg in split_code(md):
        if not is_code:
            seg = WIKI_EMBED_RE.sub(wiki_embed, seg)
            seg = MD_IMAGE_RE.sub(img, seg)
            seg = WIKI_LINK_RE.sub(wiki_link, seg)
            seg = MD_LINK_RE.sub(link, seg)
            seg = HTML_IMG_RE.sub(html_img, seg)
        parts.append(seg)
    return "".join(parts)


def parse_frontmatter(md: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(md)
    if not m:
        return {}, md
    meta: dict = {}
    key = None
    for line in m.group(1).splitlines():
        if re.match(r"^\s+-\s+", line) and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(line.split("-", 1)[1].strip().strip("'\""))
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if kv:
            key, val = kv.group(1).lower(), kv.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                meta[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            elif val:
                meta[key] = val.strip("'\"")
    return meta, md[m.end() :]


def extract_markdown(path: Path, ctx: Context) -> ExtractedDoc:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(raw)
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]
    tags += [t for t in re.findall(r"(?:^|\s)#([A-Za-z][\w/-]+)", body) if t not in tags]
    title = meta.get("title")
    if not title:
        h1 = re.search(r"^#\s+(.+?)\s*#*\s*$", body, re.M)
        title = re.sub(r"\s*\{#[\w-]+\}$", "", h1.group(1)) if h1 else path.stem
    return ExtractedDoc(title=str(title), markdown=rewrite_markdown_links(body, ctx, path), tags=tags)


def extract_text(path: Path, ctx: Context) -> ExtractedDoc:
    return ExtractedDoc(title=path.stem, markdown=path.read_text(encoding="utf-8", errors="replace"), kind="text")
