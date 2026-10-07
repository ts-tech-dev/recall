"""Indexing: scanning the notes folder, extracting and chunking files, embeddings and OCR."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote

import numpy as np

from .. import images as images_mod
from ..chunker import chunk_markdown
from ..extractors import IMAGE_EXTS, Context, file_url
from .schema import REBUILT_TABLES

log = logging.getLogger("recall.index")

DOC_LINK_RE = re.compile(r"\]\(#doc=([^)\s]+)\)")
MAX_IMAGE_TEXT = 1200


class BuildMixin:
    # ------------------------------------------------------------------ indexing

    def build(self, full: bool = False, block: bool = True) -> dict:
        """Incrementally (re)index the notes directory. Returns counts.

        With block=False, returns immediately if another build is already running.
        """
        if not self._lock.acquire(blocking=block):
            return {"skipped": "already running"}
        try:
            stats = self._build(full)
            # Text first, so the notes are searchable right away; then OCR the new images and fold their
            # text in (only passages that show those images are re-embedded).
            stats["ocr"] = self._ocr_pending()
            if stats["ocr"]:
                again = self._build(False)
                stats["updated"] += again["updated"]
            return stats
        finally:
            self.progress.update(running=False, phase="", current="", finished_at=time.time())
            self._lock.release()

    def _build(self, full: bool) -> dict:
        files = list(self.iter_files(include_images=True))
        self.progress.update(running=True, phase="reading", done=0, total=len(files), current="", errors=0)
        ctx = self.context()  # shared so the wiki-link name table is built once per run
        stats = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "errors": 0, "embedded": 0}
        changed: list[str] = []
        with self._conn() as c:
            if full:
                for t in REBUILT_TABLES:
                    c.execute(f"DELETE FROM {t}")
            known = {r["path"]: r for r in c.execute("SELECT id, path, mtime, size FROM docs")}
            seen = set()
            for p in files:
                rel = p.relative_to(self.root).as_posix()
                seen.add(rel)
                self.progress["current"] = rel
                try:
                    st = p.stat()
                except OSError:  # vanished mid-scan
                    continue
                old = known.get(rel)
                if old and old["mtime"] == st.st_mtime and old["size"] == st.st_size:
                    stats["unchanged"] += 1
                else:
                    err = self._index_file(c, p, rel, st, old["id"] if old else None, ctx)
                    stats["errors" if err else ("updated" if old else "added")] += 1
                    changed.append(rel)
                    if err:
                        self.progress["errors"] += 1
                    c.commit()
                self.progress["done"] += 1
            for rel, row in known.items():
                if rel not in seen:
                    self._delete_doc(c, row["id"])
                    self.extracts.delete(rel)
                    stats["removed"] += 1
                    changed.append(rel)
            c.commit()
        stats["embedded"] = self._embed_missing()
        if changed or stats["embedded"]:
            self.version += 1
            self.last_changed = changed[:500]
            self._vec_cache = self._doc_vec_cache = None
        return stats

    def _delete_doc(self, c: sqlite3.Connection, doc_id: int) -> None:
        c.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
        c.execute("DELETE FROM vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
        c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM links WHERE src_id=?", (doc_id,))
        c.execute("DELETE FROM doc_images WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM docs WHERE id=?", (doc_id,))

    def _image_text(self, info: dict, alt: str = "") -> str:
        if not info["ocr"]:
            return ""
        text = " ".join(info["ocr"].split())[:MAX_IMAGE_TEXT]
        return f"[image{' ' + repr(alt) if alt else ''} text in image: {text}]"

    def _index_file(self, c, p: Path, rel: str, st, old_id, ctx: Context) -> str | None:
        keep = {}  # embeddings of passages that come out the same are reused, not recomputed
        if old_id:
            keep = {(r["title"], r["heading"], r["text"]): r["vec"] for r in c.execute(
                "SELECT d.title, ch.heading, ch.text, v.vec FROM chunks ch JOIN docs d ON d.id=ch.doc_id "
                "JOIN vectors v ON v.chunk_id=ch.id WHERE ch.doc_id=?", (old_id,))}
            self._delete_doc(c, old_id)
        folder = Path(rel).parent.as_posix()
        folder = "" if folder == "." else folder
        is_image = p.suffix.lower() in IMAGE_EXTS
        err = None
        doc = None
        chunks = []
        try:
            if is_image:
                title = p.name
            else:
                doc = self.extract(p, ctx)
                title = doc.title
                chunks = chunk_markdown(doc.markdown, doc.title)
        except Exception as e:  # a broken file must not stop the whole run
            title, err = p.stem, f"{type(e).__name__}: {e}"
        cur = c.execute(
            "INSERT INTO docs(path,title,ext,folder,kind,mtime,size,tags,error,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (rel, title, p.suffix.lower(), folder, "image" if is_image else "note", st.st_mtime, st.st_size,
             json.dumps(doc.tags if doc else []), err, time.time()),
        )
        doc_id = cur.lastrowid
        tags = " ".join(doc.tags) if doc else ""

        rows = []  # (heading, anchor, text, images)
        if is_image:
            info = self.images.get(p, c, ocr=False)
            c.execute("INSERT INTO doc_images VALUES(?,?,?)", (doc_id, info["key"], str(p)))
            if info["ocr"]:  # only images with recognizable text become searchable
                rows.append((p.name, "", info["ocr"], [{"url": file_url(rel), "alt": p.stem}]))
        for ch in chunks:
            extra = []
            for img in ch.images:
                local = self.resolve_image(img["url"])
                if not local:
                    continue
                info = self.images.get(local, c, ocr=False)
                c.execute("INSERT INTO doc_images VALUES(?,?,?)", (doc_id, info["key"], str(local)))
                t = self._image_text(info, img["alt"])
                if t:
                    extra.append(t)
            rows.append((ch.heading, ch.anchor, "\n\n".join([ch.text] + extra).strip(), ch.images))
        for i, (heading, anchor, text, images) in enumerate(rows):
            cid = c.execute(
                "INSERT INTO chunks(doc_id,ord,heading,anchor,text,images) VALUES(?,?,?,?,?,?)",
                (doc_id, i, heading, anchor, text, json.dumps(images)),
            ).lastrowid
            c.execute("INSERT INTO chunks_fts(rowid,title,heading,text,tags) VALUES(?,?,?,?,?)",
                      (cid, title, heading, text, tags))
            vec = keep.get((title, heading, text))
            if vec is not None:
                c.execute("INSERT INTO vectors VALUES(?,?)", (cid, vec))
        if doc:
            targets = {unquote(m) for m in DOC_LINK_RE.findall(doc.markdown)} - {rel}
            c.executemany("INSERT INTO links VALUES(?,?)", [(doc_id, t) for t in sorted(targets)])
        return err

    def _ocr_pending(self) -> int:
        """Read the text in images that indexing recorded but didn't OCR yet. Returns how many had text."""
        if not self.images.ocr_enabled:
            return 0
        with self._conn() as c:
            todo = c.execute(
                "SELECT di.key, MIN(di.path) AS path FROM doc_images di JOIN image_meta m ON m.key=di.key "
                "WHERE m.ocr_done=0 GROUP BY di.key").fetchall()
        if not todo:
            return 0
        self.progress.update(phase="ocr", done=0, total=len(todo), current="")
        found = 0
        for i, r in enumerate(todo, 1):
            p = Path(r["path"])
            self.progress.update(current=p.name, done=i - 1)
            try:
                text = images_mod.ocr_bytes(p.read_bytes())
            except OSError:
                text = ""
            self.images.set_ocr(r["key"], text)
            if text:
                found += 1
                with self._conn() as c:  # the notes that show this image get its text on the next pass
                    c.execute("UPDATE docs SET mtime=-1 WHERE id IN (SELECT doc_id FROM doc_images WHERE key=?)",
                              (r["key"],))
        self.progress["done"] = len(todo)
        return found

    def _embed_missing(self) -> int:
        """Embed every chunk that has no vector yet (new/changed chunks, or after a model change)."""
        if not self.embedder:
            return 0
        with self._conn() as c:
            todo = c.execute(
                "SELECT c.id, d.title, c.heading, c.text FROM chunks c JOIN docs d ON d.id=c.doc_id "
                "LEFT JOIN vectors v ON v.chunk_id=c.id WHERE v.chunk_id IS NULL"
            ).fetchall()
        if not todo:
            return 0
        self.progress.update(phase="embedding", done=0, total=len(todo), current="")
        done = 0
        try:
            for i in range(0, len(todo), 64):
                batch = todo[i : i + 64]
                texts = [f"{r['title']}\n{r['heading']}\n{r['text']}"[:2000] for r in batch]
                vecs = self.embedder.embed_passages(texts)
                with self._conn() as c:
                    c.executemany("INSERT OR REPLACE INTO vectors VALUES(?,?)",
                                  [(r["id"], v.astype(np.float32).tobytes()) for r, v in zip(batch, vecs)])
                done += len(batch)
                self.progress["done"] = done
            self.embed_error = ""
        except Exception as e:  # e.g. the model can't be downloaded: keyword search still works
            self.embed_error = f"{type(e).__name__}: {e}"
            log.warning("Embedding failed: %s", self.embed_error)
        return done
