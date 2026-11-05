"""FastAPI app: request guard, the API routers (recall/api/) and the static web UI (recall/static/)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, access
from .api import routers
from .editing import EditError
from .net import setup_network
from .state import State

log = logging.getLogger("recall")

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"} | {
    h.strip() for h in os.environ.get("RECALL_ALLOWED_HOSTS", "").split(",") if h.strip()
}


def page_csp(remote_images: bool) -> str:
    """The app page's Content-Security-Policy: everything comes from Recall itself.

    Scripts only from /static (no inline scripts, no eval), so HTML that got into a note can't run code even if it
    got past the sanitizer. Images, audio and video in notes come from Recall too, unless the "remote images" setting
    is on: otherwise a note couldn't make the browser contact another server. connect-src allows other local ports,
    for following the Windows app to a new port. Inline styles are allowed (KaTeX, Mermaid, image sizes use them).
    """
    remote = " http: https:" if remote_images else ""
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: blob:{remote}",
        f"media-src 'self'{remote}",
        "font-src 'self' data:",
        "connect-src 'self' http://localhost:* http://127.0.0.1:*",
        "frame-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ])


def create_app(auto_index: bool = True) -> FastAPI:
    setup_network()  # offline unless RECALL_ALLOW_DOWNLOADS is set
    # No /docs or /redoc: those pages load Swagger UI and ReDoc from a CDN, and Recall stays offline.
    app = FastAPI(title="Recall", version=__version__, docs_url=None, redoc_url=None)
    state = State(background=auto_index)
    app.state.recall = state
    access.access_key()  # make the key now, so it exists before anyone asks for it

    async def checked(request: Request, call_next):
        # Block DNS-rebinding (foreign Host header).
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host and host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        path = request.url.path
        if not access.is_public(path):
            # /?key=…: let this browser in (cookie), then drop the key from the address bar and history.
            given = request.query_params.get(access.QUERY)
            if given is not None and request.method == "GET" and access.matches(given):
                rest = [(k, v) for k, v in request.query_params.multi_items() if k != access.QUERY]
                r = RedirectResponse(path + ("?" + urlencode(rest) if rest else ""), status_code=303)
                r.headers.append("set-cookie", access.cookie_header())
                r.headers["Cache-Control"] = "no-store"
                return r
            if not access.has_access(request):
                if path.startswith("/api/") or path == "/openapi.json":
                    return JSONResponse({"detail": "Recall's key is missing: open Recall from the app"}, status_code=401)
                return HTMLResponse(access.NO_KEY_PAGE, status_code=401,
                                    headers={"Content-Security-Policy": page_csp(False)})
        # Cross-site writes: a custom header forces a CORS preflight, which Recall doesn't answer.
        if path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            if request.headers.get("x-recall") != "1":
                return JSONResponse({"detail": "Missing X-Recall header"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        response = await checked(request, call_next)
        # Other sites can't frame Recall (clickjacking) or embed its files (probing which notes exist), and pages
        # don't tell them which note linked to them. "same-site", not "same-origin": after a port change the
        # page checks the new port (another origin on the same site).
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if "content-security-policy" not in response.headers:
            response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        return response

    @app.exception_handler(EditError)
    async def edit_error(request: Request, e: EditError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    for router in routers:
        app.include_router(router)

    @app.get("/")
    def index_page():
        return FileResponse(STATIC / "index.html", headers={
            "Content-Security-Policy": page_csp(state.settings.remote_images), "Cache-Control": "no-store"})

    @app.get("/healthz")
    def healthz():
        """Open without the key: tells the launcher and Docker's health check that Recall is up, nothing more."""
        return {"app": "recall", "ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    if auto_index:
        state.reindex_async()
    return app
