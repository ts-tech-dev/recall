"""Browsing: the file tree, folders, note previews, raw files and images extracted from documents."""

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import editing
from ..extractors import IMAGE_EXTS, NOTE_TYPES, file_url
from ..index import Index
from ..state import get_index

router = APIRouter(prefix="/api")


def safe_file(idx: Index, rel: str) -> Path:
    """An existing file inside the notes folder (403 outside it, 404 if missing)."""
    p = (idx.root / rel).resolve()
    try:
        p.relative_to(idx.root)
    except ValueError:
        raise HTTPException(403, "Path outside notes directory")
    if not p.is_file():
        raise HTTPException(404, "File not found")
    return p


@router.get("/tree")
def tree(images: bool = False, idx: Index = Depends(get_index)):
    root: dict = {"name": idx.root.name, "path": "", "type": "dir", "children": []}
    dirs = {"": root}

    def folder(key: str) -> dict:
        if key not in dirs:
            parent, _, name = key.rpartition("/")
            dirs[key] = {"name": name, "path": key, "type": "dir", "children": []}
            folder(parent)["children"].append(dirs[key])
        return dirs[key]

    for key in editing.list_folders(idx.root):  # empty folders too, so notes can be moved or created there
        if not any(not e.name.startswith(".") for e in os.scandir(idx.root / key)):
            folder(key)
    for p in idx.iter_files(include_images=images):
        rel = p.relative_to(idx.root).as_posix()
        parent, _, name = rel.rpartition("/")
        folder(parent)["children"].append({"name": name, "path": rel, "type": "file", "ext": p.suffix.lower()})

    def sort(n):
        n["children"].sort(key=lambda c: (c["type"] != "dir", c["name"].lower()))
        for c in n["children"]:
            if c["type"] == "dir":
                sort(c)

    sort(root)
    return root


@router.get("/folders")
def folders(idx: Index = Depends(get_index)):
    return {"folders": editing.list_folders(idx.root)}


@router.get("/doc")
def doc(path: str, text: bool = True, idx: Index = Depends(get_index)):
    """A file for the previewer. text=false skips reading a PDF's text (the browser's PDF viewer shows it)."""
    p = safe_file(idx, path)
    ext = p.suffix.lower()
    base = {"path": path, "name": p.name, "ext": ext, "raw_url": file_url(path), "info": idx.doc_info(path),
            "editable": editing.editable(p), "mtime_ns": editing.stamp(p)}
    if ext in IMAGE_EXTS:
        return {**base, "kind": "image", "title": p.name}
    if ext not in NOTE_TYPES:
        raise HTTPException(415, "Unsupported file type")
    if ext == ".pdf" and not text:
        info = base["info"] or {}
        return {**base, "kind": "pdf", "title": info.get("title") or p.stem, "markdown": None, "tags": info.get("tags", [])}
    try:
        d = idx.extract(p)
    except Exception as e:
        raise HTTPException(422, f"Could not read {p.name}: {e}")
    kind = "pdf" if ext == ".pdf" else d.kind
    return {**base, "kind": kind, "title": d.title, "markdown": d.markdown, "tags": d.tags}


@router.get("/file")
def file(path: str, idx: Index = Depends(get_index)):
    p = safe_file(idx, path)
    headers = {"Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"}
    if p.suffix.lower() == ".pdf":
        headers = {}  # the browser PDF viewer needs to run
    return FileResponse(p, headers=headers)


@router.get("/cache-image/{name}")
def cache_image(name: str, idx: Index = Depends(get_index)):
    if not re.fullmatch(r"[0-9a-f]{20}\.(png|jpg|jpeg|gif|webp)", name):
        raise HTTPException(400, "Bad image name")
    p = idx.cache_dir / name
    if not p.is_file():
        raise HTTPException(404, "Image not found (re-index to regenerate)")
    return FileResponse(p, headers={"Cache-Control": "max-age=86400"})
