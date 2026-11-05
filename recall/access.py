"""The access key: only someone who has it can use Recall (not a login; nobody types it).

Recall makes a random key the first time it runs and keeps it in the data folder, which only the user can read.
The Windows app opens the browser at ``/?key=…``. Recall checks the key, keeps it in a cookie and sends the
browser on to ``/`` without it in the address. After that, the cookie goes with every request. Scripts can send
the key in an ``X-Recall-Key`` header instead. ``RECALL_KEY`` sets the key yourself (Docker, tests).

Without the key, other users signed in to the same computer and other websites get nothing from Recall, even
though ``127.0.0.1`` is shared by everyone on the machine.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import tempfile
from functools import lru_cache
from pathlib import Path

from .config import data_dir

HEADER = "x-recall-key"
QUERY = "key"
COOKIE_MAX_AGE = 10 * 365 * 86400
# Open without the key: the app's own scripts and styles (nothing private), and a liveness check.
PUBLIC_PREFIXES = ("/static/",)
PUBLIC_PATHS = {"/healthz"}


def key_path() -> Path:
    return data_dir() / "key"


@lru_cache(maxsize=8)
def _key_for(env_key: str, path: str) -> str:
    if env_key:
        return env_key
    p = Path(path)
    try:
        k = p.read_text(encoding="ascii").strip()
        if len(k) >= 32:
            return k
    except (OSError, UnicodeDecodeError):
        pass
    k = secrets.token_urlsafe(32)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".key-")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(k + "\n")
        os.chmod(tmp, 0o600)  # Windows: the folder's permissions (%LOCALAPPDATA%) are what protect it
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return k


def access_key() -> str:
    """This installation's key: RECALL_KEY, or the one in the data folder (made on first use)."""
    return _key_for(os.environ.get("RECALL_KEY", "").strip(), str(key_path()))


def cookie_name(key: str | None = None) -> str:
    """Named after the key, so two Recalls on one computer (they share localhost cookies) don't overwrite each other."""
    return "recall_" + hashlib.sha256((key or access_key()).encode()).hexdigest()[:10]


def matches(given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), access_key().encode())


def has_access(request) -> bool:
    return matches(request.cookies.get(cookie_name())) or matches(request.headers.get(HEADER))


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def open_url(base: str) -> str:
    """The address that lets a browser in: `base` (ending in "/") with the key, which Recall then moves to a cookie."""
    return f"{base}?{QUERY}={access_key()}"


def cookie_header() -> str:
    return (f"{cookie_name()}={access_key()}; Max-Age={COOKIE_MAX_AGE}; Path=/; HttpOnly; SameSite=Strict")


NO_KEY_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Recall</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:34em;margin:12vh auto;padding:0 20px;color:#1d2330}
@media (prefers-color-scheme:dark){body{background:#12151b;color:#e4e7ec}}code{font-size:90%}</style></head><body>
<h1>Open Recall from the app</h1>
<p>This browser doesn't have Recall's key yet, so Recall doesn't show anything here.</p>
<ul>
<li><b>Windows app:</b> use <i>Open Recall</i> on the Recall icon in the taskbar's notification area (near the clock),
or start <code>Recall.exe</code> again.</li>
<li><b>Command line or Docker:</b> open the address Recall printed when it started (it ends in <code>?key=…</code>;
in Docker: <code>docker logs recall</code>).</li>
</ul>
<p>After that, this browser remembers the key and bookmarks work.</p>
</body></html>"""
