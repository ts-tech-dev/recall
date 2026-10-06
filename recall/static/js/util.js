// Small helpers: DOM lookup, escaping, toasts, API calls and note paths.

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const H = { "X-Recall": "1", "Content-Type": "application/json" };

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// Must match recall/chunker.py slugify()
export function slugify(s) {
  return s.toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu, "").trim().replace(/\s+/g, "-");
}
export function snippetHtml(s) {
  s = String(s ?? "").replace(/\s*\[image: [^\]]*\]/g, "").replace(/\*\*/g, "");
  return esc(s).replaceAll("\x02", "<mark>").replaceAll("\x03", "</mark>");
}
export function toast(msg, ms = 2500) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
export async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

export const folderOf = path => path.includes("/") ? path.replace(/\/[^/]*$/, "") : "";
export const joinPath = (...parts) => parts.map(p => p.replace(/^\/+|\/+$/g, "")).filter(Boolean).join("/");
export const withExt = name => /\.[^./]+$/.test(name) ? name : name + ".md";
export const folderLabel = folder => folder ? folder.replaceAll("/", " / ") : "(top level)";
