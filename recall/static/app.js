"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const H = { "X-Recall": "1", "Content-Type": "application/json" };

const state = {
  status: null,
  tree: null,
  currentDoc: null,
  scopePath: "",          // "Ask about this note" restricts the question to one document
  types: new Set(),
  answerMd: "",
  sources: [],
  asking: null,           // AbortController of the running question
  indexVersion: null,     // last seen index version (changes when files are re-indexed)
  editing: null,          // {path, mtime_ns, saved} while the editor is open
  graph: null,            // cached graph data {key, data}
};

const TYPE_LABELS = { markdown: "Markdown", pdf: "PDF", word: "Word", powerpoint: "PowerPoint", spreadsheet: "Sheets", html: "HTML", text: "Text", image: "Images" };
const ICONS = { ".md": "md", ".markdown": "md", ".pdf": "pdf", ".docx": "doc", ".pptx": "ppt", ".xlsx": "xls", ".csv": "csv",
  ".html": "htm", ".htm": "htm", ".txt": "txt", ".rst": "txt", ".org": "txt" };

// ------------------------------------------------------------------ helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// Must match recall/chunker.py slugify()
function slugify(s) {
  return s.toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu, "").trim().replace(/\s+/g, "-");
}
function snippetHtml(s) {
  s = String(s ?? "").replace(/\s*\[image: [^\]]*\]/g, "").replace(/\*\*/g, "");
  return esc(s).replaceAll("\x02", "<mark>").replaceAll("\x03", "</mark>");
}
function toast(msg, ms = 2500) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

marked.setOptions({ gfm: true, breaks: false });

