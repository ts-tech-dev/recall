// Browse view: note preview, outline, related notes (footer) and #doc= routing.

import { $, $$, H, api, esc, toast } from "./util.js";
import { state } from "./state.js";
import { renderMarkdown } from "./markdown.js";
import { closeEditor, confirmLeaveEditor, openEditor } from "./editor.js";
import { renderGraph } from "./graph.js";
import { moveCurrentDoc } from "./files.js";
import { setScope } from "./ask.js";
import { setTask } from "./tasks.js";
import { sideSearch } from "./tree.js";

export function showView(name) {
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  for (const v of ["ask", "browse", "graph"]) $("#view-" + v).hidden = name !== v;
  if (name === "graph") renderGraph();
}

export async function openDoc(path, anchor = "", { pushHash = true, force = false, keepScroll = null } = {}) {
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
    // PDFs open in the browser's viewer; their extracted text is only fetched for "Text view".
    const textless = /\.pdf$/i.test(path) ? "&text=false" : "";
    try { d = await api("/api/doc?path=" + encodeURIComponent(path) + textless); }
    catch (e) { $("#doc-content").innerHTML = `<p class="error">${esc(e.message)}</p>`; $("#toc").innerHTML = ""; updateOutline(); return; }
    if (state.currentDoc !== path) return;  // user clicked elsewhere meanwhile
    renderDoc(d);
  }
  if (anchor) scrollToAnchor(anchor);
  else $(".main").scrollTop = keepScroll ?? 0;
}

export function renderDoc(d) {
  state.docMtime = d.mtime_ns;
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
    const showPdf = () => { c.className = "md full"; c.innerHTML = `<iframe src="${esc(d.raw_url)}" title="${esc(d.title)}"></iframe>`; toggle.textContent = "Text view"; buildToc(); };
    const showText = async () => {
      if (d.markdown == null) {
        c.className = "md"; c.innerHTML = `<p class="muted">Reading the PDF's text…</p>`;
        try { d.markdown = (await api("/api/doc?path=" + encodeURIComponent(d.path))).markdown; }
        catch (e) { c.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
        if (state.currentDoc !== d.path) return;
      }
      c.className = "md"; renderMarkdown(c, d.markdown); toggle.textContent = "PDF view"; buildToc();
    };
    toggle.onclick = () => (c.querySelector("iframe") ? showText() : showPdf());
    showPdf();
  } else {
    const onTask = d.editable && d.kind === "markdown" ? (i, checked, count) => saveTask(d.path, i, checked, count) : null;
    renderMarkdown(c, d.markdown, { onTask });
  }
  buildToc();
}

/** A checkbox in the preview was clicked: write the change to the note (conflict-checked). */
async function saveTask(path, index, checked, count) {
  const src = await api("/api/source?path=" + encodeURIComponent(path));
  const reload = msg => { toast(msg, 4000); openDoc(path, "", { force: true, keepScroll: $(".main").scrollTop }); return false; };
  if (src.mtime_ns !== state.docMtime) return reload("The note changed on disk. It has been reloaded; try again.");
  const content = setTask(src.content, index, checked, count);
  if (content === null) { toast("Couldn't match this checkbox to a line in the file. Use Edit to change it.", 4000); return false; }
  const r = await fetch("/api/source", { method: "PUT", headers: H,
    body: JSON.stringify({ path, content, base_mtime_ns: src.mtime_ns }) });
  if (r.status === 409) return reload("The note changed on disk. It has been reloaded; try again.");
  if (!r.ok) { toast((await r.json().catch(() => ({}))).detail || r.statusText); return false; }
  state.docMtime = (await r.json()).mtime_ns;
  return true;
}

export function buildToc() {
  const hs = $$("#doc-content h1, #doc-content h2, #doc-content h3, #doc-content h4");
  $("#toc").innerHTML = hs.length > 1
    ? `<div class="muted small" style="margin-bottom:6px">On this page</div>` +
      hs.map(h => `<a href="#" data-anchor="${esc(h.id)}" class="l${h.tagName[1]}">${esc(h.textContent)}</a>`).join("")
    : "";
  updateOutline();
}

