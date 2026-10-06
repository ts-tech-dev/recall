"""Re-index automatically when files in the notes folder change (inotify/FSEvents via watchdog)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .extractors import IMAGE_EXTS, NOTE_TYPES

log = logging.getLogger("recall.watcher")
WATCHED_EXTS = set(NOTE_TYPES) | IMAGE_EXTS


def relevant(root: Path, path: str) -> bool:
    """Ignore hidden files/folders, Office lock files, editor temp files and unsupported types."""
    try:
        rel = Path(path).resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    if any(part.startswith(".") for part in rel.parts) or rel.name.startswith("~$"):
        return False
    return rel.suffix.lower() in WATCHED_EXTS


class Watcher:
    """Debounces bursts of file events (e.g. a git pull) into one incremental build."""

    def __init__(self, index, debounce: float = 1.5):
        self.index = index
        self.debounce = debounce
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._observer = None

    def start(self) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.warning("watchdog not installed; automatic re-indexing is off")
            return False
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.event_type in ("opened", "closed_no_write"):
                    return
                paths = [event.src_path, getattr(event, "dest_path", "") or ""]
                # Directory moves/deletes can hide many files: always rescan for those.
                if event.is_directory and event.event_type in ("moved", "deleted"):
                    watcher.trigger()
                elif any(p and relevant(watcher.index.root, p) for p in paths):
                    watcher.trigger()

        self._observer = Observer()
        self._observer.schedule(Handler(), str(self.index.root), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        return True

    def trigger(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._run)
            self._timer.daemon = True
            self._timer.start()

    def _run(self) -> None:
        try:
            self.index.build(block=True)
        except Exception as e:  # never let the watcher thread die silently
            log.warning("Automatic re-index failed: %s", e)

    def stop(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
