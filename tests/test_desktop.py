"""The Windows app's launcher: port setting, moving to another port, shutting down (and the API for them)."""

import json
import socket
import urllib.request

import pytest
from fastapi.testclient import TestClient

from recall.app import create_app
from recall.config import load_settings
from recall.desktop import Launcher

H = {"X-Recall": "1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return "notes_dir" in json.load(r)
    except OSError:
        return False


class FakeLauncher:
    def __init__(self, port=9999, locked=False):
        self.port, self.port_locked = port, locked
        self.restarted, self.shut = None, False

    def restart(self, port):
        self.restarted = port

    def shutdown(self):
        self.shut = True


@pytest.fixture()
def app_client(data):
    app = create_app(auto_index=False)
    app.state.launcher = FakeLauncher()
    return TestClient(app), app.state.launcher


def test_no_app_controls_without_launcher(data):
    c = TestClient(create_app(auto_index=False))
    assert c.get("/api/status").json()["app"] is None
    assert c.post("/api/app/shutdown", headers=H).status_code == 404
    assert c.post("/api/app/port", json={"port": 8123}, headers=H).status_code == 404


def test_change_port_saves_and_restarts(app_client):
    c, ln = app_client
    assert c.get("/api/status").json()["app"] == {"port": 9999, "port_locked": False}
    port = free_port()
    r = c.post("/api/app/port", json={"port": port}, headers=H)
    assert r.json() == {"url": f"http://localhost:{port}/"}
    assert ln.restarted == port and load_settings().port == port


def test_change_port_refusals(app_client):
    c, ln = app_client
    assert c.post("/api/app/port", json={"port": 80}, headers=H).status_code == 400
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        r = c.post("/api/app/port", json={"port": busy.getsockname()[1]}, headers=H)
    assert r.status_code == 409 and "in use" in r.json()["detail"]
    ln.port_locked = True
    assert c.post("/api/app/port", json={"port": free_port()}, headers=H).status_code == 409
    assert ln.restarted is None


def test_shutdown_needs_the_header(app_client):
    c, ln = app_client
    assert c.post("/api/app/shutdown").status_code == 403 and not ln.shut
    assert c.post("/api/app/shutdown", headers=H).json() == {"ok": True} and ln.shut


def test_launcher_moves_port_and_shuts_down(data, monkeypatch):
    monkeypatch.delenv("RECALL_PORT", raising=False)
    app = create_app(auto_index=False)
    ln = Launcher(app)
    app.state.launcher = ln
    first, second = free_port(), free_port()
    ln.serve(first)
    try:
        assert answers(first)
        ln.serve(second)  # what restart() runs, without its delay
        assert ln.port == second and answers(second) and not answers(first)
        ln.shutdown()
        assert ln.done.wait(5)
    finally:
        ln.stop()
    assert not answers(second)


def test_log_is_trimmed_when_too_big(tmp_path, monkeypatch):
    from recall import desktop

    monkeypatch.setattr(desktop, "LOG_MAX_BYTES", 1000)
    monkeypatch.setattr(desktop, "LOG_KEEP_BYTES", 300)
    log = tmp_path / "recall.log"
    log.write_text("".join(f"line {i}\n" for i in range(500)))
    desktop._trim_log(log)
    text = log.read_text()
    assert len(text) < 400 and text.startswith("[earlier log lines removed]\nline ") and text.endswith("line 499\n")
    small = tmp_path / "small.log"
    small.write_text("hello\n")
    desktop._trim_log(small)
    assert small.read_text() == "hello\n"
