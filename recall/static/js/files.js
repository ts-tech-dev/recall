// New notes and moving files, with the folder picker dialog.

import { $, H, api, esc, folderLabel, folderOf, joinPath, toast, withExt } from "./util.js";
import { state } from "./state.js";
import { openSettings } from "./settings.js";
import { loadTree } from "./tree.js";
import { openDoc } from "./viewer.js";
import { setScope } from "./ask.js";
import { closeEditor, confirmLeaveEditor, openEditor } from "./editor.js";

/**
 * Show the folder picker. `action({folder, name})` runs on OK; if it throws, the error is shown and the
 * dialog stays open. `withName` adds a note name field; `file` is the name of a file being moved.
 */
export async function pickPlace({ title, ok, folder = "", withName = false, file = "", action }) {
  let folders;
  try { folders = (await api("/api/folders")).folders; } catch (e) { toast(e.message); return; }
  const dlg = $("#place"), f = $("#place-form"), sel = $("#place-folder");
  $("#place-title").textContent = title;
  $("#place-ok").textContent = ok;
  sel.innerHTML = ["", ...folders].map(d => `<option value="${esc(d)}">${esc(folderLabel(d))}</option>`).join("");
  sel.value = folders.includes(folder) ? folder : "";
  f.elements.newdir.value = f.elements.name.value = "";
  $("#place-newdir-row").hidden = true; $("#place-newdir-btn").hidden = false;
  $("#place-name-row").hidden = !withName;
  $("#place-error").textContent = "";

  const target = () => joinPath(sel.value, f.elements.newdir.value.trim());
  const update = () => {
    $("#place-newdir-hint").textContent = `Created inside ${folderLabel(sel.value)}.`;
    const name = withName ? withExt(f.elements.name.value.trim() || "untitled") : file;
    $("#place-dest").textContent = `${withName ? "Saves as" : "New location:"} ${joinPath(target(), name)}`;
    $("#place-error").textContent = "";
  };
  f.oninput = f.onchange = update;
  $("#place-newdir-btn").onclick = () => {
    $("#place-newdir-row").hidden = false; $("#place-newdir-btn").hidden = true;
    f.elements.newdir.focus(); update();
  };
  $("#place-cancel").onclick = () => dlg.close();
  f.onsubmit = async e => {
    e.preventDefault();
    const name = f.elements.name.value.trim();
    if (withName && !name) { $("#place-error").textContent = "Give the note a name."; return; }
    try { await action({ folder: target(), name }); dlg.close(); }
    catch (err) { $("#place-error").textContent = err.message; }
  };
  dlg.showModal();
  (withName ? f.elements.name : sel).focus();
  update();
}

export async function newNote(folder) {
  if (!state.status?.notes_dir) return openSettings();
  if (state.editing && !confirmLeaveEditor()) return;
  let last = "";
  try { last = localStorage.getItem("noteFolder") || ""; } catch {}
  pickPlace({
    title: "New note", ok: "Create", withName: true,
    folder: folder ?? (state.currentDoc ? folderOf(state.currentDoc) : last),
    action: async ({ folder, name }) => {
      const r = await api("/api/note", { method: "POST", headers: H, body: JSON.stringify({ path: joinPath(folder, withExt(name)) }) });
      try { localStorage.setItem("noteFolder", folder); } catch {}
      closeEditor();
      await loadTree();
      openEditor(r.path);
    },
  });
}

/** Move a file to another folder; the server keeps relative links to and from it working. */
export async function moveFile(path, folder) {
  if (state.editing?.path === path) throw new Error("Close the editor before moving this note.");
  const r = await api("/api/move", { method: "POST", headers: H, body: JSON.stringify({ path, folder }) });
  if (state.scopePath === path) setScope(r.path);
  if (state.currentDoc === path) openDoc(r.path, "", { force: true });
  await loadTree();
  const n = r.updated.length;
  toast(`Moved to ${folder || "the top level"}` + (n ? ` · updated links in ${n} note${n > 1 ? "s" : ""}` : ""), 3500);
}

export function moveCurrentDoc() {
  const path = state.currentDoc;
  if (!path) return;
  if (state.editing?.path === path) return toast("Close the editor before moving this note.");
  const file = path.split("/").pop();
  pickPlace({ title: `Move ${file}`, ok: "Move", folder: folderOf(path), file, action: ({ folder }) => {
    if (folder === folderOf(path)) throw new Error("It's already in that folder.");
    return moveFile(path, folder);
  } });
}
