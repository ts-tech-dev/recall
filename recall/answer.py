"""Ask: retrieve the passages that best match a question and show them, with their images."""

from __future__ import annotations

import re
from collections.abc import Iterator

from .config import Settings
from .index import HL_END, HL_START, Filters, Index

MODES = ("summary", "report")


def retrieve(index: Index, question: str, filters: Filters | None, top_k: int) -> list[dict]:
    hits = index.search(question, filters, limit=top_k, per_doc=4)
    for n, h in enumerate(hits, 1):
        h["n"] = n
    return hits


def _excerpt(text: str, words: int) -> str:
    w = text.split()
    return " ".join(w[:words]) + (" …" if len(w) > words else "")


def local_answer(question: str, mode: str, hits: list[dict]) -> str:
    """The most relevant passages as Markdown: short excerpts ("summary") or longer ones ("report")."""
    if not hits:
        return "No matching notes were found for this question."
    take = hits[:4] if mode == "summary" else hits[:10]
    words = 70 if mode == "summary" else 250
    out = []
    for h in take:
        out.append(f"### [{h['n']}] {h['title']} — {h['heading']}")
        out.append("")
        snippet = h.get("snippet", "").replace(HL_START, "**").replace(HL_END, "**")
        body = snippet if mode == "summary" and snippet else _excerpt(h["text"], words)
        body = re.sub(r"\s*\[image: [^\]]*\]", "", body)  # the images are shown below
        out.append("> " + body.replace("\n", "\n> "))
        out.append("")
        for img in h["images"][: 1 if mode == "summary" else 3]:
            out.append(f"![{img['alt'] or h['heading']}]({img['url']})")
            out.append("")
    return "\n".join(out)


def answer_events(index: Index, settings: Settings, question: str, mode: str,
                  filters: Filters | None = None) -> Iterator[tuple[str, object]]:
    """Yield (event, data): one 'sources', then the passages as one 'delta', then 'done'."""
    mode = mode if mode in MODES else "summary"
    hits = retrieve(index, question, filters, settings.top_k)
    yield "sources", [
        {k: h[k] for k in ("n", "path", "title", "heading", "anchor", "snippet", "images", "ext")} for h in hits
    ]
    yield "delta", local_answer(question, mode, hits)
    yield "done", {}
