// The Settings dialog.

import { $, $$, H, api, toast } from "./util.js";
import { state } from "./state.js";
import { refreshStatus } from "./status.js";
import { loadTree } from "./tree.js";

export const TEXT_SETTINGS = ["notes_dir", "provider", "model", "base_url", "effort", "max_images", "top_k", "embed_model", "rerank_model",
  "caption_model", "caption_limit"];
export const BOOL_SETTINGS = ["ai_features", "send_images", "semantic_search", "rerank", "ocr", "watch", "caption_images"];

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
  f.elements.api_key.value = "";
  f.elements.clear_api_key.checked = false;
  $("#key-hint").textContent = s.has_key
    ? `A key ending in ${s.key_hint} is ${s.key_from_env ? "set via environment variable" : "saved"}. Leave blank to keep it.`
    : "No key set. For Anthropic you can also set ANTHROPIC_API_KEY before starting the app.";
  $("#settings-error").textContent = "";
  syncProviderFields();
  $("#settings").showModal();
}
export function syncProviderFields() {
  const f = $("#settings-form"), p = f.elements.provider.value, on = f.elements.ai_features.checked;
  $$("[data-for]", f).forEach(el => (el.hidden = el.dataset.for !== p));
  // With AI switched off, its options stay visible (and saved) but can't be changed.
  const aiBox = f.elements.ai_features.closest("fieldset");
  // "Passages retrieved" also sets how many passages Ask shows without AI, so it stays editable.
  for (const el of [...aiBox.elements, ...$$("[data-ai] input", f)]) {
    if (el.name !== "ai_features" && el.name !== "top_k") el.disabled = !on;
  }
  aiBox.classList.toggle("off", !on);
  $("#ai-off-hint").hidden = on;
  f.elements.model.placeholder = p === "anthropic" ? "claude-opus-5-5" : "gpt-4o / llama3.2 / …";
}
export async function saveSettings(ev) {
  ev.preventDefault();
  const f = $("#settings-form"), e = f.elements;
  const body = { api_key: e.api_key.value.trim(), clear_api_key: e.clear_api_key.checked };
  for (const k of TEXT_SETTINGS) body[k] = e[k].value.trim();
  for (const k of BOOL_SETTINGS) body[k] = e[k].checked;
  for (const k of ["max_images", "top_k", "caption_limit"]) body[k] = +body[k] || 0;
  if (!body.embed_model) body.embed_model = "BAAI/bge-base-en-v1.5";
  if (!body.rerank_model) body.rerank_model = "Xenova/ms-marco-MiniLM-L-6-v2";
  if (!body.model) body.model = body.provider === "anthropic" ? "claude-opus-5-5" : "";
  try {
    await api("/api/settings", { method: "POST", headers: H, body: JSON.stringify(body) });
  } catch (err) { $("#settings-error").textContent = err.message; return; }
  $("#settings").close();
  toast("Settings saved");
  await refreshStatus();
  loadTree();
}

export function bindSettings() {
  $("#open-settings").onclick = openSettings;
  $("#settings-form").elements.provider.onchange = syncProviderFields;
  $("#settings-form").elements.ai_features.onchange = syncProviderFields;
  $("#settings-form").addEventListener("submit", e => { if (e.submitter?.value === "save") saveSettings(e); });
}
