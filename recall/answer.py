"""Retrieval → prompt → answer. Works without AI (local extractive answer)."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator
from pathlib import Path

from .config import Settings
from .index import HL_END, HL_START, Filters, Index
from .llm import LLMError, stream_answer

MODES = ("summary", "report")
MAX_CATALOGUE_IMAGES = 24
MAX_VISION_BYTES = 3_500_000
VISION_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}

SYSTEM_PROMPT = """You answer questions using the user's personal notes, which are provided as numbered sources.

Ground the answer in the notes. When the notes don't cover part of the question, say so plainly instead of filling the gap from general knowledge; if brief general context genuinely helps, label it as not from the notes.

Cite the notes with bracketed source numbers such as [1] or [2][4] right after the statements they support.

The notes come with an image catalogue. When an image would help the reader — a diagram, screenshot, chart or photo that illustrates the point being made — embed it on its own line as Markdown `![short caption](url)`, copying the url exactly from the catalogue, and place it next to the text it illustrates. Only use catalogue URLs and skip images that are not relevant to the question.

Write in GitHub-flavoured Markdown."""

MODE_INSTRUCTIONS = {
    "summary": (
        "Write a concise answer: open with a one- or two-sentence direct answer, then the key points as a "
        "short bulleted list. Keep it under about 250 words and include at most two images, only if they clearly help."
    ),
    "report": (
        "Write a detailed report. Start with a short summary paragraph, then organize the details under ## "
        "headings that suit the topic. Cover everything relevant the notes contain — steps, specifics, "
        "numbers, caveats, and any differences or contradictions between notes. Use tables where they make "
        "comparisons clearer. Place each useful image beside the text it illustrates."
    ),
}


def retrieve(index: Index, question: str, filters: Filters | None, top_k: int) -> list[dict]:
    hits = index.search(question, filters, limit=top_k, per_doc=4)
    for n, h in enumerate(hits, 1):
        h["n"] = n
    return hits


def image_catalogue(hits: list[dict]) -> list[dict]:
    """Unique images from the retrieved chunks, highest-ranked chunk first."""
    seen, out = set(), []
    for h in hits:
        for img in h["images"]:
            if img["url"] in seen:
                continue
            seen.add(img["url"])
            out.append({"id": f"img{len(out) + 1}", "url": img["url"], "alt": img["alt"], "source": h["n"],
                        "context": h["heading"], "caption": img.get("caption", "")})
            if len(out) >= MAX_CATALOGUE_IMAGES:
                return out
    return out


def resolve_image_url(index: Index, url: str) -> Path | None:
    return index.resolve_image(url)


def _xml_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def build_prompt(question: str, mode: str, hits: list[dict], images: list[dict]) -> str:
    parts = ["<notes>"]
    for h in hits:
        parts.append(
            f'<source id="{h["n"]}" title="{_xml_attr(h["title"])}" path="{_xml_attr(h["path"])}" '
            f'section="{_xml_attr(h["heading"])}">\n{h["text"]}\n</source>'
        )
    parts.append("</notes>")
    if images:
        parts.append("<image_catalogue>")
        for im in images:
            parts.append(
                f'<image id="{im["id"]}" source="{im["source"]}" url="{_xml_attr(im["url"])}" '
                f'caption="{_xml_attr(im["alt"] or im["context"])}"'
                + (f' description="{_xml_attr(im["caption"])}"' if im.get("caption") else "")
                + "/>"
            )
        parts.append("</image_catalogue>")
    else:
        parts.append("<image_catalogue/>")
    parts.append(f"<instructions>{MODE_INSTRUCTIONS[mode]}</instructions>")
    parts.append(f"<question>{question}</question>")
    return "\n".join(parts)


def vision_blocks(index: Index, images: list[dict], limit: int) -> list[dict]:
    blocks = []
    for im in images:
        if len(blocks) >= limit * 2:
            break
        p = resolve_image_url(index, im["url"])
        if not p or p.suffix.lower() not in VISION_TYPES or p.stat().st_size > MAX_VISION_BYTES:
            continue
        blocks.append({"type": "text", "text": f"Image {im['id']} (catalogue url {im['url']}):"})
        blocks.append({"type": "image", "media_type": VISION_TYPES[p.suffix.lower()],
                       "data": base64.b64encode(p.read_bytes()).decode()})
    return blocks


def _excerpt(text: str, words: int) -> str:
    w = text.split()
    return " ".join(w[:words]) + (" …" if len(w) > words else "")


def local_answer(question: str, mode: str, hits: list[dict], ai_switched_off: bool = False) -> str:
    """Extractive answer used when AI is off or no AI provider is configured."""
    if not hits:
        return "No matching notes were found for this question."
    take = hits[:4] if mode == "summary" else hits[:10]
    words = 70 if mode == "summary" else 250
    why = "AI features are turned off in Settings" if ai_switched_off else "No AI provider is configured"
    out = [f"_{why}, so these are the most relevant passages from your notes._", ""]
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
    """Yield (event, data): one 'sources', then 'delta' text pieces, then 'done' (or 'error')."""
    mode = mode if mode in MODES else "summary"
    hits = retrieve(index, question, filters, settings.top_k)
    images = image_catalogue(hits)
    yield "sources", [
        {k: h[k] for k in ("n", "path", "title", "heading", "anchor", "snippet", "images", "ext")} for h in hits
    ]
    if not settings.ai_enabled() or not hits:
        yield "delta", local_answer(question, mode, hits, ai_switched_off=not settings.ai_features)
        yield "done", {"ai": False}
        return
    blocks = [{"type": "text", "text": build_prompt(question, mode, hits, images)}]
    if settings.send_images and images:
        blocks = vision_blocks(index, images, settings.max_images) + blocks
    max_tokens = 8000 if mode == "summary" else 32000
    try:
        for text in stream_answer(settings, SYSTEM_PROMPT, blocks, max_tokens):
            yield "delta", text
    except LLMError as e:
        yield "error", {"message": str(e)}
        return
    yield "done", {"ai": True, "model": settings.model}
