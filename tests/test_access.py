"""The access key (recall/access.py) and the app page's Content-Security-Policy."""

import stat

from fastapi.testclient import TestClient

from recall import access
from recall.app import create_app
from tests.conftest import keyed

H = {"X-Recall": "1"}


def test_key_is_made_once_and_kept_private(data):
    create_app(auto_index=False)
    k = access.access_key()
    assert len(k) >= 40 and access.key_path().read_text().strip() == k
    assert stat.S_IMODE(access.key_path().stat().st_mode) == 0o600
    access._key_for.cache_clear()
    assert access.access_key() == k  # the same key after a restart


def test_without_the_key_nothing_is_shown(data, notes):
    c = TestClient(create_app(auto_index=False))
    for path in ("/api/status", "/api/tree", "/api/file?path=backups.md", "/api/security-doc", "/openapi.json"):
        r = c.get(path)
        assert r.status_code == 401 and "notes" not in r.text.lower().replace("open recall", ""), path
    assert c.post("/api/settings", json={"notes_dir": str(notes)}, headers=H).status_code == 401
    page = c.get("/")
    assert page.status_code == 401 and "Open Recall from the app" in page.text and "<script" not in page.text
    assert c.get("/api/status", headers={access.HEADER: "x" * 43}).status_code == 401
    c.cookies.set(access.cookie_name(), "y" * 43)
    assert c.get("/api/status").status_code == 401
    assert c.get("/?key=wrong", follow_redirects=False).status_code == 401


def test_open_without_the_key(data):
    c = TestClient(create_app(auto_index=False))
    assert c.get("/healthz").json() == {"app": "recall", "ok": True}
    assert c.get("/static/js/main.js").status_code == 200


def test_key_link_sets_the_cookie_and_leaves_the_address(data):
    c = TestClient(create_app(auto_index=False))
    r = c.get("/?key=" + access.access_key() + "&x=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?x=1"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(access.cookie_name() + "=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert c.get("/").status_code == 200  # the cookie is enough from now on
    assert c.get("/api/status").status_code == 200


def test_two_installations_use_different_cookies(data):
    assert access.cookie_name("a" * 43) != access.cookie_name("b" * 43)


def test_key_from_environment(data, monkeypatch):
    monkeypatch.setenv("RECALL_KEY", "my-own-key-for-docker")
    c = TestClient(create_app(auto_index=False))
    assert c.get("/api/status", headers={access.HEADER: "my-own-key-for-docker"}).status_code == 200
    assert not access.key_path().exists()


def test_page_policy_allows_only_recall(data, notes):
    c = keyed(create_app(auto_index=False))
    csp = c.get("/").headers["content-security-policy"]
    assert "script-src 'self';" in csp and "unsafe-eval" not in csp and "object-src 'none'" in csp
    assert "img-src 'self' data: blob:;" in csp and "https:" not in csp
    c.post("/api/settings", json={"remote_images": True}, headers=H)
    assert "img-src 'self' data: blob: http: https:" in c.get("/").headers["content-security-policy"]


def test_page_has_no_inline_scripts(data):
    import re

    html = keyed(create_app(auto_index=False)).get("/").text
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert not re.search(r"\son[a-z]+=", html)
