# Recall — a local knowledge base for your notes

Point Recall at a folder of notes (Markdown with images, PDF, Word, PowerPoint, Excel/CSV, HTML, text),
then ask questions and get a **summary** or a **detailed report** with citations and relevant images.
It also lets you browse and preview every note.

Everything is parsed, indexed and searched **on your machine**. An AI provider is only called to write
the final answer, using just the passages retrieved for that question. With no AI configured, you get
the most relevant passages instead.

## Quick start

```bash
cd /srv/projects/recall
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # optional: or paste the key in Settings
.venv/bin/python -m recall --notes ~/notes # then open http://localhost:9999
```

If OpenCV fails to import with a `libGL.so.1` error (headless servers), run
`.venv/bin/pip uninstall -y opencv-python && .venv/bin/pip install opencv-python-headless`.

Options: `--port 9999` (or `RECALL_PORT`), `--host 127.0.0.1`. You can also set or change the notes folder later in **Settings**.
App data (settings, index, extracted images) lives in `~/.recall` (override with `RECALL_DATA_DIR`).
The settings file holds your API key and is written with `0600` permissions.

## Docker

```bash
cp .env.example .env   # set NOTES_DIR, and RECALL_BIND / RECALL_ALLOWED_HOSTS to reach it from other machines
docker compose up -d --build   # then open http://localhost:9999 (RECALL_PORT in .env)
```

Notes are mounted at `/notes` and edited as uid 1000. Settings, the index and the embedding model are kept
in the `recall-data` volume. Recall has no login, so publish it only on an address you trust (localhost or a
Tailscale IP), not `0.0.0.0` on a shared network.

## Windows app

On a Windows 10/11 machine with Python 3.11+ installed, run from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

This creates `dist\Recall\Recall.exe` (copy the whole `dist\Recall` folder, or `dist\Recall-windows.zip`).
It starts Recall and opens it in your default browser (Edge, Chrome…) at http://localhost:9999. Bookmark that
address. A tray icon (bottom-right, near the clock) has *Open Recall* and *Quit*. Starting `Recall.exe` again while it
is running just opens the browser. If port 9999 is taken, set the `RECALL_PORT` environment variable. Data is stored
in `%LOCALAPPDATA%\Recall`, and logs go to `recall.log` there. Set the notes folder in **Settings**, for example `C:\Users\you\Notes`.
To run the same launcher from source on any OS, use `python -m recall.desktop`.

## Features

- **Ask**: choose *Summary* or *Detailed report*. Filter by file type (Markdown, PDF, Word…), by folder,
  or limit the question to one note with *Ask about this note*. The answer streams in, `[n]` citations
  link to the source passages, and images from your notes are embedded where they help. Answers can be
  copied or downloaded as `.md`. Recent questions are saved in the browser.
- **Browse**: file tree with name filter, keyword search with highlighted snippets, and previews:
  rendered Markdown (relative images, Obsidian `![[embeds]]` and `[[wikilinks]]`), the browser's own PDF
  viewer (or a text view), converted Word/PowerPoint/Excel/CSV/HTML, and an outline for longer notes.
- **Markdown**: GitHub-flavored Markdown (tables, task lists, ~~strikethrough~~, autolinks, raw HTML such as
  `<details>` and `<kbd>`), plus footnotes (`[^1]`), math (`$…$`, `$$…$$`, ```` ```math ````), Mermaid diagrams
  (```` ```mermaid ````), Obsidian callouts and GitHub alerts (`> [!note] Title`, foldable with `[!tip]-`),
  `==highlights==`, `%%comments%%`, `#tags` (click to search), `:emoji:` shortcodes, definition lists
  (`Term` then `: definition`), image sizes (`![alt|300](pic.png)`, `![[pic.png|300]]`) and heading ids
  (`## Title {#id}`).
