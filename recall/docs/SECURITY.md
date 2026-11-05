# Recall: security and data handling

This document is for IT and security teams reviewing Recall for use in an organization. It covers what Recall
runs, what it listens on, every place it stores data, how it fits into Windows, and the limits you should know
about. It was written against the source code of Recall {{version}}. Each statement here was checked against the
code, and the security tests listed at the end cover the key ones.

{{installation}}

## 1. Summary

| Question | Answer |
| --- | --- |
| What is it? | A local web app that indexes a folder of notes (Markdown, PDF, Word, PowerPoint, Excel/CSV, HTML, text, images) and lets one person search, read and edit them in their web browser. |
| Where does it run? | Entirely on the user's computer: a Python server process and the user's existing browser. No servers are run by the vendor. |
| Does it send data anywhere? | The Recall server makes **no outbound network connections**. It doesn't call AI services, has no telemetry or analytics, no update check and no license check. The search models ship with it. One exception is opt-in: `RECALL_ALLOW_DOWNLOADS=1` (see §4.2). The app page's Content-Security-Policy also stops notes from making the browser contact other servers, unless the user allows images from the internet (see §4.3). |
| What does it listen on? | One TCP port, by default `127.0.0.1:9999` (loopback only). |
| Authentication? | **No login or user accounts.** Every request needs the installation's random **access key**, which only the user's own account can read (see §5). The browser gets it once, when the app opens it, and keeps it in a cookie. Whoever has the key has full access. |
| What does it store? | A settings file, a search index, caches and short-lived version history, all in one per-user data folder (see §7). Notes stay where they are. |
| Encryption at rest? | Recall doesn't encrypt anything itself. It relies on disk encryption (BitLocker, FileVault, LUKS) and on the OS's folder permissions. |
| Admin rights or install needed? | No. The Windows app is a portable folder. It writes nothing to the registry, installs no service, driver, scheduled task or startup entry, and doesn't update itself. |
| Credentials or secrets? | Recall asks for none and handles none. Its one secret is its own access key (§5). |

## 2. Architecture and trust boundaries

```
 ┌──────────────────────────── user's computer ──────────────────────────────┐
 │                                                                            │
 │  Web browser (Edge, Chrome…)      HTTP on loopback       Recall server     │
 │  page from http://localhost:9999 ◄──────────────────►  (Python: FastAPI +  │
 │  • renders notes (DOMPurify)        127.0.0.1:9999      uvicorn)           │
 │  • localStorage: questions,                             • reads/writes the │
 │    recent notes, theme                                     notes folder    │
 │                                                         • local models     │
 │                                                           (ONNX, CPU)      │
 │                                                         • OCR (ONNX, CPU)  │
 │                                                              │      │      │
 │                     notes folder (chosen by the user) ◄──────┘      │      │
 │                     data folder (index, caches, versions) ◄─────────┘      │
 └────────────────────────────────────────────────────────────────────────────┘
```

Trust boundaries:

1. **The TCP port and the access key.** Everything that can connect to the port **and** has the key has the full
   rights of the user who runs Recall over the notes folder, and can point Recall at a different folder (see §5).
   There are no per-user permissions. Without the key, the port only serves the app's own scripts and a health
   check.
2. **Other websites in the same browser.** They're untrusted. The protections in §6 stop them from reading
   Recall's responses, sending write requests, framing the app or using DNS rebinding to get around the browser's
   same-origin rules.
3. **The content of notes.** Notes and documents are treated as untrusted input. They're parsed by the libraries
   in §10 and sanitized before display. That matters when the notes folder is shared or synced from other people.

## 3. Ways to run it

