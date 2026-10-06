from recall.config import apply_update
from recall.index import Index
from recall.rerank import Reranker


class KeywordReranker(Reranker):
    """Scores passages by whether they mention a word: enough to check the re-ordering plumbing."""

    def __init__(self, word):
        self.word, self.calls = word, 0

    def score(self, query, passages):
        self.calls += 1
        return [1.0 if self.word in p.lower() else 0.0 for p in passages]


class BrokenReranker(Reranker):
    def score(self, query, passages):
        raise RuntimeError("model missing")


def test_reranker_reorders_hybrid_but_not_exact_search(notes, data):
    idx = Index(notes, data)
    idx.build()
    plain = [r["path"] for r in idx.search("upgrade", limit=5)]
    idx.reranker = KeywordReranker("etcd")
    hits = idx.search("upgrade", limit=5)
    assert "etcd" in hits[0]["text"].lower() and "rerank" in hits[0]
    assert sorted(r["path"] for r in hits) == sorted(plain)  # same candidates, new order
    calls = idx.reranker.calls
    assert [r["path"] for r in idx.search("upgrade", limit=5, mode="keyword")] == plain
    assert idx.reranker.calls == calls  # "Exact" search keeps the keyword order


def test_rerank_failure_falls_back_to_fused_order(notes, data):
    idx = Index(notes, data)
    idx.build()
    plain = [r["id"] for r in idx.search("VLAN trunk", limit=5)]
    idx.reranker = BrokenReranker()
    assert [r["id"] for r in idx.search("VLAN trunk", limit=5)] == plain
    st = idx.stats()
    assert st["rerank"] is False and "model missing" in st["rerank_error"]


def test_state_attaches_reranker_from_settings(notes, data, monkeypatch):
    from recall import state as state_mod

    monkeypatch.setattr(state_mod, "FastEmbedReranker", lambda model: KeywordReranker(model))
    st = state_mod.State(background=False)
    apply_update(st.settings, {"notes_dir": str(notes), "semantic_search": False, "rerank": True,
                               "rerank_model": "tiny"})
    assert st.open_index().reranker.word == "tiny"
    apply_update(st.settings, {"rerank": False})
    assert st.open_index().reranker is None
