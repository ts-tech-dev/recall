"""Desktop launcher: runs the server on this machine and opens Recall in your web browser.

Used as the entry point of the Windows app (see packaging/). While it runs, a tray icon offers
"Open Recall" and "Quit". Without a system tray (or with RECALL_NO_WINDOW set) it just keeps serving.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 9999  # a stable port keeps the browser's saved questions (they are per-origin)


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


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def _open_browser(url: str) -> None:
    if not os.environ.get("RECALL_NO_WINDOW"):  # set for headless runs, e.g. the CI smoke test
        webbrowser.open(url)


def _tray_image():
    """A small Recall logo for the tray, drawn at runtime so no image file has to be bundled."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63, 63), radius=14, fill=(47, 111, 223))
    d.line((18, 16, 18, 48), fill="white", width=6)
    d.arc((14, 16, 46, 36), start=-90, end=90, fill="white", width=6)
    d.line((18, 16, 30, 16), fill="white", width=6)
    d.line((18, 36, 30, 36), fill="white", width=6)
    d.line((30, 36, 44, 48), fill="white", width=6)
    return img


def _run_tray(url: str, stop) -> bool:
    """Show the tray icon until Quit; returns False if no tray is available here."""
    if os.environ.get("RECALL_NO_WINDOW"):
        return False
    try:
        import pystray

        def quit_(icon, _item):
            icon.stop()

        icon = pystray.Icon("Recall", _tray_image(), f"Recall — {url}", menu=pystray.Menu(
            pystray.MenuItem("Open Recall", lambda *_: webbrowser.open(url), default=True),
            pystray.MenuItem("Quit", quit_),
        ))
        icon.run()
    except Exception:
        import traceback

        traceback.print_exc()  # goes to recall.log; keep serving without a tray
        return False
    stop()
    return True


def main() -> None:
    data = Path(os.environ.setdefault("RECALL_DATA_DIR", str(_default_data_dir())))
    data.mkdir(parents=True, exist_ok=True)
    _redirect_output(data)
    preferred = int(os.environ.get("RECALL_PORT") or DEFAULT_PORT)

    # Already running (e.g. launched twice): just open it.
    if _running_recall(preferred):
        _open_browser(f"http://localhost:{preferred}/")
        return

    import uvicorn

    from .app import create_app

    port = _free_port(preferred)
    url = f"http://localhost:{port}/"
    server = uvicorn.Server(uvicorn.Config(create_app(), host=HOST, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise SystemExit("Recall server failed to start; see recall.log in " + str(data))
        time.sleep(0.05)

    print(f"Recall running at {url}")
    _open_browser(url)

    def stop():
        server.should_exit = True
        thread.join(timeout=5)

    if not _run_tray(url, stop):
        thread.join()


if __name__ == "__main__":
    main()
