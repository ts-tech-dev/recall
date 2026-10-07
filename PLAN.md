# Recall — local notes knowledge base

## Goals
1. Point the app at a directory of notes (Markdown with local images, PDF, Word, PowerPoint, Excel/CSV, HTML, text).
2. Ask questions → get the most relevant passages (key or full), citing sources and showing their images.
3. All parsing, indexing and retrieval happens **locally**. No calls to AI services or language models.
4. Browse and preview notes (tree navigation, rendered Markdown with images, native PDF viewer, converted Word/PowerPoint/Excel).
5. Web UI served from a local server (127.0.0.1).

## Architecture

```
browser (static/index.html + app.js)
   │  REST + Server-Sent Events
FastAPI (recall/app.py)
   ├── config.py      settings file (~/.recall/config.json, 0600) + env fallback
   ├── extractors.py  every file type → normalized Markdown (+ extracted images)
   ├── chunker.py     Markdown → sections (by heading) → chunks, with image refs per chunk
   ├── index.py       SQLite + FTS5 (BM25) index, incremental by mtime/size, filters
   └── answer.py      retrieval → the best passages (with their images and sources)
```

Key design decision: **every extractor outputs Markdown**. PDFs become `## Page N` sections,
Word headings become `#` headings, tables become Markdown tables, embedded images are written to
the cache and referenced as `![alt](/api/cache-image/…)`. One chunker, one previewer, one image path.

Images in Ask results: each retrieved chunk carries the images that appear in it, and they are
shown with the passage.

## API
| Method | Path | Purpose |
|---|---|---|
| GET | /api/status | notes dir, index stats, indexing progress |
| GET/POST | /api/settings | read / update settings |
| POST | /api/index | (re)index in background (`full=true` to rebuild) |
| GET | /api/tree | directory tree of supported files |
| GET | /api/doc?path= | preview payload (markdown / pdf / image / text) |
| GET | /api/file?path= | raw file from notes dir (path-traversal safe) |
| GET | /api/cache-image/{name} | image extracted from PDF/DOCX/PPTX |
| GET | /api/search?q=&types=&folder=&limit= | keyword search with filters |
| POST | /api/ask | SSE stream: `sources`, `delta`*, `done` / `error` |

## Phases
1. ✅ Core: config, extractors (md, pdf, docx, pptx, xlsx, csv, html, txt), chunker, FTS index, filters.
2. ✅ Ask: best passages with citations and images (AI answer providers removed).
3. ✅ Web UI: sidebar tree + filter, preview pane, Ask pane (key / full passages), settings dialog.
4. ✅ Tests: one test module per component (`tests/`), fixtures generated programmatically.
5. ✅ Search by meaning: local embeddings (fastembed / bge-small) + reciprocal-rank fusion with BM25.
6. ✅ OCR (RapidOCR) for images and scanned PDF pages, cached by image hash.
7. ✅ Folder watcher (watchdog, debounced); the UI polls the index version and refreshes.
8. ✅ Links table, backlinks, similar notes, and a d3 force-directed graph.
9. ✅ In-browser editing: conflict-checked atomic saves, version history, new notes, image paste/upload.
10. Next: saving Ask results as notes, tag/date filters, login for remote access.
