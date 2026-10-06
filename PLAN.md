# Recall — local notes knowledge base

## Goals
1. Point the app at a directory of notes (Markdown with local images, PDF, Word, PowerPoint, Excel/CSV, HTML, text).
2. Ask questions → get a **summary** or a **detailed report**, citing sources and embedding relevant images from the notes.
3. All parsing, indexing and retrieval happens **locally**. An AI provider (API key preconfigured in settings or env) is only used to write the final answer. Without a key, a local extractive answer is returned.
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
   ├── answer.py      retrieval → prompt (sources + image catalogue) → provider stream
   └── llm.py         providers: Anthropic (default, claude-opus-5-5), OpenAI-compatible
                      (OpenAI / Ollama / LM Studio), none (local extractive answer)
```

Key design decision: **every extractor outputs Markdown**. PDFs become `## Page N` sections,
Word headings become `#` headings, tables become Markdown tables, embedded images are written to
the cache and referenced as `![alt](/api/cache-image/…)`. One chunker, one previewer, one image path.

Images in answers: each retrieved chunk carries the images that appear in it. The prompt lists
them as an image catalogue (id, caption, URL); the model is told to embed only those URLs. With
"send images to AI" enabled, up to N of them are also attached as vision inputs so the model can
judge relevance.

## API
| Method | Path | Purpose |
|---|---|---|
| GET | /api/status | notes dir, index stats, indexing progress, provider configured |
| GET/POST | /api/settings | read (key masked) / update settings |
| POST | /api/index | (re)index in background (`full=true` to rebuild) |
| GET | /api/tree | directory tree of supported files |
| GET | /api/doc?path= | preview payload (markdown / pdf / image / text) |
| GET | /api/file?path= | raw file from notes dir (path-traversal safe) |
| GET | /api/cache-image/{name} | image extracted from PDF/DOCX/PPTX |
| GET | /api/search?q=&types=&folder=&limit= | keyword search with filters |
| POST | /api/ask | SSE stream: `sources`, `delta`*, `done` / `error` |

## Phases
1. ✅ Core: config, extractors (md, pdf, docx, pptx, xlsx, csv, html, txt), chunker, FTS index, filters.
2. ✅ Answering: Anthropic streaming + OpenAI-compatible + local fallback, image catalogue, citations.
3. ✅ Web UI: sidebar tree + filter, preview pane, Ask pane (summary / report), settings dialog.
4. ✅ Tests: one test module per component (`tests/`), fixtures generated programmatically.
5. ✅ Search by meaning: local embeddings (fastembed / bge-small) + reciprocal-rank fusion with BM25.
6. ✅ OCR (RapidOCR) for images and scanned PDF pages, plus optional AI captions, cached by image hash.
7. ✅ Folder watcher (watchdog, debounced); the UI polls the index version and refreshes.
8. ✅ Links table, backlinks, similar notes, and a d3 force-directed graph.
9. ✅ In-browser editing: conflict-checked atomic saves, version history, new notes, image paste/upload.
10. Next: follow-up chat, saving answers as notes, tag/date filters, login for remote access.