/** Render Markdown safely. `answer` mode only allows images served by this app. */
function renderMarkdown(el, md, { answer = false } = {}) {
  el.innerHTML = DOMPurify.sanitize(marked.parse(md), { ADD_ATTR: ["target"] });
  if (answer) {
    $$("img", el).forEach(img => { if (!(img.getAttribute("src") || "").startsWith("/api/")) img.remove(); });
    linkCitations(el);
  }
  const seen = {};
  $$("h1,h2,h3,h4,h5,h6", el).forEach(h => {
    let id = slugify(h.textContent);
    if (seen[id] !== undefined) id += "-" + (++seen[id]); else seen[id] = 0;
    h.id = "h-" + id;
  });
  $$("pre code", el).forEach(c => { try { hljs.highlightElement(c); } catch {} });
  $$("a[href]", el).forEach(a => {
    const href = a.getAttribute("href");
    if (/^https?:/.test(href)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
  });
}

/** Turn [3] / [2][4] in answer text into clickable source chips (skips code). */
function linkCitations(el) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest("code,pre,a") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) if (/\[\d+(?:\s*,\s*\d+)*\]/.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    const text = node.nodeValue;
    for (const m of text.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
      frag.append(text.slice(last, m.index));
      for (const n of m[1].split(",").map(x => x.trim())) {
        const src = state.sources.find(s => s.n === +n);
        if (!src) { frag.append(`[${n}]`); continue; }
        const a = document.createElement("a");
        a.className = "cite"; a.href = "#"; a.dataset.n = n; a.textContent = n;
        a.title = `${src.title} — ${src.heading}`;
        frag.append(a);
      }
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

// ------------------------------------------------------------------ status / settings

const PHASES = { reading: "Indexing", embedding: "Building smart search", captioning: "Describing images" };

async function refreshStatus() {
  try {
    state.status = await api("/api/status");
  } catch (e) { $("#status").textContent = e.message; return; }
  const s = state.status, el = $("#status");
  if (!s.notes_dir) {
    el.innerHTML = `<span class="err">No notes folder — open Settings</span>`;
    return;
  }
  const p = s.progress, ix = s.index;
  const ai = s.settings.ai_enabled ? `AI: ${esc(s.settings.model)}` : "AI off";
  const errs = ix && ix.errors.length ? ` · <span class="err" title="${esc(ix.errors.map(e => e.path + ": " + e.error).join("\n"))}">${ix.errors.length} unreadable</span>` : "";
  const sem = ix && ix.embed_error ? ` · <span class="err" title="${esc(ix.embed_error)}">smart search off</span>` : "";
  el.innerHTML = p && p.running
    ? `${PHASES[p.phase] || "Indexing"} ${p.done}/${p.total}…`
    : `${ix ? ix.docs : 0} notes${ix && ix.images ? ` · ${ix.images} images` : ""} · ${ai}${sem}${errs}${s.watching ? ` · <span title="Watching the folder for changes">live</span>` : ""}`;
  el.title = s.notes_dir;
  if (ix && state.indexVersion !== null && ix.version !== state.indexVersion) onIndexChanged(s.changed || []);
  if (ix) state.indexVersion = ix.version;
}

/** Files were (re)indexed — by the watcher, a save, or a manual re-index. */
function onIndexChanged(changed) {
  loadTree();
  state.graph = null;
  if (!$("#view-graph").hidden) renderGraph();
  const cur = state.currentDoc;
  if (!cur || !changed.includes(cur)) return;
  if (state.editing && state.editing.path === cur) checkEditorDisk();
  else if (!$("#view-browse").hidden) {
    const top = $(".main").scrollTop;
    openDoc(cur, "", { force: true, keepScroll: top });
  }
}

const TEXT_SETTINGS = ["notes_dir", "provider", "model", "base_url", "effort", "max_images", "top_k", "embed_model",
  "caption_model", "caption_limit"];
const BOOL_SETTINGS = ["send_images", "semantic_search", "ocr", "watch", "caption_images"];

function openSettings() {
  const f = $("#settings-form"), s = state.status?.settings || {};
  for (const k of TEXT_SETTINGS) f.elements[k].value = s[k] ?? "";
  for (const k of BOOL_SETTINGS) f.elements[k].checked = !!s[k];
  const feat = state.status?.features || {}, ix = state.status?.index;
  $("#feature-hint").textContent = [
    feat.semantic === false && "Smart search needs the fastembed package (pip install fastembed).",
    feat.ocr === false && "OCR needs the rapidocr-onnxruntime package.",
    ix?.embed_error && `Smart search error: ${ix.embed_error}`,
  ].filter(Boolean).join(" ");
  f.elements.api_key.value = "";
  f.elements.clear_api_key.checked = false;
  $("#key-hint").textContent = s.has_key
    ? `A key ending in ${s.key_hint} is ${s.key_from_env ? "set via environment variable" : "saved"}. Leave blank to keep it.`
    : "No key set. For Anthropic you can also set ANTHROPIC_API_KEY before starting the app.";
  $("#settings-error").textContent = "";
  syncProviderFields();
  $("#settings").showModal();
}
function syncProviderFields() {
  const f = $("#settings-form"), p = f.elements.provider.value;
  $$("[data-for]", f).forEach(el => (el.hidden = el.dataset.for !== p));
  f.elements.model.placeholder = p === "anthropic" ? "claude-opus-5-5" : "gpt-4o / llama3.2 / …";
}
async function saveSettings(ev) {
  ev.preventDefault();
  const f = $("#settings-form"), e = f.elements;
  const body = { api_key: e.api_key.value.trim(), clear_api_key: e.clear_api_key.checked };
  for (const k of TEXT_SETTINGS) body[k] = e[k].value.trim();
  for (const k of BOOL_SETTINGS) body[k] = e[k].checked;
  for (const k of ["max_images", "top_k", "caption_limit"]) body[k] = +body[k] || 0;
  if (!body.embed_model) body.embed_model = "BAAI/bge-small-en-v1.5";
  if (!body.model) body.model = body.provider === "anthropic" ? "claude-opus-5-5" : "";
  try {
    await api("/api/settings", { method: "POST", headers: H, body: JSON.stringify(body) });
  } catch (err) { $("#settings-error").textContent = err.message; return; }
  $("#settings").close();
  toast("Settings saved");
  await refreshStatus();
  loadTree();
}

// ------------------------------------------------------------------ tree

async function loadTree() {
  if (!state.status?.notes_dir) {
    $("#tree").innerHTML = `<div class="tree-empty">Choose a notes folder in <a href="#" id="tree-settings">Settings</a>.</div>`;
    $("#tree-settings").onclick = e => { e.preventDefault(); openSettings(); };
    return;
  }
  try {
    state.tree = await api("/api/tree" + ($("#tree-images").checked ? "?images=true" : ""));
  } catch (e) { $("#tree").innerHTML = `<div class="tree-empty">${esc(e.message)}</div>`; return; }
  renderTree();
  fillFolderFilter();
}

function renderTree() {
  const filter = $("#tree-filter").value.trim().toLowerCase();
  const collapsed = JSON.parse(localStorage.getItem("collapsed") || "[]");
  const build = node => {
    const items = [];
    for (const c of node.children) {
      if (c.type === "dir") {
        const inner = build(c);
        if (!inner && filter) continue;
        const cls = !filter && collapsed.includes(c.path) ? "dir collapsed" : "dir";
        items.push(`<li class="${cls}" data-dir="${esc(c.path)}"><div class="node" role="treeitem"><span class="ico chev" aria-hidden="true"></span><span class="name">${esc(c.name)}</span></div>${inner || "<ul></ul>"}</li>`);
      } else {
        if (filter && !c.path.toLowerCase().includes(filter)) continue;
        const active = state.currentDoc === c.path ? " active" : "";
        items.push(`<li><div class="node${active}" role="treeitem" data-path="${esc(c.path)}" title="${esc(c.path)}"><span class="ico badge b-${ICONS[c.ext] || "img"}" aria-hidden="true">${ICONS[c.ext] || "img"}</span><span class="name">${esc(c.name)}</span></div></li>`);
      }
    }
    return items.length ? `<ul>${items.join("")}</ul>` : "";
  };
  $("#tree").innerHTML = build(state.tree) || `<div class="tree-empty">${filter ? "No matching files." : "No supported files found in this folder."}</div>`;
}

function fillFolderFilter() {
  const sel = $("#folder-filter"), cur = sel.value, dirs = [];
  const walk = n => n.children.forEach(c => { if (c.type === "dir") { dirs.push(c.path); walk(c); } });
  walk(state.tree);
  sel.innerHTML = `<option value="">All folders</option>` + dirs.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
  sel.value = dirs.includes(cur) ? cur : "";
}

// ------------------------------------------------------------------ sidebar search

async function sideSearch(q) {
  const box = $("#side-results");
  if (!q) { box.hidden = true; $("#tree").hidden = false; return; }
  box.hidden = false; $("#tree").hidden = true;
  box.innerHTML = `<div class="tree-empty">Searching…</div>`;
  try {
    const { results } = await api("/api/search?" + new URLSearchParams({ q, limit: 40, mode: $("#side-mode").value }));
    box.innerHTML = results.length
      ? results.map(r => `<div class="result" data-path="${esc(r.path)}" data-anchor="${esc(r.anchor)}">
          <div class="t">${esc(r.title)}${r.match === "semantic" ? `<span class="badge-sem" title="Matched by meaning (${r.similarity})">≈ meaning</span>` : ""}</div><div class="h">${esc(r.path)} › ${esc(r.heading)}</div>
          <div class="s">${snippetHtml(r.snippet)}</div></div>`).join("")
      : `<div class="tree-empty">No matches.</div>`;
  } catch (e) { box.innerHTML = `<div class="tree-empty">${esc(e.message)}</div>`; }
}

// ------------------------------------------------------------------ browse / preview

function showView(name) {
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  for (const v of ["ask", "browse", "graph"]) $("#view-" + v).hidden = name !== v;
  if (name === "graph") renderGraph();
}

async function openDoc(path, anchor = "", { pushHash = true, force = false, keepScroll = null } = {}) {
  if (state.editing) {
    if (state.editing.path === path && !force) { showView("browse"); return; }
    if (!confirmLeaveEditor()) return;
    closeEditor();
  }
  showView("browse");
  $("#sidebar").classList.remove("open");
  if (pushHash) history.replaceState(null, "", "#doc=" + encodeURIComponent(path) + (anchor ? "&a=" + encodeURIComponent(anchor) : ""));
  if (state.currentDoc !== path || force) {
    state.currentDoc = path;
    $$(".node.active").forEach(n => n.classList.remove("active"));
    $$(".node[data-path]").forEach(n => { if (n.dataset.path === path) n.classList.add("active"); });
    $("#doc-empty").hidden = true; $("#doc").hidden = false;
    $("#doc-title").textContent = path.split("/").pop();
    if (keepScroll === null) { $("#doc-content").className = "md"; $("#doc-content").innerHTML = `<p class="muted">Loading…</p>`; }
    let d;
    try { d = await api("/api/doc?path=" + encodeURIComponent(path)); }
    catch (e) { $("#doc-content").innerHTML = `<p class="error">${esc(e.message)}</p>`; $("#toc").innerHTML = ""; return; }
    if (state.currentDoc !== path) return;  // user clicked elsewhere meanwhile
    renderDoc(d);
  }
  if (anchor) scrollToAnchor(anchor);
  else $(".main").scrollTop = keepScroll ?? 0;
}

function renderDoc(d) {
  $("#doc-title").textContent = d.title;
  $("#doc-path").textContent = d.path;
  $("#doc-raw").href = d.raw_url;
  $("#doc-tags").innerHTML = (d.tags || []).map(t => `<span class="tag">#${esc(t)}</span>`).join("");
  const c = $("#doc-content"), toggle = $("#doc-pdf-toggle");
  toggle.hidden = d.kind !== "pdf";
  $("#doc-edit").hidden = !d.editable;
  loadRelated(d.path);
  c.className = "md";
  if (d.kind === "image") {
    c.innerHTML = `<img class="solo" src="${esc(d.raw_url)}" alt="${esc(d.name)}">`;
  } else if (d.kind === "text") {
    c.innerHTML = `<pre class="plain"></pre>`;
    $("pre", c).textContent = d.markdown;
  } else if (d.kind === "pdf") {
    const showPdf = () => { c.className = "md full"; c.innerHTML = `<iframe src="${esc(d.raw_url)}" title="${esc(d.title)}"></iframe>`; toggle.textContent = "Text view"; };
    const showText = () => { c.className = "md"; renderMarkdown(c, d.markdown); toggle.textContent = "PDF view"; buildToc(); };
    toggle.onclick = () => (c.querySelector("iframe") ? showText() : showPdf());
    showPdf();
  } else {
    renderMarkdown(c, d.markdown);
  }
  buildToc();
}

function buildToc() {
  const hs = $$("#doc-content h1, #doc-content h2, #doc-content h3, #doc-content h4");
  $("#toc").innerHTML = hs.length > 1
    ? `<div class="muted small" style="margin-bottom:6px">On this page</div>` +
      hs.map(h => `<a href="#" data-anchor="${esc(h.id)}" class="l${h.tagName[1]}">${esc(h.textContent)}</a>`).join("")
    : "";
}

function scrollToAnchor(anchor, tries = 0) {
  const c = $("#doc-content");
  if (c.querySelector("iframe")) {
    // Search hits in PDFs point at "page-N": jump the native viewer there.
    const m = anchor.match(/^page-(\d+)$/);
    if (m) { const f = c.querySelector("iframe"); f.src = f.src.split("#")[0] + "#page=" + m[1]; }
    return;
  }
  const el = document.getElementById(anchor.startsWith("h-") ? anchor : "h-" + anchor);
  if (!el) { if (tries < 5) setTimeout(() => scrollToAnchor(anchor, tries + 1), 120); return; }
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600);
}

// ------------------------------------------------------------------ ask

function renderTypeChips() {
  const groups = Object.keys(state.status?.type_groups || TYPE_LABELS);
  $("#type-chips").innerHTML = groups.map(g =>
    `<button type="button" class="chip" data-type="${g}" aria-pressed="${state.types.has(g)}">${TYPE_LABELS[g] || g}</button>`).join("");
}

function setScope(path) {
  state.scopePath = path;
  const el = $("#doc-scope");
  el.hidden = !path;
  el.innerHTML = path ? `Only: ${esc(path.split("/").pop())} <button type="button" title="Search all notes" aria-label="Clear">✕</button>` : "";
}

async function ask(question, mode) {
  if (state.asking) state.asking.abort();
  const ctrl = new AbortController();
  state.asking = ctrl;
  showView("ask");
  const out = $("#answer");
  $("#answer-area").hidden = false;
  $("#answer-q").textContent = question;
  $("#answer-meta").textContent = mode === "report" ? "Detailed report" : "Summary";
  $("#sources").innerHTML = "";
  out.innerHTML = `<p class="muted">Searching your notes…</p>`;
  out.classList.add("loading");
  $("#ask-btn").disabled = true;
  state.answerMd = ""; state.sources = [];

  let pending = false;
  const paint = () => { pending = false; renderMarkdown(out, state.answerMd, { answer: true }); };
  const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(paint); } };
  let failed = false;
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: H, signal: ctrl.signal,
      body: JSON.stringify({ question, mode, types: [...state.types], folder: $("#folder-filter").value, path: state.scopePath }),
    });
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail; } catch {} throw new Error(m); }
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = /^event: (.*)$/m.exec(block)?.[1], data = JSON.parse(/^data: (.*)$/m.exec(block)?.[1] ?? "null");
        if (ev === "sources") { state.sources = data; renderSources(); out.innerHTML = `<p class="muted">${data.length ? "Writing answer…" : ""}</p>`; }
        else if (ev === "delta") { state.answerMd += data; schedule(); }
        else if (ev === "error") { failed = true; state.answerMd += `\n\n**Error:** ${data.message}`; paint(); }
        else if (ev === "done") { $("#answer-meta").textContent += data.ai ? ` · ${data.model}` : " · local"; }
      }
    }
  } catch (e) {
    if (e.name === "AbortError") return;
    failed = true;
    out.innerHTML = `<p class="error">${esc(e.message)}</p>`;
  } finally {
    if (state.asking === ctrl) { state.asking = null; $("#ask-btn").disabled = false; out.classList.remove("loading"); }
  }
  if (state.answerMd) paint();
  if (!failed && state.answerMd) saveHistory({ question, mode, md: state.answerMd, sources: state.sources, at: Date.now() });
}