// The outline sits beside the note only when switched on (remembered per browser), so the note gets the full width.
function outlineWanted() { try { return localStorage.getItem("outline") === "1"; } catch { return false; } }
export function updateOutline() {
  const has = !!$("#toc").innerHTML, on = has && outlineWanted(), btn = $("#doc-outline");
  btn.hidden = !has;
  btn.setAttribute("aria-pressed", on);
  $(".doc-body").classList.toggle("with-outline", on);
}

export function scrollToAnchor(anchor, tries = 0) {
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

export async function loadRelated(path) {
  // Linked from / Links to / Similar notes: small lines under the note.
  const foot = $("#doc-footer");
  foot.innerHTML = ""; foot.hidden = true;
  let r;
  try { r = await api("/api/related?path=" + encodeURIComponent(path)); } catch { return; }
  if (state.currentDoc !== path) return;
  const line = (label, items, extra = () => "") => items.length
    ? `<div><span class="lbl">${label}:</span> ${items.map(i =>
        `<a href="#" data-path="${esc(i.path)}" title="${esc(i.path)}">${esc(i.title)}</a>${extra(i)}`).join(" · ")}</div>`
    : "";
  foot.innerHTML = line("Linked from", r.backlinks) + line("Links to", r.outgoing) +
    line("Similar notes", r.similar, i => ` <span class="pct">${Math.round(i.similarity * 100)}%</span>`);
  foot.hidden = !foot.innerHTML;
}

export function route() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("doc")) openDoc(p.get("doc"), p.get("a") || "", { pushHash: false });
}

export function bindViewer() {
  $$(".tab").forEach(t => (t.onclick = () => showView(t.dataset.view)));
  $("#doc-edit").onclick = () => state.currentDoc && openEditor(state.currentDoc);
  $("#doc-move").onclick = moveCurrentDoc;
  $("#doc-ask").onclick = () => {
    setScope(state.currentDoc);
    showView("ask");
    $("#question").focus();
  };
  $("#toc").addEventListener("click", e => {
    const a = e.target.closest("a[data-anchor]");
    if (a) { e.preventDefault(); scrollToAnchor(a.dataset.anchor); }
  });
  $("#doc-content").addEventListener("click", e => {
    const tag = e.target.closest("a.tag");
    if (tag) { e.preventDefault(); $("#side-q").value = "#" + tag.dataset.tag; sideSearch(tag.dataset.tag); $("#sidebar").classList.add("open"); }
  });
  $("#doc-footer").addEventListener("click", e => {
    const a = e.target.closest("a[data-path]");
    if (a) { e.preventDefault(); openDoc(a.dataset.path); }
  });
  $("#doc-outline").onclick = () => {
    try { localStorage.setItem("outline", outlineWanted() ? "0" : "1"); } catch {}
    updateOutline();
  };
  // Images open in a lightbox; links to other notes (#doc=…) open in the previewer.
  document.addEventListener("click", e => {
    const img = e.target.closest(".md img:not(.solo), .thumbs img");
    if (img) { e.stopPropagation(); $("img", $("#lightbox")).src = img.src; $("#lightbox").hidden = false; return; }
    const a = e.target.closest(".md a[href^='#doc=']");
    if (a) { e.preventDefault(); const p = new URLSearchParams(a.getAttribute("href").slice(1)); openDoc(p.get("doc")); return; }
    // Links within the note (#heading, footnotes) scroll to their target instead of changing the address.
    const local = e.target.closest(".md a[href^='#']:not(.tag):not(.cite)");
    const id = local && decodeURIComponent(local.getAttribute("href").slice(1));
    if (id) {
      e.preventDefault();
      const md = local.closest(".md");
      const target = [...md.querySelectorAll("[id]")].find(el => el.id === id || el.id === "h-" + id);
      if (target) { target.scrollIntoView({ behavior: "smooth", block: "start" }); target.classList.add("flash"); setTimeout(() => target.classList.remove("flash"), 1600); }
    }
  }, true);
  $("#lightbox").onclick = () => ($("#lightbox").hidden = true);
  document.addEventListener("keydown", e => { if (e.key === "Escape") $("#lightbox").hidden = true; });
  window.addEventListener("hashchange", route);
}
