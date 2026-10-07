// Markdown rendering: marked (GitHub-flavored, plus the extensions in md/extensions.js, footnotes and KaTeX math),
// sanitized with DOMPurify, then enhanced in the page (md/enhance.js) and given working task checkboxes (tasks.js).

import { $$ } from "./util.js";
import { state } from "./state.js";
import { emojiExtension, extensions } from "./md/extensions.js";
import { callouts, codeBlocks, headingIds, imageSizes, links, tags } from "./md/enhance.js";
import { enableTasks } from "./tasks.js";

const emoji = await fetch("/static/vendor/emoji.json").then(r => r.json()).catch(() => ({}));

/**
 * marked asks every block extension where it could start before each paragraph, passing the whole rest of the
 * document. Searching all of it makes long notes slow (quadratic: 9 s for a 750 KB PDF text), and only the
 * current paragraph matters, since an extension can only interrupt that one. So each search stops at the next
 * blank line.
 */
function withinParagraph(options) {
  const exts = (options.extensions || []).map(ext => ext.level !== "block" || !ext.start ? ext : {
    ...ext,
    start(src) {
      const blank = /\n[ \t]*\n/.exec(src);
      return ext.start.call(this, blank ? src.slice(0, blank.index + 1) : src);
    },
  });
  return { ...options, extensions: exts };
}

function makeParser(footnotes) {
  const m = new marked.Marked({ gfm: true, breaks: false });
  m.use(withinParagraph({ extensions: [...extensions, emojiExtension(emoji)] }));
  if (window.markedKatex) m.use(withinParagraph(markedKatex({ throwOnError: false, output: "htmlAndMathml" })));
  if (footnotes && window.markedFootnote) m.use(withinParagraph(markedFootnote({ description: "Footnotes" })));
  return m;
}
// The footnote extension slows every parse (~2 s on a 750 KB note), so it's only used when a note has "[^".
const parser = makeParser(false), footnoteParser = makeParser(true);
const parse = md => (md.includes("[^") ? footnoteParser : parser).parse(md);

/** Mark the checkboxes of task list items and bare "[ ]" lines (in document order). */
function markTasks(el) {
  const boxes = $$("li > input[type=checkbox]:first-child, li > p:first-child > input[type=checkbox]:first-child, " +
    "p.task-line > input[type=checkbox]:first-child", el);
  for (const box of boxes) {
    box.classList.add("task");
    const item = box.closest("li, p.task-line");
    item.classList.add(item.tagName === "LI" ? "task-item" : "task-line");
    item.classList.toggle("done", box.checked);
    item.parentElement.closest("ul, ol")?.classList.add("task-list");
    if (item.tagName === "LI") item.parentElement.classList.add("task-list");
  }
}

/**
 * Render Markdown safely into `el`.
 * `answer`: an Ask result (only images served by this app, [n] citations become links).
 * `onTask(index, checked, count)`: make task checkboxes clickable; it saves and returns true, or false to undo.
 */
export function renderMarkdown(el, md, { answer = false, onTask = null } = {}) {
  el.innerHTML = DOMPurify.sanitize(parse(md), { ADD_ATTR: ["target"] });
  if (answer) {
    $$("img", el).forEach(img => { if (!(img.getAttribute("src") || "").startsWith("/api/")) img.remove(); });
    linkCitations(el);
  }
  headingIds(el);
  callouts(el);
  markTasks(el);
  imageSizes(el);
  codeBlocks(el);
  tags(el);
  links(el);
  if (onTask) enableTasks(el, onTask);
}

/** Turn [3] / [2][4] in answer text into clickable source chips (skips code). */
export function linkCitations(el) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest("code,pre,a") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) if (/\[\d+(?:\s*,\s*\d+)*\]/.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    const text = node.nodeValue;
    for (const m of text.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
      frag.append(text.slice(last, m.index));
      for (const n of m[1].split(",").map(x => x.trim())) {
        const src = state.sources.find(s => s.n === +n);
        if (!src) { frag.append(`[${n}]`); continue; }
        const a = document.createElement("a");
        a.className = "cite"; a.href = "#"; a.dataset.n = n; a.textContent = n;
        a.title = `${src.title} — ${src.heading}`;
        frag.append(a);
      }
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}
