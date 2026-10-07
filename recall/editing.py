"""Editing notes in place: conflict-checked atomic saves, version history, new notes, moves, image uploads."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, unquote

from .extractors import EXTERNAL_RE, HTML_IMG_RE, IMAGE_EXTS, MD_IMAGE_RE, MD_LINK_RE, split_code
from .index import SKIP_DIRS

EDITABLE_EXTS = {".md", ".markdown", ".txt"}
MARKDOWN_EXTS = {".md", ".markdown"}
UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_NOTE_BYTES = 5_000_000
MAX_UPLOAD_BYTES = 25_000_000
IMAGES_DIR = "images"  # uploads go to this folder next to the note
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


def _walk(root: Path):
    """Yield (folder, filenames) for every visible folder under root, skipping hidden and tool folders."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        yield Path(dirpath), [f for f in filenames if not f.startswith(".")]


def list_folders(root: Path) -> list[str]:
    """Every folder under the notes root (including empty ones), as paths relative to it."""
    root = root.resolve()
    return [d.relative_to(root).as_posix() for d, _ in _walk(root) if d != root]


def _folder(root: Path, rel: str) -> Path:
    """The notes root for "" (top level), otherwise a folder inside it."""
    rel = rel.strip().strip("/")
    return root.resolve() if not rel else inside(root, rel)


def _retarget(md: str, root: Path, base_old: Path, base_new: Path, moved: dict[Path, Path]) -> str:
    """Rewrite relative link/image targets in `md` so they still point at the same files.

    `base_old`/`base_new` are the note's folder before and after a move (equal for notes that only link
    to the moved files); links to a key of `moved` are pointed at its value. Broken and external links are kept.
    """

    def fix(target: str) -> str | None:
        if EXTERNAL_RE.match(target):
            return None
        path, suffix = re.match(r"([^#?]*)(.*)", target, re.S).groups()  # keep #anchor / ?query
        if not path:
            return None
        absolute = path.startswith("/")
        try:
            dest = ((root / unquote(path).lstrip("/")) if absolute else (base_old / unquote(path))).resolve()
        except OSError:
            return None
        if dest in moved:
            dest = moved[dest]
        elif base_old == base_new or absolute or not dest.exists():
            return None
        rel = Path(os.path.relpath(dest, root if absolute else base_new)).as_posix()
        rel = "/" + rel if absolute else rel
        if "%" in path or " " in rel:
            rel = quote(rel, safe="/")
        return rel + suffix if rel + suffix != target else None

    parts = []
    for is_code, seg in split_code(md):
        if not is_code:
            spans = {}
            for rx, g in ((MD_IMAGE_RE, 2), (MD_LINK_RE, 2), (HTML_IMG_RE, 3)):
                for m in rx.finditer(seg):
                    spans[m.span(g)] = m.group(g)
            for (a, b), target in sorted(spans.items(), reverse=True):
                repl = fix(target)
                if repl is not None:
                    seg = seg[:a] + repl + seg[b:]
        parts.append(seg)
    return "".join(parts)


def _image_targets(md: str, base: Path) -> set[Path]:
    """Local image files that `md` (a note in folder `base`) shows through relative Markdown or HTML images."""
    out = set()
    for is_code, seg in split_code(md):
        if is_code:
            continue
        targets = [m.group(2) for m in MD_IMAGE_RE.finditer(seg)] + [m.group(3) for m in HTML_IMG_RE.finditer(seg)]
        for t in targets:
            path = re.match(r"[^#?]*", t).group(0)
            if not path or EXTERNAL_RE.match(t) or path.startswith("/"):
                continue
            try:
                p = (base / unquote(path)).resolve()
            except OSError:
                continue
            if p.suffix.lower() in IMAGE_EXTS and p.is_file():
                out.add(p)
    return out


def _notes(root: Path):
    for d, files in _walk(root):
        for f in files:
            if Path(f).suffix.lower() in MARKDOWN_EXTS:
                yield d, d / f


