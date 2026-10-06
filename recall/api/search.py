"""Search, related notes and the note graph."""

from fastapi import APIRouter, Depends, Query

from ..index import Filters, Index
from ..state import get_index

router = APIRouter(prefix="/api")


@router.get("/search")
def search(q: str, types: str = "", folder: str = "", path: str = "", limit: int = Query(20, ge=1, le=100),
           mode: str = Query("hybrid", pattern="^(hybrid|keyword|semantic)$"), idx: Index = Depends(get_index)):
    filters = Filters(types=[t for t in types.split(",") if t], folder=folder, path=path)
    return {"results": idx.search(q, filters, limit=limit, mode=mode)}


@router.get("/related")
def related(path: str, idx: Index = Depends(get_index)):
    return {**idx.links(path), "similar": idx.similar(path)}


@router.get("/graph")
def graph(similar: bool = True, idx: Index = Depends(get_index)):
    return idx.graph(similar=similar)
