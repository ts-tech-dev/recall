"""AI answers, streamed as server-sent events."""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..answer import answer_events
from ..index import Filters, Index
from ..state import State, get_index, get_state

router = APIRouter(prefix="/api")


class AskBody(BaseModel):
    question: str
    mode: str = "summary"
    types: list[str] = []
    folder: str = ""
    path: str = ""


@router.post("/ask")
def ask(body: AskBody, state: State = Depends(get_state), idx: Index = Depends(get_index)):
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