- **Checkboxes**: tick a task (`- [ ] item`, or `[ ] item` on its own line) in a note's preview and it is saved to
  the file straight away (with version history, and only if the file hasn't changed on disk since it was shown).
  In the editor's preview, ticking a box changes the text, which you then save.
- **AI providers**:
  - Anthropic: default model `claude-opus-5-5`, adjustable effort, and server-side refusal fallback.
  - OpenAI-compatible: OpenAI, or local models through Ollama or LM Studio (set a Base URL such as `http://localhost:11434/v1`).
  - None: shows the retrieved passages, no AI call.

  With *Send note images to the AI* on, the top images are attached so the model can judge which ones are relevant.
- **Smart search (by meaning)**: a local embedding model (`BAAI/bge-base-en-v1.5`, about 210 MB, downloaded
  once, CPU only) finds passages that mean the same thing even without shared words. For example, "power outage
  protection" finds a note about a UPS battery. Results are merged with keyword ranking, then a local re-ranking
  model (`ms-marco-MiniLM-L-6-v2`, about 80 MB) reads the question with each top passage and puts the best
  answers first (about half a second per search; it can be turned off in Settings). The sidebar search has
  *Smart*, *Exact* and *Meaning* modes. *Exact* skips re-ranking. Ask uses the same search.
- **PDF structure**: PDFs are split into their own sections (from the bookmarks, or from headings set in a larger
  font), so section titles help search and results say "4.2 Installing the agent" instead of "Page 37". Each
  result still opens the PDF at its page. Running headers and footers are dropped, and ruled tables become tables.
- **Text in images**: OCR (local RapidOCR) reads screenshots, diagrams and scanned PDF pages. That text becomes
  searchable and is passed to the AI with the passage the image belongs to. Images that sit loose in the folder
  become searchable on their own. Optional **AI captions** describe each image once and are cached by image content.
  They're off by default because they cost API calls. You can choose a cheaper caption model and a per-run limit.
- **Live folder watching**: adding, editing, renaming or deleting files outside the app triggers an incremental
  re-index within about 2 seconds. The open note and the file tree refresh on their own.
- **Graph**: every note is a node. Solid lines are links (`[[wikilinks]]`, relative Markdown links) and dashed
  lines are similar-topic connections from the embeddings. You can highlight, focus on the current note,
  hide unconnected notes and click to open. Each note also shows *Linked from*, *Links to* and *Similar notes*.
- **Editing**: edit Markdown and text notes with a live preview. Paste, drop or pick images and they are saved next
  to the note and linked automatically. Ctrl+S saves. If the file changed on disk since you opened it, you're
  asked before anything is overwritten. The last 30 versions of each note are kept (*History…*). *+ Note*
  creates a new note: pick the folder from a list, or make a new one. The **+** next to a folder in the file
  list creates the note in that folder.
- **Moving files**: drag a file onto a folder in the file list, or onto empty space for the top level, or use
  *Move…* on the open note. Relative links and images inside a moved note are updated, and so are relative
  links to it from other notes. `[[Wiki links]]` find notes by name, so they keep working.
- **Incremental indexing**: only new or changed files are re-read, OCR'd and embedded. Unreadable files are listed
  in the status bar.

## How it works

```
file ─► extractors/ ─► Markdown (+ images saved to cache) ─► chunker.py ─► heading-scoped chunks
                                                                            │ (each remembers its images)
question ─► index/ (SQLite FTS5, BM25, filters) ─► top chunks + image catalogue ─► answer.py ─► llm.py
```

Every format is normalized to Markdown, so one chunker and one previewer cover every type.
Each chunk carries the images that appear in it. The AI gets an *image catalogue* of those URLs and
may embed only those. The browser also removes any answer image that isn't served by Recall.

## Project layout

Each piece lives in its own file, so it can be changed without touching the rest.

| Path | What it does |
| --- | --- |
| `recall/__main__.py` | Command line: `python -m recall` |
| `recall/desktop.py` | Windows app launcher: server + browser + tray icon |
| `recall/app.py` | Builds the FastAPI app: request guard, routers, static files |
| `recall/state.py` | Settings, the open index and the folder watcher, shared by the routes |
| `recall/api/` | HTTP API, one router per area: `status`, `files`, `search`, `edit`, `ask` |
| `recall/extractors/` | One module per file format (`markdown`, `pdf`, `docx`, `pptx`, `sheets`, `html`); register new ones in `__init__.py` |
| `recall/index/` | The index: `core` (database, files), `build` (indexing), `search`, `graph`, `query` (filters), `schema` |
| `recall/chunker.py` | Splits Markdown into heading-scoped chunks |
| `recall/editing.py` | Saving, versions, new notes, moving files, uploads |
| `recall/answer.py`, `recall/llm.py` | Retrieval for questions, and the AI providers |
| `recall/embeddings.py`, `recall/rerank.py` | Local models for search by meaning and re-ranking |
| `recall/images.py`, `recall/watcher.py` | OCR, folder watching |
| `recall/static/index.html` | The page layout |
| `recall/static/js/` | Browser code as ES modules, one per area (`main.js` lists them); no build step |
| `recall/static/js/md/` | Markdown syntax extensions (`extensions.js`) and rendering touches (`enhance.js`) |
| `recall/static/css/` | Styles, one file per area |
| `recall/static/vendor/` | Third-party browser libraries (see the README there) |

## Security notes

- The server binds to `127.0.0.1`. Requests with a foreign `Host` header are refused (DNS-rebinding
  protection). Write requests need an `X-Recall` header, so other websites can't drive the API.
- Files are served only from inside the notes folder (path traversal is blocked), with a sandboxing CSP.
- Note content and AI output are sanitized with DOMPurify before rendering.
- The browser never receives the API key. It only sees a masked hint.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The fixtures generate a sample notes folder covering every file type, with images. Coverage:

| File | What it tests |
|---|---|
| `test_extractors.py` | Each file type, Obsidian syntax, traversal safety, PDF sections, tables and running headers |
| `test_chunker.py` | Sections, code fences, images per chunk, splitting long text and tables |
| `test_index.py` | Incremental and full builds, search for each type, every filter and combinations, FTS-injection safety |
| `test_answer.py` | Retrieval, prompt, image catalogue, vision blocks, local answers, fake providers, and the real Anthropic SDK against a mocked HTTP transport |
| `test_config.py` | Key masking and file permissions, validation |
| `test_api.py` | Every endpoint, previews for each type, SSE streaming, security guards |
| `test_semantic.py` | Real embedding model: meaning-only matches, hybrid fusion, filters, no re-embedding, model change, failure fallback |
| `test_rerank.py` | Re-ranking order, *Exact* mode untouched, fallback when the model fails, Settings switch |
| `test_images.py` | OCR (images, scanned PDFs, cache), loose versus embedded images, captions (folding, limit, failure, refusal) |
| `test_watcher.py` | Create, modify and delete picked up live; bursts debounced; hidden files ignored |
| `test_graph.py` | Links and backlinks, updates after edits, dangling links, graph nodes and edges, similarity edges |
| `test_editing.py` | Save, conflict and force, versions, unsafe paths, new notes, moves and link updates, folders, image uploads, timestamp precision, API flow |

See `PLAN.md` for the design and roadmap.
