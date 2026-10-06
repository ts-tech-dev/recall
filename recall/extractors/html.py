"""HTML pages, converted to simple Markdown."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from .base import Context, ExtractedDoc, file_url
from .markdown import EXTERNAL_RE, resolve_local

class _HTMLToMarkdown(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "br", "tr", "table", "ul", "ol", "blockquote", "pre"}
    SKIP = {"script", "style", "head", "noscript", "svg"}

    def __init__(self, on_img):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.on_img = on_img
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in {"td", "th"}:
            self.out.append(" | ")
        elif tag in self.BLOCK:
            self.out.append("\n\n")
        elif tag == "img" and a.get("src"):
            md = self.on_img(a["src"], a.get("alt") or "")
            if md:
                self.out.append(f"\n\n{md}\n\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))


def extract_html(path: Path, ctx: Context) -> ExtractedDoc:
    def on_img(src: str, alt: str) -> str:
        if EXTERNAL_RE.match(src):
            return f"![{alt}]({src})" if src.startswith("http") else ""
        rel = resolve_local(ctx, path, src)
        return f"![{alt}]({file_url(rel)})" if rel else ""

    p = _HTMLToMarkdown(on_img)
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    md = re.sub(r"\n{3,}", "\n\n", "".join(p.out))
    md = "\n".join(line.strip() for line in md.splitlines())
    return ExtractedDoc(title=p.title.strip() or path.stem, markdown=md.strip())
