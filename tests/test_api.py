import json
import posixpath
import re

import pytest
from fastapi.testclient import TestClient

from recall import answer as answer_mod
from recall.app import create_app

H = {"X-Recall": "1"}


@pytest.fixture()
def client(data):
    return TestClient(create_app(auto_index=False))


@pytest.fixture()
def ready(client, notes):
    r = client.post("/api/settings", json={"notes_dir": str(notes), "provider": "none", "semantic_search": False,
                                           "ocr": False}, headers=H)
    assert r.status_code == 200
    r = client.post("/api/index?wait=true", headers=H)
    assert r.json()["index"]["docs"] == 11
    return client


def sse(text: str) -> list[tuple[str, object]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_ui_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>Recall</title>" in r.text
    # Every script/stylesheet the page loads, and every ES module those import, is served.
    pending = re.findall(r'(?:src|href)="(/static/[^"]+)"', r.text)
    seen = set()
    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        res = client.get(url)
        assert res.status_code == 200, url
        if url.endswith(".js") and "/vendor/" not in url:
            base = url.rsplit("/", 1)[0]
            for imp in re.findall(r'^import .* from "(\.{1,2}/[^"]+)";', res.text, re.M):
                pending.append(posixpath.normpath(f"{base}/{imp}"))
    assert "/static/js/main.js" in seen and "/static/js/md/enhance.js" in seen


def test_status_without_notes(client):
    st = client.get("/api/status").json()
    assert st["notes_dir"] == "" and st["index"] is None
    assert client.get("/api/tree").status_code == 400


def test_writes_require_header(client, notes):
    assert client.post("/api/settings", json={"notes_dir": str(notes)}).status_code == 403


def test_foreign_host_rejected(client):
    assert client.get("/api/status", headers={"Host": "evil.example.com"}).status_code == 403


def test_settings_validation_and_masking(client, notes):
    assert client.post("/api/settings", json={"provider": "x"}, headers=H).status_code == 400
    r = client.post("/api/settings", json={"api_key": "sk-ant-verysecret-9999"}, headers=H).json()
    assert r["has_key"] and r["key_hint"] == "…9999" and "verysecret" not in json.dumps(r)


def test_tree(ready, notes):
    t = ready.get("/api/tree").json()
    names = [c["name"] for c in t["children"]]
    assert names[0] == "networking"  # folders first
    assert ".hidden" not in names and "images" not in names
    with_images = ready.get("/api/tree?images=true").json()
    assert "images" in [c["name"] for c in with_images["children"]]
    (notes / "inbox" / "later").mkdir(parents=True)
    inbox = next(c for c in ready.get("/api/tree").json()["children"] if c["name"] == "inbox")
    assert inbox["children"] == [{"name": "later", "path": "inbox/later", "type": "dir", "children": []}]


def test_doc_preview_markdown(ready):
    d = ready.get("/api/doc", params={"path": "networking/vlans.md"}).json()
    assert d["kind"] == "markdown" and d["title"] == "VLAN Setup Guide"
    assert "/api/file?path=images/vlan-diagram.png" in d["markdown"]
    assert d["tags"] == ["networking", "homelab"]


@pytest.mark.parametrize(
    "path,kind", [("k8s-upgrade.pdf", "pdf"), ("onboarding.docx", "markdown"), ("roadmap.pptx", "markdown"),
                  ("inventory.xlsx", "markdown"), ("subnets.csv", "markdown"), ("wiki.html", "markdown"),
                  ("todo.txt", "text"), ("images/vlan-diagram.png", "image")],
)
def test_doc_preview_each_type(ready, path, kind):
    d = ready.get("/api/doc", params={"path": path}).json()
    assert d["kind"] == kind
    assert ready.get(d["raw_url"]).status_code == 200


def test_doc_preview_broken_file(ready):
    assert ready.get("/api/doc", params={"path": "broken.pdf"}).status_code == 422


def test_file_traversal_blocked(ready):
    assert ready.get("/api/file", params={"path": "../data/config.json"}).status_code == 403
    assert ready.get("/api/file", params={"path": "/etc/passwd"}).status_code == 403
    assert ready.get("/api/file", params={"path": "nope.md"}).status_code == 404


def test_file_is_sandboxed(ready):
    r = ready.get("/api/file", params={"path": "wiki.html"})
    assert "sandbox" in r.headers["content-security-policy"]


def test_cache_image_served_and_validated(ready):
    d = ready.get("/api/doc", params={"path": "onboarding.docx"}).json()
    url = d["markdown"].split("](/api/cache-image/")[1].split(")")[0]
    r = ready.get("/api/cache-image/" + url)
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert ready.get("/api/cache-image/..%2F..%2Fconfig.json").status_code in (400, 404)


def test_search_with_filters(ready):
    r = ready.get("/api/search", params={"q": "VLAN upgrade", "types": "pdf"}).json()
    assert {x["path"] for x in r["results"]} == {"k8s-upgrade.pdf"}
    r = ready.get("/api/search", params={"q": "router VLAN", "folder": "networking"}).json()
    assert {x["path"] for x in r["results"]} == {"networking/vlans.md", "networking/router.md"}


def test_ask_local_streams_sse(ready):
    r = ready.post("/api/ask", json={"question": "How do I upgrade kubernetes?", "mode": "summary"}, headers=H)
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = sse(r.text)
    assert ev[0][0] == "sources" and ev[0][1][0]["path"] == "k8s-upgrade.pdf"
    assert ev[-1] == ("done", {"ai": False})


def test_ask_ai_with_filters(ready, monkeypatch):
    ready.post("/api/settings", json={"provider": "anthropic", "api_key": "sk-test-0000"}, headers=H)
    monkeypatch.setattr(answer_mod, "stream_answer", lambda *a, **k: iter(["A", "B"]))
    r = ready.post("/api/ask", json={"question": "VLAN", "mode": "report", "types": ["markdown"]}, headers=H)
    ev = sse(r.text)
    assert all(s["ext"] == ".md" for s in ev[0][1])
    assert "".join(d for e, d in ev if e == "delta") == "AB"
    assert ev[-1][0] == "done"


def test_ask_unexpected_error_is_reported(ready, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("recall.api.ask.answer_events", boom)
    ev = sse(ready.post("/api/ask", json={"question": "x"}, headers=H).text)
    assert ev == [("error", {"message": "RuntimeError: kaboom"})]


def test_ask_empty_question(ready):
    assert ready.post("/api/ask", json={"question": "  "}, headers=H).status_code == 400
