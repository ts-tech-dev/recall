"""Local re-ranking of search results with a cross-encoder (ONNX via fastembed; no GPU or API needed).

Keyword and embedding search find candidates quickly; the cross-encoder then reads the question and each
candidate passage together, which ranks paraphrased questions much better.
"""

from __future__ import annotations

import threading

from .config import downloads_allowed, model_dir
from .net import model_load_error

DEFAULT_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"  # ~80 MB, English


class Reranker:
    """Interface: `score(query, passages)` returns one relevance score per passage (higher is better)."""

    name = "base"

    def score(self, query: str, passages: list[str]) -> list[float]:
        raise NotImplementedError


class FastEmbedReranker(Reranker):
    def __init__(self, model: str = DEFAULT_MODEL):
        self.name = model
        self._model = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                try:
                    # offline: only the model folder is read, never the network
                    self._model = TextCrossEncoder(
                        self.name, cache_dir=str(model_dir()), local_files_only=not downloads_allowed()
                    )
                except Exception as e:
                    err = model_load_error(self.name, e)
                    if err is e:
                        raise
                    raise err from e
            return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self._get().rerank(query, passages, batch_size=16)]


def available() -> bool:
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: F401

        return True
    except ImportError:
        return False
