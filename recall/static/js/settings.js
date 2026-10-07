// The Settings dialog.

import { $, H, api, toast } from "./util.js";
import { state } from "./state.js";
import { refreshStatus } from "./status.js";
import { loadTree } from "./tree.js";
import { changePort } from "./appctl.js";

export const TEXT_SETTINGS = ["notes_dir", "top_k", "embed_model", "rerank_model"];
export const BOOL_SETTINGS = ["semantic_search", "rerank", "ocr", "watch"];

export function openSettings() {
  const f = $("#settings-form"), s = state.status?.settings || {};
  for (const k of TEXT_SETTINGS) f.elements[k].value = s[k] ?? "";
  for (const k of BOOL_SETTINGS) f.elements[k].checked = !!s[k];
  const feat = state.status?.features || {}, ix = state.status?.index;
  $("#feature-hint").textContent = [
    feat.semantic === false && "Smart search needs the fastembed package (pip install fastembed).",
    feat.ocr === false && "OCR needs the rapidocr-onnxruntime package.",
    ix?.embed_error && `Smart search error: ${ix.embed_error}`,
    ix?.rerank_error && `Re-ranking error: ${ix.rerank_error}`,
  ].filter(Boolean).join(" ");
  // The Windows app also shows its port here (the first time it opens, too).
  const app = state.status?.app;
  $("#app-settings").hidden = !app;
  f.elements.port.disabled = !app || app.port_locked;
  f.elements.port.value = app?.port ?? "";
  $("#port-hint").textContent = !app ? "" : app.port_locked
    ? "Set by the RECALL_PORT environment variable."
    : `Recall runs at http://localhost:${app.port}/. Changing the port restarts Recall there. ` +
      "Recent questions, the theme and other browser-saved choices are kept per address.";
  $("#settings-error").textContent = "";
  $("#settings").showModal();
}
export async function saveSettings(ev) {
  ev.preventDefault();
  const f = $("#settings-form"), e = f.elements;
  const body = {};
  for (const k of TEXT_SETTINGS) body[k] = e[k].value.trim();
  for (const k of BOOL_SETTINGS) body[k] = e[k].checked;
  body.top_k = +body.top_k || 0;
  if (!body.embed_model) body.embed_model = "BAAI/bge-base-en-v1.5";
  if (!body.rerank_model) body.rerank_model = "Xenova/ms-marco-MiniLM-L-6-v2";
  const app = state.status?.app, port = +e.port.value;
  if (app && !app.port_locked && port !== app.port && !(port >= 1024 && port <= 65535)) {
    $("#settings-error").textContent = "Pick a port from 1024 to 65535."; return;
  }
  try {
    await api("/api/settings", { method: "POST", headers: H, body: JSON.stringify(body) });
  } catch (err) { $("#settings-error").textContent = err.message; return; }
  if (app && !app.port_locked && port !== app.port) {
    try { await changePort(port); } catch (err) { $("#settings-error").textContent = err.message; }
    return;
  }
  $("#settings").close();
  toast("Settings saved");
  await refreshStatus();
  loadTree();
}

export function bindSettings() {
  $("#open-settings").onclick = openSettings;
  $("#settings-form").addEventListener("submit", e => { if (e.submitter?.value === "save") saveSettings(e); });
}
