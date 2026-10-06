"""FastAPI server: JSON API + static web UI."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .answer import answer_events
from . import editing
from .config import apply_update, data_dir, load_settings, save_settings
from .embeddings import FastEmbedEmbedder
from .embeddings import available as embeddings_available
from .extractors import IMAGE_EXTS, NOTE_TYPES, Context, extract, file_url, rewrite_markdown_links
from .images import ocr_available
from .index import TYPE_GROUPS, Filters, Index
from .llm import caption_image
from .watcher import Watcher

log = logging.getLogger("recall")

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"} | {
    h.strip() for h in os.environ.get("RECALL_ALLOWED_HOSTS", "").split(",") if h.strip()
}


class AskBody(BaseModel):
    question: str
    mode: str = "summary"
    types: list[str] = []
    folder: str = ""
    path: str = ""


class SaveBody(BaseModel):
    path: str
    content: str
    base_mtime_ns: str | None = None
    force: bool = False


class NewNoteBody(BaseModel):
    path: str
    title: str = ""


class RenderBody(BaseModel):
    path: str
    markdown: str


class State:
    def __init__(self, background: bool = True):
        self.settings = load_settings()
        self.index: Index | None = None
        self.watcher: Watcher | None = None
        self.background = background  # start the folder watcher (off in most tests)
        self.lock = threading.RLock()
        self._signature = None
        self._embedders: dict[str, FastEmbedEmbedder] = {}

    def _embedder(self):
        s = self.settings
        if not s.semantic_search or not embeddings_available():
            return None
        if s.embed_model not in self._embedders:
            self._embedders[s.embed_model] = FastEmbedEmbedder(s.embed_model)
        return self._embedders[s.embed_model]

    def _captioner(self):
        s = self.settings
        if not (s.caption_images and s.ai_enabled()):
            return None
        return lambda data, media_type: caption_image(self.settings, base64.b64encode(data).decode(), media_type)

    def open_index(self) -> Index | None:
        """Return the index for the current settings, rebuilding the Index object if they changed."""
        with self.lock:
            s = self.settings
            nd = s.notes_dir
            if not nd or not Path(nd).is_dir():
                self._close()
                return None
            sig = (str(Path(nd).resolve()), s.semantic_search, s.embed_model, s.ocr, s.watch)
            if self.index is None or sig != self._signature:
                self._close()
                self.index = Index(Path(nd), data_dir(), embedder=self._embedder(), ocr=s.ocr and ocr_available())
                self._signature = sig
                if s.watch and self.background:
                    self.watcher = Watcher(self.index)
                    self.watcher.start()
            self.index.captioner = self._captioner()
            self.index.caption_limit = s.caption_limit
            return self.index

    def _close(self):
        if self.watcher:
            self.watcher.stop()
        self.index, self.watcher, self._signature = None, None, None

    def require_index(self) -> Index:
        idx = self.open_index()
        if idx is None:
            raise HTTPException(400, "No notes directory configured. Open Settings and choose one.")
        return idx

    def reindex_async(self, full: bool = False, wait_turn: bool = False) -> None:
        """Start a background build. wait_turn=True queues behind a running build instead of skipping."""
        idx = self.open_index()
        if idx is not None:
            threading.Thread(target=idx.build, kwargs={"full": full, "block": wait_turn}, daemon=True).start()


def create_app(auto_index: bool = True) -> FastAPI:
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

    if auto_index:
        state.reindex_async()

    # ------------------------------------------------------------------ settings / status

    @app.get("/api/status")
    def status():
        idx = state.open_index()
        return {
            "version": __version__,
            "notes_dir": state.settings.notes_dir,
            "settings": state.settings.public(),
            "index": idx.stats() if idx else None,
            "progress": idx.progress if idx else None,
            "changed": idx.last_changed[:50] if idx else [],
            "watching": bool(state.watcher),
            "features": {"semantic": embeddings_available(), "ocr": ocr_available()},
            "type_groups": TYPE_GROUPS,
        }

    @app.get("/api/settings")
    def get_settings():
        return state.settings.public()

    @app.post("/api/settings")
    def post_settings(update: dict):
        with state.lock:
            before = state._signature
            caption_before = (state.settings.caption_images, state.settings.caption_model)
            try:
                apply_update(state.settings, dict(update))
            except (ValueError, TypeError) as e:
                raise HTTPException(400, str(e))
            save_settings(state.settings)
            state.open_index()
            # New folder, embedding model, OCR switch, or captions turned on: index (incrementally).
            caption_now = (state.settings.caption_images, state.settings.caption_model)
            if state._signature != before or (caption_now != caption_before and caption_now[0]):
                state.reindex_async(wait_turn=True)
        return state.settings.public()

    @app.post("/api/index")
    def reindex(full: bool = False, wait: bool = False):
        idx = state.require_index()
        if wait:
            return {"stats": idx.build(full=full), "index": idx.stats()}
        state.reindex_async(full)
        return {"started": True}

    # ------------------------------------------------------------------ browsing

    def safe_path(rel: str) -> Path:
        idx = state.require_index()
        p = (idx.root / rel).resolve()
        try:
            p.relative_to(idx.root)
        except ValueError:
            raise HTTPException(403, "Path outside notes directory")
        if not p.is_file():
            raise HTTPException(404, "File not found")
        return p

    @app.get("/api/tree")
    def tree(images: bool = False):
        idx = state.require_index()
        root: dict = {"name": idx.root.name, "path": "", "type": "dir", "children": []}
        dirs = {"": root}
        for p in idx.iter_files(include_images=images):
            rel = p.relative_to(idx.root).as_posix()
            parts = rel.split("/")
            parent = root
            for i in range(len(parts) - 1):
                key = "/".join(parts[: i + 1])
                if key not in dirs:
                    node = {"name": parts[i], "path": key, "type": "dir", "children": []}
                    dirs[key] = node
                    parent["children"].append(node)
                parent = dirs[key]
            parent["children"].append({"name": parts[-1], "path": rel, "type": "file", "ext": p.suffix.lower()})

        def sort(n):
            n["children"].sort(key=lambda c: (c["type"] != "dir", c["name"].lower()))
            for c in n["children"]:
                if c["type"] == "dir":
                    sort(c)

        sort(root)
        return root

    @app.get("/api/doc")
    def doc(path: str):
        p = safe_path(path)
        idx = state.require_index()
        ext = p.suffix.lower()
        raw_url = file_url(path)
        base = {"path": path, "name": p.name, "ext": ext, "raw_url": raw_url, "info": idx.doc_info(path),
                "editable": editing.editable(p), "mtime_ns": editing.stamp(p)}
        if ext in IMAGE_EXTS:
            return {**base, "kind": "image", "title": p.name}
        if ext not in NOTE_TYPES:
            raise HTTPException(415, "Unsupported file type")
        try:
            d = extract(p, idx.context())
        except Exception as e:
            raise HTTPException(422, f"Could not read {p.name}: {e}")
        kind = "pdf" if ext == ".pdf" else d.kind
        return {**base, "kind": kind, "title": d.title, "markdown": d.markdown, "tags": d.tags}

    @app.get("/api/file")
    def file(path: str):
        p = safe_path(path)
        headers = {"Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"}
        if p.suffix.lower() == ".pdf":
            headers = {}  # the browser PDF viewer needs to run
        return FileResponse(p, headers=headers)

    @app.get("/api/cache-image/{name}")
    def cache_image(name: str):
        idx = state.require_index()
        if not re.fullmatch(r"[0-9a-f]{20}\.(png|jpg|jpeg|gif|webp)", name):
            raise HTTPException(400, "Bad image name")
        p = idx.cache_dir / name
        if not p.is_file():
            raise HTTPException(404, "Image not found (re-index to regenerate)")
        return FileResponse(p, headers={"Cache-Control": "max-age=86400"})

    # ------------------------------------------------------------------ search / ask

    def make_filters(types: str, folder: str, path: str) -> Filters:
        return Filters(types=[t for t in types.split(",") if t], folder=folder, path=path)

    @app.get("/api/search")
    def search(q: str, types: str = "", folder: str = "", path: str = "", limit: int = Query(20, ge=1, le=100),
               mode: str = Query("hybrid", pattern="^(hybrid|keyword|semantic)$")):
        idx = state.require_index()
        return {"results": idx.search(q, make_filters(types, folder, path), limit=limit, mode=mode)}

    # ------------------------------------------------------------------ links / graph

    @app.get("/api/related")
    def related(path: str):
        idx = state.require_index()
        return {**idx.links(path), "similar": idx.similar(path)}

    @app.get("/api/graph")
    def graph(similar: bool = True):
        return state.require_index().graph(similar=similar)

    # ------------------------------------------------------------------ editing

    def edit_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except editing.EditError as e:
            raise HTTPException(e.status, str(e))

    @app.get("/api/source")
    def get_source(path: str):
        return edit_call(editing.read_source, state.require_index().root, path)

    @app.put("/api/source")
    def put_source(body: SaveBody):
        idx = state.require_index()
        res = edit_call(editing.save_source, idx.root, idx.versions_dir, body.path, body.content,
                        body.base_mtime_ns, body.force)
        if res["changed"]:
            state.reindex_async(wait_turn=True)
        return res

    @app.post("/api/note")
    def new_note(body: NewNoteBody):
        idx = state.require_index()
        res = edit_call(editing.create_note, idx.root, body.path, body.title)
        state.reindex_async(wait_turn=True)
        return res

    @app.post("/api/render")
    def render(body: RenderBody):
        """Rewrite links/images of unsaved Markdown exactly as the previewer would for the saved file."""
        idx = state.require_index()
        p = edit_call(editing.inside, idx.root, body.path)
        return {"markdown": rewrite_markdown_links(body.markdown, Context(root=idx.root, cache_dir=idx.cache_dir), p)}

    @app.post("/api/upload")
    async def upload(note: str = Form(...), file: UploadFile = File(...)):
        idx = state.require_index()
        data = await file.read(editing.MAX_UPLOAD_BYTES + 1)
        return edit_call(editing.save_upload, idx.root, note, file.filename or "pasted.png", data)

    @app.get("/api/versions")
    def versions(path: str):
        return {"versions": editing.list_versions(state.require_index().versions_dir, path)}

    @app.get("/api/version")
    def version(path: str, id: str):
        return {"content": edit_call(editing.read_version, state.require_index().versions_dir, path, id)}

    @app.post("/api/ask")
    def ask(body: AskBody):
        idx = state.require_index()
        if not body.question.strip():
            raise HTTPException(400, "Question is empty")
        filters = Filters(types=body.types, folder=body.folder, path=body.path)

        def gen():
            try:
                for event, data in answer_events(idx, state.settings, body.question.strip(), body.mode, filters):
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
            except Exception as e:  # surface unexpected failures to the UI instead of a dead stream
                yield f"event: error\ndata: {json.dumps({'message': f'{type(e).__name__}: {e}'})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------------ UI

    @app.get("/")
    def index_page():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
