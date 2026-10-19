// Resizing images in the editor preview: drag an image's bottom-right corner and its width is written back
// into the text being edited, as ![alt|400](…), ![[pic.png|400]] or <img width="400">.

const CORNER = 22;  // px from the bottom-right corner that start a resize
const IMAGE_EXTS = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
const IMAGE_RE = /!\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]|!\[((?:\\.|[^\]\\])*)\]\(|<img\b[^>]*>/gi;

/** The images of a note's Markdown in document order (code is skipped), as {index, raw, kind}. */
function findImages(md) {
  // blank out fenced code and inline code, keeping offsets
  const blank = s => s.replace(/[^\n]/g, " ");
  const masked = md
    .replace(/^([ \t]*)(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\n[ \t]*\2[^\n]*|(?![\s\S]))/gm, blank)
    .replace(/(`+)(?!`)[\s\S]*?[^`]\1(?!`)/g, blank)
    .replace(/%%[\s\S]*?%%/g, blank);
  const found = [];
  for (const m of masked.matchAll(IMAGE_RE)) {
    if (m[1] !== undefined && !IMAGE_EXTS.test(m[1].trim())) continue;  // ![[note]] embeds a link, not an image
    found.push({ index: m.index, raw: md.slice(m.index, m.index + m[0].length),
                 kind: m[1] !== undefined ? "wiki" : m[2] !== undefined ? "md" : "html" });
  }
  return found;
}

function withWidth({ raw, kind }, w) {
  if (kind === "wiki") return raw.replace(/^!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]$/, `![[$1|${w}]]`);
  if (kind === "md") {
    const alt = raw.slice(2, -2).replace(/\s*\|\s*\d+(?:\s*x\s*\d+)?\s*$/, "");
    return `![${alt}|${w}](`;
  }
  const tag = raw.replace(/\s(?:width|height)\s*=\s*(?:"[^"]*"|'[^']*'|\S+)/gi, "");
  return tag.replace(/^<img\b/i, `<img width="${w}"`);
}

/**
 * Let the images in `preview` be resized by dragging. `getText`/`setText` read and replace the note text;
 * nothing happens if the preview's images can't be matched one-to-one with the images in the text.
 */
export function enableImageResize(preview, getText, setText) {
  const imgs = [...preview.querySelectorAll("img")];
  if (!imgs.length || findImages(getText()).length !== imgs.length) return;
  imgs.forEach((img, n) => {
    img.classList.add("resizable");
    img.title = "Drag the bottom-right corner to resize";
    const inCorner = e => {
      const r = img.getBoundingClientRect();
      return e.clientX > r.right - CORNER && e.clientY > r.bottom - CORNER;
    };
    img.addEventListener("pointermove", e => { if (!img.hasPointerCapture(e.pointerId)) img.classList.toggle("corner", inCorner(e)); });
    img.addEventListener("pointerleave", () => img.classList.remove("corner"));
    img.addEventListener("pointerdown", e => {
      if (e.button !== 0 || !inCorner(e)) return;
      e.preventDefault();
      img.setPointerCapture(e.pointerId);
      const startX = e.clientX, startW = img.getBoundingClientRect().width;
      const max = preview.clientWidth;
      let w = Math.round(startW);
      const move = ev => {
        w = Math.max(40, Math.min(max, Math.round(startW + ev.clientX - startX)));
        img.style.width = w + "px"; img.style.height = "auto";
      };
      const up = () => {
        img.removeEventListener("pointermove", move);
        img.removeEventListener("pointerup", up);
        img.removeEventListener("pointercancel", up);
        // the click that ends a drag shouldn't open the lightbox
        window.addEventListener("click", ev => { ev.stopPropagation(); ev.preventDefault(); }, { capture: true, once: true });
        setTimeout(() => window.dispatchEvent(new Event("click")), 0);  // drops the guard if no click came
        const md = getText(), target = findImages(md)[n];
        if (!target || Math.abs(w - startW) < 2) return;
        setText(md.slice(0, target.index) + withWidth(target, w) + md.slice(target.index + target.raw.length));
      };
      img.addEventListener("pointermove", move);
      img.addEventListener("pointerup", up);
      img.addEventListener("pointercancel", up);
    });
  });
}