| | Windows app (`Recall.exe`) | Command line (`python -m recall`) | Docker |
| --- | --- | --- | --- |
| Listens on | `127.0.0.1`, port from Settings (default 9999). If that port is taken, a free random port on `127.0.0.1`. | `--host` (default `127.0.0.1`) and `--port` (default 9999 or `RECALL_PORT`) | `0.0.0.0:9999` inside the container, published on the host at `RECALL_BIND:RECALL_PORT` (default `127.0.0.1:9999`) |
| Data folder | `%LOCALAPPDATA%\Recall` | `~/.recall` (on Windows `%USERPROFILE%\.recall`) | `/data` (the `recall-data` Docker volume) |
| Runs as | The signed-in user | The user who starts it | uid 1000 (`recall`), not root |
| Logs | `%LOCALAPPDATA%\Recall\recall.log` | The terminal | `docker logs` |
| How the browser gets the key | The app opens the browser with it (at start, from the tray's *Open Recall*, or when `Recall.exe` is started again) | Recall prints the address with the key when it starts | The same, in `docker logs recall`, or set your own with `RECALL_KEY` |
| Port change and shut down from the web page | Yes (`/api/app/*`) | No (404) | No (404) |

`RECALL_DATA_DIR` moves the data folder in all three cases.

## 4. Network behavior

### 4.1 Inbound

- One HTTP listener (uvicorn), plain HTTP without TLS. Over loopback the traffic never leaves the computer.
- HTTP access logs are off (uvicorn runs at `warning` level), so request URLs, search terms and questions aren't
  logged.
- With the default `127.0.0.1` binding, other computers can't connect. Binding to another address (`--host`,
  `RECALL_BIND`) lets every host that can reach it connect. They still need the key, but it would travel over the
  network in plain HTTP. Recall's own docs say not to do that on a shared network.

### 4.2 Outbound (server)

- **None by default.** At start-up Recall sets `HF_HUB_OFFLINE=1`, and both local models are loaded with
  `local_files_only=True`, so the Hugging Face libraries only read the models folder (`recall/net.py`,
  `recall/embeddings.py`, `recall/rerank.py`).
- The FastAPI pages `/docs` and `/redoc` are turned off, because they would load scripts from a public CDN.
- Opt-in: with `RECALL_ALLOW_DOWNLOADS=1` set, a model that isn't in the models folder is downloaded from
  `huggingface.co`. That happens only when Settings names such a model. The download uses the OS certificate store
  (via `truststore`) and honors `HTTPS_PROXY`. If it fails, one diagnostic request goes to
  `https://huggingface.co/api/models/BAAI/bge-base-en-v1.5`. Leave this variable unset in managed environments.
- Build time only, not at run time: `python -m recall.fetch_models` downloads the two models from Hugging Face
  while the Windows app or Docker image is built.

### 4.3 Outbound (browser)

The browser loads Recall's own page, scripts, styles and fonts from Recall itself. Nothing comes from a CDN (see
`recall/static/vendor/README.md`). The page's Content-Security-Policy (§6) only lets the page load from, and
connect to, Recall. So a note can't make the browser reach the internet on its own:

- **Images, video and audio from the internet in notes** (`![](https://…)`, `<img src="https://…">`) are **not
  loaded**. They show as a link with the server's name instead. Loading them would let that server learn the
  computer's IP address and that the note was opened (a "web beacon"). Their addresses are removed while the note
  is sanitized, so the browser doesn't even try, and the policy blocks anything that gets through. The setting
  *Show images from the internet in notes* (Settings → Security & data, off by default) allows them again. Recall
  then sends no `Referer` header (`Referrer-Policy: no-referrer`), so the note's name isn't disclosed. Ask results
  always drop images that aren't served by Recall.
- **Links** open only when the user clicks them. External links open in a new tab with
  `rel="noopener noreferrer"`.
- **Open original** for a PDF uses the browser's built-in PDF viewer.

## 5. Authentication and authorization

Recall has **no login, user accounts or roles**. Instead, each installation has a random **access key**, and the
server answers only requests that carry it (`recall/access.py`). Nobody types the key:

1. The first time Recall runs, it makes a 256-bit random key (`secrets.token_urlsafe(32)`) and saves it as
   `<data>/key` (§7.1). Only the user's own account can read that file. `RECALL_KEY` sets the key instead (for
   Docker or scripts).
