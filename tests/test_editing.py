import pytest
from fastapi.testclient import TestClient

from recall import editing
from recall.app import create_app
from recall.editing import EditError
from tests.conftest import make_png

H = {"X-Recall": "1"}


def test_read_and_save_roundtrip(notes, tmp_path):
    src = editing.read_source(notes, "backups.md")
    res = editing.save_source(notes, tmp_path / "v", "backups.md", src["content"] + "\nMore.\n", src["mtime_ns"])
    assert res["changed"] and (notes / "backups.md").read_text().endswith("More.\n")
    assert not list(notes.glob(".recall-*"))  # atomic write leaves no temp files


def test_unchanged_save_is_noop(notes, tmp_path):
    src = editing.read_source(notes, "backups.md")
    assert not editing.save_source(notes, tmp_path / "v", "backups.md", src["content"], src["mtime_ns"])["changed"]
    assert editing.list_versions(tmp_path / "v", "backups.md") == []


def test_conflict_detected_and_force(notes, tmp_path):
    src = editing.read_source(notes, "backups.md")
    import os
    import time

    (notes / "backups.md").write_text("changed elsewhere")
    os.utime(notes / "backups.md", ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    with pytest.raises(EditError) as e:
        editing.save_source(notes, tmp_path / "v", "backups.md", "mine", src["mtime_ns"])
    assert e.value.status == 409
    editing.save_source(notes, tmp_path / "v", "backups.md", "mine", src["mtime_ns"], force=True)
    assert (notes / "backups.md").read_text() == "mine"


def test_versions_kept_and_capped(notes, tmp_path, monkeypatch):
    monkeypatch.setattr(editing, "KEEP_VERSIONS", 3)
    v = tmp_path / "v"
    for i in range(5):
        editing.save_source(notes, v, "backups.md", f"version {i}", None)
    vs = editing.list_versions(v, "backups.md")
    assert len(vs) == 3
    assert editing.read_version(v, "backups.md", vs[0]["id"]) == "version 3"  # newest first
    with pytest.raises(EditError):
        editing.read_version(v, "backups.md", "../../etc")


@pytest.mark.parametrize("rel,status", [
    ("../outside.md", 400), ("/etc/passwd", 415), (".hidden/secret.md", 400), ("k8s-upgrade.pdf", 415), ("", 400)])
def test_unsafe_or_uneditable_paths(notes, tmp_path, rel, status):
    with pytest.raises(EditError) as e:
        editing.save_source(notes, tmp_path / "v", rel, "x", None)
    assert e.value.status == status


def test_create_note(notes):
    r = editing.create_note(notes, "projects/new-idea")
    assert r["path"] == "projects/new-idea.md"
    assert (notes / "projects/new-idea.md").read_text() == "# new idea\n\n"
    with pytest.raises(EditError) as e:
        editing.create_note(notes, "projects/new-idea.md")
    assert e.value.status == 409
    with pytest.raises(EditError):
        editing.create_note(notes, "evil.html")


def test_upload_next_to_note(notes):
    png = make_png()
    r = editing.save_upload(notes, "networking/vlans.md", "My Screen Shot.png", png)
    assert r == {"name": "My-Screen-Shot.png", "path": "networking/My-Screen-Shot.png"}
    assert editing.save_upload(notes, "networking/vlans.md", "My Screen Shot.png", png)["name"] == "My-Screen-Shot.png"
    other = editing.save_upload(notes, "networking/vlans.md", "My Screen Shot.png", make_png(rgb=(1, 2, 3)))
    assert other["name"] == "My-Screen-Shot-1.png"
    with pytest.raises(EditError):
        editing.save_upload(notes, "networking/vlans.md", "script.js", b"alert(1)")


def test_list_folders_includes_empty(notes):
    (notes / "empty" / "deeper").mkdir(parents=True)
    folders = editing.list_folders(notes)
    assert {"networking", "images", "empty", "empty/deeper"} <= set(folders)
    assert not any(f.startswith(".") for f in folders)


def test_move_note_keeps_its_links(notes, tmp_path):
    r = editing.move_file(notes, tmp_path / "v", "networking/vlans.md", "guides/net")
    assert r["path"] == "guides/net/vlans.md" and not (notes / "networking/vlans.md").exists()
    text = (notes / "guides/net/vlans.md").read_text()
    assert "](../../images/vlan-diagram.png)" in text
    assert "[the router notes](../../networking/router.md)" in text
    assert "[[backups]]" in text and "![[topology.png]]" in text  # found by name, unchanged
    assert 'echo "![fake](nothere.png)"' in text  # code untouched


def test_move_updates_links_to_the_file(notes, tmp_path):
    (notes / "index.md").write_text(
        "See [VLANs](networking/vlans.md#trunk-configuration), ![t](<networking/topology.png>) "
        "and [web](https://example.com/networking/vlans.md).\n\n```\n[code](networking/vlans.md)\n```\n")
    r = editing.move_file(notes, tmp_path / "v", "networking/vlans.md", "")
    assert r == {"path": "vlans.md", "updated": ["index.md"]}
    text = (notes / "index.md").read_text()
    assert "[VLANs](vlans.md#trunk-configuration)" in text
    assert "](<networking/topology.png>)" in text and "https://example.com/networking/vlans.md" in text
    assert "[code](networking/vlans.md)" in text
    editing.move_file(notes, tmp_path / "v", "networking/topology.png", "images/net diagrams")
    assert "![t](<images/net%20diagrams/topology.png>)" in (notes / "index.md").read_text()


def test_move_carries_version_history(notes, tmp_path):
    v = tmp_path / "v"
    editing.save_source(notes, v, "backups.md", "edited", None)
    editing.move_file(notes, v, "backups.md", "archive")
    assert editing.list_versions(v, "backups.md") == []
    assert len(editing.list_versions(v, "archive/backups.md")) == 1


@pytest.mark.parametrize("rel,folder,status", [
    ("backups.md", "", 400), ("networking/router.md", "", 409), ("missing.md", "x", 404),
    ("backups.md", "../outside", 400), ("backups.md", "networking/../..", 400), ("backups.md", ".hidden", 400)])
def test_move_refusals(notes, tmp_path, rel, folder, status):
    (notes / "router.md").write_text("# Another router")
    with pytest.raises(EditError) as e:
        editing.move_file(notes, tmp_path / "v", rel, folder)
    assert e.value.status == status


# ------------------------------------------------------------------ API


@pytest.fixture()
def api(data, notes):
    c = TestClient(create_app(auto_index=False))
    c.post("/api/settings", json={"notes_dir": str(notes), "provider": "none", "semantic_search": False, "ocr": False},
           headers=H)
    c.post("/api/index?wait=true", headers=H)
    return c


def test_api_edit_flow(api):
    d = api.get("/api/doc", params={"path": "backups.md"}).json()
    assert d["editable"] is True
    assert api.get("/api/doc", params={"path": "k8s-upgrade.pdf"}).json()["editable"] is False
    src = api.get("/api/source", params={"path": "backups.md"}).json()
    r = api.put("/api/source", json={"path": "backups.md", "content": "# Backups\n\nNow with zebrafish.\n",
                                     "base_mtime_ns": src["mtime_ns"]}, headers=H)
    assert r.status_code == 200 and r.json()["changed"]
    api.post("/api/index?wait=true", headers=H)
    assert api.get("/api/search", params={"q": "zebrafish"}).json()["results"][0]["path"] == "backups.md"
    stale = api.put("/api/source", json={"path": "backups.md", "content": "x", "base_mtime_ns": src["mtime_ns"]}, headers=H)
    assert stale.status_code == 409
    vs = api.get("/api/versions", params={"path": "backups.md"}).json()["versions"]
    assert len(vs) == 1
    old = api.get("/api/version", params={"path": "backups.md", "id": vs[0]["id"]}).json()["content"]
    assert "Restic" in old


def test_api_requires_header_for_writes(api):
    assert api.put("/api/source", json={"path": "backups.md", "content": "x"}).status_code == 403


def test_api_new_note_render_and_upload(api, notes):
    r = api.post("/api/note", json={"path": "ideas/today"}, headers=H)
    assert r.json()["path"] == "ideas/today.md"
    up = api.post("/api/upload", data={"note": "ideas/today.md"}, files={"file": ("shot.png", make_png(), "image/png")},
                  headers=H)
    assert up.json()["name"] == "shot.png" and (notes / "ideas/shot.png").exists()
    md = api.post("/api/render", json={"path": "ideas/today.md", "markdown": "![s](shot.png) [[backups]]"}, headers=H)
    assert md.json()["markdown"] == "![s](/api/file?path=ideas/shot.png) [backups](#doc=backups.md)"
    bad = api.post("/api/upload", data={"note": "../x.md"}, files={"file": ("a.png", make_png(), "image/png")}, headers=H)
    assert bad.status_code == 400


def test_api_related_and_graph(api):
    r = api.get("/api/related", params={"path": "backups.md"}).json()
    assert [x["path"] for x in r["backlinks"]] == ["networking/vlans.md"] and r["similar"] == []
    g = api.get("/api/graph").json()
    assert len(g["nodes"]) == 11 and len(g["edges"]) == 2


def test_api_search_modes(api):
    assert api.get("/api/search", params={"q": "VLAN", "mode": "keyword"}).status_code == 200
    assert api.get("/api/search", params={"q": "VLAN", "mode": "bogus"}).status_code == 422


def test_version_stamp_survives_json_numbers(notes, tmp_path):
    """Browsers parse JSON numbers as doubles; nanosecond mtimes must travel as strings."""
    import json

    src = editing.read_source(notes, "backups.md")
    assert isinstance(src["mtime_ns"], str)
    roundtrip = json.loads(json.dumps(src))["mtime_ns"]
    assert editing.save_source(notes, tmp_path / "v", "backups.md", "x", roundtrip)["changed"]


def test_api_folders_and_move(api, notes):
    assert "networking" in api.get("/api/folders").json()["folders"]
    r = api.post("/api/move", json={"path": "backups.md", "folder": "archive"}, headers=H)
    assert r.status_code == 200 and r.json()["path"] == "archive/backups.md"
    assert "networking/vlans.md" not in r.json()["updated"]  # [[backups]] is a wiki link: found by name
    api.post("/api/index?wait=true", headers=H)
    assert api.get("/api/doc", params={"path": "archive/backups.md"}).status_code == 200
    assert api.post("/api/move", json={"path": "archive/backups.md", "folder": "archive"}, headers=H).status_code == 400
