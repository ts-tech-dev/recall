"""Desktop launcher: runs the server on this machine and opens Recall in your web browser.

Used as the entry point of the Windows app (see packaging/). While it runs, a tray icon offers
"Open Recall" and "Quit". Without a system tray (or with RECALL_NO_WINDOW set) it keeps serving until
"Shut down" in the web UI. The port is a setting; changing it in the web UI restarts the server there.
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


def _probe(port: int) -> int | None:
    """Bind `port` (0 = any) the way uvicorn will and return it, or None if another program holds it."""
    with socket.socket() as s:
        if sys.platform != "win32":
            # Like uvicorn: a port Recall just let go of (TIME_WAIT) can be used again straight away.
            # (On Windows this option would allow sharing a port that is in use, so it's left off there.)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, port))
            return s.getsockname()[1]
        except OSError:
            return None


def port_free(port: int) -> bool:
    return _probe(port) is not None


def _free_port(preferred: int) -> int:
    port = _probe(preferred) or _probe(0)
    if port is None:
        raise RuntimeError("no free port")
    return port


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


class Launcher:
    """Runs the server and lets the web UI restart it on another port or shut the app down.

    The same FastAPI app (and so the same open index and folder watcher) is served across restarts.
    """

    def __init__(self, app):
        self.app = app
        self.server = None
        self.thread: threading.Thread | None = None
        self.port = 0
        self.icon = None  # the tray icon, while it runs
        self.port_locked = bool(os.environ.get("RECALL_PORT"))  # the environment wins over the setting
        self.done = threading.Event()  # set by shutdown()
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/"

    def serve(self, port: int) -> None:
        """Start serving on `port`, then stop the previous server (so the app is never unreachable)."""
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(self.app, host=HOST, port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 60
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                server.should_exit = True
                raise RuntimeError(f"Recall could not start on port {port}")
            time.sleep(0.05)
        old, old_thread = self.server, self.thread
        self.server, self.thread, self.port = server, thread, port
        if self.icon is not None:
            self.icon.title = f"Recall — {self.url}"
        if old is not None:
            old.should_exit = True
            old_thread.join(timeout=5)

    def restart(self, port: int) -> None:
        """Move to `port` shortly after the current request has been answered."""
        def later():
            time.sleep(0.3)
            with self._lock:
                try:
                    self.serve(port)
                except Exception:
                    import traceback

                    traceback.print_exc()  # stays on the old port; see recall.log

        threading.Thread(target=later, daemon=True).start()

    def shutdown(self) -> None:
        def later():
            time.sleep(0.3)
            self.done.set()
            if self.icon is not None:
                self.icon.stop()

        threading.Thread(target=later, daemon=True).start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
            self.thread.join(timeout=5)


def _run_tray(launcher: Launcher) -> bool:
    """Show the tray icon until Quit (or Shut down in the app); returns False if no tray is available here."""
    if os.environ.get("RECALL_NO_WINDOW"):
        return False
    try:
        import pystray

        icon = pystray.Icon("Recall", _tray_image(), f"Recall — {launcher.url}", menu=pystray.Menu(
            pystray.MenuItem("Open Recall", lambda *_: webbrowser.open(launcher.url), default=True),
            pystray.MenuItem("Quit", lambda icon, _item: icon.stop()),
        ))
        launcher.icon = icon
        icon.run()
    except Exception:
        import traceback

        traceback.print_exc()  # goes to recall.log; keep serving without a tray
        launcher.icon = None
        return False
    launcher.icon = None
    return True


def main() -> None:
    data = Path(os.environ.setdefault("RECALL_DATA_DIR", str(_default_data_dir())))
    data.mkdir(parents=True, exist_ok=True)
    _redirect_output(data)

    from .config import load_settings

    preferred = int(os.environ.get("RECALL_PORT") or load_settings().port or DEFAULT_PORT)

    # Already running (e.g. launched twice): just open it.
    if _running_recall(preferred):
        _open_browser(f"http://localhost:{preferred}/")
        return

    from .app import create_app

    app = create_app()
    launcher = Launcher(app)
    app.state.launcher = launcher
    try:
        launcher.serve(_free_port(preferred))
    except RuntimeError as e:
        raise SystemExit(f"{e}; see recall.log in {data}")

    print(f"Recall running at {launcher.url}")
    _open_browser(launcher.url)

    if not _run_tray(launcher):
        launcher.done.wait()
    launcher.stop()


if __name__ == "__main__":
    main()
