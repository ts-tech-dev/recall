"""The Index: one SQLite database per notes folder, plus caches for extracted images and versions."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..embeddings import Embedder
from ..extractors import IMAGE_EXTS, NOTE_TYPES, Context, ExtractedDoc, extract
from ..extractors.cache import ExtractCache
from ..images import ImageText, ocr_bytes
from .build import BuildMixin
from .graph import GraphMixin
from .schema import REBUILT_TABLES, SCHEMA, SCHEMA_VERSION
from .search import SearchMixin

SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv"}



class Index(BuildMixin, SearchMixin, GraphMixin):
    def __init__(self, root: Path, data_dir: Path, embedder: Embedder | None = None, ocr: bool = False):
        self.root = Path(root).resolve()
        key = hashlib.sha1(str(self.root).encode()).hexdigest()[:12]
        base = data_dir / "indexes"
        base.mkdir(parents=True, exist_ok=True)
        self.db_path = base / f"{key}.db"
        self.cache_dir = base / f"{key}_images"
        self.versions_dir = base / f"{key}_versions"
        self.extracts = ExtractCache(base / f"{key}_extracted")  # Markdown of PDFs/Office files, for previews
        self.embedder = embedder
        self.embed_error = ""
        # Set by the app: a cross-encoder that re-orders the top results (see recall/rerank.py).
        self.reranker = None
        self.rerank_error = ""
        self.ocr = ocr
        self.images = ImageText(self._conn, ocr_enabled=ocr)
        # Set by the app: fn(image_bytes, media_type) -> caption, and the per-run limit.
        self.captioner: Callable[[bytes, str], str] | None = None
        self.caption_limit = 100
        self.version = 0  # bumped whenever indexed content changes (the UI polls this)
        self.last_changed: list[str] = []
        self.progress = {"running": False, "phase": "", "done": 0, "total": 0, "current": "", "errors": 0,
                         "finished_at": None}
        self._lock = threading.Lock()
        self._vec_cache: tuple | None = None
        self._doc_vec_cache: tuple | None = None
        self._init_schema()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = None
            if "meta" in tables:
                row = c.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                version = row[0] if row else None
            if tables and version != SCHEMA_VERSION:
                for t in REBUILT_TABLES:
                    c.execute(f"DROP TABLE IF EXISTS {t}")
            c.executescript(SCHEMA)
            c.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (SCHEMA_VERSION,))
            # Vectors from a different embedding model are not comparable: drop them.
            want = self.embedder.name if self.embedder else ""
            row = c.execute("SELECT value FROM meta WHERE key='embed_model'").fetchone()
            if want and (row[0] if row else None) != want:
                c.execute("DELETE FROM vectors")
                c.execute("INSERT OR REPLACE INTO meta VALUES('embed_model', ?)", (want,))

    # ------------------------------------------------------------------ files

    def iter_files(self, include_images: bool = False):
        exts = set(NOTE_TYPES) | (IMAGE_EXTS if include_images else set())
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
            for f in sorted(filenames):
                if f.startswith(".") or f.startswith("~$"):  # hidden / Office lock files
                    continue
                p = Path(dirpath) / f
                if p.suffix.lower() in exts:
                    yield p

    def extract(self, p: Path, ctx: Context | None = None) -> ExtractedDoc:
        """A file as Markdown, from the extraction cache when it's unchanged (see extractors/cache.py)."""
        return self.extracts.extract(p, p.relative_to(self.root).as_posix(), ctx or self.context(), extract)

    def context(self) -> Context:
        return Context(root=self.root, cache_dir=self.cache_dir, ocr=ocr_bytes if self.ocr else None)

    def resolve_image(self, url: str) -> Path | None:
        """Map an /api/file or /api/cache-image URL back to a local file (or None)."""
        u = urlparse(url)
        if u.path == "/api/file":
            rel = parse_qs(u.query).get("path", [""])[0]
            p = (self.root / rel).resolve()
            try:
                p.relative_to(self.root)
            except ValueError:
                return None
            return p if p.is_file() else None
        if u.path.startswith("/api/cache-image/"):
            name = unquote(u.path.rsplit("/", 1)[-1])
            if not re.fullmatch(r"[0-9a-f]{20}\.(png|jpg|jpeg|gif|webp)", name):
                return None
            p = self.cache_dir / name
            return p if p.is_file() else None
        return None
