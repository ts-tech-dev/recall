// "Export PDF": the note is rendered again in the light theme into a hidden page laid out for paper
// (css/print.css), with every image loaded and every diagram drawn, then the browser's print dialog opens on it,
// where "Save as PDF" writes the file. The browser does the layout, so math, code and diagrams come out
// exactly as in the preview, as selectable text with working links.

import { $$, api, esc, toast } from "./util.js";
import { renderMarkdown } from "./markdown.js";

const STYLES = ["/static/vendor/katex/katex.min.css", "/static/vendor/hljs-github.min.css",
  "/static/css/markdown.css", "/static/css/print.css"];
const PAGE_WIDTH = 680;  // px, about the width of the text on an A4 or Letter page, for drawing diagrams

let busy = false;

export async function exportPdf(path) {
  if (busy) return;
  busy = true;
  toast("Preparing the PDF…", 10000);
  try {
    const d = await api("/api/doc?path=" + encodeURIComponent(path));
    const body = await renderForPaper(d.markdown);
    await printPage(d, body);
  } catch (e) {
    toast("Couldn't export the PDF: " + e.message, 5000);
  } finally {
    busy = false;
  }
}

/** Render the note off screen (diagrams need a laid-out page to measure their text) and return its HTML. */
async function renderForPaper(md) {
  const box = document.createElement("article");
  box.className = "md";
  box.style.cssText = `position:fixed;left:-10000px;top:0;width:${PAGE_WIDTH}px;visibility:hidden`;
  document.body.append(box);
  try {
    await renderMarkdown(box, md, { light: true });
    for (const img of $$("img", box)) img.removeAttribute("loading");  // lazy images would never load off screen
    for (const det of $$("details", box)) det.open = true;  // folded callouts and <details> are printed open
    // Links to other notes and #tag searches mean nothing on paper: keep their text only.
    for (const a of $$("a[href^='#doc='], a.tag", box)) {
      const span = document.createElement("span");
      span.className = a.className;
      span.append(...a.childNodes);
      a.replaceWith(span);
    }
    return box.innerHTML;
  } finally {
    box.remove();
  }
}

async function printPage(d, bodyHtml) {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
  document.body.append(frame);
  const doc = frame.contentDocument;
  // A heading at the top that repeats the title isn't shown twice.
  const first = /^\s*<h1[^>]*>([\s\S]*?)<\/h1>/.exec(bodyHtml);
  if (first && first[1].replace(/<[^>]+>/g, "").trim() === d.title.trim()) bodyHtml = bodyHtml.slice(first[0].length);
  const tags = (d.tags || []).map(t => `<span class="tag">#${esc(t)}</span>`).join(" ");
  doc.open();
  doc.write(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<base href="${esc(location.origin)}/"><title>${esc(d.title)}</title>
${STYLES.map(s => `<link rel="stylesheet" href="${s}">`).join("\n")}
</head><body>
<header class="paper-head"><h1>${esc(d.title)}</h1>${tags ? `<div class="paper-tags">${tags}</div>` : ""}</header>
<article class="md">${bodyHtml}</article>
</body></html>`);
  doc.close();
  await loaded(frame);
  const win = frame.contentWindow;
  // Chrome and Edge name the PDF after the page that asked to print; use the note's title, not "Recall".
  const title = document.title;
  document.title = d.title;
  const cleanup = () => { document.title = title; frame.remove(); };
  win.addEventListener("afterprint", () => setTimeout(cleanup, 0), { once: true });
  setTimeout(cleanup, 10 * 60_000);  // if a browser never says printing is over
  toast("Choose “Save as PDF” as the printer.", 5000);
  win.focus();
  win.print();
}

/** Wait for the stylesheets, fonts and every image (a broken one is skipped, not waited on forever). */
async function loaded(frame) {
  const doc = frame.contentDocument;
  if (doc.readyState !== "complete") await new Promise(r => frame.contentWindow.addEventListener("load", r, { once: true }));
  await Promise.all([...doc.images].map(img => img.complete ? null : img.decode().catch(() => {})));
  await doc.fonts?.ready;
}
