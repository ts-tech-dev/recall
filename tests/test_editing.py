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


def test_versions_older_than_max_age_are_hidden_and_deleted(notes, tmp_path):
    import time

    v = tmp_path / "v"
    editing.save_source(notes, v, "backups.md", "recent", None)
    vd = next(v.iterdir())
    old = vd / f"{time.time_ns() - (editing.VERSION_MAX_AGE + 60) * 10**9}.bak"
    old.write_text("old")
    with pytest.raises(EditError):
        editing.read_version(v, "backups.md", old.stem)
    assert len(editing.list_versions(v, "backups.md")) == 1
    assert not old.exists()


def test_prune_versions_sweeps_notes_not_edited_since(notes, tmp_path, monkeypatch):
    v = tmp_path / "v"
    editing.save_source(notes, v, "backups.md", "edited", None)
    monkeypatch.setattr(editing, "VERSION_MAX_AGE", -1)
    editing.prune_versions(v)
    assert list(v.iterdir()) == []  # the emptied note folder is removed too


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


def test_upload_into_images_folder_next_to_note(notes):
    png = make_png()
    assert not (notes / "networking" / "images").exists()
    r = editing.save_upload(notes, "networking/vlans.md", "My Screen Shot.png", png)
    assert r == {"name": "vlans_1.png", "path": "networking/images/vlans_1.png", "link": "images/vlans_1.png"}
    assert (notes / "networking/images/vlans_1.png").read_bytes() == png
    # the same image again is reused; a new one gets the next number, whatever its file name
    assert editing.save_upload(notes, "networking/vlans.md", "pasted.png", png)["name"] == "vlans_1.png"
    assert editing.save_upload(notes, "networking/vlans.md", "x.jpg", make_png(rgb=(1, 2, 3)))["name"] == "vlans_2.jpg"
    # another note in the same folder numbers its own images
    assert editing.save_upload(notes, "networking/My Note.md", "a.png", make_png(rgb=(4, 5, 6)))["name"] == "My-Note_1.png"
    with pytest.raises(EditError):
        editing.save_upload(notes, "networking/vlans.md", "script.js", b"alert(1)")
    # a top-level note uses the existing top-level images folder
    assert editing.save_upload(notes, "backups.md", "b.png", png)["path"] == "images/backups_1.png"