def _move_images(root: Path, src: Path, dest_dir: Path, text: str) -> dict[Path, Path]:
    """Bring along the images a note shows from its own folder or the `images` folder next to it.

    They keep their place relative to the note. An image another note also shows is copied instead, so that
    note keeps working. Returns {old path: new path}.
    """
    own = {p for p in _image_targets(text, src.parent)
           if p.parent == src.parent or p.is_relative_to(src.parent / IMAGES_DIR)}
    if not own:
        return {}
    shared = set()
    names = {n for p in own for n in (p.name, quote(p.name))}
    for d, p in _notes(root):
        if p == src:
            continue
        try:
            other = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(n in other for n in names):
            shared |= own & _image_targets(other, d)
    moved = {}
    for img in sorted(own):
        target = dest_dir / img.relative_to(src.parent)
        n = 1
        while target.exists() and target.read_bytes() != img.read_bytes():
            target = target.with_name(f"{img.stem}-{n}{img.suffix}")
            n += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        if img in shared:
            if not target.exists():
                shutil.copy2(img, target)
        elif target.exists():  # the same image is already there
            img.unlink()
        else:
            os.replace(img, target)
        if img not in shared:  # drop folders (like `images`) the move left empty
            for d in img.parents:
                if d == src.parent or not d.is_relative_to(src.parent) or any(d.iterdir()):
                    break
                d.rmdir()
        moved[img] = target
    return moved


def move_file(root: Path, versions_root: Path, rel: str, folder: str) -> dict:
    """Move a file into another folder (created if needed), keeping relative links working.

    Links inside a moved Markdown note are rewritten for its new location, and the images it shows from its
    own folder move with it (see `_move_images`). Relative links to the file from other Markdown notes are
    updated. Wiki links ([[name]]) find notes by name, so they need no change.
    """
    root = root.resolve()
    src = inside(root, rel)
    if not src.is_file():
        raise EditError("File not found", 404)
    dest_dir = _folder(root, folder)
    dest = dest_dir / src.name
    if dest == src:
        raise EditError("The file is already in that folder")
    if dest.exists():
        raise EditError(f"{dest.relative_to(root).as_posix()} already exists", 409)
    dest_dir.mkdir(parents=True, exist_ok=True)
    images: dict[Path, Path] = {}
    if src.suffix.lower() in MARKDOWN_EXTS:
        images = _move_images(root, src, dest_dir, src.read_text(encoding="utf-8", errors="replace"))
    os.replace(src, dest)
    new_rel = dest.relative_to(root).as_posix()

    if dest.suffix.lower() in MARKDOWN_EXTS:
        text = dest.read_text(encoding="utf-8", errors="replace")
        fixed = _retarget(text, root, src.parent, dest_dir, {src: dest, **images})
        if fixed != text:
            _atomic_write(dest, fixed.encode("utf-8"))

    updated = []
    needles = {src.name, quote(src.name)}
    for d, files in _walk(root):
        for f in files:
            p = d / f
            if p == dest or p.suffix.lower() not in MARKDOWN_EXTS:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not any(n in text for n in needles):
                continue
            fixed = _retarget(text, root, d, d, {src: dest})
            if fixed != text:
                _atomic_write(p, fixed.encode("utf-8"))
                updated.append(p.relative_to(root).as_posix())

    old_vd, new_vd = _version_dir(versions_root, src.relative_to(root).as_posix()), _version_dir(versions_root, new_rel)
    if old_vd.is_dir() and not new_vd.exists():
        old_vd.rename(new_vd)
        (new_vd / "path.txt").write_text(new_rel)
    return {"path": new_rel, "updated": sorted(updated),
            "images": sorted(p.relative_to(root).as_posix() for p in images.values())}


def _safe_name(name: str) -> str:
    stem, ext = os.path.splitext(Path(name).name)
    stem = re.sub(r"[^\w.-]+", "-", stem).strip("-.") or "image"
    return stem[:80] + ext.lower()


def save_upload(root: Path, note_rel: str, filename: str, data: bytes) -> dict:
    """Store an image in the `images` folder next to the note (created if needed); return the link to insert."""
    note = inside(root, note_rel)
    name = _safe_name(filename or "pasted.png")
    if Path(name).suffix not in UPLOAD_EXTS:
        raise EditError("Only image files can be uploaded", 415)
    if len(data) > MAX_UPLOAD_BYTES:
        raise EditError("Image is too large", 413)
    folder = note.parent / IMAGES_DIR
    if folder.exists() and not folder.is_dir():
        raise EditError(f"'{IMAGES_DIR}' next to this note is a file, not a folder", 409)
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
    return {"name": target.name, "path": target.relative_to(root.resolve()).as_posix(),
            "link": f"{IMAGES_DIR}/{target.name}"}
