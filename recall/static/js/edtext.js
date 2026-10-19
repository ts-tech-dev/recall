// Typing help in the note editor: lists continue on Enter and indent with Tab / Shift+Tab, tables can be
// inserted, are kept aligned, and Tab / Enter move between their cells. Pasted spreadsheet cells become a table.
// Changes go through the browser's undo history (Ctrl+Z works).

const ITEM = /^([ \t]*)(?:([-*+])|(\d{1,9})([.)]))([ \t]+)(\[[ xX]\][ \t]+)?/;
const DELIM_CELL = /^:?-+:?$/;

// ---------- text helpers ----------

/** Replace value[start:end] with `text` as an undoable edit, then select [selStart, selEnd]. */
export function replaceRange(ta, start, end, text, selStart = start + text.length, selEnd = selStart) {
  ta.focus();
  ta.setSelectionRange(start, end);
  const ok = text ? document.execCommand("insertText", false, text)
    : start === end || document.execCommand("delete");
  if (!ok) {
    ta.setRangeText(text, start, end);
    ta.dispatchEvent(new Event("input"));
  }
  ta.setSelectionRange(selStart, selEnd);
}

/** Make the textarea hold `next`, changing only the part that differs (keeps undo history and scroll). */
export function setText(ta, next) {
  const cur = ta.value;
  if (cur === next) return;
  let a = 0;
  while (a < cur.length && a < next.length && cur[a] === next[a]) a++;
  let b = 0;
  while (b < cur.length - a && b < next.length - a && cur[cur.length - 1 - b] === next[next.length - 1 - b]) b++;
  const { selectionStart: s, selectionEnd: e } = ta, top = ta.scrollTop;
  replaceRange(ta, a, cur.length - b, next.slice(a, next.length - b));
  const fix = p => (p <= a ? p : p >= cur.length - b ? p + next.length - cur.length : a);
  ta.setSelectionRange(fix(s), fix(e));
  ta.scrollTop = top;
}

const lineStart = (v, p) => v.lastIndexOf("\n", p - 1) + 1;
const lineEnd = (v, p) => { const i = v.indexOf("\n", p); return i < 0 ? v.length : i; };

function indentWidth(ws) {
  let w = 0;
  for (const ch of ws) w = ch === "\t" ? w + 4 - (w % 4) : w + 1;
  return w;
}

/** A list item line: {indent, content (column of its text), num, delim, bullet, task}, or null. */
function item(line) {
  const m = ITEM.exec(line);
  if (!m) return null;
  const indent = indentWidth(m[1]);
  return { indent, content: indent + (m[2] || m[3] + m[4]).length + m[5].length, bullet: m[2], num: m[3],
           delim: m[4], task: !!m[6], marker: m[0].length };
}

/** The item above line `i` (in `lines`) that `pick(it)` accepts, looking back within the same list. */
function itemAbove(lines, i, pick) {
  for (let k = i - 1; k >= 0; k--) {
    const it = item(lines[k]);
    if (it) { if (pick(it)) return it; continue; }
    if (lines[k].trim() && !/^[ \t]/.test(lines[k])) return null;  // left the list
  }
  return null;
}

// ---------- lists ----------

/** Indent (dir 1) or outdent (dir -1) the lines touched by the selection. */
function indentLines(ta, dir) {
  const v = ta.value, { selectionStart: s, selectionEnd: e } = ta;
  const from = lineStart(v, s);
  const to = lineEnd(v, e > s && v[e - 1] === "\n" ? e - 1 : e);
  const lines = v.slice(0, to).split("\n");
  const first = v.slice(0, from).split("\n").length - 1;
  const block = lines.slice(first);
  const lead = block.map(item).find(Boolean);
  if (!lead && s === e && dir > 0) { replaceRange(ta, s, e, "  "); return; }  // plain text: a soft tab
  let delta = dir > 0 ? 2 : -2;
  let renumber = null;
  if (lead) {
    const at = first + block.findIndex(l => item(l));
    if (dir > 0) {
      // nest under the item above at this level: line the marker up with that item's text
      const sib = itemAbove(lines, at, it => it.indent <= lead.indent);
      delta = (sib && sib.indent === lead.indent ? sib.content : lead.content) - lead.indent;
      if (lead.num && block.length === 1) renumber = 1;
    } else {
      const parent = itemAbove(lines, at, it => it.indent < lead.indent);
      delta = (parent ? parent.indent : 0) - lead.indent;
      if (lead.num && block.length === 1) {
        const sib = itemAbove(lines, at, it => it.indent <= lead.indent + delta);
        renumber = sib && sib.num && sib.indent === lead.indent + delta ? +sib.num + 1 : 1;
      }
    }
    if (!delta) return;
  }
  const out = block.map(line => {
    if (!line.trim()) return line;
    const ws = /^[ \t]*/.exec(line)[0];
    let next = " ".repeat(Math.max(0, indentWidth(ws) + delta)) + line.slice(ws.length);
    if (renumber !== null) next = next.replace(/^( *)\d+/, `$1${renumber}`);
    return next;
  });
  const text = out.join("\n");
  if (s === e) {  // keep the caret on the same character
    const col = s - from, oldLen = block[0].length;
    replaceRange(ta, from, to, text);
    const p = from + Math.max(0, col + out[0].length - oldLen);
    ta.setSelectionRange(p, p);
  } else {
    replaceRange(ta, from, to, text, from, from + text.length);
  }
}

