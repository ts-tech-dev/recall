// "Export PDF": notes are rendered again in the light theme into a hidden page laid out for paper
// (css/print.css), with every image loaded and every diagram drawn, then the browser's print dialog opens on it,
// where "Save as PDF" writes the file. The browser does the layout, so math, code and diagrams come out
// exactly as in the preview, as selectable text with working links.
//
// One note, or several (a folder): then a title page with a table of contents, and each note on new pages.

import { $, $$, api, esc, folderLabel, toast } from "./util.js";
import { state } from "./state.js";
import { renderMarkdown } from "./markdown.js";

const STYLES = ["/static/vendor/katex/katex.min.css", "/static/vendor/hljs-github.min.css",
  "/static/css/markdown.css", "/static/css/print.css"];
const PAGE_WIDTH = 680;  // px, about the width of the text on an A4 or Letter page, for drawing diagrams
// Files whose preview is rendered Markdown (PDFs are PDFs already; plain text and images aren't notes to print).
export const EXPORTABLE = new Set([".md", ".markdown", ".docx", ".pptx", ".xlsx", ".csv", ".html", ".htm"]);

let busy = false;

export const exportPdf = path => exportNotes([path]);

/** Export `paths` as one PDF. Several notes get a title page named `title`, with a table of contents. */
export async function exportNotes(paths, { title = "", toc = true } = {}) {
  if (busy) return;
  busy = true;
  const many = paths.length > 1;
  try {
    const anchors = new Map(paths.map((p, i) => [p, "note-" + i]));
    const notes = [];
    for (const [i, path] of paths.entries()) {
      toast(many ? `Preparing the PDF… ${i + 1} of ${paths.length}` : "Preparing the PDF…", 60000);
      const d = await api("/api/doc?path=" + encodeURIComponent(path));
      if (d.markdown == null || d.kind !== "markdown") continue;
      notes.push({ ...d, anchor: anchors.get(path), html: await renderForPaper(d, many ? anchors.get(path) + "-" : "", anchors) });
    }
    if (!notes.length) throw new Error("none of these files can be exported");
    await printPage(many ? title || "Notes" : notes[0].title, pageBody(notes, many ? { title: title || "Notes", toc } : null));
  } catch (e) {
    toast("Couldn't export the PDF: " + e.message, 5000);
  } finally {
    busy = false;
  }
}

/**
 * Render a note off screen (diagrams need a laid-out page to measure their text) and return its HTML.
 * `prefix` keeps ids apart when several notes share the page; links to notes in `anchors` (the export) jump
 * to them, other links to notes and #tag searches mean nothing on paper and keep their text only.
 */
async function renderForPaper(d, prefix, anchors) {
  const box = document.createElement("article");
  box.className = "md";
  box.style.cssText = `position:fixed;left:-10000px;top:0;width:${PAGE_WIDTH}px;visibility:hidden`;
  document.body.append(box);
  try {
    await renderMarkdown(box, d.markdown, { light: true });
    for (const img of $$("img", box)) img.removeAttribute("loading");  // lazy images would never load off screen
    for (const det of $$("details", box)) det.open = true;  // folded callouts and <details> are printed open
    // A heading at the top that repeats the title isn't shown twice (the note's header shows it).
    const first = box.firstElementChild;
    if (first?.tagName === "H1" && first.textContent.trim() === d.title.trim()) first.remove();
    const ids = new Set($$("[id]", box).map(el => el.id));
    for (const a of $$("a[href^='#']", box)) {
      const href = a.getAttribute("href");
      if (href.startsWith("#doc=")) {
        const target = anchors.get(new URLSearchParams(href.slice(1)).get("doc"));
        if (target) { a.setAttribute("href", "#" + target); continue; }
      } else if (!a.classList.contains("tag")) {
        const id = decodeURIComponent(href.slice(1));
        const hit = ids.has(id) ? id : ids.has("h-" + id) ? "h-" + id : null;
        if (hit) { a.setAttribute("href", "#" + prefix + hit); continue; }
      }
      const span = document.createElement("span");
      span.className = a.className;
      span.append(...a.childNodes);
      a.replaceWith(span);
    }
    if (prefix) for (const el of $$("[id]", box)) el.id = prefix + el.id;
    return box.innerHTML;
  } finally {
    box.remove();
  }
}

const tagsHtml = tags => tags?.length
  ? `<div class="paper-tags">${tags.map(t => `<span class="tag">#${esc(t)}</span>`).join(" ")}</div>` : "";

