// Note editor: live preview, saving with conflict checks, history and image uploads.

import { $, H, api, toast } from "./util.js";
import { state } from "./state.js";
import { renderMarkdown } from "./markdown.js";
import { setTask } from "./tasks.js";
import { openDoc, showView } from "./viewer.js";

export function confirmLeaveEditor() {
  return !isDirty() || confirm("You have unsaved changes. Discard them?");
}
export function isDirty() {
  return !!state.editing && $("#ed-text").value !== state.editing.saved;
}
export function setEditorState(msg) {
  $("#ed-state").textContent = msg ?? (isDirty() ? "● unsaved changes" : "saved");
}

export async function openEditor(path) {
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

export function closeEditor() {
  state.editing = null;
  $("#editor").hidden = true;
}

let previewTimer;
export function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(renderEditorPreview, 300);
}
/** A checkbox in the editor preview was clicked: change the text being edited (saved with Save). */
function toggleTaskInEditor(index, checked, count) {
  const ta = $("#ed-text"), next = setTask(ta.value, index, checked, count);
  if (next === null) { toast("Couldn't match this checkbox to a line in the text."); return false; }
  const { selectionStart: a, selectionEnd: b } = ta;
  ta.value = next;
  ta.setSelectionRange(a, b);
  setEditorState();
  return true;
}

export async function renderEditorPreview() {
  if (!state.editing || !$("#ed-preview-toggle").checked) return;
  const path = state.editing.path, md = $("#ed-text").value;
  if (path.toLowerCase().endsWith(".txt")) {
    $("#ed-preview").innerHTML = `<pre class="plain"></pre>`;
    $("#ed-preview pre").textContent = md;
    return;
  }
  try {
    const r = await api("/api/render", { method: "POST", headers: H, body: JSON.stringify({ path, markdown: md }) });
    if (state.editing?.path === path) renderMarkdown($("#ed-preview"), r.markdown, { onTask: toggleTaskInEditor });
  } catch {}
}

export async function saveEditor(force = false) {
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
export async function checkEditorDisk() {
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

export async function loadVersions() {
  const sel = $("#ed-versions"), path = state.editing?.path;
  if (!path) return;
  try {
    const { versions } = await api("/api/versions?path=" + encodeURIComponent(path));
    sel.innerHTML = `<option value="">History (${versions.length})…</option>` +
      versions.map(v => `<option value="${v.id}">${new Date(v.saved_at * 1000).toLocaleString()}</option>`).join("");
  } catch {}
}

export async function uploadImages(files) {
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

export function insertAtCursor(ta, text) {
  ta.focus();
  const { selectionStart: a, selectionEnd: b, value } = ta;
  const pre = a > 0 && value[a - 1] !== "\n" ? "\n" : "";
  ta.setRangeText(pre + text, a, b, "end");
  ta.dispatchEvent(new Event("input"));
}

export function bindEditor() {
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
}