/** Enter on a list item: start the next item, or end the list on an empty one. */
function listEnter(ta) {
  const v = ta.value, p = ta.selectionStart;
  const from = lineStart(v, p), line = v.slice(from, lineEnd(v, p));
  const it = item(line);
  if (!it || p - from < it.marker) return false;
  if (!line.slice(it.marker).trim()) {  // empty item: outdent it, or drop the marker at the top level
    if (it.indent > 0) indentLines(ta, -1);
    else replaceRange(ta, from, from + line.length, "");
    return true;
  }
  const ws = " ".repeat(it.indent);
  const marker = it.bullet ? it.bullet : `${+it.num + 1}${it.delim}`;
  replaceRange(ta, p, ta.selectionEnd, `\n${ws}${marker} ${it.task ? "[ ] " : ""}`);
  return true;
}

// ---------- tables ----------

const isRow = line => /^\s*\|/.test(line);

/** Split a table row into trimmed cells (a "\|" stays inside its cell). */
function cells(line) {
  let t = line.trim().replace(/^\|/, "");
  if (/(^|[^\\])\|$/.test(t)) t = t.slice(0, -1);
  return t.split(/(?<!\\)\|/).map(c => c.trim());
}

/** Rows as aligned Markdown lines (every column as wide as its widest cell). */
function formatTable(rows) {
  const n = Math.max(...rows.map(r => r.length));
  const full = rows.map(r => [...r, ...Array(n - r.length).fill("")]);
  const delim = full.length > 1 && full[1].every(c => DELIM_CELL.test(c) || !c) ? 1 : -1;
  const w = Array.from({ length: n }, (_, c) =>
    Math.max(3, ...full.map((r, i) => (i === delim ? 3 : [...r[c]].length))));
  return full.map((r, i) => "| " + r.map((c, j) => {
    if (i !== delim) return c + " ".repeat(w[j] - [...c].length);
    const l = c.startsWith(":"), rt = c.length > 1 && c.endsWith(":");
    return (l ? ":" : "") + "-".repeat(w[j] - l - rt) + (rt ? ":" : "");
  }).join(" | ") + " |");
}

/** The table around the caret: {from, to, rows, row, col}, or null if the caret isn't in a table row. */
function tableAt(v, p) {
  if (!isRow(v.slice(lineStart(v, p), lineEnd(v, p)))) return null;
  let from = lineStart(v, p), to = lineEnd(v, p);
  while (from > 0 && isRow(v.slice(lineStart(v, from - 1), from - 1))) from = lineStart(v, from - 1);
  while (to < v.length && isRow(v.slice(to + 1, lineEnd(v, to + 1)))) to = lineEnd(v, to + 1);
  const lines = v.slice(from, to).split("\n");
  const row = v.slice(from, p).split("\n").length - 1;
  const before = v.slice(lineStart(v, p), p).replace(/^\s*\|/, "");
  const col = (before.match(/(?<!\\)\|/g) || []).length;
  return { from, to, rows: lines.map(cells), row, col };
}

/** Write `rows` over the table, then select the contents of cell (row, col). */
function writeTable(ta, t, rows, row, col) {
  const lines = formatTable(rows), text = lines.join("\n");
  let p = t.from;
  for (let i = 0; i < row; i++) p += lines[i].length + 1;
  const line = lines[row];
  let start = 2;
  for (let c = 0; c < col; c++) {
    do start = line.indexOf("|", start) + 1; while (line[start - 2] === "\\");
    start++;
  }
  const cell = rows[row][col] || "";
  replaceRange(ta, t.from, t.to, text, p + start, p + start + cell.length);
}

const isDelimRow = (rows, r) => r === 1 && rows[1].every(c => DELIM_CELL.test(c) || !c);