def test_upload_refuses_images_file(notes):
    (notes / "ideas").mkdir()
    (notes / "ideas" / "images").write_text("not a folder")
    with pytest.raises(EditError):
        editing.save_upload(notes, "ideas/today.md", "a.png", make_png())


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
    assert r == {"path": "vlans.md", "updated": ["index.md"], "images": []}
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
    c.post("/api/settings", json={"notes_dir": str(notes), "semantic_search": False, "rerank": False, "ocr": False},
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
    assert up.json()["link"] == "images/today_1.png" and (notes / "ideas/images/today_1.png").exists()
    md = api.post("/api/render", json={"path": "ideas/today.md", "markdown": "![s](images/today_1.png) [[backups]]"},
                  headers=H)
    assert md.json()["markdown"] == "![s](/api/file?path=ideas/images/today_1.png) [backups](#doc=backups.md)"
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


def test_move_note_takes_its_images_along(notes, tmp_path):
    (notes / "trips" / "images").mkdir(parents=True)
    (notes / "trips" / "images" / "map.png").write_bytes(make_png())
    (notes / "trips" / "beach.jpg").write_bytes(make_png(rgb=(9, 9, 9)))
    (notes / "trips" / "rome.md").write_text(
        "# Rome\n\n![map](images/map.png)\n\n<img src=\"beach.jpg\">\n\n![d](../images/vlan-diagram.png)\n")
    r = editing.move_file(notes, tmp_path / "v", "trips/rome.md", "archive/2025")
    assert r["images"] == ["archive/2025/images/beach.jpg", "archive/2025/images/map.png"]
    assert (notes / "archive/2025/images/map.png").is_file() and (notes / "archive/2025/images/beach.jpg").is_file()
    assert not (notes / "trips/images").exists() and not (notes / "trips/beach.jpg").exists()  # emptied folder removed
    text = (notes / "archive/2025/rome.md").read_text()
    # all of them end up in the images folder next to the note
    assert "![map](images/map.png)" in text and '<img src="images/beach.jpg">' in text
    assert "![d](../../images/vlan-diagram.png)" in text  # images from elsewhere stay put, link updated
    assert (notes / "images/vlan-diagram.png").is_file()


def test_move_note_copies_images_other_notes_use(notes, tmp_path):
    (notes / "a" / "images").mkdir(parents=True)
    (notes / "a" / "images" / "shared.png").write_bytes(make_png())
    (notes / "a" / "one.md").write_text("![s](images/shared.png)\n")
    (notes / "a" / "two.md").write_text("![s](images/shared.png)\n")
    r = editing.move_file(notes, tmp_path / "v", "a/one.md", "b")
    assert r["images"] == ["b/images/shared.png"]
    assert (notes / "a/images/shared.png").is_file() and (notes / "b/images/shared.png").is_file()
    assert (notes / "b/one.md").read_text() == "![s](images/shared.png)\n"
    assert (notes / "a/two.md").read_text() == "![s](images/shared.png)\n"


def test_move_note_image_name_clash(notes, tmp_path):
    (notes / "a" / "images").mkdir(parents=True)
    (notes / "a" / "images" / "pic.png").write_bytes(make_png())
    (notes / "a" / "n.md").write_text("![p](images/pic.png)\n")
    (notes / "b" / "images").mkdir(parents=True)
    (notes / "b" / "images" / "pic.png").write_bytes(make_png(rgb=(1, 2, 3)))  # a different image
    r = editing.move_file(notes, tmp_path / "v", "a/n.md", "b")
    assert r["images"] == ["b/images/pic-1.png"]
    assert (notes / "b/n.md").read_text() == "![p](images/pic-1.png)\n"
    assert (notes / "b/images/pic.png").read_bytes() == make_png(rgb=(1, 2, 3))  # untouched


def test_saved_answer_gets_relative_links_and_its_images(api, notes):
    """A saved Ask result: links to notes and images become relative, extracted images are copied next to it."""
    hit = next(r for r in api.get("/api/search", params={"q": "kubelet"}).json()["results"] if r["images"])
    cached = hit["images"][0]["url"]
    assert cached.startswith("/api/cache-image/")
    content = ("# Question\n\n![k8s](" + cached + ")\n![dns](/api/file?path=images/vlan-diagram.png)\n\n"
               "1. [Router](#doc=networking%2Frouter.md)\n2. [Backups](#doc=backups.md)\n\n"
               "```\n[keep](#doc=backups.md)\n```\n")
    r = api.post("/api/note", json={"path": "answers/q.md", "content": content}, headers=H)
    assert r.status_code == 200
    text = (notes / "answers/q.md").read_text()
    assert "![k8s](images/q_1.png)" in text and (notes / "answers/images/q_1.png").is_file()
    assert "![dns](../images/vlan-diagram.png)" in text
    assert "[Router](../networking/router.md)" in text and "[Backups](../backups.md)" in text
    assert "[keep](#doc=backups.md)" in text  # code is left alone
    # and the previewer resolves them again
    d = api.get("/api/doc", params={"path": "answers/q.md"}).json()
    assert "(#doc=networking/router.md)" in d["markdown"] and "/api/file?path=answers/images/q_1.png" in d["markdown"]


def test_rename_note_updates_links_and_title(notes, tmp_path):
    (notes / "plan-a.md").write_text("# plan a\n\nSee [[plan-a#Goals|the goals]] and [self](plan-a.md).\n")
    (notes / "networking" / "uses.md").write_text(
        "# Uses\n\n[[Plan-A]], [[plan-a.md]], ![[plan-a]], [[backups]], [rel](../plan-a.md), "
        "[[networking/router]]\n\n```\n[[plan-a]]\n```\n")
    (notes / "untouched.md").write_text("# Untouched\n\n[[plan-ab]] and plan-a in text\n")
    before = (notes / "untouched.md").stat().st_mtime_ns
    r = editing.rename_file(notes, tmp_path / "v", "plan-a.md", "Plan B")
    assert r == {"path": "Plan B.md", "updated": ["networking/uses.md"], "retitled": True, "images": []}
    assert not (notes / "plan-a.md").exists()
    assert (notes / "Plan B.md").read_text() == "# Plan B\n\nSee [[Plan B#Goals|the goals]] and [self](Plan%20B.md).\n"
    assert (notes / "networking/uses.md").read_text() == (
        "# Uses\n\n[[Plan B]], [[Plan B.md]], ![[Plan B]], [[backups]], [rel](../Plan%20B.md), "
        "[[networking/router]]\n\n```\n[[plan-a]]\n```\n")
    assert (notes / "untouched.md").stat().st_mtime_ns == before


def test_rename_keeps_extension_folder_and_custom_title(notes, tmp_path):
    (notes / "networking" / "old.md").write_text("# A real title\n")
    r = editing.rename_file(notes, tmp_path / "v", "networking/old.md", "new.md")
    assert r["path"] == "networking/new.md" and not r["retitled"]
    assert (notes / "networking/new.md").read_text() == "# A real title\n"
    r = editing.rename_file(notes, tmp_path / "v", "networking/new.md", "v1.2 notes")
    assert r["path"] == "networking/v1.2 notes.md"


def test_rename_carries_version_history(notes, tmp_path):
    src = editing.read_source(notes, "backups.md")
    editing.save_source(notes, tmp_path / "v", "backups.md", "# Backups\n\nchanged\n", src["mtime_ns"])
    editing.rename_file(notes, tmp_path / "v", "backups.md", "restic")
    assert len(editing.list_versions(tmp_path / "v", "restic.md")) == 1


@pytest.mark.parametrize("rel,name,status", [
    ("backups.md", "networking/x", 400), ("backups.md", "backups", 400), ("backups.md", "todo.txt", 400),
    ("backups.md", ".hidden", 400), ("nope.md", "x", 404),
])
def test_rename_refusals(notes, tmp_path, rel, name, status):
    (notes / "todo.txt.md").write_text("clash")  # "todo.txt" + the kept .md extension
    with pytest.raises(EditError) as e:
        editing.rename_file(notes, tmp_path / "v", rel, name)
    assert e.value.status == (409 if name == "todo.txt" else status)


def test_api_rename(api, notes):
    r = api.post("/api/rename", json={"path": "backups.md", "name": "restic"}, headers=H)
    assert r.status_code == 200 and r.json()["path"] == "restic.md"
    assert api.post("/api/rename", json={"path": "backups.md", "name": "x"}, headers=H).status_code == 404


def test_rename_note_renames_its_pasted_images(notes, tmp_path):
    (notes / "trips" / "images").mkdir(parents=True)
    for name in ("rome-trip_1.png", "rome-trip_2.png", "rome-trip_3.png", "sunset.png"):
        (notes / "trips" / "images" / name).write_bytes(make_png())
    (notes / "trips" / "images" / "italy_2.png").write_bytes(make_png(rgb=(1, 2, 3)))  # taken: _2 can't be used
    (notes / "trips" / "rome trip.md").write_text(
        "# Rome\n\n![a](images/rome-trip_1.png) ![b](images/rome-trip_2.png) ![[rome-trip_3.png|200]] "
        "![s](images/sunset.png)\n")
    (notes / "trips" / "other.md").write_text("Shared: ![a](images/rome-trip_1.png)\n")
    r = editing.rename_file(notes, tmp_path / "v", "trips/rome trip.md", "italy")
    assert r["images"] == ["trips/images/italy_1.png", "trips/images/italy_3.png", "trips/images/italy_4.png"]
    assert r["updated"] == ["trips/other.md"]
    assert (notes / "trips/italy.md").read_text() == (
        "# Rome\n\n![a](images/italy_1.png) ![b](images/italy_3.png) ![[italy_4.png|200]] ![s](images/sunset.png)\n")
    assert (notes / "trips/other.md").read_text() == "Shared: ![a](images/italy_1.png)\n"
    assert (notes / "trips/images/sunset.png").is_file()  # a name you chose yourself stays
    assert not list((notes / "trips/images").glob("rome-trip_*"))


def test_rename_leaves_pasted_images_the_note_no_longer_shows(notes, tmp_path):
    (notes / "images" / "notes_1.png").write_bytes(make_png())
    (notes / "notes.md").write_text("# notes\n\nNo pictures any more.\n")
    r = editing.rename_file(notes, tmp_path / "v", "notes.md", "journal")
    assert r["images"] == [] and (notes / "images/notes_1.png").is_file()
