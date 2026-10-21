"""Link check: links in Markdown notes that lead nowhere, and notes that nothing links to.

A link is broken when the previewer can't find what it points at, using the same rules (relative paths, then
Obsidian-style lookup by file name). Fenced and inline code is skipped. Notes in the top-level `templates`
folder are left out of both lists: they hold placeholders, and nothing is meant to link to them.
"""

from __future__ import annotations

import re
from pathlib import Path

from .extractors import EXTERNAL_RE, HTML_IMG_RE, MD_IMAGE_RE, MD_LINK_RE, Context, split_code
from .extractors.markdown import WIKI_EMBED_RE, WIKI_LINK_RE, resolve_local

MARKDOWN_EXTS = {".md", ".markdown"}
TEMPLATES_DIR = "templates"
INLINE_CODE_RE = re.compile(r"(`+)[^\n]*?\1")


def is_template(rel: str) -> bool:
    return rel.split("/", 1)[0].lower() == TEMPLATES_DIR and "/" in rel


def broken_links(ctx: Context, p: Path, text: str) -> list[dict]:
    """The links in one note that lead nowhere: {line, link, target}, in the order they appear."""
    found = []

    def missing_note(target: str) -> bool:
        return not (ctx.find_by_name(target) or ctx.find_by_name(target + ".md"))

    def missing_file(target: str) -> bool:
        return not EXTERNAL_RE.match(target) and not resolve_local(ctx, p, target)

    checks = (  # (pattern, group with the target, is it broken?)
        (WIKI_EMBED_RE, 1, lambda t: not (resolve_local(ctx, p, t.strip()) or ctx.find_by_name(t.strip()))),
        (WIKI_LINK_RE, 1, lambda t: missing_note(t.strip())),
        (MD_IMAGE_RE, 2, missing_file),
        (MD_LINK_RE, 2, missing_file),
        (HTML_IMG_RE, 3, missing_file),
    )
    offset = 0
    for is_code, seg in split_code(text):
        if not is_code:
            # Blank out inline code (same length, so positions and line numbers stay right).
            seg_checked = INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), seg)
            for rx, group, broken in checks:
                for m in rx.finditer(seg_checked):
                    if broken(m.group(group)):
                        found.append({"pos": offset + m.start(), "link": m.group(0), "target": m.group(group).strip()})
        offset += len(seg)
    found.sort(key=lambda f: f["pos"])
    return [{"line": text.count("\n", 0, f.pop("pos")) + 1, **f} for f in found]


def check(index) -> dict:
    """Broken links per note, and the notes nothing links to (Markdown notes only)."""
    ctx = index.context()
    broken = []
    for p in index.iter_files():
        rel = p.relative_to(index.root).as_posix()
        if p.suffix.lower() not in MARKDOWN_EXTS or is_template(rel):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        links = broken_links(ctx, p, text)
        if links:
            broken.append({"path": rel, "links": links})
    orphans = [n for n in index.unlinked_notes() if Path(n["path"]).suffix.lower() in MARKDOWN_EXTS
               and not is_template(n["path"])]
    return {"broken": broken, "orphans": orphans}