function renderSources() {
  $("#sources").innerHTML = state.sources.length ? state.sources.map(s => `
    <li class="source" data-n="${s.n}" data-path="${esc(s.path)}" data-anchor="${esc(s.anchor)}">
      <div><span class="n">${s.n}</span><span class="t">${esc(s.title)}</span></div>
      <div class="h">${esc(s.path)} › ${esc(s.heading)}</div>
      <div>${snippetHtml(s.snippet)}</div>
      ${s.images.length ? `<div class="thumbs">${s.images.slice(0, 4).map(i => `<img src="${esc(i.url)}" alt="${esc(i.alt)}" loading="lazy">`).join("")}</div>` : ""}
    </li>`).join("") : `<li class="muted small">No matching notes.</li>`;
}

// ------------------------------------------------------------------ history (per browser)

function loadHistory() { try { return JSON.parse(localStorage.getItem("history") || "[]"); } catch { return []; } }
function saveHistory(item) {
  const h = loadHistory().filter(x => !(x.question === item.question && x.mode === item.mode));
  h.unshift(item);
  try { localStorage.setItem("history", JSON.stringify(h.slice(0, 25))); } catch {}
  renderHistory();
}
function renderHistory() {
  const h = loadHistory();
  $("#history-wrap").hidden = !h.length;
  $("#history").innerHTML = h.map((x, i) => `<li><a data-i="${i}">${esc(x.question)}</a>
    <span class="muted small">${x.mode === "report" ? "report" : "summary"} · ${new Date(x.at).toLocaleDateString()}</span></li>`).join("");
}
function showHistory(i) {
  const x = loadHistory()[i];
  if (!x) return;
  state.sources = x.sources; state.answerMd = x.md;
  $("#answer-area").hidden = false;
  $("#answer-q").textContent = x.question;
  $("#answer-meta").textContent = (x.mode === "report" ? "Detailed report" : "Summary") + " · saved";
  renderSources();
  renderMarkdown($("#answer"), x.md, { answer: true });
  $("#question").value = x.question;
  window.scrollTo(0, 0); $(".main").scrollTop = 0;
}

