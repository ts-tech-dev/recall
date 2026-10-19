// List indentation as Obsidian reads it: an item indented further than the one above is nested under it,
// however far (2 spaces under "1.", 4 spaces, a tab, or two levels at once). Strict Markdown wants a nested
// item exactly under its parent's text (3 spaces under "1. "), and otherwise breaks the list apart or shows
// "- item" as plain text. So before parsing, each list line is re-indented to where Markdown expects it.
// Lines are only re-indented, never added or removed (task checkboxes are matched to lines by position).

const ITEM = /^([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+|$)/;
const FENCE = /^[ \t]*(`{3,}|~{3,})/;
const FRONTMATTER = /^---[ \t]*\n[\s\S]*?\n(?:---|\.\.\.)[ \t]*(?:\n|$)/;

/** Width of leading whitespace, a tab counting to the next multiple of 4. */
function width(ws) {
  let w = 0;
  for (const ch of ws) w = ch === "\t" ? w + 4 - (w % 4) : w + 1;
  return w;
}

export function normalizeLists(md) {
  if (!ITEM.test(md) && !/\n[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]/.test(md)) return md;
  const fm = FRONTMATTER.exec(md);
  const head = fm ? fm[0] : "";
  const lines = md.slice(head.length).split("\n");
  let stack = [];  // open items: {src: indent in the text, out: indent written, content: column of its text}
  let fence = null, shift = 0, blank = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const ws = /^[ \t]*/.exec(line)[0], src = width(ws), rest = line.slice(ws.length);
    const reindent = out => { lines[i] = " ".repeat(Math.max(0, out)) + rest; };
    if (fence) {  // inside a fenced block: move it by as much as its opening line moved
      if (shift) lines[i] = shift > 0 ? " ".repeat(shift) + line : line.replace(new RegExp(`^ {0,${-shift}}`), "");
      const f = FENCE.exec(line);
      if (f && f[1][0] === fence[0] && f[1].length >= fence.length && !rest.slice(f[1].length).trim()) fence = null;
      continue;
    }
    if (!rest) { blank = true; continue; }
    const m = ITEM.exec(line);
    if (m && !(src >= 4 && !stack.length)) {  // 4+ spaces outside a list is a code block
      while (stack.length && stack.at(-1).src > src + 1) stack.pop();
      const top = stack.at(-1);
      let out;
      if (!top) out = Math.min(src, 3);
      else if (src <= top.src + 1) { out = top.out; stack.pop(); }  // a sibling (one space off still counts)
      else out = top.content;  // nested under the item above
      const marker = m[2].length + Math.min(Math.max(width(m[3]), 1), 4);
      stack.push({ src, out, content: out + marker });
      reindent(out);
    } else if (stack.length && src > 0) {  // an indented line in a list: the text of the item it falls under
      let k = stack.length - 1;
      while (k > 0 && stack[k].src >= src) k--;
      reindent(stack[k].content);
    } else if (src === 0 && blank) {
      stack = [];  // a paragraph after a blank line ends the list
    }
    const f = FENCE.exec(lines[i]);
    if (f) { fence = f[1]; shift = width(/^[ \t]*/.exec(lines[i])[0]) - src; }
    blank = false;
  }
  return head + lines.join("\n");
}
