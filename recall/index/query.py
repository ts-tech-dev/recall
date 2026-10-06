"""Search inputs: file-type groups, filters and turning a question into an FTS5 query."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..extractors import IMAGE_EXTS

RRF_K = 60

STOPWORDS = set(
    """a about above after again against all am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further had
    has have having he her here hers herself him himself his how i if in into is it its itself just
    me more most my myself no nor not now of off on once only or other our ours ourselves out over
    own same she should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where which while who
    whom why will with would you your yours yourself yourselves tell explain describe give show
    summarize summary detailed report notes note please know find list""".split()
)

TYPE_GROUPS = {
    "markdown": [".md", ".markdown"],
    "pdf": [".pdf"],
    "word": [".docx"],
    "powerpoint": [".pptx"],
    "spreadsheet": [".xlsx", ".csv"],
    "html": [".html", ".htm"],
    "text": [".txt", ".rst", ".org"],
    "image": sorted(IMAGE_EXTS),
}

# Markers around matched terms in snippets; the browser escapes the text, then swaps these
# for <mark>, so note content is never interpreted as HTML.
HL_START, HL_END = "\x02", "\x03"


def build_match_query(q: str) -> str:
    """Turn a natural-language question into a safe FTS5 OR-query of quoted terms/phrases."""
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]*"', " ", q)
    terms: list[str] = []
    for p in phrases:
        words = re.findall(r"\w+", p.lower())
        if words:
            terms.append('"' + " ".join(words) + '"')
    seen = set()
    for w in re.findall(r"\w+", rest.lower()):
        if (len(w) < 2 and not w.isdigit()) or w in STOPWORDS or w in seen:
            continue
        seen.add(w)
        terms.append(f'"{w}"' + ("*" if len(w) >= 4 else ""))
    if not terms:  # question was all stopwords — fall back to every word
        terms = [f'"{w}"' for w in dict.fromkeys(re.findall(r"\w+", rest.lower()))]
    return " OR ".join(terms)


@dataclass
class Filters:
    types: list[str] = field(default_factory=list)  # group names or extensions
    folder: str = ""  # restrict to this folder (recursive)
    path: str = ""  # restrict to a single document

    def extensions(self) -> list[str]:
        exts: list[str] = []
        for t in self.types:
            t = t.strip().lower()
            if not t:
                continue
            if t in TYPE_GROUPS:
                exts += TYPE_GROUPS[t]
            else:
                exts.append(t if t.startswith(".") else "." + t)
        return exts
