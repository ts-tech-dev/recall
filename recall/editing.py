"""Editing notes in place: conflict-checked atomic saves, version history, new notes, moves, image uploads."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .extractors import EXTERNAL_RE, HTML_IMG_RE, IMAGE_EXTS, MD_IMAGE_RE, MD_LINK_RE, Context, split_code
from .extractors.markdown import WIKI_EMBED_RE, WIKI_LINK_RE, resolve_local
from .index import SKIP_DIRS

EDITABLE_EXTS = {".md", ".markdown", ".txt"}
MARKDOWN_EXTS = {".md", ".markdown"}
UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_NOTE_BYTES = 5_000_000
MAX_UPLOAD_BYTES = 25_000_000
IMAGES_DIR = "images"  # uploads go to this folder next to the note
KEEP_VERSIONS = 30
VERSION_MAX_AGE = 4 * 3600  # seconds; older saved versions are hidden and deleted


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


def _expired(f: Path) -> bool:
    return int(f.stem) < time.time_ns() - VERSION_MAX_AGE * 10**9


def _prune_version_dir(vd: Path) -> None:
    """Delete versions older than VERSION_MAX_AGE (and beyond KEEP_VERSIONS); drop the folder once it's empty."""
    baks = sorted(vd.glob("*.bak"))
    for f in baks[:-KEEP_VERSIONS] + [f for f in baks[-KEEP_VERSIONS:] if _expired(f)]:
        f.unlink(missing_ok=True)
    if not any(vd.glob("*.bak")):
        shutil.rmtree(vd, ignore_errors=True)


def prune_versions(versions_root: Path) -> None:
    """Delete expired versions of every note, including notes that haven't been edited since."""
    if versions_root.is_dir():
        for vd in versions_root.iterdir():
            if vd.is_dir():
                _prune_version_dir(vd)


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
        _prune_version_dir(vd)
    elif not force and base_mtime_ns is not None:
        raise EditError("The file was deleted on disk since you opened it", 409)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, data)
    return {"path": rel, "mtime_ns": stamp(p), "changed": True}


def list_versions(versions_root: Path, rel: str) -> list[dict]:
    vd = _version_dir(versions_root, rel)
    if vd.is_dir():
        _prune_version_dir(vd)
    out = []
    for f in sorted(vd.glob("*.bak"), reverse=True):
        out.append({"id": f.stem, "saved_at": int(f.stem) / 1e9, "size": f.stat().st_size})
    return out


def read_version(versions_root: Path, rel: str, vid: str) -> str:
    if not re.fullmatch(r"\d+", vid):
        raise EditError("Bad version id")
    f = _version_dir(versions_root, rel) / f"{vid}.bak"
    if not f.is_file() or _expired(f):
        raise EditError("Version not found", 404)
    return f.read_text(encoding="utf-8", errors="replace")


def create_note(root: Path, rel: str, title: str = "", content: str | None = None,
                resolve_image: Callable[[str], Path | None] | None = None) -> dict:
    """A new note: a "# title" heading, or `content` (its app links and images made local, see `localize_links`)."""
    rel = rel.strip()
    if not Path(rel).suffix:
        rel += ".md"
    p = inside(root, rel)
    if not editable(p):
        raise EditError("New notes must be .md or .txt")
    if p.exists():
        raise EditError("A file with that name already exists", 409)
    rel = p.relative_to(root.resolve()).as_posix()
    if content is None:
        title = title.strip() or p.stem.replace("-", " ").replace("_", " ")
        body = f"# {title}\n\n" if p.suffix.lower() != ".txt" else ""
    else:
        body = localize_links(root, rel, content, resolve_image)
    data = body.encode("utf-8")
    if len(data) > MAX_NOTE_BYTES:
        raise EditError("Note is too large", 413)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, data)
    return {"path": rel, "mtime_ns": stamp(p)}