/** Tab / Shift+Tab in a table: go to the next / previous cell (a new row after the last one). */
function tableTab(ta, back) {
  const t = tableAt(ta.value, ta.selectionStart);
  if (!t) return false;
  const rows = t.rows, n = Math.max(...rows.map(r => r.length));
  let { row, col } = t;
  col = Math.min(col, n - 1);
  do {
    col += back ? -1 : 1;
    if (col >= n) { col = 0; row++; }
    if (col < 0) { col = n - 1; row--; }
  } while (isDelimRow(rows, row));
  if (row < 0) { row = 0; col = 0; }
  if (row >= rows.length) rows.push(Array(n).fill(""));
  writeTable(ta, t, rows, row, col);
  return true;
}

/** Enter in a table: the same column on the next row (adding one); on an empty last row, leave the table. */
function tableEnter(ta) {
  const t = tableAt(ta.value, ta.selectionStart);
  if (!t) return false;
  const rows = t.rows, n = Math.max(...rows.map(r => r.length));
  if (t.row === rows.length - 1 && t.row > 1 && rows[t.row].every(c => !c)) {
    const keep = formatTable(rows.slice(0, -1)).join("\n") + "\n\n";
    replaceRange(ta, t.from, t.to, keep + (ta.value[t.to] === "\n" ? "\n" : ""), t.from + keep.length);
    return true;
  }
  let row = t.row + 1;
  if (isDelimRow(rows, row)) row++;
  if (row >= rows.length) rows.push(Array(n).fill(""));
  writeTable(ta, t, rows, row, Math.min(t.col, n - 1));
  return true;
}

/** Insert an empty table of `cols` columns and `bodyRows` rows at the caret, on lines of its own. */
export function insertTable(ta, cols, bodyRows) {
  const v = ta.value, p = ta.selectionStart;
  const header = Array.from({ length: cols }, (_, i) => `Column ${i + 1}`);
  const rows = [header, Array(cols).fill("---"), ...Array.from({ length: bodyRows }, () => Array(cols).fill(""))];
  const text = formatTable(rows).join("\n");
  const ls = lineStart(v, p), le = lineEnd(v, p);
  const onEmptyLine = !v.slice(ls, le).trim();
  const at = onEmptyLine ? ls : le;
  const before = at === 0 || /\n\n$/.test(v.slice(0, at)) ? "" : onEmptyLine ? "\n" : "\n\n";
  const rest = v.slice(onEmptyLine ? le : at);
  const after = !rest ? "\n" : rest.startsWith("\n\n") ? "" : rest.startsWith("\n") ? "\n" : "\n\n";
  const start = at + before.length + 2;
  replaceRange(ta, at, onEmptyLine ? le : at, before + text + after, start, start + header[0].length);
}

/** Cells copied from a spreadsheet or web page (an HTML table) as Markdown table text, or null. */
export function tableFromClipboard(data) {
  const html = data.getData("text/html");
  if (!html || !/<table/i.test(html)) return null;
  const doc = new DOMParser().parseFromString(html, "text/html");
  const tables = doc.querySelectorAll("table");
  if (tables.length !== 1) return null;
  const size = el => el.textContent.replace(/\s+/g, "").length;
  if (size(doc.body) > size(tables[0]) + 10) return null;  // more than a table was copied: paste it as text
  const rows = [...tables[0].rows].map(tr => [...tr.cells].map(td =>
    td.textContent.replace(/\s+/g, " ").trim().replace(/\|/g, "\\|")));
  if (!rows.length || !rows[0].length) return null;
  const n = Math.max(...rows.map(r => r.length));
  rows.splice(1, 0, Array(n).fill("---"));
  if (rows.length === 2) rows.push(Array(n).fill(""));
  return formatTable(rows).join("\n");
}

/** Paste `text` (a table) at the caret on lines of its own. */
export function pasteBlock(ta, text) {
  const v = ta.value, { selectionStart: s, selectionEnd: e } = ta;
  const before = s === 0 || v[s - 1] === "\n" ? (s < 2 || v[s - 2] === "\n" ? "" : "\n") : "\n\n";
  const after = v[e] === "\n" ? "\n" : "\n\n";
  replaceRange(ta, s, e, before + text + after);
}

// ---------- keys ----------

/** Handle Enter / Tab / Shift+Tab in the editor textarea; true if handled. */
export function editorKey(ta, e) {
  if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return false;
  if (e.key === "Tab") {
    if (!tableTab(ta, e.shiftKey)) indentLines(ta, e.shiftKey ? -1 : 1);
    return true;
  }
  if (e.key === "Enter" && !e.shiftKey && ta.selectionStart === ta.selectionEnd) {
    return tableEnter(ta) || listEnter(ta);
  }
  return false;
}
