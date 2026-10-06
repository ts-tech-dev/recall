// Post-processing of rendered (and sanitized) Markdown: heading ids, callouts, #tags, image sizes,
// code highlighting, ```math blocks, Mermaid diagrams and link behavior. Each step is one function.

import { $$, slugify } from "../util.js";

/** Heading ids for the outline and #links. "## Title {#custom-id}" sets the id explicitly. */
export function headingIds(el) {
  const seen = {};
  $$("h1,h2,h3,h4,h5,h6", el).forEach(h => {
    let id;
    const last = h.lastChild;
    const m = last?.nodeType === Node.TEXT_NODE && /\s*\{#([\w-]+)\}\s*$/.exec(last.nodeValue);
    if (m) { last.nodeValue = last.nodeValue.slice(0, m.index); id = m[1]; }
    else id = slugify(h.textContent);
    if (seen[id] !== undefined) id += "-" + (++seen[id]); else seen[id] = 0;
    h.id = "h-" + id;
  });
}

const CALLOUT_KINDS = {
  note: "note", info: "note", todo: "note",
  abstract: "abstract", summary: "abstract", tldr: "abstract",
  tip: "tip", hint: "tip", important: "important",
  success: "success", check: "success", done: "success",
  question: "question", help: "question", faq: "question",
  warning: "warning", caution: "warning", attention: "warning",
  failure: "danger", fail: "danger", missing: "danger", danger: "danger", error: "danger", bug: "danger",
  example: "example", quote: "quote", cite: "quote",
};
const CALLOUT_ICONS = { note: "ℹ️", abstract: "📋", tip: "💡", important: "❗", success: "✅", question: "❓",
  warning: "⚠️", danger: "⛔", example: "🧪", quote: "❝" };

/** Obsidian callouts and GitHub alerts: "> [!note] Title", "> [!WARNING]", foldable with [!tip]- / [!tip]+. */
export function callouts(el) {
  for (const bq of $$("blockquote", el)) {
    const p = bq.firstElementChild;
    const text = p?.tagName === "P" && p.firstChild?.nodeType === Node.TEXT_NODE ? p.firstChild : null;
    const m = text && /^\[!([\w-]+)\]([+-]?)[ \t]*([^\n]*)\n?/.exec(text.nodeValue);
    if (!m) continue;
    const type = m[1].toLowerCase(), kind = CALLOUT_KINDS[type] || "note";
    text.nodeValue = text.nodeValue.slice(m[0].length);
    if (p.firstChild?.nodeName === "BR") p.firstChild.remove();
    if (!p.textContent.trim() && !p.querySelector("img")) p.remove();

    const box = document.createElement(m[2] ? "details" : "div");
    box.className = `callout callout-${kind}`;
    if (m[2] === "+") box.open = true;
    const title = document.createElement(m[2] ? "summary" : "div");
    title.className = "callout-title";
    title.textContent = `${CALLOUT_ICONS[kind]} ${m[3].trim() || type[0].toUpperCase() + type.slice(1)}`;
    const body = document.createElement("div");
    body.className = "callout-body";
    body.append(...bq.childNodes);
    box.append(title);
    if (body.textContent.trim() || body.querySelector("img")) box.append(body);
    bq.replaceWith(box);
  }
}

/** Inline #tags become chips (click: search for the tag). Not inside code, links or headings. */
export function tags(el) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest("code,pre,a,h1,h2,h3,h4,h5,h6,.katex,.mermaid")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const re = /(^|\s)#([\p{L}\p{N}_/-]*[\p{L}_][\p{L}\p{N}_/-]*)/gu;
  const nodes = [];
  while (walker.nextNode()) if (re.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment(), text = node.nodeValue;
    let last = 0;
    for (const m of text.matchAll(re)) {
      frag.append(text.slice(last, m.index) + m[1]);
      const a = document.createElement("a");
      a.className = "tag"; a.href = "#"; a.dataset.tag = m[2]; a.textContent = "#" + m[2];
      frag.append(a);
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

/** "![alt|300](x.png)" or "![alt|300x200](x.png)" sets the image size (Obsidian style). */
export function imageSizes(el) {
  for (const img of $$("img", el)) {
    const m = /^(.*?)\|\s*(\d+)(?:\s*x\s*(\d+))?\s*$/.exec(img.getAttribute("alt") || "");
    if (!m) continue;
    img.alt = m[1].trim();
    img.width = +m[2];
    if (m[3]) img.height = +m[3];
    img.classList.add("sized");
  }
}

/** Syntax highlighting, ```math blocks (KaTeX) and ```mermaid diagrams. */
export function codeBlocks(el) {
  const diagrams = [];
  for (const code of $$("pre > code", el)) {
    const lang = (/language-(\S+)/.exec(code.className) || [])[1];
    if (lang === "mermaid") {
      const div = document.createElement("div");
      div.className = "mermaid";
      div.textContent = code.textContent;
      code.parentElement.replaceWith(div);
      diagrams.push(div);
    } else if (lang === "math" && window.katex) {
      const div = document.createElement("div");
      div.className = "math-block";
      try { katex.render(code.textContent, div, { displayMode: true, throwOnError: false }); code.parentElement.replaceWith(div); }
      catch { /* leave the source visible */ }
    } else {
      try { hljs.highlightElement(code); } catch {}
    }
  }
  if (diagrams.length) renderDiagrams(diagrams);
}

let mermaidReady = null;
function loadMermaid() {
  mermaidReady ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/static/vendor/mermaid.min.js";  // large, so only loaded when a note has a diagram
    s.onload = () => {
      const dark = matchMedia("(prefers-color-scheme: dark)").matches;
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default" });
      resolve(window.mermaid);
    };
    s.onerror = reject;
    document.head.append(s);
  });
  return mermaidReady;
}

async function renderDiagrams(nodes) {
  try {
    const m = await loadMermaid();
    await m.run({ nodes: nodes.filter(n => n.isConnected), suppressErrors: true });
  } catch (e) {
    nodes.forEach(n => n.classList.add("mermaid-error"));
    console.warn("Mermaid failed:", e);
  }
}

/** External links open in a new tab. */
export function links(el) {
  for (const a of $$("a[href]", el)) {
    if (/^https?:/.test(a.getAttribute("href"))) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
  }
}