def localize_links(root: Path, note_rel: str, md: str, resolve_image: Callable[[str], Path | None] | None) -> str:
    """Turn the app's own links in `md` (written by the browser, e.g. a saved Ask result) into plain relative ones.

    "#doc=…" links to notes and "/api/file?path=…" images become paths relative to the note, so the note works
    in any Markdown editor. Images extracted from PDFs and Office files ("/api/cache-image/…") only live in
    Recall's cache, so they are copied into the `images` folder next to the note.
    """
    root = root.resolve()
    base = inside(root, note_rel).parent

    def fix(target: str) -> str | None:
        if target.startswith("#doc="):
            rel = unquote(target[5:].split("&")[0])
        elif target.startswith("/api/file?"):
            rel = parse_qs(urlparse(target).query).get("path", [""])[0]
        elif target.startswith("/api/cache-image/") and resolve_image:
            local = resolve_image(target)
            return save_upload(root, note_rel, local.name, local.read_bytes())["link"] if local else None
        else:
            return None
        if not rel:
            return None
        try:
            dest = inside(root, rel)
        except EditError:
            return None
        return quote(Path(os.path.relpath(dest, base)).as_posix(), safe="/")

    parts = []
    for is_code, seg in split_code(md):
        if not is_code:
            spans = {}
            for rx in (MD_IMAGE_RE, MD_LINK_RE):
                for m in rx.finditer(seg):
                    spans[m.span(2)] = m.group(2)
            for (a, b), target in sorted(spans.items(), reverse=True):
                repl = fix(target)
                if repl is not None:
                    seg = seg[:a] + repl + seg[b:]
        parts.append(seg)
    return "".join(parts)


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

    They go to the `images` folder next to the note's new place (keeping any sub-folder inside `images`). An image
    another note also shows is copied instead, so that note keeps working. Returns {old path: new path}.
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
        rel = img.relative_to(src.parent)
        target = dest_dir / (rel if rel.parts[0] == IMAGES_DIR else Path(IMAGES_DIR) / rel)
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

    updated = _relink(root, {src: dest}, skip=dest)
    _move_versions(root, versions_root, src, new_rel)
    return {"path": new_rel, "updated": updated,
            "images": sorted(p.relative_to(root).as_posix() for p in images.values())}


def _move_versions(root: Path, versions_root: Path, src: Path, new_rel: str) -> None:
    old_vd, new_vd = _version_dir(versions_root, src.relative_to(root).as_posix()), _version_dir(versions_root, new_rel)
    if old_vd.is_dir() and not new_vd.exists():
        old_vd.rename(new_vd)
        (new_vd / "path.txt").write_text(new_rel)


def _relink(root: Path, moved: dict[Path, Path], skip: Path | None = None, names: Context | None = None) -> list[str]:
    """Point the links in every Markdown note (but `skip`) that led to a key of `moved` at its value.

    Relative links are rewritten. With `names` (a lookup made before the files changed name), so are wiki links
    ([[name]], ![[name]]) that found them by their old names. Returns the notes that changed.
    """
    updated = []
    renamed = {dest: src for src, dest in moved.items()}
    needles = {n.lower() for src in moved for n in {src.name, quote(src.name)} | ({src.stem} if names else set())}
    for d, p in _notes(root):
        if p == skip:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(n in text.lower() for n in needles):
            continue
        fixed = _retarget(text, root, d, d, moved)
        if names:
            fixed = _rename_wiki_links(fixed, names, renamed.get(p, p), root, moved)
        if fixed != text:
            _atomic_write(p, fixed.encode("utf-8"))
            updated.append(p.relative_to(root).as_posix())
    return sorted(updated)


def _rename_wiki_links(md: str, names: Context, note: Path, root: Path, moved: dict[Path, Path]) -> str:
    """Rename the wiki links in `md` (a note at `note`) that found a key of `moved` by name, keeping any folder,
    #heading or |alias."""
    by_rel = {src.relative_to(root).as_posix(): (src, dest) for src, dest in moved.items()}

    def fix(m: re.Match, embed: bool) -> str:
        target = m.group(1).strip()
        found = (resolve_local(names, note, target) or names.find_by_name(target)) if embed \
            else (names.find_by_name(target) or names.find_by_name(target + ".md"))
        if found not in by_rel:
            return m.group(0)
        src, dest = by_rel[found]
        head, _, last = target.rpartition("/")
        new = dest.name if last.lower() == src.name.lower() else dest.stem
        a, b = m.span(1)
        return m.group(0)[: a - m.start()] + (head + "/" if head else "") + new + m.group(0)[b - m.start():]

    parts = []
    for is_code, seg in split_code(md):
        if not is_code:
            seg = WIKI_EMBED_RE.sub(lambda m: fix(m, True), seg)
            seg = WIKI_LINK_RE.sub(lambda m: fix(m, False), seg)
        parts.append(seg)
    return "".join(parts)


