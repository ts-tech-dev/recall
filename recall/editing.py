"""Editing notes in place: conflict-checked atomic saves, version history, new notes, image uploads."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path

EDITABLE_EXTS = {".md", ".markdown", ".txt"}
UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_NOTE_BYTES = 5_000_000
MAX_UPLOAD_BYTES = 25_000_000
KEEP_VERSIONS = 30


class EditError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def inside(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes it or touches hidden folders."""
    rel = rel.strip().lstrip("/")
    if not rel or any(part.startswith(".") for part in Path(rel).parts):
        raise EditError("Invalid path")
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        raise EditError("Path outside notes directory", 403)
    return p


def editable(p: Path) -> bool:
    return p.suffix.lower() in EDITABLE_EXTS


def read_source(root: Path, rel: str) -> dict:
    p = inside(root, rel)
    if not p.is_file():
        raise EditError("File not found", 404)
    if not editable(p):
        raise EditError("Only Markdown and text notes can be edited", 415)
    return {"path": rel, "content": p.read_text(encoding="utf-8", errors="replace"), "mtime_ns": stamp(p)}


def stamp(p: Path) -> str:
    """File version stamp. A string, because nanosecond mtimes don't fit in a JavaScript number."""
    return str(p.stat().st_mtime_ns)


def _atomic_write(p: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".recall-", dir=p.parent)  # hidden: ignored by indexer and watcher
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if p.exists():
            os.chmod(tmp, p.stat().st_mode & 0o777)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _version_dir(versions_root: Path, rel: str) -> Path:
    return versions_root / hashlib.sha1(rel.encode()).hexdigest()[:16]


def save_source(root: Path, versions_root: Path, rel: str, content: str, base_mtime_ns: str | int | None,
                force: bool = False) -> dict:
    """Write a note. Refuses (409) if the file changed on disk since `base_mtime_ns`, unless `force`."""
    p = inside(root, rel)
    if not editable(p):
        raise EditError("Only Markdown and text notes can be edited", 415)
    data = content.encode("utf-8")
    if len(data) > MAX_NOTE_BYTES:
        raise EditError("Note is too large", 413)
    if p.exists():
        cur = stamp(p)
        if not force and base_mtime_ns is not None and cur != str(base_mtime_ns):
            raise EditError("The file was changed on disk since you opened it", 409)
        old = p.read_bytes()
        if old == data:
            return {"path": rel, "mtime_ns": cur, "changed": False}
        # keep the previous content so a bad save can be undone
        vd = _version_dir(versions_root, rel)
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "path.txt").write_text(rel)
        (vd / f"{time.time_ns()}.bak").write_bytes(old)
        for f in sorted(vd.glob("*.bak"))[:-KEEP_VERSIONS]:
            f.unlink()
    elif not force and base_mtime_ns is not None:
        raise EditError("The file was deleted on disk since you opened it", 409)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, data)
    return {"path": rel, "mtime_ns": stamp(p), "changed": True}


def list_versions(versions_root: Path, rel: str) -> list[dict]:
    vd = _version_dir(versions_root, rel)
    out = []
    for f in sorted(vd.glob("*.bak"), reverse=True):
        out.append({"id": f.stem, "saved_at": int(f.stem) / 1e9, "size": f.stat().st_size})
    return out


def read_version(versions_root: Path, rel: str, vid: str) -> str:
    if not re.fullmatch(r"\d+", vid):
        raise EditError("Bad version id")
    f = _version_dir(versions_root, rel) / f"{vid}.bak"
    if not f.is_file():
        raise EditError("Version not found", 404)
    return f.read_text(encoding="utf-8", errors="replace")


def create_note(root: Path, rel: str, title: str = "") -> dict:
    rel = rel.strip()
    if not Path(rel).suffix:
        rel += ".md"
    p = inside(root, rel)
    if not editable(p):
        raise EditError("New notes must be .md or .txt")
    if p.exists():
        raise EditError("A file with that name already exists", 409)
    title = title.strip() or p.stem.replace("-", " ").replace("_", " ")
    p.parent.mkdir(parents=True, exist_ok=True)
    body = f"# {title}\n\n" if p.suffix.lower() != ".txt" else ""
    _atomic_write(p, body.encode())
    return {"path": p.relative_to(root.resolve()).as_posix(), "mtime_ns": stamp(p)}


def _safe_name(name: str) -> str:
    stem, ext = os.path.splitext(Path(name).name)
    stem = re.sub(r"[^\w.-]+", "-", stem).strip("-.") or "image"
    return stem[:80] + ext.lower()


def save_upload(root: Path, note_rel: str, filename: str, data: bytes) -> dict:
    """Store an image next to the note (same folder) and return the relative link to insert."""
    note = inside(root, note_rel)
    name = _safe_name(filename or "pasted.png")
    if Path(name).suffix not in UPLOAD_EXTS:
        raise EditError("Only image files can be uploaded", 415)
    if len(data) > MAX_UPLOAD_BYTES:
        raise EditError("Image is too large", 413)
    folder = note.parent
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    n = 1
    while target.exists():
        if target.read_bytes() == data:
            break  # same image pasted twice: reuse it
        target = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    else:
        _atomic_write(target, data)
    return {"name": target.name, "path": target.relative_to(root.resolve()).as_posix()}
