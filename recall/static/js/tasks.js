// Task checkboxes: finding "- [ ]" / "[x]" items in a note's source, and toggling one.
//
// Rendered checkboxes are matched to source lines by position (the Nth checkbox is the Nth task line).
// If the counts differ (e.g. a task inside raw HTML), nothing is changed and the caller says so.

const FRONTMATTER = /^---[ \t]*\n[\s\S]*?\n(?:---|\.\.\.)[ \t]*\n/;
const FENCE = /^[ \t]*(?:>[ \t]?)*[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?(`{3,}|~{3,})/;
// Optional blockquote markers, an optional list marker, then [ ] / [x] / [X] and a space.
// The bare form (no list marker) matches the bareTask extension in md/extensions.js.
const TASK = /^([ \t]*(?:>[ \t]?)*[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?\[)([ xX])(?=\][ \t]+\S)/;

/** Offsets of the character inside each task's brackets, in document order. */
export function taskOffsets(src) {
  const out = [];
  let pos = 0, fence = null;
  const fm = FRONTMATTER.exec(src);
  if (fm) pos = fm[0].length;
  for (const line of src.slice(pos).split("\n")) {
    const f = FENCE.exec(line);
    if (fence) {
      if (f && f[1][0] === fence[0] && f[1].length >= fence.length) fence = null;
    } else if (f) {
      fence = f[1];
    } else {
      const m = TASK.exec(line);
      if (m) out.push(pos + m[1].length);
    }
    pos += line.length + 1;
  }
  return out;
}

/**
 * The source with task `index` set to `checked`, or null if the source doesn't have exactly
 * `expected` tasks (so the rendered checkboxes can't be matched to it reliably).
 */
export function setTask(src, index, checked, expected) {
  const offs = taskOffsets(src);
  if (offs.length !== expected || index >= offs.length) return null;
  const at = offs[index];
  return src.slice(0, at) + (checked ? "x" : " ") + src.slice(at + 1);
}

/** The checkboxes of rendered tasks inside `el`, in document order. */
export function taskBoxes(el) {
  return [...el.querySelectorAll("input.task")];
}

/**
 * Make the task checkboxes in `el` clickable. `apply(index, checked, count)` saves the change and
 * returns true, or returns false to put the checkbox back.
 */
export function enableTasks(el, apply) {
  const boxes = taskBoxes(el);
  boxes.forEach((box, i) => {
    box.disabled = false;
    box.title = "Click to check or uncheck (saved to the file)";
    box.onchange = async () => {
      const want = box.checked;
      boxes.forEach(b => (b.disabled = true));
      let ok = false;
      try { ok = await apply(i, want, boxes.length); } catch { ok = false; }
      if (!ok) box.checked = !want;
      boxes.forEach(b => (b.disabled = false));
      box.closest(".task-item, .task-line")?.classList.toggle("done", box.checked);
    };
  });
}
