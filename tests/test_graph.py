import numpy as np

from recall.embeddings import Embedder
from recall.index import Index


def test_outgoing_links_and_backlinks(index):
    l = index.links("networking/vlans.md")
    assert {x["path"] for x in l["outgoing"]} == {"backups.md", "networking/router.md"}
    assert l["backlinks"] == []
    assert [x["path"] for x in index.links("backups.md")["backlinks"]] == ["networking/vlans.md"]


def test_links_follow_edits(index, notes):
    import os
    import time

    (notes / "backups.md").write_text("# Backups\n\nSee [[router]].\n")
    os.utime(notes / "backups.md", (time.time() + 5, time.time() + 5))
    index.build()
    assert [x["path"] for x in index.links("backups.md")["outgoing"]] == ["networking/router.md"]
    assert {x["path"] for x in index.links("networking/router.md")["backlinks"]} == {"backups.md", "networking/vlans.md"}


def test_dangling_links_hidden(index, notes):
    (notes / "dangling.md").write_text("# D\n\n[gone](missing.md)\n")
    index.build()
    assert index.links("dangling.md")["outgoing"] == []


def test_graph_nodes_and_link_edges(index):
    g = index.graph(similar=False)
    ids = {n["id"] for n in g["nodes"]}
    assert len(ids) == 11 and "images/vlan-diagram.png" not in ids  # notes only
    assert {(e["source"], e["target"]) for e in g["edges"]} == {
        ("networking/vlans.md", "backups.md"), ("networking/vlans.md", "networking/router.md")}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])


class TopicEmbedder(Embedder):
    """Deterministic: texts mentioning VLAN/router/DNS cluster together."""

    name = "topic"
    min_similarity = 0.5

    def _v(self, t):
        t = t.lower()
        v = np.array([("vlan" in t) + ("router" in t) + ("dns" in t), 0.3, ("upgrade" in t) * 1.0], dtype=np.float32)
        return v / np.linalg.norm(v)

    def embed_passages(self, texts):
        return np.vstack([self._v(t) for t in texts])

    def embed_query(self, text):
        return self._v(text)


def test_similarity_edges(notes, data):
    idx = Index(notes, data, embedder=TopicEmbedder())
    idx.build()
    g = idx.graph(similar=True, k=3)
    sim = [e for e in g["edges"] if e["type"] == "similar"]
    assert sim and all(e["weight"] >= idx.doc_similarity_threshold() for e in sim)
    pairs = [tuple(sorted((e["source"], e["target"]))) for e in g["edges"]]
    assert len(pairs) == len(set(pairs))  # a link and a similarity edge are never duplicated
    assert {s["path"] for s in idx.similar("networking/router.md")} >= {"wiki.html"}
