"""Status, settings and re-indexing."""

from fastapi import APIRouter, Depends, HTTPException

from .. import __version__
from ..config import apply_update, save_settings
from ..embeddings import available as embeddings_available
from ..images import ocr_available
from ..index import TYPE_GROUPS, Index
from ..state import State, get_index, get_state

router = APIRouter(prefix="/api")


@router.get("/status")
def status(state: State = Depends(get_state)):
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


@router.get("/settings")
def get_settings(state: State = Depends(get_state)):
    return state.settings.public()


@router.post("/settings")
def post_settings(update: dict, state: State = Depends(get_state)):
    with state.lock:
        before = state.signature
        caption_before = (state.settings.caption_images, state.settings.caption_model)
        try:
            apply_update(state.settings, dict(update))
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        save_settings(state.settings)
        state.open_index()
        # New folder, embedding model, OCR switch, or captions turned on: index (incrementally).
        caption_now = (state.settings.caption_images, state.settings.caption_model)
        if state.signature != before or (caption_now != caption_before and caption_now[0]):
            state.reindex_async(wait_turn=True)
    return state.settings.public()


@router.post("/index")
def reindex(full: bool = False, wait: bool = False, state: State = Depends(get_state),
            idx: Index = Depends(get_index)):
    if wait:
        return {"stats": idx.build(full=full), "index": idx.stats()}
    state.reindex_async(full)
    return {"started": True}