/** The page: one note, or (with `book`) a title page, contents and every note starting on a new page. */
function pageBody(notes, book) {
  if (!book) {
    const [n] = notes;
    return `<header class="paper-head"><h1>${esc(n.title)}</h1>${tagsHtml(n.tags)}</header><article class="md">${n.html}</article>`;
  }
  const date = new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  const contents = book.toc ? `<nav class="contents"><h2>Contents</h2><ol>${notes.map(n =>
    `<li><a href="#${n.anchor}">${esc(n.title)}</a><span class="where">${esc(n.path)}</span></li>`).join("")}</ol></nav>` : "";
  return `<header class="paper-head cover"><h1>${esc(book.title)}</h1>
<div class="sub">${notes.length} notes · ${esc(date)}</div></header>${contents}` +
    notes.map(n => `<section class="note" id="${n.anchor}"><header class="paper-head"><h1>${esc(n.title)}</h1>${tagsHtml(n.tags)}</header>
<article class="md">${n.html}</article></section>`).join("");
}

async function printPage(title, bodyHtml) {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
  document.body.append(frame);
  const doc = frame.contentDocument;
  doc.open();
  doc.write(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<base href="${esc(location.origin)}/"><title>${esc(title)}</title>
${STYLES.map(s => `<link rel="stylesheet" href="${s}">`).join("\n")}
</head><body>${bodyHtml}</body></html>`);
  doc.close();
  await loaded(frame);
  const win = frame.contentWindow;
  // Chrome and Edge name the PDF after the page that asked to print; use the note's title, not "Recall".
  const appTitle = document.title;
  document.title = title;
  const cleanup = () => { document.title = appTitle; frame.remove(); };
  win.addEventListener("afterprint", () => setTimeout(cleanup, 0), { once: true });
  setTimeout(cleanup, 10 * 60_000);  // if a browser never says printing is over
  toast("Choose “Save as PDF” as the printer.", 5000);
  win.focus();
  win.print();
}

/** Wait for the stylesheets, fonts and every image (a broken one is skipped, not waited on forever). */
async function loaded(frame) {
  const doc = frame.contentDocument;
  if (doc.readyState !== "complete") await new Promise(r => frame.contentWindow.addEventListener("load", r, { once: true }));
  await Promise.all([...doc.images].map(img => img.complete ? null : img.decode().catch(() => {})));
  await doc.fonts?.ready;
}

// ------------------------------------------------------------------ choosing the notes (a folder)

/** Files under `folder` (all of them for ""), in the order of the file list. */
function filesIn(folder) {
  const out = [];
  const walk = n => n.children.forEach(c => c.type === "dir" ? walk(c) : EXPORTABLE.has(c.ext) && out.push(c.path));
  const find = (n, path) => n.path === path ? n : n.children.filter(c => c.type === "dir").map(c => find(c, path)).find(Boolean);
  const start = state.tree && find(state.tree, folder);
  if (start) walk(start);
  return out;
}

export function exportFolder(folder = "") {
  const dlg = $("#export"), f = $("#export-form"), sel = $("#export-folder"), list = $("#export-list");
  const dirs = [""];
  const walk = n => n.children.forEach(c => { if (c.type === "dir") { dirs.push(c.path); walk(c); } });
  if (state.tree) walk(state.tree);
  sel.innerHTML = dirs.map(d => `<option value="${esc(d)}">${esc(folderLabel(d))}</option>`).join("");
  const show = () => {
    const files = filesIn(sel.value);
    f.elements.title.value = sel.value ? sel.value.split("/").pop() : state.tree?.name || "Notes";
    list.innerHTML = files.length ? files.map(p => `<label class="check"><input type="checkbox" value="${esc(p)}" checked>
      <span>${esc(sel.value ? p.slice(sel.value.length + 1) : p)}</span></label>`).join("")
      : `<p class="muted small">No notes to export in this folder.</p>`;
    count();
  };
  const count = () => {
    const n = $$("input:checked", list).length;
    $("#export-ok").disabled = !n;
    $("#export-count").textContent = `${n} of ${$$("input", list).length} selected`;
  };
  sel.value = dirs.includes(folder) ? folder : "";
  sel.onchange = show;
  list.onchange = count;
  $("#export-all").onclick = () => { const boxes = $$("input", list), on = boxes.some(b => !b.checked); boxes.forEach(b => (b.checked = on)); count(); };
  $("#export-cancel").onclick = () => dlg.close();
  f.onsubmit = e => {
    e.preventDefault();
    const paths = $$("input:checked", list).map(b => b.value);
    dlg.close();
    exportNotes(paths, { title: f.elements.title.value.trim(), toc: f.elements.toc.checked });
  };
  show();
  dlg.showModal();
}
