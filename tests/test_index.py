import os
import time

from recall.index import Filters, Index, build_match_query


def paths(results):
    return [r["path"] for r in results]


def test_build_indexes_every_type_and_skips_hidden(index):
    st = index.stats()
    assert st["by_type"] == {".md": 3, ".pdf": 2, ".docx": 1, ".pptx": 1, ".xlsx": 1, ".csv": 1, ".html": 1, ".txt": 1}
    assert [e["path"] for e in st["errors"]] == ["broken.pdf"]
    assert not index.search("secret should be indexed")


def test_incremental_rebuild(index, notes):
    assert index.build() == {"added": 0, "updated": 0, "unchanged": 13, "removed": 0, "errors": 0, "embedded": 0}
    (notes / "backups.md").write_text("# Backups\n\nNow using Borg instead.\n")
    os.utime(notes / "backups.md", (time.time() + 5, time.time() + 5))
    (notes / "todo.txt").unlink()
    (notes / "new.md").write_text("# New\n\nZeppelin notes")
    s = index.build()
    assert (s["added"], s["updated"], s["removed"]) == (1, 1, 1)
    assert paths(index.search("borg")) == ["backups.md"]
    assert not index.search("restic")
    assert paths(index.search("zeppelin")) == ["new.md"]


def test_full_rebuild(index):
    s = index.build(full=True)
    assert s["added"] == 12 and s["errors"] == 1


def test_search_each_file_type(index):
    cases = {
        "trunk 802.1Q tagging": "networking/vlans.md",
        "drain node kubelet": "k8s-upgrade.pdf",
        "VPN disk encryption": "onboarding.docx",
        "Prometheus monitoring": "roadmap.pptx",
        "Grafana": "roadmap.pptx",  # speaker notes
        "hypervisor pve01": "inventory.xlsx",
        "iot subnet": "subnets.csv",
        "Pi-hole Quad9": "wiki.html",
        "UPS battery": "todo.txt",
    }
    for q, expected in cases.items():
        assert paths(index.search(q))[0] == expected, q


def test_search_stemming_and_question_phrasing(index):
    assert paths(index.search("How do I upgrade the kubernetes clusters?"))[0] == "k8s-upgrade.pdf"
    assert "backups.md" in paths(index.search("what is the retention for snapshots"))


def test_search_results_carry_images_and_snippets(index):
    r = index.search("trunk")[0]
    assert r["heading"] == "VLAN Setup Guide > Trunk configuration"
    assert r["anchor"] == "trunk-configuration"
    assert r["images"][0]["url"] == "/api/file?path=images/vlan-diagram.png"
    assert "\x02" in r["snippet"]


def test_tags_searchable(index):
    assert "networking/vlans.md" in paths(index.search("homelab"))


def test_filter_by_type_group(index):
    q = "upgrade VLAN VPN Prometheus Pi-hole"
    assert set(paths(index.search(q, Filters(types=["pdf"])))) == {"k8s-upgrade.pdf"}
    assert set(paths(index.search(q, Filters(types=["word"])))) == {"onboarding.docx"}
    assert set(paths(index.search(q, Filters(types=["powerpoint"])))) == {"roadmap.pptx"}
    assert set(paths(index.search(q, Filters(types=["html"])))) == {"wiki.html"}
    assert set(paths(index.search(q, Filters(types=["markdown"])))) == {"networking/vlans.md"}


def test_filter_by_multiple_types_and_raw_extensions(index):
    q = "hypervisor iot"
    assert set(paths(index.search(q, Filters(types=["spreadsheet"])))) == {"inventory.xlsx", "subnets.csv"}
    assert set(paths(index.search(q, Filters(types=["csv"])))) == {"subnets.csv"}
    assert set(paths(index.search(q, Filters(types=[".xlsx"])))) == {"inventory.xlsx"}


def test_filter_by_folder(index):
    q = "router VLAN backups restic"
    assert set(paths(index.search(q, Filters(folder="networking")))) == {"networking/vlans.md", "networking/router.md"}
    assert set(paths(index.search(q, Filters(folder="/networking/")))) == {"networking/vlans.md", "networking/router.md"}
    assert index.search(q, Filters(folder="networ")) == []  # prefix of a name is not a folder match


def test_filter_by_single_document(index):
    assert set(paths(index.search("VLAN topology trunk router", Filters(path="networking/router.md")))) == {
        "networking/router.md"
    }


def test_filters_combined(index):
    assert index.search("VLAN", Filters(types=["pdf"], folder="networking")) == []


def test_per_doc_cap(index):
    res = index.search("VLAN trunk topology intro", limit=10, per_doc=1)
    assert len(paths(res)) == len(set(paths(res)))


def test_match_query_is_injection_safe(index):
    for q in ['"unbalanced', "NEAR(a b)", "a AND OR NOT", "col:val", "*", "'; DROP TABLE docs; --", "(((", ""]:
        index.search(q)  # must not raise
    assert build_match_query('the "trunk port" config') == '"trunk port" OR "config"*'


def test_index_per_notes_dir(notes, data, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    a, b = Index(notes, data), Index(other, data)
    assert a.db_path != b.db_path
