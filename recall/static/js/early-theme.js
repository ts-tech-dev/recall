// Apply the saved color theme before the page paints (see theme.js). A plain script, loaded in <head>, not a module:
// modules run after the page is drawn. In a file, not inline, because the page's CSP allows no inline scripts.
(() => {
  let t = "auto";
  try { t = localStorage.getItem("theme") || "auto"; } catch {}
  if (t === "auto") t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = t;
})();
