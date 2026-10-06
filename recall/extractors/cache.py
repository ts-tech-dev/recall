"""Cache of extracted Markdown for formats that are slow to read (PDF, Word, PowerPoint, Excel).

The indexer extracts every file anyway; keeping the result means opening a 700-page manual in Browse
doesn't read it all again. An entry is valid while the file's size and modification time are unchanged
and the extractors haven't changed (EXTRACTOR_VERSION).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .base import Context, ExtractedDoc

EXTRACTOR_VERSION = 1  # bump when extraction output changes, so cached results are rebuilt
CACHED_EXTS = {".pdf", ".docx", ".pptx", ".xlsx"}  # Markdown, text, CSV and HTML are quick to read


class ExtractCache:
    def __init__(self, folder: Path):
        self.folder = folder

    def _file(self, rel: str) -> Path:
        return self.folder / (hashlib.sha1(rel.encode()).hexdigest()[:24] + ".json.gz")

    @staticmethod
    def _stamp(p: Path) -> str:
        st = p.stat()
        return f"{st.st_mtime_ns}:{st.st_size}:{EXTRACTOR_VERSION}"

    def get(self, p: Path, rel: str) -> ExtractedDoc | None:
        try:
            data = json.loads(gzip.decompress(self._file(rel).read_bytes()))
            if data.get("stamp") == self._stamp(p):
                return ExtractedDoc(**data["doc"])
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None

    def put(self, p: Path, rel: str, doc: ExtractedDoc) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        body = gzip.compress(json.dumps({"stamp": self._stamp(p), "path": rel, "doc": asdict(doc)}).encode(), 5)
        fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, self._file(rel))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, rel: str) -> None:
        self._file(rel).unlink(missing_ok=True)

    def extract(self, p: Path, rel: str, ctx: Context, extract) -> ExtractedDoc:
        """`extract(p, ctx)`, reusing the cached result for slow formats when the file is unchanged."""
        if p.suffix.lower() not in CACHED_EXTS:
            return extract(p, ctx)
        doc = self.get(p, rel)
        if doc is None:
            doc = extract(p, ctx)
            self.put(p, rel, doc)
        return doc