// ------------------------------------------------------------------ related notes (links, backlinks, similar)

async function loadRelated(path) {
  const box = $("#related");
  box.innerHTML = "";
  let r;
  try { r = await api("/api/related?path=" + encodeURIComponent(path)); } catch { return; }
  if (state.currentDoc !== path) return;
  const list = (title, items, extra = () => "") => items.length
    ? `<h4>${title}</h4><ul>${items.map(i => `<li><a href="#" data-path="${esc(i.path)}" title="${esc(i.path)}">${esc(i.title)}${extra(i)}</a></li>`).join("")}</ul>`
    : "";
  box.innerHTML = list("Linked from", r.backlinks) + list("Links to", r.outgoing) +
    list("Similar notes", r.similar, i => ` <span class="pct">${Math.round(i.similarity * 100)}%</span>`);
}

// ------------------------------------------------------------------ editor

function confirmLeaveEditor() {
  return !isDirty() || confirm("You have unsaved changes. Discard them?");
}
function isDirty() {
  return !!state.editing && $("#ed-text").value !== state.editing.saved;
}
function setEditorState(msg) {
  $("#ed-state").textContent = msg ?? (isDirty() ? "● unsaved changes" : "saved");
}

async function openEditor(path) {
  let src;
  try { src = await api("/api/source?path=" + encodeURIComponent(path)); }
  catch (e) { toast(e.message); return; }
  state.editing = { path, mtime_ns: src.mtime_ns, saved: src.content };
  state.currentDoc = path;
  showView("browse");
  $("#doc").hidden = true; $("#doc-empty").hidden = true; $("#editor").hidden = false;
  $("#ed-conflict").hidden = true;
  $("#ed-path").textContent = path;
  const ta = $("#ed-text");
  ta.value = src.content;
  setEditorState();
  renderEditorPreview();
  loadVersions();
  ta.focus();
}

