// Quick switcher (Ctrl+K): type part of a file's name or folder, Enter opens it. Recently opened notes come first.

import { $, esc, folderOf } from "./util.js";
import { ICONS, state } from "./state.js";
import { openDoc } from "./viewer.js";

const MAX_RESULTS = 50, MAX_RECENT = 30;
let results = [], current = 0;

function recent() { try { return JSON.parse(localStorage.getItem("recent") || "[]"); } catch { return []; } }

/** Remember an opened file for the switcher (called when a note opens). */
export function rememberRecent(path) {
  try { localStorage.setItem("recent", JSON.stringify([path, ...recent().filter(p => p !== path)].slice(0, MAX_RECENT))); } catch {}
}

function files() {
  const out = [];
  const walk = n => n.children.forEach(c => c.type === "dir" ? walk(c) : out.push(c));
  if (state.tree) walk(state.tree);
  return out;
}

/**
 * How well a file matches: every word of the query must be in its name or path, or (for one word) its letters in
 * order in the name ("prjpln" finds "project-plan"). Matches in the name, and at the start of a word, count most.
 * Returns 0 for no match.
 */
export function score(path, query) {
  const name = path.split("/").pop().toLowerCase(), full = path.toLowerCase();
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  let total = 0;
  for (const w of words) {
    const i = name.indexOf(w);
    if (i >= 0) total += 10 + (i === 0 ? 6 : /[\s_\-./(]/.test(name[i - 1]) ? 4 : 0);
    else if (full.includes(w)) total += 3;
    else if (words.length === 1 && subsequence(name, w)) total += 1;
    else return 0;
  }
  return total;
}

function subsequence(text, w) {
  let i = 0;
  for (const ch of text) if (ch === w[i] && ++i === w.length) return true;
  return false;
}

function search(query) {
  const all = files(), rank = new Map(recent().map((p, i) => [p, i]));
  const byRecent = (a, b) => (rank.get(a.path) ?? 1e9) - (rank.get(b.path) ?? 1e9);
  if (!query.trim()) return all.filter(f => rank.has(f.path)).sort(byRecent).slice(0, MAX_RESULTS);
  return all.map(f => ({ ...f, s: score(f.path, query) })).filter(f => f.s)
    .sort((a, b) => b.s - a.s || byRecent(a, b) || a.path.length - b.path.length).slice(0, MAX_RESULTS);
}

function render() {
  const q = $("#switch-q").value;
  results = search(q);
  current = Math.min(current, Math.max(results.length - 1, 0));
  $("#switch-list").innerHTML = results.length ? results.map((f, i) => `
    <li role="option" data-i="${i}" aria-selected="${i === current}">
      <span class="ico badge b-${ICONS[f.ext] || "img"}" aria-hidden="true">${ICONS[f.ext] || "img"}</span>
      <span class="name">${esc(f.name)}</span><span class="where">${esc(folderOf(f.path))}</span></li>`).join("")
    : `<li class="none">${q.trim() ? "No matching files." : "Type to find a note."}</li>`;
}

function move(by) {
  if (!results.length) return;
  current = (current + by + results.length) % results.length;
  $("#switch-list").querySelectorAll("li").forEach((li, i) => li.setAttribute("aria-selected", i === current));
  $(`#switch-list li[data-i="${current}"]`)?.scrollIntoView({ block: "nearest" });
}

function choose(i) {
  const f = results[i];
  if (!f) return;
  $("#switcher").close();
  openDoc(f.path);
}

export function openSwitcher() {
  if (!state.tree) return;
  $("#switch-q").value = "";
  current = 0;
  render();
  $("#switcher").showModal();
  $("#switch-q").focus();
}

export function bindSwitcher() {
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      $("#switcher").open ? $("#switcher").close() : openSwitcher();
    }
  });
  $("#open-switcher").onclick = openSwitcher;
  $("#switch-q").addEventListener("input", () => { current = 0; render(); });
  $("#switch-q").addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { e.preventDefault(); choose(current); }
  });
  $("#switch-list").addEventListener("click", e => { const li = e.target.closest("li[data-i]"); if (li) choose(+li.dataset.i); });
  $("#switcher").addEventListener("click", e => { if (e.target === $("#switcher")) $("#switcher").close(); });  // backdrop
}