def rename_file(root: Path, versions_root: Path, rel: str, name: str) -> dict:
    """Give a file a new name in the same folder, and update the links to it in every Markdown note.

    The extension stays (it is added when `name` leaves it out). A note whose title heading is still its old file
    name ("# old name", as new notes start) gets the new name there too.
    """
    root = root.resolve()
    src = inside(root, rel)
    if not src.is_file():
        raise EditError("File not found", 404)
    name = name.strip()
    if not name or "/" in name or "\\" in name:
        raise EditError("Give a name without folders (use Move… to change the folder)")
    if not name.lower().endswith(src.suffix.lower()):
        name += src.suffix
    new_rel = (src.parent / name).relative_to(root).as_posix()
    dest = inside(root, new_rel)
    if dest == src:
        raise EditError("That is already its name")
    if dest.exists() and not os.path.samefile(dest, src):  # a change of case only is fine on Windows
        raise EditError(f"{new_rel} already exists", 409)
    names = Context(root=root, cache_dir=root)
    names.find_by_name(src.name)  # look up every name now, before the file has its new one
    pasted = _pasted_images(src, names) if src.suffix.lower() in MARKDOWN_EXTS else []
    os.replace(src, dest)
    images = _rename_pasted_images(pasted, src, dest)

    retitled = False
    if dest.suffix.lower() in MARKDOWN_EXTS:
        text = dest.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^#[ \t]+(.+?)[ \t]*$", text, re.M)
        if m and m.group(1) in {src.stem, src.stem.replace("-", " ").replace("_", " ")}:
            title = dest.stem if m.group(1) == src.stem else dest.stem.replace("-", " ").replace("_", " ")
            text = text[: m.start(1)] + title + text[m.end(1):]
            _atomic_write(dest, text.encode("utf-8"))
            retitled = True

    updated = [u for u in _relink(root, {src: dest, **images}, names=names) if u != new_rel]
    _move_versions(root, versions_root, src, new_rel)
    return {"path": new_rel, "updated": updated, "retitled": retitled,
            "images": sorted(p.relative_to(root).as_posix() for p in images.values())}


def _upload_stem(note: Path) -> str:
    """The start of the names `save_upload` gives images pasted into `note` ("my note.md" -> "my-note")."""
    return Path(_safe_name(note.stem + ".x")).stem


def _pasted_images(note: Path, names: Context) -> list[Path]:
    """The images pasted into `note` that it still shows: <note name>_<n> in the `images` folder next to it."""
    text = note.read_text(encoding="utf-8", errors="replace")
    shown = _image_targets(text, note.parent)
    root = names.root.resolve()
    for is_code, seg in split_code(text):
        if not is_code:
            for m in WIKI_EMBED_RE.finditer(seg):
                found = resolve_local(names, note, m.group(1).strip()) or names.find_by_name(m.group(1).strip())
                if found:
                    shown.add(root / found)
    numbered = re.compile(re.escape(_upload_stem(note)) + r"_\d+\.[^.]+", re.IGNORECASE)
    return sorted(p for p in shown if p.parent == note.parent / IMAGES_DIR and numbered.fullmatch(p.name) and p.is_file())


def _rename_pasted_images(images: list[Path], src: Path, dest: Path) -> dict[Path, Path]:
    """Give pasted images the renamed note's name, keeping their numbers where free. Returns {old: new}."""
    old_stem, new_stem = _upload_stem(src), _upload_stem(dest)
    if old_stem == new_stem:
        return {}
    renamed = {}
    for img in images:
        folder, n = img.parent, int(img.stem.rsplit("_", 1)[1])
        target = folder / f"{new_stem}_{n}{img.suffix}"
        if target.exists():  # numbered after the highest image of that name instead
            taken = [int(m.group(1)) for f in folder.iterdir()
                     if (m := re.fullmatch(re.escape(new_stem) + r"_(\d+)\.[^.]+", f.name, re.IGNORECASE))]
            target = folder / f"{new_stem}_{max(taken) + 1}{img.suffix}"
        os.replace(img, target)
        renamed[img] = target
    return renamed


def _safe_name(name: str) -> str:
    stem, ext = os.path.splitext(Path(name).name)
    stem = re.sub(r"[^\w.-]+", "-", stem).strip("-.") or "image"
    return stem[:80] + ext.lower()


def save_upload(root: Path, note_rel: str, filename: str, data: bytes) -> dict:
    """Store an image in the `images` folder next to the note (created if needed); return the link to insert.

    Images are named after the note and numbered: pictures pasted into dicom.md become dicom_1.png, dicom_2.png…
    """
    note = inside(root, note_rel)
    ext = Path(_safe_name(filename or "pasted.png")).suffix
    if ext not in UPLOAD_EXTS:
        raise EditError("Only image files can be uploaded", 415)
    if len(data) > MAX_UPLOAD_BYTES:
        raise EditError("Image is too large", 413)
    folder = note.parent / IMAGES_DIR
    if folder.exists() and not folder.is_dir():
        raise EditError(f"'{IMAGES_DIR}' next to this note is a file, not a folder", 409)
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(_safe_name(note.stem + ".x")).stem
    numbered = re.compile(re.escape(stem) + r"_(\d+)\.[^.]+", re.IGNORECASE)
    last = 0
    for f in folder.iterdir():
        m = numbered.fullmatch(f.name)
        if not m:
            continue
        if f.is_file() and f.stat().st_size == len(data) and f.read_bytes() == data:
            target = f  # same image pasted twice: reuse it
            break
        last = max(last, int(m.group(1)))
    else:
        target = folder / f"{stem}_{last + 1}{ext}"
        _atomic_write(target, data)
    return {"name": target.name, "path": target.relative_to(root.resolve()).as_posix(),
            "link": f"{IMAGES_DIR}/{target.name}"}
