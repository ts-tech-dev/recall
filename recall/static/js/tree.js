// Sidebar: file tree, sidebar search, and dragging files onto folders.

import { $, $$, api, esc, folderOf, snippetHtml, toast } from "./util.js";
import { ICONS, state } from "./state.js";
import { openSettings } from "./settings.js";
import { moveFile, newNote } from "./files.js";
import { openDoc } from "./viewer.js";

export async function loadTree() {
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

export function renderTree() {
  const filter = $("#tree-filter").value.trim().toLowerCase();
  const collapsed = JSON.parse(localStorage.getItem("collapsed") || "[]");
  const build = node => {
    const items = [];
    for (const c of node.children) {
      if (c.type === "dir") {
        const inner = build(c);
        if (!inner && filter) continue;
        const cls = !filter && collapsed.includes(c.path) ? "dir collapsed" : "dir";
        items.push(`<li class="${cls}" data-dir="${esc(c.path)}"><div class="node" role="treeitem"><span class="ico chev" aria-hidden="true"></span><span class="name">${esc(c.name)}</span><button class="add" data-add="${esc(c.path)}" title="New note in ${esc(c.path)}" aria-label="New note in ${esc(c.path)}">+</button></div>${inner || "<ul></ul>"}</li>`);
      } else {
        if (filter && !c.path.toLowerCase().includes(filter)) continue;
        const active = state.currentDoc === c.path ? " active" : "";
        items.push(`<li><div class="node${active}" role="treeitem" draggable="true" data-path="${esc(c.path)}" title="${esc(c.path)}"><span class="ico badge b-${ICONS[c.ext] || "img"}" aria-hidden="true">${ICONS[c.ext] || "img"}</span><span class="name">${esc(c.name)}</span></div></li>`);
      }
    }
    return items.length ? `<ul>${items.join("")}</ul>` : "";
  };
  $("#tree").innerHTML = build(state.tree) || `<div class="tree-empty">${filter ? "No matching files." : "No supported files found in this folder."}</div>`;
}

export function fillFolderFilter() {
  const sel = $("#folder-filter"), cur = sel.value, dirs = [];
  const walk = n => n.children.forEach(c => { if (c.type === "dir") { dirs.push(c.path); walk(c); } });
  walk(state.tree);
  sel.innerHTML = `<option value="">All folders</option>` + dirs.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
  sel.value = dirs.includes(cur) ? cur : "";
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
    if (add) return newNote(add.dataset.add);
    const node = e.target.closest(".node");
    if (!node) return;
    if (node.dataset.path) return openDoc(node.dataset.path);
    const li = node.parentElement;
    li.classList.toggle("collapsed");
    const set = new Set(JSON.parse(localStorage.getItem("collapsed") || "[]"));
    li.classList.contains("collapsed") ? set.add(li.dataset.dir) : set.delete(li.dataset.dir);
    localStorage.setItem("collapsed", JSON.stringify([...set]));
  });
  bindTreeDrag();
  $("#tree-filter").oninput = renderTree;
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
