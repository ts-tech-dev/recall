"""Semantic + hybrid search with the real local embedding model."""

import numpy as np
import pytest

from recall.embeddings import Embedder
from recall.index import Filters, Index


@pytest.fixture()
def sem_index(notes, data, embedder):
    idx = Index(notes, data, embedder=embedder)
    idx.build()
    return idx


def paths(rs):
    return [r["path"] for r in rs]


def test_every_chunk_embedded(sem_index):
    st = sem_index.stats()
    assert st["vectors"] == st["chunks"] > 0 and st["semantic"]


def test_finds_meaning_without_shared_words(sem_index):
    assert sem_index.search("power outage protection hardware", mode="keyword") == []
    assert paths(sem_index.search("power outage protection hardware", mode="semantic"))[0] == "todo.txt"
    assert paths(sem_index.search("name resolution server for the lab", mode="semantic"))[0] == "wiki.html"


def test_hybrid_merges_and_labels_matches(sem_index):
    rs = sem_index.search("power outage protection hardware")  # hybrid default
    assert rs[0]["path"] == "todo.txt" and rs[0]["match"] == "semantic"
    both = sem_index.search("trunk port VLAN tagging")
    assert both[0]["path"] == "networking/vlans.md" and both[0]["match"] == "both"
    assert both[0]["snippet"]


def test_filters_apply_to_semantic_results(sem_index):
    rs = sem_index.search("power outage protection hardware", Filters(types=["pdf"]), mode="semantic")
    assert all(r["ext"] == ".pdf" for r in rs)
    rs = sem_index.search("name resolution server", Filters(folder="networking"), mode="semantic")
    assert all(r["path"].startswith("networking/") for r in rs)


def test_incremental_build_does_not_reembed(sem_index, notes):
    assert sem_index.build()["embedded"] == 0
    (notes / "ups.md").write_text("# Power\n\nThe rack has an uninterruptible power supply.\n")
    s = sem_index.build()
    assert s["added"] == 1 and s["embedded"] == 1


def test_similar_notes(sem_index):
    sims = sem_index.similar("networking/vlans.md")
    assert sims and all(s["path"] != "networking/vlans.md" for s in sims)
    assert sims == sorted(sims, key=lambda s: -s["similarity"])


class BrokenEmbedder(Embedder):
    name = "broken"

    def embed_passages(self, texts):
        raise RuntimeError("model download failed")

    def embed_query(self, text):
        raise RuntimeError("model download failed")


def test_embedding_failure_falls_back_to_keyword(notes, data):
    idx = Index(notes, data, embedder=BrokenEmbedder())
    idx.build()
    assert "model download failed" in idx.stats()["embed_error"]
    assert paths(idx.search("trunk VLAN"))[0] == "networking/vlans.md"


class FakeEmbedder(Embedder):
    def __init__(self, name):
        self.name = name

    def embed_passages(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32) / 2

    def embed_query(self, text):
        return np.ones(4, dtype=np.float32) / 2


def test_changing_model_drops_old_vectors(notes, data):
    a = Index(notes, data, embedder=FakeEmbedder("model-a"))
    a.build()
    assert a.stats()["vectors"] > 0
    b = Index(notes, data, embedder=FakeEmbedder("model-b"))
    assert b.stats()["vectors"] == 0
    assert b.build()["embedded"] == b.stats()["chunks"]
