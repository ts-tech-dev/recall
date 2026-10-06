"""Local index over the notes directory.

Keyword search uses SQLite FTS5 (BM25); semantic search uses local embeddings. The two are merged
with reciprocal-rank fusion. Image text (OCR, AI captions) is folded into the chunk it appears in.
Links between notes are stored for backlinks and the note graph.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from collections.abc import Callable
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np

from .chunker import chunk_markdown
from .embeddings import Embedder
from .extractors import IMAGE_EXTS, NOTE_TYPES, Context, extract, file_url
from .images import ImageText, ocr_bytes

log = logging.getLogger("recall.index")

SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv"}

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS docs(
    id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, ext TEXT, folder TEXT, kind TEXT,
    mtime REAL, size INTEGER, tags TEXT, error TEXT, indexed_at REAL);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, heading TEXT, anchor TEXT,
    text TEXT, images TEXT);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, heading, text, tags, tokenize='porter unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS vectors(chunk_id INTEGER PRIMARY KEY, vec BLOB);
CREATE TABLE IF NOT EXISTS links(src_id INTEGER, dst_path TEXT);
CREATE INDEX IF NOT EXISTS links_src ON links(src_id);
CREATE INDEX IF NOT EXISTS links_dst ON links(dst_path);
CREATE TABLE IF NOT EXISTS doc_images(doc_id INTEGER, key TEXT, path TEXT);
CREATE INDEX IF NOT EXISTS doc_images_doc ON doc_images(doc_id);
CREATE INDEX IF NOT EXISTS doc_images_key ON doc_images(key);
CREATE TABLE IF NOT EXISTS image_meta(
    key TEXT PRIMARY KEY, ocr TEXT, ocr_done INTEGER DEFAULT 0, caption TEXT, caption_model TEXT);
"""
# Tables rebuilt when the schema changes; image_meta (OCR/caption cache) survives.
REBUILT_TABLES = ("docs", "chunks", "chunks_fts", "vectors", "links", "doc_images")

RRF_K = 60
DOC_LINK_RE = re.compile(r"\]\(#doc=([^)\s]+)\)")
CAPTION_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_CAPTION_BYTES = 3_500_000
MAX_IMAGE_TEXT = 1200

STOPWORDS = set(
    """a about above after again against all am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further had
    has have having he her here hers herself him himself his how i if in into is it its itself just
    me more most my myself no nor not now of off on once only or other our ours ourselves out over
    own same she should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where which while who
    whom why will with would you your yours yourself yourselves tell explain describe give show
    summarize summary detailed report notes note please know find list""".split()
)

TYPE_GROUPS = {
    "markdown": [".md", ".markdown"],
    "pdf": [".pdf"],
    "word": [".docx"],
    "powerpoint": [".pptx"],
    "spreadsheet": [".xlsx", ".csv"],
    "html": [".html", ".htm"],
    "text": [".txt", ".rst", ".org"],
    "image": sorted(IMAGE_EXTS),
}

# Markers around matched terms in snippets; the browser escapes the text, then swaps these
# for <mark>, so note content is never interpreted as HTML.
HL_START, HL_END = "\x02", "\x03"


def build_match_query(q: str) -> str:
    """Turn a natural-language question into a safe FTS5 OR-query of quoted terms/phrases."""
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]*"', " ", q)
    terms: list[str] = []
    for p in phrases:
        words = re.findall(r"\w+", p.lower())
        if words:
            terms.append('"' + " ".join(words) + '"')
    seen = set()
    for w in re.findall(r"\w+", rest.lower()):
        if (len(w) < 2 and not w.isdigit()) or w in STOPWORDS or w in seen:
            continue
        seen.add(w)
        terms.append(f'"{w}"' + ("*" if len(w) >= 4 else ""))
    if not terms:  # question was all stopwords — fall back to every word
        terms = [f'"{w}"' for w in dict.fromkeys(re.findall(r"\w+", rest.lower()))]
    return " OR ".join(terms)


@dataclass
class Filters:
    types: list[str] = field(default_factory=list)  # group names or extensions
    folder: str = ""  # restrict to this folder (recursive)
    path: str = ""  # restrict to a single document

    def extensions(self) -> list[str]:
        exts: list[str] = []
        for t in self.types:
            t = t.strip().lower()
            if not t:
                continue
            if t in TYPE_GROUPS:
                exts += TYPE_GROUPS[t]
            else:
                exts.append(t if t.startswith(".") else "." + t)
        return exts