2. The Windows app opens the browser at `http://localhost:<port>/?key=…`. The command line and Docker print that
   address.
3. Recall checks the key, keeps it in a cookie and redirects to `/` without it, so the key doesn't stay in the
   address bar.
4. From then on the browser sends the cookie with every request, and bookmarks work. Scripts can send the key in an
   `X-Recall-Key` header instead.

The cookie is `HttpOnly` (page scripts can't read it) and `SameSite=Strict` (other websites can't make the browser
send it). Its name is derived from the key, so two Recalls on one computer don't overwrite each other's. Keys are
compared in constant time.

Without the key, Recall serves only its own scripts and styles (`/static/…`, nothing private) and `/healthz`
(answers that Recall is running, nothing more). Everything else gets 401: the API returns an error, and the page
explains how to open Recall from the app.

**Whoever has the key** can:

- read every file in the notes folder through the API, except hidden ones (`GET /api/file` serves any file
  type, not only notes), along with the index;
- create, edit, move and rename notes, and upload images;
- **change the notes folder to any folder the Recall process can read** (`POST /api/settings`). Recall would then
  index that folder and serve the files in it. What a key holder can reach is therefore limited only by the OS
  permissions of the account that runs Recall;
- in the Windows app, move it to another port or shut it down.

What protects a default install:

- **The access key.** Other users signed in to the same computer at the same time (Remote Desktop Session Host,
  Citrix, shared lab machines) can reach `127.0.0.1`, but they can't read the key file or the user's browser
  cookies, so Recall gives them nothing.
- **Loopback binding.** Other computers can't connect.
- **Browser protections** (§6). Websites the user visits can't use Recall's API.

What doesn't:

- **Programs running as the same user.** They can read the key file and the browser profile, as they can read the
  notes themselves. See §11.

To change the key, quit Recall, delete `<data>/key` and start it again. Every browser then needs to be opened from
the app once more.

## 6. Request and content protections

