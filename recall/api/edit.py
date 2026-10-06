"""Editing: note sources, new notes, moves, uploads and version history.

Errors raised by `editing` (EditError) become HTTP errors through the handler registered in app.py.
"""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from .. import editing
from ..extractors import Context, rewrite_markdown_links
from ..index import Index
from ..state import State, get_index, get_state

router = APIRouter(prefix="/api")


class SaveBody(BaseModel):
    path: str
    content: str
    base_mtime_ns: str | None = None
    force: bool = False


class NewNoteBody(BaseModel):
    path: str
    title: str = ""


class MoveBody(BaseModel):
    path: str
    folder: str


class RenderBody(BaseModel):
    path: str
    markdown: str


@router.get("/source")
def get_source(path: str, idx: Index = Depends(get_index)):
    return editing.read_source(idx.root, path)


@router.put("/source")
def put_source(body: SaveBody, state: State = Depends(get_state), idx: Index = Depends(get_index)):
    res = editing.save_source(idx.root, idx.versions_dir, body.path, body.content, body.base_mtime_ns, body.force)
    if res["changed"]:
        state.reindex_async(wait_turn=True)
    return res


@router.post("/note")
def new_note(body: NewNoteBody, state: State = Depends(get_state), idx: Index = Depends(get_index)):
    res = editing.create_note(idx.root, body.path, body.title)
    state.reindex_async(wait_turn=True)
    return res


@router.post("/move")
def move(body: MoveBody, state: State = Depends(get_state), idx: Index = Depends(get_index)):
    res = editing.move_file(idx.root, idx.versions_dir, body.path, body.folder)
    state.reindex_async(wait_turn=True)
    return res


@router.post("/render")
def render(body: RenderBody, idx: Index = Depends(get_index)):
    """Rewrite links/images of unsaved Markdown exactly as the previewer would for the saved file."""
    p = editing.inside(idx.root, body.path)
    return {"markdown": rewrite_markdown_links(body.markdown, Context(root=idx.root, cache_dir=idx.cache_dir), p)}


@router.post("/upload")
async def upload(note: str = Form(...), file: UploadFile = File(...), idx: Index = Depends(get_index)):
    data = await file.read(editing.MAX_UPLOAD_BYTES + 1)
    return editing.save_upload(idx.root, note, file.filename or "pasted.png", data)


@router.get("/versions")
def versions(path: str, idx: Index = Depends(get_index)):
    return {"versions": editing.list_versions(idx.versions_dir, path)}


@router.get("/version")
def version(path: str, id: str, idx: Index = Depends(get_index)):
    return {"content": editing.read_version(idx.versions_dir, path, id)}
