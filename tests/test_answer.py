from recall.answer import answer_events, retrieve
from recall.config import Settings
from recall.index import Filters


def collect(events):
    out = {"sources": None, "text": "", "done": None, "error": None}
    for ev, data in events:
        if ev == "delta":
            out["text"] += data
        else:
            out[ev] = data
    return out


def test_retrieve_numbers_sources(index):
    hits = retrieve(index, "VLAN trunk", None, 5)
    assert [h["n"] for h in hits] == list(range(1, len(hits) + 1))


def test_passages_with_sources_and_images(index):
    out = collect(answer_events(index, Settings(), "kubelet drain upgrade", "summary"))
    assert out["sources"][0]["path"] == "k8s-upgrade.pdf"
    assert out["done"] == {}
    assert out["text"].startswith("### [1] ")
    assert "/api/cache-image/" in out["text"]  # the PDF figure comes along


def test_full_passages_longer_than_key_passages(index):
    s = collect(answer_events(index, Settings(), "VLAN network router DNS", "summary"))
    r = collect(answer_events(index, Settings(), "VLAN network router DNS", "report"))
    assert len(r["text"]) > len(s["text"])


def test_no_hits(index):
    out = collect(answer_events(index, Settings(), "xylophone quasar", "summary"))
    assert out["sources"] == [] and "No matching notes" in out["text"]


def test_filters_reach_retrieval(index):
    out = collect(answer_events(index, Settings(), "VLAN upgrade", "summary", Filters(types=["pdf"])))
    assert {s["path"] for s in out["sources"]} == {"k8s-upgrade.pdf"}


def test_top_k_limits_sources(index):
    out = collect(answer_events(index, Settings(top_k=2), "VLAN network router DNS", "report"))
    assert len(out["sources"]) <= 2