| Protection | Where | What it does |
| --- | --- | --- |
| Access key | `recall/access.py`, `recall/app.py` (`guard`) | Every request except `/static/…` and `/healthz` needs the installation's key, in its cookie or the `X-Recall-Key` header (§5). Otherwise 401. |
| Host header check | `recall/app.py` (`guard`) | Requests whose `Host` isn't `localhost`, `127.0.0.1`, `[::1]` or a name in `RECALL_ALLOWED_HOSTS` get 403. This blocks DNS-rebinding attacks, where a website's domain is made to resolve to 127.0.0.1. |
| Write header | `recall/app.py` | Every `/api/` request other than GET/HEAD needs the header `X-Recall: 1`. A custom header makes the browser send a CORS preflight, which Recall doesn't answer, so other websites can't send write requests (CSRF). |
| No CORS | the whole app | No `Access-Control-Allow-*` headers are sent, so other websites can't read any response. |
| Content-Security-Policy of the app page | `recall/app.py` (`page_csp`) | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self'; font-src 'self' data:; connect-src 'self' http://localhost:* http://127.0.0.1:*; frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'`. Scripts load only from Recall's own files: no inline scripts, no `eval`. So HTML in a note can't run code, even if it got past the sanitizer. Images and media come only from Recall (with *Show images from the internet* on, `img-src` and `media-src` also allow `http:` and `https:`). Forms can't post elsewhere. `connect-src` allows other local ports, so the page can follow the Windows app to a new port. Inline styles are allowed because KaTeX, Mermaid and image sizes use them. |
| Anti-framing | `recall/app.py` | `X-Frame-Options: SAMEORIGIN` and `Content-Security-Policy: frame-ancestors 'self'` (clickjacking). |
| Resource policy | `recall/app.py` | `Cross-Origin-Resource-Policy: same-site`: other websites can't embed Recall's files as images or scripts to find out which files exist. |
| Referrer policy | `recall/app.py` | `Referrer-Policy: no-referrer`. |
| Path confinement (read) | `recall/api/files.py` (`safe_file`), `recall/index/core.py` (`resolve_image`) | Paths are resolved, symbolic links included, and must stay inside the notes folder (403 otherwise). Hidden files and folders (names starting with `.`, such as `.git` or `.obsidian`) aren't served. |
| Path confinement (write) | `recall/editing.py` (`inside`) | The same check for every write, which also refuses any path with a hidden part. Saves go through a temporary file and an atomic rename. |
| Edit allowlist | `recall/editing.py` | Only `.md`, `.markdown` and `.txt` files can be created or edited. Notes are limited to 5 MB. |
| Upload allowlist | `recall/editing.py` (`save_upload`) | Only `.png .jpg .jpeg .gif .webp .svg`, up to 25 MB, saved in an `images` folder next to the note under a generated name. |
| Cached image names | `recall/api/files.py` | Only names matching `[0-9a-f]{20}.(png|jpg|jpeg|gif|webp)` are served from the image cache. |
| Raw files are sandboxed | `GET /api/file` | Files are sent with `Content-Security-Policy: sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'`. An HTML or SVG file opened with *Open original* can't run scripts. PDFs are the exception, because the browser's PDF viewer has to run (§11). |
| HTML sanitizing | `recall/static/js/markdown.js` | Rendered notes go through DOMPurify 3.4.16, which removes scripts, event-handler attributes and `javascript:` URLs. Recall also removes `<style>` elements, which would restyle the whole app, and the addresses of remote images and media (§4.3). Mermaid diagrams use `securityLevel: "strict"`, and KaTeX runs with its default `trust: false`. |
| Search snippets | `recall/index/query.py`, `recall/static/js/util.js` | Match markers are control characters. The browser escapes the text first and only then turns the markers into `<mark>`. |
| Safe search queries | `recall/index/query.py` (`build_match_query`), `recall/index/search.py` | Every term is quoted before it reaches SQLite FTS5, and all SQL uses bound parameters. |
| Conflict checks | `recall/editing.py` (`save_source`) | A save is refused (409) when the file changed on disk since it was opened, so a change made elsewhere isn't silently overwritten. |
| Settings validation | `recall/config.py` (`apply_update`) | The notes folder must be an existing directory. Numbers are clamped (`top_k` 1–50, port 1024–65535). |

## 7. Where data is stored

### 7.1 The data folder

`<data>` is `%LOCALAPPDATA%\Recall` for the Windows app, `~/.recall` from the command line and `/data` in
Docker. `<key>` is the first 12 hex characters of the SHA-1 of the notes folder's full path.

| Path | Contents | Sensitivity | Kept for |
| --- | --- | --- | --- |
| `<data>/config.json` | Settings: notes-folder path, on/off switches, model names, `top_k`, port. No secrets. | Low (reveals the notes path) | Until deleted |
| `<data>/key` | The access key (§5) | **High**: it gives full access to the notes through Recall | Until deleted (a new one is made at the next start) |
| `<data>/indexes/<key>.db` (+ `-wal`, `-shm`) | SQLite database: file paths, titles, tags, modification times, **full text of every note in passages**, a full-text search index, **embedding vectors** of each passage, **OCR text** from images, links between notes, and error messages for unreadable files | **As sensitive as the notes** | Until the notes folder changes, or deleted |
| `<data>/indexes/<key>_extracted/*.json.gz` | Text extracted from PDF, Word, PowerPoint and Excel files (gzip JSON), for quick previews | **As sensitive as the notes** | While the source file exists and is unchanged |
| `<data>/indexes/<key>_images/` | Images extracted from PDF, Word and PowerPoint files | **As sensitive as the notes** | Until the notes folder changes. Images from an older version of a document aren't removed when it changes. |
| `<data>/indexes/<key>_versions/<hash>/` | The previous content of each note saved in Recall (`<time>.bak`), and `path.txt` with the note's path | **As sensitive as the notes** | **4 hours**, at most 30 per note. Older ones are hidden at once, and deleted the next time that note is saved or its *History* is opened, and for every note when Recall starts. |
| `<data>/recall.log` | Windows app only: the start-up line, warnings and error tracebacks. These can contain file names and paths, but not request URLs, search terms or questions. | Low to medium | Cut back to its last 200 KB at start-up once it passes 1 MB |
| `<data>/models/` | Only when `RECALL_MODEL_DIR` isn't set and no bundled models exist (a source checkout without `fetch_models`) | None | |

