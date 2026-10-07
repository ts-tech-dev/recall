"""Local text embeddings for semantic search (ONNX via fastembed — no GPU or API needed)."""

from __future__ import annotations

import threading

import numpy as np

from .config import downloads_allowed, model_dir
from .net import model_load_error


class Embedder:
    """Interface: `embed_passages` / `embed_query` return L2-normalized float32 vectors."""

    name = "base"
    min_similarity = 0.0  # cosine below this is treated as "not related"

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        raise NotImplementedError


def _normalize(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float32)
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    return a / np.maximum(n, 1e-12)


class FastEmbedEmbedder(Embedder):
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        self.name = model
        # bge-small: unrelated text tops out around 0.55, related passages score ~0.58-0.75;
        # bge-base separates them more (unrelated up to ~0.48), so the same cutoff works with room to spare.
        self.min_similarity = 0.56 if "bge" in model.lower() else 0.3
        self._model = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                try:
                    # offline: only the model folder is read, never the network
                    self._model = TextEmbedding(
                        self.name, cache_dir=str(model_dir()), local_files_only=not downloads_allowed()
                    )
                except Exception as e:
                    err = model_load_error(self.name, e)
                    if err is e:
                        raise
                    raise err from e
            return self._model

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return _normalize(np.array(list(self._get().passage_embed(texts, batch_size=32))))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(np.array(list(self._get().query_embed(text)))[0])


def available() -> bool:
    try:
        import fastembed  # noqa: F401

        return True
    except ImportError:
        return False
