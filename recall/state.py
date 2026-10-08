"""Server state shared by the API routes: settings, the open index and the folder watcher."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import HTTPException, Request

from .config import data_dir, load_settings
from .embeddings import FastEmbedEmbedder
from .embeddings import available as embeddings_available
from .rerank import FastEmbedReranker
from .rerank import available as rerank_available
from .images import ocr_available
from .editing import prune_versions
from .index import Index, prune_other_indexes
from .watcher import Watcher


class State:
    def __init__(self, background: bool = True):
        self.settings = load_settings()
        self.index: Index | None = None
        self.watcher: Watcher | None = None
        self.background = background  # start the folder watcher (off in most tests)
        self.lock = threading.RLock()
        self._signature = None
        self._embedders: dict[str, FastEmbedEmbedder] = {}
        self._rerankers: dict[str, FastEmbedReranker] = {}

    def _embedder(self):
        s = self.settings
        if not s.semantic_search or not embeddings_available():
            return None
        if s.embed_model not in self._embedders:
            self._embedders[s.embed_model] = FastEmbedEmbedder(s.embed_model)
        return self._embedders[s.embed_model]

    def _reranker(self):
        s = self.settings
        if not s.rerank or not rerank_available():
            return None
        if s.rerank_model not in self._rerankers:
            self._rerankers[s.rerank_model] = FastEmbedReranker(s.rerank_model)
        return self._rerankers[s.rerank_model]

    @property
    def signature(self):
        """Changes whenever the settings that define the index (folder, models, OCR, watching) change."""
        return self._signature

    def open_index(self) -> Index | None:
        """Return the index for the current settings, rebuilding the Index object if they changed."""
        with self.lock:
            s = self.settings
            nd = s.notes_dir
            if not nd or not Path(nd).is_dir():
                self._close()
                return None
            sig = (str(Path(nd).resolve()), s.semantic_search, s.embed_model, s.ocr, s.watch)
            if self.index is None or sig != self._signature:
                self._close()
                prune_other_indexes(data_dir(), Path(nd))
                self.index = Index(Path(nd), data_dir(), embedder=self._embedder(), ocr=s.ocr and ocr_available())
                prune_versions(self.index.versions_dir)
                self._signature = sig
                if s.watch and self.background:
                    self.watcher = Watcher(self.index)
                    self.watcher.start()
            reranker = self._reranker()
            if reranker is not self.index.reranker:
                self.index.reranker, self.index.rerank_error = reranker, ""
            return self.index

    def _close(self):
        if self.watcher:
            self.watcher.stop()
        self.index, self.watcher, self._signature = None, None, None

    def require_index(self) -> Index:
        idx = self.open_index()
        if idx is None:
            raise HTTPException(400, "No notes directory configured. Open Settings and choose one.")
        return idx

    def reindex_async(self, full: bool = False, wait_turn: bool = False) -> None:
        """Start a background build. wait_turn=True queues behind a running build instead of skipping."""
        idx = self.open_index()
        if idx is not None:
            threading.Thread(target=idx.build, kwargs={"full": full, "block": wait_turn}, daemon=True).start()


def get_state(request: Request) -> State:
    """FastAPI dependency: the app's State."""
    return request.app.state.recall


def get_index(request: Request) -> Index:
    """FastAPI dependency: the open index (400 if no notes folder is configured)."""
    return get_state(request).require_index()
