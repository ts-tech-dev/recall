"""Links between notes: backlinks, similar notes and the graph view."""

from __future__ import annotations

import numpy as np


class GraphMixin:
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