class Index:
    def __init__(self, root: Path, data_dir: Path, embedder: Embedder | None = None, ocr: bool = False):
        self.root = Path(root).resolve()
        key = hashlib.sha1(str(self.root).encode()).hexdigest()[:12]
        base = data_dir / "indexes"
        base.mkdir(parents=True, exist_ok=True)
        self.db_path = base / f"{key}.db"
        self.cache_dir = base / f"{key}_images"
        self.versions_dir = base / f"{key}_versions"
        self.embedder = embedder
        self.embed_error = ""
        self.ocr = ocr
        self.images = ImageText(self._conn, ocr_enabled=ocr)
        # Set by the app: fn(image_bytes, media_type) -> caption, and the per-run limit.
        self.captioner: Callable[[bytes, str], str] | None = None
        self.caption_limit = 100
        self.version = 0  # bumped whenever indexed content changes (the UI polls this)
        self.last_changed: list[str] = []
        self.progress = {"running": False, "phase": "", "done": 0, "total": 0, "current": "", "errors": 0,
                         "finished_at": None}
        self._lock = threading.Lock()
        self._vec_cache: tuple | None = None
        self._doc_vec_cache: tuple | None = None
        self._init_schema()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = None
            if "meta" in tables:
                row = c.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                version = row[0] if row else None
            if tables and version != SCHEMA_VERSION:
                for t in REBUILT_TABLES:
                    c.execute(f"DROP TABLE IF EXISTS {t}")
            c.executescript(SCHEMA)
            c.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (SCHEMA_VERSION,))
            # Vectors from a different embedding model are not comparable: drop them.
            want = self.embedder.name if self.embedder else ""
            row = c.execute("SELECT value FROM meta WHERE key='embed_model'").fetchone()
            if want and (row[0] if row else None) != want:
                c.execute("DELETE FROM vectors")
                c.execute("INSERT OR REPLACE INTO meta VALUES('embed_model', ?)", (want,))

    # ------------------------------------------------------------------ files

    def iter_files(self, include_images: bool = False):
        exts = set(NOTE_TYPES) | (IMAGE_EXTS if include_images else set())
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
            for f in sorted(filenames):
                if f.startswith(".") or f.startswith("~$"):  # hidden / Office lock files
                    continue
                p = Path(dirpath) / f
                if p.suffix.lower() in exts:
                    yield p

    def context(self) -> Context:
        return Context(root=self.root, cache_dir=self.cache_dir, ocr=ocr_bytes if self.ocr else None)

    def resolve_image(self, url: str) -> Path | None:
        """Map an /api/file or /api/cache-image URL back to a local file (or None)."""
        u = urlparse(url)
        if u.path == "/api/file":
            rel = parse_qs(u.query).get("path", [""])[0]
            p = (self.root / rel).resolve()
            try:
                p.relative_to(self.root)
            except ValueError:
                return None
            return p if p.is_file() else None
        if u.path.startswith("/api/cache-image/"):
            name = unquote(u.path.rsplit("/", 1)[-1])
            if not re.fullmatch(r"[0-9a-f]{20}\.(png|jpg|jpeg|gif|webp)", name):
                return None
            p = self.cache_dir / name
            return p if p.is_file() else None
        return None

    # ------------------------------------------------------------------ indexing

    def build(self, full: bool = False, block: bool = True) -> dict:
        """Incrementally (re)index the notes directory. Returns counts.

        With block=False, returns immediately if another build is already running.
        """
        if not self._lock.acquire(blocking=block):
            return {"skipped": "already running"}
        try:
            stats = self._build(full)
            if self.captioner and self.caption_limit > 0:
                stats["captioned"] = self._caption_images()
                if stats["captioned"]:
                    again = self._build(False)  # fold the new captions into chunk text
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
        parts = []
        if info["caption"]:
            parts.append(f"shows: {info['caption']}")
        if info["ocr"]:
            parts.append("text in image: " + " ".join(info["ocr"].split())[:MAX_IMAGE_TEXT])
        return f"[image{' ' + repr(alt) if alt else ''} {'; '.join(parts)}]" if parts else ""

    def _index_file(self, c, p: Path, rel: str, st, old_id, ctx: Context) -> str | None:
        if old_id:
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
                doc = extract(p, ctx)
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
            info = self.images.get(p, c)
            c.execute("INSERT INTO doc_images VALUES(?,?,?)", (doc_id, info["key"], str(p)))
            text = "\n".join(x for x in (info["caption"], info["ocr"]) if x)
            if text:  # only images with recognizable content become searchable
                rows.append((p.name, "", text, [{"url": file_url(rel), "alt": p.stem, "caption": info["caption"]}]))
        for ch in chunks:
            extra = []
            for img in ch.images:
                local = self.resolve_image(img["url"])
                if not local:
                    continue
                info = self.images.get(local, c)
                c.execute("INSERT INTO doc_images VALUES(?,?,?)", (doc_id, info["key"], str(local)))
                if info["caption"]:
                    img["caption"] = info["caption"]
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
        if doc:
            targets = {unquote(m) for m in DOC_LINK_RE.findall(doc.markdown)} - {rel}
            c.executemany("INSERT INTO links VALUES(?,?)", [(doc_id, t) for t in sorted(targets)])
        return err

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

    def _caption_images(self) -> int:
        """Ask the AI to describe images that don't have a caption yet (up to caption_limit)."""
        with self._conn() as c:
            todo = c.execute(
                "SELECT di.key, MIN(di.path) AS path FROM doc_images di LEFT JOIN image_meta m ON m.key=di.key "
                "WHERE m.caption IS NULL AND m.caption_model IS NULL GROUP BY di.key LIMIT ?",
                (self.caption_limit,),
            ).fetchall()
        if not todo:
            return 0
        self.progress.update(phase="captioning", done=0, total=len(todo), current="")
        done = 0
        for r in todo:
            p = Path(r["path"])
            self.progress["current"] = p.name
            mt = CAPTION_TYPES.get(p.suffix.lower())
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if not mt or len(data) > MAX_CAPTION_BYTES:
                self.images.set_caption(r["key"], "", "skipped")
                continue
            try:
                caption = (self.captioner(data, mt) or "").strip()
            except Exception as e:
                log.warning("Captioning stopped: %s", e)
                self.progress["error"] = str(e)
                break  # bad key / rate limit: try again next run
            self.images.set_caption(r["key"], caption, "ai")
            done += 1
            self.progress["done"] = done
            with self._conn() as c:  # force the notes that show this image to be re-indexed
                c.execute("UPDATE docs SET mtime=-1 WHERE id IN (SELECT doc_id FROM doc_images WHERE key=?)", (r["key"],))
        return done

    # ------------------------------------------------------------------ queries

    def stats(self) -> dict:
        with self._conn() as c:
            docs = c.execute("SELECT COUNT(*) FROM docs WHERE kind='note'").fetchone()[0]
            images = c.execute("SELECT COUNT(*) FROM docs WHERE kind='image'").fetchone()[0]
            chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            vectors = c.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            captions = c.execute("SELECT COUNT(*) FROM image_meta WHERE caption != ''").fetchone()[0]
            errors = [dict(r) for r in c.execute("SELECT path, error FROM docs WHERE error IS NOT NULL")]
            by_type = {r[0]: r[1] for r in c.execute("SELECT ext, COUNT(*) FROM docs WHERE kind='note' GROUP BY ext")}
        return {"docs": docs, "images": images, "chunks": chunks, "vectors": vectors, "captions": captions,
                "errors": errors, "by_type": by_type, "version": self.version,
                "semantic": bool(self.embedder) and not self.embed_error, "embed_error": self.embed_error}

    @staticmethod
    def _filter_sql(filters: Filters) -> tuple[list[str], list]:
        # An image that appears inside a note is found through that note (with its context),
        # so only "loose" images are returned as results of their own.
        where, args = ["""NOT (d.kind = 'image' AND EXISTS (
            SELECT 1 FROM doc_images mine JOIN doc_images other ON other.key = mine.key
            JOIN docs n ON n.id = other.doc_id WHERE mine.doc_id = d.id AND n.kind = 'note'))"""], []
        exts = filters.extensions()
        if exts:
            where.append(f"d.ext IN ({','.join('?' * len(exts))})")
            args += exts
        if filters.folder.strip("/"):
            f = filters.folder.strip("/")
            where.append("(d.folder = ? OR d.folder LIKE ? ESCAPE '\\')")
            args += [f, f.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"]
        if filters.path:
            where.append("d.path = ?")
            args.append(filters.path)
        return where, args

    def _keyword(self, q: str, filters: Filters, fetch: int) -> list[dict]:
        match = build_match_query(q)
        if not match:
            return []
        where, args = self._filter_sql(filters)
        sql = f"""
            SELECT c.id, c.heading, c.anchor, c.text, c.images, d.path, d.title, d.ext,
                   bm25(chunks_fts, 4.0, 3.0, 1.0, 2.0) AS score,
                   snippet(chunks_fts, 2, '{HL_START}', '{HL_END}', '…', 32) AS snippet
            FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid JOIN docs d ON d.id = c.doc_id
            WHERE {' AND '.join(['chunks_fts MATCH ?'] + where)}
            ORDER BY score LIMIT ?"""
        with self._conn() as c:
            try:
                return [dict(r) for r in c.execute(sql, [match] + args + [fetch]).fetchall()]
            except sqlite3.OperationalError:
                return []

    def _vectors(self) -> tuple[np.ndarray, np.ndarray]:
        cache = self._vec_cache
        if cache is None or cache[0] != self.version:
            with self._conn() as c:
                rows = c.execute("SELECT chunk_id, vec FROM vectors ORDER BY chunk_id").fetchall()
            ids = np.array([r[0] for r in rows], dtype=np.int64)
            mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 1), np.float32)
            cache = self._vec_cache = (self.version, ids, mat)
        return cache[1], cache[2]

    def _semantic(self, q: str, filters: Filters, fetch: int) -> list[dict]:
        if not self.embedder or self.embed_error or not q.strip():
            return []
        ids, mat = self._vectors()
        if not len(ids):
            return []
        try:
            qv = self.embedder.embed_query(q)
        except Exception as e:
            self.embed_error = f"{type(e).__name__}: {e}"
            return []
        sims = mat @ qv
        order = np.argsort(-sims)[: fetch * 5]  # extra room: filters are applied afterwards
        order = [i for i in order if sims[i] >= self.embedder.min_similarity]
        if not order:
            return []
        sim_by_id = {int(ids[i]): float(sims[i]) for i in order}
        where, args = self._filter_sql(filters)
        id_list = list(sim_by_id)
        sql = f"""SELECT c.id, c.heading, c.anchor, c.text, c.images, d.path, d.title, d.ext
                  FROM chunks c JOIN docs d ON d.id = c.doc_id
                  WHERE {' AND '.join([f"c.id IN ({','.join('?' * len(id_list))})"] + where)}"""
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(sql, id_list + args)]
        for r in rows:
            r["similarity"] = round(sim_by_id[r["id"]], 3)
            words = r["text"].split()
            r["snippet"] = " ".join(words[:40]) + (" …" if len(words) > 40 else "")
        rows.sort(key=lambda r: -r["similarity"])
        return rows[:fetch]

    def search(self, q: str, filters: Filters | None = None, limit: int = 20, per_doc: int = 0,
               mode: str = "hybrid") -> list[dict]:
        """Hybrid search. mode: hybrid | keyword | semantic.

        `per_doc` > 0 caps results per document for more diverse context.
        """
        filters = filters or Filters()
        fetch = max(limit * 4, 40)
        kw = self._keyword(q, filters, fetch) if mode != "semantic" else []
        sem = self._semantic(q, filters, fetch) if mode != "keyword" else []
        merged: dict[int, dict] = {}
        for source, rows in (("keyword", kw), ("semantic", sem)):
            for rank, r in enumerate(rows):
                m = merged.setdefault(r["id"], {**r, "rrf": 0.0, "match": source})
                if m["match"] != source:
                    m["match"] = "both"
                    m.setdefault("similarity", r.get("similarity"))
                m["rrf"] += 1.0 / (RRF_K + rank + 1)
        ranked = sorted(merged.values(), key=lambda r: -r["rrf"])
        out, per = [], {}
        for r in ranked:
            if per_doc and per.get(r["path"], 0) >= per_doc:
                continue
            per[r["path"]] = per.get(r["path"], 0) + 1
            r["images"] = json.loads(r["images"] or "[]")
            r["score"] = round(r.pop("rrf") * 1000, 2)
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def doc_info(self, rel: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT path, title, ext, tags, error FROM docs WHERE path=?", (rel,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        return d

    # ------------------------------------------------------------------ links / related / graph

    def links(self, rel: str) -> dict:
        """Outgoing links and backlinks of one note (only to notes that exist)."""
        with self._conn() as c:
            out = c.execute(
                "SELECT DISTINCT t.path, t.title, t.ext FROM docs s JOIN links l ON l.src_id=s.id "
                "JOIN docs t ON t.path=l.dst_path WHERE s.path=? ORDER BY t.title", (rel,)).fetchall()
            back = c.execute(
                "SELECT DISTINCT s.path, s.title, s.ext FROM links l JOIN docs s ON s.id=l.src_id "
                "WHERE l.dst_path=? ORDER BY s.title", (rel,)).fetchall()
        return {"outgoing": [dict(r) for r in out], "backlinks": [dict(r) for r in back]}

    def _doc_vectors(self) -> tuple[list[dict], np.ndarray]:
        """One vector per note: the normalized mean of its chunk vectors."""
        cache = self._doc_vec_cache
        if cache is None or cache[0] != self.version:
            ids, mat = self._vectors()
            docs, vecs = [], []
            if len(ids):
                pos = {int(i): n for n, i in enumerate(ids)}
                with self._conn() as c:
                    rows = c.execute("SELECT d.id, d.path, d.title, d.ext, c.id AS cid FROM docs d "
                                     "JOIN chunks c ON c.doc_id=d.id WHERE d.kind='note' ORDER BY d.id").fetchall()
                by_doc: dict[int, list] = {}
                meta = {}
                for r in rows:
                    if r["cid"] in pos:
                        by_doc.setdefault(r["id"], []).append(pos[r["cid"]])
                        meta[r["id"]] = {"path": r["path"], "title": r["title"], "ext": r["ext"]}
                for did, idx in by_doc.items():
                    v = mat[idx].mean(axis=0)
                    vecs.append(v / max(np.linalg.norm(v), 1e-12))
                    docs.append(meta[did])
            m = np.vstack(vecs).astype(np.float32) if vecs else np.zeros((0, 1), np.float32)
            cache = self._doc_vec_cache = (self.version, docs, m)
        return cache[1], cache[2]

    def doc_similarity_threshold(self) -> float:
        return (self.embedder.min_similarity + 0.15) if self.embedder else 1.0

    def similar(self, rel: str, k: int = 6) -> list[dict]:
        if not self.embedder or self.embed_error:
            return []
        docs, mat = self._doc_vectors()
        idx = next((i for i, d in enumerate(docs) if d["path"] == rel), None)
        if idx is None:
            return []
        sims = mat @ mat[idx]
        out = []
        for j in np.argsort(-sims):
            if j == idx:
                continue
            if sims[j] < self.doc_similarity_threshold() or len(out) >= k:
                break
            out.append({**docs[j], "similarity": round(float(sims[j]), 3)})
        return out

    def graph(self, similar: bool = True, k: int = 3) -> dict:
        with self._conn() as c:
            nodes = [dict(r) for r in c.execute(
                "SELECT path AS id, title, ext, folder FROM docs WHERE kind='note' ORDER BY path")]
            link_rows = c.execute(
                "SELECT DISTINCT s.path AS source, l.dst_path AS target FROM links l JOIN docs s ON s.id=l.src_id "
                "JOIN docs t ON t.path=l.dst_path WHERE t.kind='note'").fetchall()
        edges = [{"source": r["source"], "target": r["target"], "type": "link"} for r in link_rows]
        if similar and self.embedder and not self.embed_error:
            docs, mat = self._doc_vectors()
            seen = {tuple(sorted((e["source"], e["target"]))) for e in edges}
            thr = self.doc_similarity_threshold()
            for start in range(0, len(docs), 512):  # blockwise to bound memory on big vaults
                block = mat[start : start + 512] @ mat.T
                for bi, row in enumerate(block):
                    i = start + bi
                    row[i] = -1
                    for j in np.argsort(-row)[:k]:
                        if row[j] < thr:
                            break
                        pair = tuple(sorted((docs[i]["path"], docs[j]["path"])))
                        if pair not in seen:
                            seen.add(pair)
                            edges.append({"source": pair[0], "target": pair[1], "type": "similar",
                                          "weight": round(float(row[j]), 3)})
        return {"nodes": nodes, "edges": edges}