When the user switches to another notes folder, the old folder's database, caches and version history are
deleted. A file that Windows still has open is removed on a later start (`prune_other_indexes`).

**Permissions.** Recall writes `config.json` and `key` with mode `0600`. On Windows a mode only sets the read-only
attribute, so protection comes from `%LOCALAPPDATA%`, which by default only the user, SYSTEM and Administrators
can open. On Linux and macOS the other files get the process's umask, typically `0644` for files and `0755` for
folders, so other local accounts may be able to read the index. Make `~/.recall` `0700` on shared Unix machines.

### 7.2 The notes folder

Recall reads every supported file in the notes folder and its sub-folders. It skips hidden files and folders,
Office lock files (`~$…`) and `node_modules`, `__pycache__`, `venv` and `.venv`. It changes the folder only when
the user acts:

| Action | Writes |
| --- | --- |
| Save, or tick a checkbox in the preview | The note (atomically, through a temporary `.recall-…` file in the same folder) |
| *+ Note*, *Save as note…* (Ask) | A new `.md`. Saving an Ask result also copies images extracted from documents into an `images` folder next to it. |
| Paste, drop or pick an image | `images/<note>_<n>.<ext>` next to the note |
| *Move…*, drag in the file list | Moves the file and the images it shows, and rewrites relative links in the note and in every other note that links to it. Can remove an `images` folder the move left empty. |
| *Rename…* | Renames the file and its pasted images, and rewrites links (relative and `[[wiki]]`) in every note that points at them |

Recall never deletes a note. The only files it deletes in the notes folder are images whose identical copy
already sits at the destination of a move, and folders that a move left empty.

### 7.3 In the browser

Saved in the browser's `localStorage` for the origin `http://localhost:<port>`, in the user's browser profile:

| Key | Contents |
| --- | --- |
| `history` | The last 25 questions **with their answers (passages quoted from notes) and source list** |
| `recent` | Paths of the last notes opened (for Ctrl+K) |
| `collapsed` | Folders collapsed in the file list |
| `noteFolder` | The folder last used for a new note |
| `theme`, `outline` | Display choices |

These keys stay until the user clears the site's data in the browser. Recall sets one cookie,
`recall_<10 hex characters>`, holding the access key (`HttpOnly`, `SameSite=Strict`, kept for 10 years, §5). It
doesn't use `sessionStorage` or IndexedDB.

### 7.4 Not stored anywhere

Search terms and questions aren't kept on the server: the Ask and search endpoints don't save them, and the HTTP
access log is off. Only the browser history in §7.3 keeps them.

## 8. Windows integration

