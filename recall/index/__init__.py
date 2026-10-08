"""Local index over the notes directory.

Keyword search uses SQLite FTS5 (BM25); semantic search uses local embeddings. The two are merged
with reciprocal-rank fusion. Image text (OCR) is folded into the chunk it appears in.
Links between notes are stored for backlinks and the note graph.

    core.py     the Index class: database, files, image lookup, stats
    build.py    indexing (scan, extract, chunk, embed, OCR)
    search.py   keyword / semantic / hybrid search
    graph.py    links, backlinks, similar notes, graph
    query.py    filters, file-type groups, FTS query building
    schema.py   SQLite schema
"""

from .core import SKIP_DIRS, Index, prune_other_indexes
from .query import HL_END, HL_START, TYPE_GROUPS, Filters, build_match_query

__all__ = ["Index", "Filters", "TYPE_GROUPS", "SKIP_DIRS", "HL_START", "HL_END", "build_match_query", "prune_other_indexes"]
