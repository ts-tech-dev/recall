"""Searching: keyword (FTS5/BM25), semantic (embeddings) and hybrid (reciprocal-rank fusion)."""

from __future__ import annotations

import json
import logging
import sqlite3

import numpy as np

from .query import HL_END, HL_START, RRF_K, Filters, build_match_query

log = logging.getLogger("recall.index")

# The cross-encoder re-orders the best fused results plus the best of each list on its own, so a passage
# that only one method found (e.g. a paraphrase only the embeddings matched) still gets a fair look.
RERANK_FUSED = 20
RERANK_PER_LIST = 15
RERANK_CHARS = 1500  # passage text given to the cross-encoder (it reads ~512 tokens)


class SearchMixin:
    # ------------------------------------------------------------------ queries

    def stats(self) -> dict:
        with self._conn() as c:
            docs = c.execute("SELECT COUNT(*) FROM docs WHERE kind='note'").fetchone()[0]
            images = c.execute("SELECT COUNT(*) FROM docs WHERE kind='image'").fetchone()[0]
            chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            vectors = c.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            errors = [dict(r) for r in c.execute("SELECT path, error FROM docs WHERE error IS NOT NULL")]
            by_type = {r[0]: r[1] for r in c.execute("SELECT ext, COUNT(*) FROM docs WHERE kind='note' GROUP BY ext")}
        return {"docs": docs, "images": images, "chunks": chunks, "vectors": vectors,
                "errors": errors, "by_type": by_type, "version": self.version,
                "semantic": bool(self.embedder) and not self.embed_error, "embed_error": self.embed_error,
                "rerank": bool(self.reranker) and not self.rerank_error, "rerank_error": self.rerank_error}

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
        if mode != "keyword":
            ranked = self._rerank(q, ranked, [r["id"] for r in kw[:RERANK_PER_LIST]] +
                                  [r["id"] for r in sem[:RERANK_PER_LIST]])
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

    def _rerank(self, q: str, ranked: list[dict], also: list[int]) -> list[dict]:
        """Re-order the candidates with the cross-encoder; everything else keeps its fused order after them."""
        if not self.reranker or not q.strip() or len(ranked) < 2:
            return ranked
        pick = {r["id"] for r in ranked[:RERANK_FUSED]} | set(also)
        top = [r for r in ranked if r["id"] in pick]
        try:
            scores = self.reranker.score(q, [f"{r['title']}\n{r['heading']}\n{r['text']}"[:RERANK_CHARS] for r in top])
            self.rerank_error = ""
        except Exception as e:
            self.rerank_error = f"{type(e).__name__}: {e}"
            log.warning("Re-ranking failed: %s", self.rerank_error)
            return ranked
        for r, s in zip(top, scores):
            r["rerank"] = round(s, 3)
        return sorted(top, key=lambda r: -r["rerank"]) + [r for r in ranked if r["id"] not in pick]

    def doc_info(self, rel: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT path, title, ext, tags, error FROM docs WHERE path=?", (rel,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        return d