| Area | Behavior |
| --- | --- |
| Install | A portable folder (`dist\Recall`, or the `Recall-windows.zip` release). Built with PyInstaller in *onedir* mode: `Recall.exe` plus `_internal\` (Python runtime, libraries, `recall\static`, `models`). Nothing is unpacked to `%TEMP%` at start. No installer and no admin rights. |
| Code signing | **`Recall.exe` isn't code-signed.** SmartScreen may warn on first run. Organizations that allow it should sign it or allowlist its hash. |
| Registry | Recall writes nothing to the registry. It only reads from it: Python's standard library looks up file-type (MIME) associations, and the Windows shell finds the default browser when Recall opens the page. |
| Services, tasks, autostart | None. Recall runs only while the user has started it. |
| Processes | One `Recall.exe` with several threads (web server, indexer, folder watcher, tray icon). A PDF of 60 pages or more is read by up to min(CPUs − 1, 8) child `Recall.exe` processes (Python *multiprocessing*), which exit when the PDF is done. `RECALL_PDF_WORKERS=1` turns that off. |
| Network | One loopback listener (§3). Windows Defender Firewall usually doesn't prompt for a program that only listens on `127.0.0.1`. |
| Browser | At start, and from the tray's *Open Recall*, the default browser is opened at `http://localhost:<port>/?key=…` (Python `webbrowser`, which uses the shell's default-program association). The key passes from Recall to the browser through the shell, within the user's session. Starting `Recall.exe` again while it runs only opens the browser. A running copy is detected through `/healthz`. |
| Tray icon | A notification-area icon (`pystray`, Win32 API) with *Open Recall* and *Quit*. The icon is drawn at run time. |
| Console | None (windowed build). Output goes to `recall.log`. |
| File watching | `watchdog` (`ReadDirectoryChangesW`) watches the notes folder, so changes made outside Recall are re-indexed within about 2 seconds. It can be switched off in Settings. |
| Certificates | Not used offline. With `RECALL_ALLOW_DOWNLOADS=1`, model downloads trust the Windows certificate store, so company TLS-inspection proxies work. |
| Hidden files | Recall's temporary files start with `.recall-`. Windows doesn't treat a leading dot as hidden, so if Recall stops mid-save one can stay visible. It's ignored by indexing and can be deleted. |
| Removal | Quit from the tray. Delete the `Recall` folder, then `%LOCALAPPDATA%\Recall`, then clear site data and cookies for `http://localhost:9999` (or the port used) in the browser. Notes are untouched. |

## 9. Environment variables

| Variable | Effect |
| --- | --- |
| `RECALL_DATA_DIR` | Data folder |
| `RECALL_NOTES_DIR` | Notes folder, used when none is set in Settings |
| `RECALL_PORT` | Port. In the Windows app it overrides the setting and locks it. |
| `RECALL_KEY` | Use this access key instead of the generated one (§5). Keep it secret, as you would a password. |
| `RECALL_ALLOWED_HOSTS` | Extra host names accepted in the `Host` header, comma-separated (for a Tailscale IP, for example) |
| `RECALL_ALLOW_DOWNLOADS` | `1` lets Recall download models it doesn't have (§4.2). Unset (the default) means fully offline. |
| `RECALL_MODEL_DIR` | Folder to load the models from, instead of the bundled ones |
| `RECALL_PDF_WORKERS` | How many processes read long PDFs (`1` = none) |
| `RECALL_NO_WINDOW` | Windows app: don't open a browser or show a tray icon (headless testing) |
| `HTTPS_PROXY` | Used only by opt-in model downloads |

## 10. Third-party components