function closeEditor() {
  state.editing = null;
  $("#editor").hidden = true;
}

let previewTimer;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(renderEditorPreview, 300);
}
async function renderEditorPreview() {
  if (!state.editing || !$("#ed-preview-toggle").checked) return;
  const path = state.editing.path, md = $("#ed-text").value;
  if (path.toLowerCase().endsWith(".txt")) {
    $("#ed-preview").innerHTML = `<pre class="plain"></pre>`;
    $("#ed-preview pre").textContent = md;
    return;
  }
  try {
    const r = await api("/api/render", { method: "POST", headers: H, body: JSON.stringify({ path, markdown: md }) });
    if (state.editing?.path === path) renderMarkdown($("#ed-preview"), r.markdown);
  } catch {}
}

async function saveEditor(force = false) {
  const ed = state.editing;
  if (!ed) return;
  const content = $("#ed-text").value;
  setEditorState("saving…");
  try {
    const r = await fetch("/api/source", {
      method: "PUT", headers: H,
      body: JSON.stringify({ path: ed.path, content, base_mtime_ns: ed.mtime_ns, force }),
    });
    const body = await r.json();
    if (r.status === 409) { $("#ed-conflict").hidden = false; setEditorState("not saved — conflict"); return; }
    if (!r.ok) throw new Error(body.detail || r.statusText);
    ed.mtime_ns = body.mtime_ns; ed.saved = content;
    $("#ed-conflict").hidden = true;
    setEditorState();
    if (body.changed) { toast("Saved"); loadVersions(); }
  } catch (e) { setEditorState("not saved"); toast(e.message); }
}

/** The file changed on disk while open in the editor (seen via the index watcher). */
async function checkEditorDisk() {
  const ed = state.editing;
  if (!ed) return;
  let src;
  try { src = await api("/api/source?path=" + encodeURIComponent(ed.path)); } catch { return; }
  if (src.mtime_ns === ed.mtime_ns) return;  // our own save
  if (!isDirty()) {
    ed.mtime_ns = src.mtime_ns; ed.saved = src.content;
    $("#ed-text").value = src.content;
    renderEditorPreview(); setEditorState("reloaded — changed on disk");
  } else {
    $("#ed-conflict").hidden = false;
  }
}

async function loadVersions() {
  const sel = $("#ed-versions"), path = state.editing?.path;
  if (!path) return;
  try {
    const { versions } = await api("/api/versions?path=" + encodeURIComponent(path));
    sel.innerHTML = `<option value="">History (${versions.length})…</option>` +
      versions.map(v => `<option value="${v.id}">${new Date(v.saved_at * 1000).toLocaleString()}</option>`).join("");
  } catch {}
}

