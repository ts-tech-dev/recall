// Entry point: wires up every part of the UI, then loads the status, the file tree and the current route.
//
//   util.js      helpers (DOM, escaping, API calls, paths)     state.js   shared state
//   markdown.js  Markdown rendering                             status.js  status bar + re-index polling
//   settings.js  Settings dialog                                tree.js    sidebar tree, search, drag & drop
//   viewer.js    Browse view (preview, outline, related)        editor.js  note editor
//   ask.js       Ask view (answers, sources, history)           files.js   new note / move dialogs
//   graph.js     Graph view                                     theme.js   Auto / Light / Grey / Dark
//   appctl.js    Windows app: port and shut down

import { state } from "./state.js";
import { bindAsk, renderHistory, renderTypeChips } from "./ask.js";
import { bindEditor } from "./editor.js";
import { bindGraph } from "./graph.js";
import { bindSettings, openSettings } from "./settings.js";
import { bindStatus, refreshStatus } from "./status.js";
import { bindTree, loadTree } from "./tree.js";
import { bindViewer, route } from "./viewer.js";
import { bindTheme } from "./theme.js";
import { bindAppControls } from "./appctl.js";

bindTheme();
bindViewer();
bindTree();
bindSettings();
bindStatus();
bindAsk();
bindEditor();
bindGraph();
bindAppControls();

await refreshStatus();
renderTypeChips();
renderHistory();
await loadTree();
if (!state.status?.notes_dir) openSettings();
route();
setInterval(refreshStatus, 3000);  // picks up re-indexing (watcher, saves) and progress