**Parsing untrusted files.** Notes are parsed in the Recall process with PyMuPDF (PDF), python-docx, python-pptx,
openpyxl (`read_only`, `data_only`: formulas aren't evaluated and macros never run), Python's `csv` and
`html.parser`, and RapidOCR on ONNX Runtime for images. A bug in one of these libraries could be triggered by a
crafted file in the notes folder. A file that fails to parse is recorded as an error and skipped. Keep the
libraries up to date.

**Python packages** (`requirements.txt`, minimum versions, not locked): fastapi, uvicorn, python-multipart,
pymupdf (<2), python-docx, python-pptx, openpyxl, numpy, fastembed, truststore, rapidocr-onnxruntime,
opencv-python-headless, watchdog. Windows app only: pystray, pyinstaller. Builds take the newest allowed versions,
so two builds can contain different versions. Pin versions (or use a lock file with hashes) for repeatable,
reviewable builds.

**Browser libraries**, served from Recall itself (`recall/static/vendor/`): marked 15.0.12, marked-footnote 1.4.0,
marked-katex-extension 5.1.13, KaTeX 0.18.11, Mermaid 12.1.0, DOMPurify 3.4.16, highlight.js 11.12.0, d3 7.9.0, and
an emoji table from gemoji 8.1.0.

**Models** (ONNX, CPU only, bundled):

| Model | Use | Source (Hugging Face) |
| --- | --- | --- |
| BAAI/bge-base-en-v1.5 (quantized ONNX, about 210 MB) | Embeddings for search by meaning | `Qdrant/bge-base-en-v1.5-onnx-Q` |
| ms-marco-MiniLM-L-6-v2 (about 80 MB) | Re-ranking search results | `Xenova/ms-marco-MiniLM-L-6-v2` |
| RapidOCR detection and recognition models | Text in images | Shipped inside the `rapidocr-onnxruntime` package |

These models only turn text into numbers or images into text. They don't generate text, and none of them is a
chatbot or a large language model.

**Build pipeline.** GitHub Actions (`.github/workflows/build.yml`) runs the tests, builds `Recall.exe` on
`windows-latest`, smoke-tests it offline and attaches the zip to tagged releases. The actions are referenced by
major version tag, not pinned to commit hashes.

## 11. Known limitations and residual risks

| Risk | Detail | Mitigation |
| --- | --- | --- |
| No user accounts | Anyone with the key has the user's full access to the notes, and can repoint Recall at any folder the user can read (§5). | Treat the key, its file and the browser profile as sensitive. Delete `<data>/key` to change the key if it leaks. |
| Programs running as the same user | They can read the key file and the browser's cookies, and so use the API, as they can read the notes folder directly. | The usual endpoint protection. Recall can't protect against code running as the same user. |
| Cookie shared across local ports | Browsers send cookies for `localhost` to every port. A web server on another `localhost` port that the user visits could receive the key's cookie. On a shared computer, another user could run such a server. | Don't open unknown `localhost` addresses. Change the key (delete `<data>/key`) if you suspect it leaked. |
| Plain HTTP | Traffic isn't encrypted. On loopback that's harmless. Over a network, others on the path can read it, the key included. | Don't bind to a network address. If remote access is needed, use an encrypted tunnel (VPN or Tailscale) and `RECALL_ALLOWED_HOSTS`. |
| PDFs served without a sandbox | PDFs are shown by the browser's built-in viewer, which runs inside the browser's own sandbox. Recall can't add its CSP sandbox to them. | Keep browsers updated. Browser policies can turn off scripts in PDFs. |
| Remote content in notes | Blocked by default (§4.3). With *Show images from the internet* on, a note can load remote images, video and audio, and their servers learn that it was opened. `style="…"` attributes in notes are kept, so a note can still change how its own content looks. | Leave the setting off where that matters. |
| Parser vulnerabilities | Crafted documents could exploit the parsing libraries (§10). | Keep Recall updated. Index only folders you trust. |
| Index readable on Unix | Index files get the default umask (§7.1). | `chmod 700 ~/.recall` |
| Sensitive copies outside the notes folder | The index, caches, version history and browser history hold copies of note content (§7). Data-loss-prevention or retention policies that cover the notes folder don't automatically cover these. | Include `%LOCALAPPDATA%\Recall` in those policies. Use disk encryption. |
| Unsigned executable, unlocked dependencies | See §8 and §10. | Sign the executable and pin dependencies in your own build. |

## 12. Recommended configuration for organizations

1. Use the Windows app or the command line with the default `127.0.0.1` binding. The access key also keeps other
   users of a shared computer out.
2. Leave `RECALL_ALLOW_DOWNLOADS` unset and *Show images from the internet* off. Recall and the page it shows are
   then fully offline.
3. Turn on disk encryption (BitLocker), and include `%LOCALAPPDATA%\Recall` in data-handling policies.
4. Sign `Recall.exe` with your code-signing certificate, or allowlist its hash.
5. Build from a reviewed commit with pinned dependency versions if you need a repeatable build.
6. If people must reach Recall from other computers, use an encrypted tunnel and set `RECALL_ALLOWED_HOSTS`.
   Everyone given the key has full access, because there are no user accounts.

## 13. API reference

Every endpoint, with what it reads and writes. All of them need the access key (§5), except `/static/…` and
`/healthz`. "Write" endpoints also need the `X-Recall: 1` header (§6).

| Method and path | Write | What it does |
| --- | --- | --- |
| `GET /` | | The app's page. `?key=…` with the right key sets the cookie and redirects to `/`. Without the key: a page explaining how to open Recall. |
| `GET /static/…` | | The app's scripts, styles and fonts (no key needed) |
| `GET /healthz` | | `{"app": "recall", "ok": true}`, for the launcher and Docker's health check (no key needed) |
| `GET /openapi.json` | | The API description (generated by FastAPI) |
| `GET /api/status` | | Version, notes-folder path, settings, index statistics and progress, files that recently changed, files that failed to index, and the desktop port |
| `GET /api/settings` | | The settings |
| `POST /api/settings` | ✔ | Change settings, including the notes folder. Saves `config.json` and re-indexes. |
| `POST /api/index` | ✔ | Re-index (`full=true` rebuilds everything) |
| `GET /api/tree` | | Folders and supported files in the notes folder |
| `GET /api/folders` | | Every folder in the notes folder |
| `GET /api/doc?path=` | | A file prepared for preview (its text as Markdown, title and tags) |
| `GET /api/file?path=` | | A raw file from the notes folder (sandboxed, §6) |
| `GET /api/cache-image/{name}` | | An image extracted from a document |
| `GET /api/search` | | Keyword and meaning search, with filters |
| `GET /api/tags` | | Tags and how many notes have each |
| `GET /api/related?path=` | | Links to and from a note, and similar notes |
| `GET /api/graph` | | Every note and the links between them |
| `GET /api/linkcheck` | | Broken links and notes nothing links to |
| `GET /api/source?path=` | | The text of a Markdown or text note, for editing |
| `PUT /api/source` | ✔ | Save a note (keeps the previous version, checks for conflicts) |
| `POST /api/note` | ✔ | Create a note |
| `POST /api/move` | ✔ | Move a file and update links |
| `POST /api/rename` | ✔ | Rename a file and update links |
| `POST /api/render` | ✔ | Prepare unsaved Markdown for the editor's preview. Writes nothing. |
| `POST /api/upload` | ✔ | Save an image next to a note |
| `GET /api/versions?path=` | | A note's saved versions (and deletes expired ones) |
| `GET /api/version?path=&id=` | | One saved version |
| `POST /api/ask` | ✔ | The passages that best answer a question (server-sent events). Writes nothing. |
| `GET /api/security-doc` | | Downloads this document |
| `POST /api/app/port` | ✔ | Windows app: save a new port and restart there |
| `POST /api/app/shutdown` | ✔ | Windows app: quit |

## 14. How this was checked

The statements above were checked against the source code of this version, function by function, and the
behaviors below were also tested against a running server. The automated tests (`python -m pytest`) cover them:

- `tests/test_access.py`: the key is made once and saved with `0600`, nothing but `/static` and `/healthz` without
  it, a wrong key refused, the key link sets an `HttpOnly`, `SameSite=Strict` cookie and leaves the address,
  `RECALL_KEY`, the page's Content-Security-Policy (and remote images only with the setting), no inline scripts or
  event handlers in the page.
- `tests/test_api.py`: write requests without `X-Recall` refused, foreign `Host` refused, path traversal refused,
  hidden files not served, raw files sandboxed, anti-framing and resource-policy headers, image-cache name
  validation.
- `tests/test_offline.py`: offline by default (`HF_HUB_OFFLINE`), models loaded only from local files, downloads
  only when allowed.
- `tests/test_editing.py`: unsafe paths refused, conflict detection, version history and its expiry, uploads
  saved only in the `images` folder next to the note.
- `tests/test_config.py`: `config.json` written with `0600`, settings validation.
- `tests/test_extractors.py`: links in notes can't point outside the notes folder.
- `tests/test_index.py`: special characters in search queries can't inject FTS syntax.

Report security problems to the maintainer privately instead of in a public issue.