async function uploadImages(files) {
  const ed = state.editing;
  if (!ed) return;
  const ta = $("#ed-text");
  for (const f of files) {
    if (!f.type.startsWith("image/")) continue;
    const fd = new FormData();
    fd.append("note", ed.path);
    const ext = (f.type.split("/")[1] || "png").replace("jpeg", "jpg").replace("svg+xml", "svg");
    fd.append("file", f, f.name && f.name !== "image.png" ? f.name : `pasted-${Date.now()}.${ext}`);
    try {
      const r = await fetch("/api/upload", { method: "POST", headers: { "X-Recall": "1" }, body: fd });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || r.statusText);
      const alt = body.name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ");
      insertAtCursor(ta, `![${alt}](${encodeURI(body.name)})\n`);
    } catch (e) { toast("Upload failed: " + e.message); }
  }
}

function insertAtCursor(ta, text) {
  ta.focus();
  const { selectionStart: a, selectionEnd: b, value } = ta;
  const pre = a > 0 && value[a - 1] !== "\n" ? "\n" : "";
  ta.setRangeText(pre + text, a, b, "end");
  ta.dispatchEvent(new Event("input"));
}

async function newNote() {
  if (!state.status?.notes_dir) return openSettings();
  const folder = state.currentDoc && state.currentDoc.includes("/") ? state.currentDoc.replace(/\/[^/]*$/, "/") : "";
  const path = prompt("New note path (relative to your notes folder):", folder + "untitled.md");
  if (!path) return;
  try {
    const r = await api("/api/note", { method: "POST", headers: H, body: JSON.stringify({ path }) });
    if (state.editing && !confirmLeaveEditor()) return;
    closeEditor();
    await loadTree();
    openEditor(r.path);
  } catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ graph

const EXT_COLOR = { ".md": "#4b5563", ".markdown": "#4b5563", ".pdf": "#d14343", ".docx": "#2f6fdf", ".pptx": "#d9741c",
  ".xlsx": "#1f8f4e", ".csv": "#1f8f4e", ".html": "#7c4dcc", ".htm": "#7c4dcc", ".txt": "#8a94a6" };

async function renderGraph() {
  const svgEl = $("#graph");
  if (!state.status?.notes_dir) { $("#graph-info").textContent = "Choose a notes folder first."; return; }
  const similar = $("#graph-similar").checked;
  const key = `${state.indexVersion}:${similar}`;
  if (!state.graph || state.graph.key !== key) {
    $("#graph-info").textContent = "Loading…";
    try { state.graph = { key, data: await api("/api/graph?similar=" + similar) }; }
    catch (e) { $("#graph-info").textContent = e.message; return; }
  }
  let nodes = state.graph.data.nodes.map(n => ({ ...n }));
  let edges = state.graph.data.edges.map(e => ({ ...e }));
  const deg = {};
  edges.forEach(e => { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; });
  if ($("#graph-local").checked && state.currentDoc) {
    const keep = new Set([state.currentDoc]);
    for (let hop = 0; hop < 2; hop++) {
      for (const e of edges) {
        if (keep.has(e.source)) keep.add(e.target);
        else if (keep.has(e.target)) keep.add(e.source);
      }
    }
    nodes = nodes.filter(n => keep.has(n.id));
    edges = edges.filter(e => keep.has(e.source) && keep.has(e.target));
  } else if (!$("#graph-orphans").checked) {
    nodes = nodes.filter(n => deg[n.id]);
  }
  const ids = new Set(nodes.map(n => n.id));
  edges = edges.filter(e => ids.has(e.source) && ids.has(e.target));
  $("#graph-info").textContent = `${nodes.length} notes · ${edges.filter(e => e.type === "link").length} links` +
    (similar ? ` · ${edges.filter(e => e.type === "similar").length} similar` : "");

  const svg = d3.select(svgEl);
  svg.selectAll("*").remove();
  const { width, height } = svgEl.getBoundingClientRect();
  const root = svg.append("g");
  const zoom = d3.zoom().scaleExtent([0.2, 6]).on("zoom", ev => {
    root.attr("transform", ev.transform);
    root.classed("zoomed", ev.transform.k > 1.6);
    label.attr("display", d => (ev.transform.k > 1.6 || d.big) ? null : "none");
  });
  svg.call(zoom);

  const radius = d => 4 + Math.sqrt(deg[d.id] || 0) * 2.5;
  const labelAll = nodes.length <= 60;  // small vaults: label everything; big ones: hubs only until zoomed
  nodes.forEach(n => (n.big = labelAll || (deg[n.id] || 0) >= 3 || n.id === state.currentDoc));
  const link = root.append("g").selectAll("line").data(edges).join("line")
    .attr("class", d => "link " + d.type).attr("stroke-width", d => d.type === "link" ? 1.5 : 1);
  const node = root.append("g").selectAll("g").data(nodes).join("g")
    .attr("class", d => "node" + (d.id === state.currentDoc ? " current" : ""));
  node.append("circle").attr("r", radius).attr("fill", d => EXT_COLOR[d.ext] || "#8a94a6");
  node.append("title").text(d => `${d.title}\n${d.id}`);
  const label = node.append("text").text(d => d.title).attr("x", d => radius(d) + 3).attr("y", 4)
    .attr("display", d => d.big ? null : "none");

  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(edges).id(d => d.id).distance(d => d.type === "link" ? 60 : 90).strength(d => d.type === "link" ? 0.6 : 0.15))
    .force("charge", d3.forceManyBody().strength(-140))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("x", d3.forceX(width / 2).strength(0.04)).force("y", d3.forceY(height / 2).strength(0.04))
    .force("collide", d3.forceCollide(d => radius(d) + 4))
    .on("tick", () => {
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y).attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });
  state.graphSim?.stop();
  state.graphSim = sim;

  node.call(d3.drag()
    .on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
    .on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
  node.on("click", (ev, d) => openDoc(d.id));
  node.on("mouseenter", (ev, d) => {
    const near = new Set([d.id]);
    edges.forEach(e => { if (e.source.id === d.id) near.add(e.target.id); if (e.target.id === d.id) near.add(e.source.id); });
    node.classed("dim", n => !near.has(n.id));
    link.classed("dim", e => e.source.id !== d.id && e.target.id !== d.id);
    label.attr("display", n => near.has(n.id) || n.big ? null : "none");
  }).on("mouseleave", () => { node.classed("dim", false); link.classed("dim", false); applyGraphFilter(); label.attr("display", n => n.big ? null : "none"); });

  function applyGraphFilter() {
    const q = $("#graph-filter").value.trim().toLowerCase();
    node.classed("hit", d => q && (d.title.toLowerCase().includes(q) || d.id.toLowerCase().includes(q)));
    if (q) node.classed("dim", d => !(d.title.toLowerCase().includes(q) || d.id.toLowerCase().includes(q)));
  }
  state.applyGraphFilter = applyGraphFilter;
  applyGraphFilter();
}

