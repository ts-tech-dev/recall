// Sidebar: file tree, sidebar search, and dragging files onto folders.

import { $, $$, api, esc, folderOf, snippetHtml, toast } from "./util.js";
import { ICONS, state } from "./state.js";
import { openSettings } from "./settings.js";
import { moveFile, newNote } from "./files.js";
import { openDoc } from "./viewer.js";
import { exportFolder } from "./export.js";

export async function loadTree() {
  $("#tree-count").textContent = "";
  if (!state.status?.notes_dir) {
    $("#tree").innerHTML = `<div class="tree-empty">Choose a notes folder in <a href="#" id="tree-settings">Settings</a>.</div>`;
    $("#tree-settings").onclick = e => { e.preventDefault(); openSettings(); };
    return;
  }
  try {
    state.tree = await api("/api/tree" + ($("#tree-images").checked ? "?images=true" : ""));
  } catch (e) { $("#tree").innerHTML = `<div class="tree-empty">${esc(e.message)}</div>`; return; }
  renderTree();
  const root = $("#doc-path a[data-folder='']");  // a note opened before the list arrived: name the top folder
  if (root) root.textContent = state.tree.name;
  fillFolderFilter();
  fillTagFilter();
}

function collapsedSet() {
  try { return new Set(JSON.parse(localStorage.getItem("collapsed") || "[]")); } catch { return new Set(); }
}
function saveCollapsed(set) {
  try { localStorage.setItem("collapsed", JSON.stringify([...set])); } catch {}
}

/** `text` escaped, with the first match of `filter` (lowercase) marked. */
function withMatch(text, filter) {
  const i = filter ? text.toLowerCase().indexOf(filter) : -1;
  if (i < 0) return esc(text);
  return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + filter.length)) + "</mark>" + esc(text.slice(i + filter.length));
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * The file list. Folders have an icon, the number of files inside and a guide line down the side of their contents.
 * A folder's row sticks to the top while its contents scroll past, so the folder you're in stays in view, and the
 * folders that hold the open note are highlighted, even when collapsed.
 */
export function renderTree() {
  const filter = $("#tree-filter").value.trim().toLowerCase();
  const collapsed = collapsedSet();
  const current = state.currentDoc || "";
  const build = (node, depth) => {
    const items = [];
    let count = 0;
    for (const c of node.children) {
      if (c.type === "dir") {
        const inner = build(c, depth + 1);
        if (!inner.count && filter) continue;
        count += inner.count;
        const cls = ["dir", !filter && collapsed.has(c.path) && "collapsed", current.startsWith(c.path + "/") && "has-active"]
          .filter(Boolean).join(" ");
        items.push(`<li class="${cls}" data-dir="${esc(c.path)}"><div class="node" role="treeitem" style="--d:${depth}" title="${esc(c.path)}">` +
          `<span class="chev" aria-hidden="true"></span><span class="ico folder" aria-hidden="true"></span>` +
          `<span class="name">${withMatch(c.name, filter)}</span><span class="count" title="${plural(inner.count, "file")}">${inner.count}</span>` +
          `<span class="actions"><button class="add pdf" data-export="${esc(c.path)}" title="Export notes in ${esc(c.path)} as one PDF" aria-label="Export notes in ${esc(c.path)} as one PDF">PDF</button>` +
          `<button class="add" data-add="${esc(c.path)}" title="New note in ${esc(c.path)}" aria-label="New note in ${esc(c.path)}">+</button></span></div>` +
          `${inner.html || "<ul></ul>"}</li>`);
      } else {
        if (filter && !c.path.toLowerCase().includes(filter)) continue;
        count++;
        const active = current === c.path ? " active" : "";
        items.push(`<li><div class="node file${active}" role="treeitem" draggable="true" data-path="${esc(c.path)}" style="--d:${depth}" title="${esc(c.path)}">` +
          `<span class="ico badge b-${ICONS[c.ext] || "img"}" aria-hidden="true">${ICONS[c.ext] || "img"}</span><span class="name">${withMatch(c.name, filter)}</span></div></li>`);
      }
    }
    return { html: items.length ? `<ul>${items.join("")}</ul>` : "", count };
  };
  const { html, count } = build(state.tree, 0);
  $("#tree").innerHTML = html || `<div class="tree-empty">${filter ? "No matching files." : "No supported files found in this folder."}</div>`;
  $("#tree-count").textContent = filter ? `${plural(count, "file")} found` : plural(count, "file");
}

/** Highlight `path` (the open note) and the folders it's in; with `reveal`, open those folders and scroll to it. */
export function markActive(path, { reveal = true } = {}) {
  const tree = $("#tree");
  $$(".node.active", tree).forEach(n => n.classList.remove("active"));
  $$(".dir.has-active", tree).forEach(n => n.classList.remove("has-active"));
  const node = path && $$(".node[data-path]", tree).find(n => n.dataset.path === path);
  if (!node) return;
  node.classList.add("active");
  const set = collapsedSet();
  let opened = false;
  for (let li = node.closest("li.dir"); li; li = li.parentElement.closest("li.dir")) {
    li.classList.add("has-active");
    if (reveal && li.classList.contains("collapsed")) { li.classList.remove("collapsed"); set.delete(li.dataset.dir); opened = true; }
  }
  if (opened) saveCollapsed(set);
  if (reveal && !tree.hidden) node.scrollIntoView({ block: "nearest" });
}

