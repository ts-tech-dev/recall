// Shared UI state and lookup tables.

export const state = {
  status: null,
  tree: null,
  currentDoc: null,
  scopePath: "",          // "Ask about this note" restricts the question to one document
  types: new Set(),
  answerMd: "",
  sources: [],
  asking: null,           // AbortController of the running question
  indexVersion: null,     // last seen index version (changes when files are re-indexed)
  editing: null,          // {path, mtime_ns, saved} while the editor is open
  graph: null,            // cached graph data {key, data}
  docMtime: null,         // version stamp of the note shown in Browse (for saving checkbox clicks)
};

export const TYPE_LABELS = { markdown: "Markdown", pdf: "PDF", word: "Word", powerpoint: "PowerPoint", spreadsheet: "Sheets", html: "HTML", text: "Text", image: "Images" };
export const ICONS = { ".md": "md", ".markdown": "md", ".pdf": "pdf", ".docx": "doc", ".pptx": "ppt", ".xlsx": "xls", ".csv": "csv",
  ".html": "htm", ".htm": "htm", ".txt": "txt", ".rst": "txt", ".org": "txt" };