// ------------------------------------------------------------------ routing & events

function route() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("doc")) openDoc(p.get("doc"), p.get("a") || "", { pushHash: false });
}

function bind() {
  $$(".tab").forEach(t => (t.onclick = () => showView(t.dataset.view)));
  $("#open-settings").onclick = openSettings;
  $("#settings-form").elements.provider.onchange = syncProviderFields;
  $("#settings-form").addEventListener("submit", e => { if (e.submitter?.value === "save") saveSettings(e); });
  $("#toggle-sidebar").onclick = () => $("#sidebar").classList.toggle("open");
  $("#reindex").onclick = async () => {
    try { await api("/api/index", { method: "POST", headers: H }); toast("Re-indexing…"); setTimeout(refreshStatus, 300); }
    catch (e) { toast(e.message); }
  };

  $("#tree").addEventListener("click", e => {
    const node = e.target.closest(".node");
    if (!node) return;
    if (node.dataset.path) return openDoc(node.dataset.path);
    const li = node.parentElement;
    li.classList.toggle("collapsed");
    const set = new Set(JSON.parse(localStorage.getItem("collapsed") || "[]"));
    li.classList.contains("collapsed") ? set.add(li.dataset.dir) : set.delete(li.dataset.dir);
    localStorage.setItem("collapsed", JSON.stringify([...set]));
  });
  $("#tree-filter").oninput = renderTree;
  $("#tree-images").onchange = loadTree;
  $("#side-q").addEventListener("keydown", e => { if (e.key === "Enter") sideSearch(e.target.value.trim()); });
  $("#side-q").addEventListener("input", e => { if (!e.target.value) sideSearch(""); });
  $("#side-results").addEventListener("click", e => {
    const r = e.target.closest(".result");
    if (r) openDoc(r.dataset.path, r.dataset.anchor);
  });

  $("#ask-form").addEventListener("submit", e => {
    e.preventDefault();
    const q = $("#question").value.trim();
    if (q) ask(q, $("input[name=mode]:checked").value);
  });
  $("#question").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#ask-form").requestSubmit();
  });
  $("#type-chips").addEventListener("click", e => {
    const c = e.target.closest(".chip");
    if (!c) return;
    state.types.has(c.dataset.type) ? state.types.delete(c.dataset.type) : state.types.add(c.dataset.type);
    c.setAttribute("aria-pressed", state.types.has(c.dataset.type));
  });
  $("#doc-scope").addEventListener("click", e => { if (e.target.closest("button")) setScope(""); });

  $("#answer").addEventListener("click", e => {
    const a = e.target.closest("a.cite");
    if (!a) return;
    e.preventDefault();
    const li = $(`#sources .source[data-n="${a.dataset.n}"]`);
    if (li) { li.scrollIntoView({ block: "nearest", behavior: "smooth" }); li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), 1200); }
  });
  $("#sources").addEventListener("click", e => {
    const li = e.target.closest(".source");
    if (li) openDoc(li.dataset.path, li.dataset.anchor);
  });
  $("#history").addEventListener("click", e => { const a = e.target.closest("a[data-i]"); if (a) showHistory(+a.dataset.i); });
  $("#copy-answer").onclick = async () => {
    try { await navigator.clipboard.writeText(state.answerMd); toast("Copied Markdown"); } catch { toast("Copy failed"); }
  };
  $("#download-answer").onclick = () => {
    const srcs = state.sources.map(s => `${s.n}. ${s.title} — ${s.heading} (${s.path})`).join("\n");
    const md = `# ${$("#answer-q").textContent}\n\n${state.answerMd}\n\n---\n**Sources**\n\n${srcs}\n`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    a.download = $("#answer-q").textContent.slice(0, 60).replace(/[^\w -]+/g, "").trim().replace(/\s+/g, "-") + ".md";
    a.click(); URL.revokeObjectURL(a.href);
  };

  // editor
  $("#new-note").onclick = newNote;
  $("#doc-edit").onclick = () => state.currentDoc && openEditor(state.currentDoc);
  const ta = $("#ed-text");
  ta.addEventListener("input", () => { setEditorState(); schedulePreview(); });
  ta.addEventListener("keydown", e => {
    if (e.key === "Tab" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); ta.setRangeText("  ", ta.selectionStart, ta.selectionEnd, "end"); ta.dispatchEvent(new Event("input")); }
  });
  ta.addEventListener("paste", e => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); uploadImages(files); }
  });
  ta.addEventListener("dragover", e => { e.preventDefault(); ta.classList.add("dragging"); });
  ta.addEventListener("dragleave", () => ta.classList.remove("dragging"));
  ta.addEventListener("drop", e => {
    ta.classList.remove("dragging");
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) { e.preventDefault(); uploadImages(files); }
  });
  $("#ed-image").onclick = () => $("#ed-file").click();
  $("#ed-file").onchange = e => { uploadImages([...e.target.files]); e.target.value = ""; };
  $("#ed-save").onclick = () => saveEditor();
  $("#ed-overwrite").onclick = () => saveEditor(true);
  $("#ed-discard").onclick = () => { const p = state.editing.path; state.editing.saved = $("#ed-text").value; closeEditor(); openEditor(p); };
  $("#ed-cancel").onclick = () => {
    if (!confirmLeaveEditor()) return;
    const p = state.editing.path;
    closeEditor();
    openDoc(p, "", { force: true });
  };
  $("#ed-preview-toggle").onchange = e => { $("#ed-panes").classList.toggle("solo", !e.target.checked); renderEditorPreview(); };
  $("#ed-versions").onchange = async e => {
    const id = e.target.value;
    e.target.value = "";
    if (!id || !state.editing) return;
    if (isDirty() && !confirm("Replace your unsaved changes with this older version?")) return;
    try {
      const { content } = await api(`/api/version?path=${encodeURIComponent(state.editing.path)}&id=${id}`);
      ta.value = content; ta.dispatchEvent(new Event("input"));
      toast("Older version loaded — Save to keep it");
    } catch (err) { toast(err.message); }
  };
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && state.editing) { e.preventDefault(); saveEditor(); }
  });
  window.addEventListener("beforeunload", e => { if (isDirty()) { e.preventDefault(); e.returnValue = ""; } });

  // related + graph
  $("#related").addEventListener("click", e => {
    const a = e.target.closest("a[data-path]");
    if (a) { e.preventDefault(); openDoc(a.dataset.path); }
  });
  $("#graph-filter").oninput = () => state.applyGraphFilter?.();
  for (const id of ["#graph-similar", "#graph-orphans", "#graph-local"]) $(id).onchange = renderGraph;
  $("#side-mode").onchange = () => { const q = $("#side-q").value.trim(); if (q) sideSearch(q); };

  $("#doc-ask").onclick = () => {
    setScope(state.currentDoc);
    showView("ask");
    $("#question").focus();
  };
  $("#toc").addEventListener("click", e => {
    const a = e.target.closest("a[data-anchor]");
    if (a) { e.preventDefault(); scrollToAnchor(a.dataset.anchor); }
  });

  // Images open in a lightbox; links to other notes (#doc=…) open in the previewer.
  document.addEventListener("click", e => {
    const img = e.target.closest(".md img:not(.solo), .thumbs img");
    if (img) { e.stopPropagation(); $("img", $("#lightbox")).src = img.src; $("#lightbox").hidden = false; return; }
    const a = e.target.closest(".md a[href^='#doc=']");
    if (a) { e.preventDefault(); const p = new URLSearchParams(a.getAttribute("href").slice(1)); openDoc(p.get("doc")); }
  }, true);
  $("#lightbox").onclick = () => ($("#lightbox").hidden = true);
  document.addEventListener("keydown", e => { if (e.key === "Escape") $("#lightbox").hidden = true; });
  window.addEventListener("hashchange", route);
}

(async function init() {
  bind();
  await refreshStatus();
  renderTypeChips();
  renderHistory();
  await loadTree();
  if (!state.status?.notes_dir) openSettings();
  route();
  setInterval(refreshStatus, 3000);  // picks up re-indexing (watcher, saves) and progress
})();