/** Show the whole file list: clear the name filter and the search results. */
function showWholeTree() {
  if ($("#tree-filter").value) { $("#tree-filter").value = ""; renderTree(); }
  if (!$("#side-results").hidden) { $("#side-q").value = ""; sideSearch(""); }
}

/** Open a folder (and the folders around it) in the file list, scroll to it and flash it ("" = the top). */
export function revealFolder(folder) {
  showWholeTree();
  $("#sidebar").classList.add("open");  // narrow screens: the file list slides in
  const tree = $("#tree");
  const li = folder && $(`li[data-dir="${CSS.escape(folder)}"]`, tree);
  if (!li) { tree.scrollTop = 0; return; }
  const set = collapsedSet();
  for (let d = li; d; d = d.parentElement.closest("li.dir")) { d.classList.remove("collapsed"); set.delete(d.dataset.dir); }
  saveCollapsed(set);
  const node = li.firstElementChild;
  node.scrollIntoView({ block: "start" });
  node.classList.add("flash"); setTimeout(() => node.classList.remove("flash"), 1600);
}

/** Collapse or expand every folder. */
function setAllCollapsed(collapse) {
  const dirs = $$("li.dir", $("#tree"));
  const set = new Set(collapse ? dirs.map(li => li.dataset.dir) : []);
  saveCollapsed(set);
  dirs.forEach(li => li.classList.toggle("collapsed", set.has(li.dataset.dir)));
}

export function fillFolderFilter() {
  const sel = $("#folder-filter"), cur = sel.value, dirs = [];
  const walk = n => n.children.forEach(c => { if (c.type === "dir") { dirs.push(c.path); walk(c); } });
  walk(state.tree);
  sel.innerHTML = `<option value="">All folders</option>` + dirs.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
  sel.value = dirs.includes(cur) ? cur : "";
}

/** Tags for the Ask filter. A nested tag ("project/alpha") is also found by choosing its parent ("project"). */
export async function fillTagFilter() {
  let tags;
  try { tags = (await api("/api/tags")).tags; } catch { return; }
  const sel = $("#tag-filter"), cur = sel.value;
  sel.innerHTML = `<option value="">All tags</option>` +
    tags.map(t => `<option value="${esc(t.tag)}">#${esc(t.tag)} (${t.count})</option>`).join("");
  sel.value = tags.some(t => t.tag === cur) ? cur : "";
  sel.hidden = !tags.length;
}

// ------------------------------------------------------------------ sidebar search

export async function sideSearch(q) {
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

export function bindTreeDrag() {
  const tree = $("#tree"), MIME = "application/x-recall-path";
  const clear = () => { $$(".drop-target", tree).forEach(n => n.classList.remove("drop-target")); tree.classList.remove("drop-root"); };
  const dropFolder = e => {
    const node = e.target.closest(".node");
    if (!node) return "";
    return node.dataset.path ? folderOf(node.dataset.path) : node.parentElement.dataset.dir;
  };
  tree.addEventListener("dragstart", e => {
    const node = e.target.closest(".node[data-path]");
    if (!node) return;
    e.dataTransfer.setData(MIME, node.dataset.path);
    e.dataTransfer.effectAllowed = "move";
  });
  tree.addEventListener("dragover", e => {
    if (!e.dataTransfer.types.includes(MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const folder = dropFolder(e);
    clear();
    if (!folder) tree.classList.add("drop-root");
    else $(`li[data-dir="${CSS.escape(folder)}"] > .node`, tree)?.classList.add("drop-target");
  });
  tree.addEventListener("dragleave", e => { if (!tree.contains(e.relatedTarget)) clear(); });
  tree.addEventListener("dragend", clear);
  tree.addEventListener("drop", e => {
    const path = e.dataTransfer.getData(MIME);
    if (!path) return;
    e.preventDefault();
    clear();
    const folder = dropFolder(e);
    if (folder !== folderOf(path)) moveFile(path, folder).catch(err => toast(err.message));
  });
}

export function bindTree() {
  $("#toggle-sidebar").onclick = () => $("#sidebar").classList.toggle("open");
  $("#tree").addEventListener("click", e => {
    const add = e.target.closest(".add");
    if (add) return add.dataset.export !== undefined ? exportFolder(add.dataset.export) : newNote(add.dataset.add);
    const node = e.target.closest(".node");
    if (!node) return;
    if (node.dataset.path) return openDoc(node.dataset.path);
    const li = node.parentElement;
    li.classList.toggle("collapsed");
    const set = collapsedSet();
    li.classList.contains("collapsed") ? set.add(li.dataset.dir) : set.delete(li.dataset.dir);
    saveCollapsed(set);
  });
  bindTreeDrag();
  $("#tree-filter").oninput = renderTree;
  $("#tree-collapse").onclick = () => setAllCollapsed(true);
  $("#tree-expand").onclick = () => setAllCollapsed(false);
  $("#tree-reveal").onclick = () => {
    if (!state.currentDoc) return toast("Open a note first");
    showWholeTree();
    markActive(state.currentDoc);
  };
  $("#tree-images").onchange = loadTree;
  $("#new-note").onclick = () => newNote();
  $("#side-q").addEventListener("keydown", e => { if (e.key === "Enter") sideSearch(e.target.value.trim()); });
  $("#side-q").addEventListener("input", e => { if (!e.target.value) sideSearch(""); });
  $("#side-mode").onchange = () => { const q = $("#side-q").value.trim(); if (q) sideSearch(q); };
  $("#side-results").addEventListener("click", e => {
    const r = e.target.closest(".result");
    if (r) openDoc(r.dataset.path, r.dataset.anchor);
  });
}
