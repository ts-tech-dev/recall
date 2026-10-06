// Ask view: questions, streamed answers, sources and history.

import { $, H, esc, snippetHtml, toast } from "./util.js";
import { TYPE_LABELS, state } from "./state.js";
import { renderMarkdown } from "./markdown.js";
import { openDoc, showView } from "./viewer.js";

export function renderTypeChips() {
  const groups = Object.keys(state.status?.type_groups || TYPE_LABELS);
  $("#type-chips").innerHTML = groups.map(g =>
    `<button type="button" class="chip" data-type="${g}" aria-pressed="${state.types.has(g)}">${TYPE_LABELS[g] || g}</button>`).join("");
}

export function setScope(path) {
  state.scopePath = path;
  const el = $("#doc-scope");
  el.hidden = !path;
  el.innerHTML = path ? `Only: ${esc(path.split("/").pop())} <button type="button" title="Search all notes" aria-label="Clear">✕</button>` : "";
}

export async function ask(question, mode) {
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

export function renderSources() {
  $("#sources").innerHTML = state.sources.length ? state.sources.map(s => `
    <li class="source" data-n="${s.n}" data-path="${esc(s.path)}" data-anchor="${esc(s.anchor)}">
      <div><span class="n">${s.n}</span><span class="t">${esc(s.title)}</span></div>
      <div class="h">${esc(s.path)} › ${esc(s.heading)}</div>
      <div>${snippetHtml(s.snippet)}</div>
      ${s.images.length ? `<div class="thumbs">${s.images.slice(0, 4).map(i => `<img src="${esc(i.url)}" alt="${esc(i.alt)}" loading="lazy">`).join("")}</div>` : ""}
    </li>`).join("") : `<li class="muted small">No matching notes.</li>`;
}

// ------------------------------------------------------------------ history (per browser)

export function loadHistory() { try { return JSON.parse(localStorage.getItem("history") || "[]"); } catch { return []; } }
export function saveHistory(item) {
  const h = loadHistory().filter(x => !(x.question === item.question && x.mode === item.mode));
  h.unshift(item);
  try { localStorage.setItem("history", JSON.stringify(h.slice(0, 25))); } catch {}
  renderHistory();
}
export function renderHistory() {
  const h = loadHistory();
  $("#history-wrap").hidden = !h.length;
  $("#history").innerHTML = h.map((x, i) => `<li><a data-i="${i}">${esc(x.question)}</a>
    <span class="muted small">${x.mode === "report" ? "report" : "summary"} · ${new Date(x.at).toLocaleDateString()}</span></li>`).join("");
}
export function showHistory(i) {
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

export function bindAsk() {
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
}
