"""Desktop launcher: runs the server on this machine and shows Recall in its own window.

Used as the entry point of the Windows app (see packaging/). Falls back to the default browser
when no native web view is available.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PREFERRED_PORT = 8765  # a stable port keeps the browser's saved questions (they are per-origin)


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Recall"
    return Path.home() / ".recall"


def _redirect_output(data: Path) -> None:
    # A windowed (no console) build has no stdout/stderr, and uvicorn's logging would crash on them.
    if sys.stdout is None or sys.stderr is None:
        log = open(data / "recall.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def _running_recall(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=1) as r:
            return "notes_dir" in json.load(r)
    except Exception:
        return False


def _free_port() -> int:
    for port in (PREFERRED_PORT, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def _unblock_dlls() -> None:
    """Remove the "downloaded from the internet" mark from the bundled DLLs.

    Extracting a downloaded zip with Explorer marks every file (a Zone.Identifier stream), and .NET
    Framework then refuses to load pythonnet's DLLs, which the native window needs.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    for dll in Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).rglob("*.dll"):
        try:
            os.remove(f"{dll}:Zone.Identifier")
        except OSError:
            pass


def _show(url: str) -> bool:
    """Open a native window; returns False if no web view is available."""
    if os.environ.get("RECALL_NO_WINDOW"):
        return False
    _unblock_dlls()
    try:
        import webview
    except Exception:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True  # "Download .md" on answers
    except Exception:
        pass
    try:
        webview.create_window("Recall", url, width=1400, height=900, min_size=(800, 560))
        webview.start()
    except Exception:
        traceback.print_exc()  # goes to recall.log; fall back to the browser
        return False
    return True


def main() -> None:
    data = Path(os.environ.setdefault("RECALL_DATA_DIR", str(_default_data_dir())))
    data.mkdir(parents=True, exist_ok=True)
    _redirect_output(data)

    # Already open (e.g. launched twice): just show the running instance.
    if _running_recall(PREFERRED_PORT):
        url = f"http://{HOST}:{PREFERRED_PORT}/"
        if not _show(url):
            webbrowser.open(url)
        return

    import uvicorn

    from .app import create_app

    port = _free_port()
    url = f"http://{HOST}:{port}/"
    server = uvicorn.Server(uvicorn.Config(create_app(), host=HOST, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise SystemExit("Recall server failed to start; see recall.log in " + str(data))
        time.sleep(0.05)

    if _show(url):
        server.should_exit = True  # window closed: stop the server
        thread.join(timeout=5)
    else:
        print(f"Recall running at {url}")
        if not os.environ.get("RECALL_NO_WINDOW"):  # set for headless runs, e.g. the CI smoke test
            webbrowser.open(url)
        thread.join()


if __name__ == "__main__":
    main()
