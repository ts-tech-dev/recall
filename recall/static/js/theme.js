// Color theme: Auto (follows the system), Light, Grey or Dark. Saved per browser.
// The <head> script in index.html applies the saved theme before the page paints.

import { $ } from "./util.js";

const DARK_THEMES = new Set(["grey", "dark"]);
const systemDark = matchMedia("(prefers-color-scheme: dark)");

export function savedTheme() {
  try { return localStorage.getItem("theme") || "auto"; } catch { return "auto"; }
}

/** The theme in effect: light, grey or dark. */
export function resolvedTheme(choice = savedTheme()) {
  return choice === "auto" ? (systemDark.matches ? "dark" : "light") : choice;
}

export const isDark = () => DARK_THEMES.has(document.documentElement.dataset.theme);

export function applyTheme(choice = savedTheme()) {
  const theme = resolvedTheme(choice);
  document.documentElement.dataset.theme = theme;
  const dark = DARK_THEMES.has(theme);
  $("#hljs-light").media = dark ? "not all" : "all";
  $("#hljs-dark").media = dark ? "all" : "not all";
}

export function bindTheme() {
  const sel = $("#theme");
  sel.value = savedTheme();
  applyTheme();
  sel.onchange = () => {
    try { localStorage.setItem("theme", sel.value); } catch {}
    applyTheme(sel.value);
  };
  systemDark.addEventListener("change", () => { if (savedTheme() === "auto") applyTheme(); });
}
