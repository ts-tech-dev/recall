// Index status bar, polling, and reacting when files are re-indexed.

import { $, H, api, esc, toast } from "./util.js";
import { state } from "./state.js";
import { loadTree } from "./tree.js";
import { openDoc } from "./viewer.js";
import { checkEditorDisk } from "./editor.js";
import { renderGraph } from "./graph.js";

export const PHASES = { reading: "Indexing", embedding: "Building smart search", captioning: "Describing images" };

export async function refreshStatus() {
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
export function onIndexChanged(changed) {
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

export function bindStatus() {
  $("#reindex").onclick = async () => {
    try { await api("/api/index", { method: "POST", headers: H }); toast("Re-indexing…"); setTimeout(refreshStatus, 300); }
    catch (e) { toast(e.message); }
  };
}
