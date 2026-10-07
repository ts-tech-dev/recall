// The Windows app's controls: the port it runs on (changing it restarts Recall there) and shutting it down.
// Only shown when the server reports `app` in /api/status (it doesn't on the command line or in Docker).

import { $, H, api } from "./util.js";
import { state } from "./state.js";

const sleep = ms => new Promise(r => setTimeout(r, ms));

function overlay(html) {
  document.querySelectorAll("dialog[open]").forEach(d => d.close());  // dialogs sit above everything else
  $("#app-overlay-msg").innerHTML = html;
  $("#app-overlay").hidden = false;
}

/** Wait until something answers at `url` (another origin, so the reply itself can't be read). */
async function reachable(url, ms = 30000) {
  for (const end = Date.now() + ms; Date.now() < end; await sleep(500)) {
    try { await fetch(url + "api/status", { mode: "no-cors", cache: "no-store" }); return true; } catch {}
  }
  return false;
}

/** Restart Recall on `port` and follow it there. Throws (nothing changes) if the port can't be used. */
export async function changePort(port) {
  const { url } = await api("/api/app/port", { method: "POST", headers: H, body: JSON.stringify({ port }) });
  overlay(`Restarting Recall at <b>${url}</b>…`);
  await sleep(800);
  if (await reachable(url)) location.href = url;
  else overlay(`Recall didn't come up at ${url}.<div class="muted">It is still running here; reload this page.</div>`);
}

export async function shutdownApp() {
  if (!confirm("Shut down Recall? Start Recall.exe again to reopen it.")) return;
  try { await api("/api/app/shutdown", { method: "POST", headers: H }); }
  catch (e) { alert(e.message); return; }
  overlay(`Recall has shut down.<div class="muted">You can close this tab. Start Recall again to reopen it.</div>`);
  state.shutDown = true;
}

/** Show or hide the app controls to match the latest status. */
export function syncAppControls() {
  $("#shutdown").hidden = !state.status?.app;
}

export function bindAppControls() {
  $("#shutdown").onclick = shutdownApp;
  $("#settings-shutdown").onclick = shutdownApp;
}
