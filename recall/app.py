"""FastAPI app: request guard, the API routers (recall/api/) and the static web UI (recall/static/)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import routers
from .editing import EditError
from .net import setup_network
from .state import State

log = logging.getLogger("recall")

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"} | {
    h.strip() for h in os.environ.get("RECALL_ALLOWED_HOSTS", "").split(",") if h.strip()
}


def create_app(auto_index: bool = True) -> FastAPI:
    setup_network()  # offline unless RECALL_ALLOW_DOWNLOADS is set
    app = FastAPI(title="Recall", version=__version__)
    state = State(background=auto_index)
    app.state.recall = state

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Block DNS-rebinding (foreign Host header) and cross-site writes (custom header forces CORS preflight).
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host and host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            if request.headers.get("x-recall") != "1":
                return JSONResponse({"detail": "Missing X-Recall header"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(EditError)
    async def edit_error(request: Request, e: EditError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    for router in routers:
        app.include_router(router)

    @app.get("/")
    def index_page():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    if auto_index:
        state.reindex_async()
    return app
