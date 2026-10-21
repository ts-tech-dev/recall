// Link check (Graph view): links in notes that lead nowhere, and notes nothing links to.

import { $, api, esc } from "./util.js";
import { closeEditor, confirmLeaveEditor, openEditor } from "./editor.js";
import { state } from "./state.js";
import { openDoc } from "./viewer.js";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export async function openLinkCheck() {
  const dlg = $("#linkcheck"), out = $("#linkcheck-body");
  out.innerHTML = `<p class="muted">Checking links…</p>`;
  dlg.showModal();
  let r;
  try { r = await api("/api/linkcheck"); } catch (e) { out.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
  const count = r.broken.reduce((n, b) => n + b.links.length, 0);
  out.innerHTML = `
    <h3>Broken links <span class="muted small">${count ? `${plural(count, "link")} in ${plural(r.broken.length, "note")}` : "none"}</span></h3>
    ${r.broken.map(b => `<div class="lc-note"><a href="#" data-path="${esc(b.path)}">${esc(b.path)}</a><ul>${b.links.map(l =>
      `<li><a href="#" data-edit="${esc(b.path)}" data-line="${l.line}" data-link="${esc(l.link)}" title="Open in the editor at this link">
        <span class="muted">line ${l.line}</span> <code>${esc(l.link)}</code></a></li>`).join("")}</ul></div>`).join("") ||
      `<p class="muted small">Every link in your notes leads somewhere.</p>`}
    <h3>Notes nothing links to <span class="muted small">${r.orphans.length || "none"}</span></h3>
    ${r.orphans.length ? `<ul class="lc-orphans">${r.orphans.map(o =>
      `<li><a href="#" data-path="${esc(o.path)}">${esc(o.title)}</a> <span class="muted small">${esc(o.path)}</span></li>`).join("")}</ul>` : ""}`;
}

export function bindLinkCheck() {
  $("#graph-linkcheck").onclick = openLinkCheck;
  $("#linkcheck-close").onclick = () => $("#linkcheck").close();
  $("#linkcheck-body").addEventListener("click", e => {
    const a = e.target.closest("a");
    if (!a) return;
    e.preventDefault();
    if (a.dataset.edit) {
      if (state.editing && state.editing.path !== a.dataset.edit) { if (!confirmLeaveEditor()) return; closeEditor(); }
      $("#linkcheck").close();
      openEditor(a.dataset.edit, { line: +a.dataset.line, find: a.dataset.link });
    } else if (a.dataset.path) {
      $("#linkcheck").close();
      openDoc(a.dataset.path);
    }
  });
}
