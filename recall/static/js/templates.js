// Note templates: the Markdown notes in a "templates" folder at the top of the notes folder (as in Obsidian).
// A new note made from one gets its text, with these filled in (in the browser, so in local time):
//
//   {{title}}                 the new note's name
//   {{date}}  {{time}}        2026-10-21, 14:05
//   {{date:dddd D MMMM YYYY}} a format: YYYY YY MMMM MMM MM M DD D dddd ddd HH H mm ss; text in [brackets] is kept

import { state } from "./state.js";

export const TEMPLATES_DIR = "templates";

/** Paths of the template notes, by name. */
export function listTemplates() {
  const dir = state.tree?.children.find(c => c.type === "dir" && c.name.toLowerCase() === TEMPLATES_DIR);
  const out = [];
  const walk = n => n.children.forEach(c => c.type === "dir" ? walk(c) : /\.(md|markdown)$/i.test(c.name) && out.push(c.path));
  if (dir) walk(dir);
  return out.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

export const templateName = path => path.split("/").pop().replace(/\.[^.]+$/, "");

const pad = n => String(n).padStart(2, "0");

export function formatDate(d, fmt) {
  const name = opts => d.toLocaleString(undefined, opts);
  const tokens = {
    YYYY: () => d.getFullYear(), YY: () => pad(d.getFullYear() % 100),
    MMMM: () => name({ month: "long" }), MMM: () => name({ month: "short" }), MM: () => pad(d.getMonth() + 1), M: () => d.getMonth() + 1,
    dddd: () => name({ weekday: "long" }), ddd: () => name({ weekday: "short" }), DD: () => pad(d.getDate()), D: () => d.getDate(),
    HH: () => pad(d.getHours()), H: () => d.getHours(), mm: () => pad(d.getMinutes()), ss: () => pad(d.getSeconds()),
  };
  return fmt.replace(/\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|M|dddd|ddd|DD|D|HH|H|mm|ss/g, (m, literal) => literal ?? String(tokens[m]()));
}

export function fillTemplate(text, { title, now = new Date() }) {
  return text.replace(/\{\{\s*(title|date|time)\s*(?::([^}]*))?\}\}/gi, (_, key, fmt) => {
    key = key.toLowerCase();
    if (key === "title") return title;
    return formatDate(now, fmt?.trim() || (key === "date" ? "YYYY-MM-DD" : "HH:mm"));
  });
}
