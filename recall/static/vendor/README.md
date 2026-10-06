# Vendored browser libraries

Served locally so Recall works offline. To update one, download the file from the same path on
`https://cdn.jsdelivr.net/npm/` with the new version number.

| File | Package | License |
| --- | --- | --- |
| `marked.min.js` | marked 15.0.12 (`lib/marked.umd.js`, minified) | MIT |
| `marked-footnote.umd.js` | marked-footnote 1.4.0 (`dist/index.umd.js`) | MIT |
| `marked-katex.umd.js` | marked-katex-extension 5.1.13 (`lib/index.umd.js`) | MIT |
| `katex/` | katex 0.18.11 (`dist/katex.min.js`, `dist/katex.min.css`, `dist/fonts/*.woff2`) | MIT |
| `mermaid.min.js` | mermaid 12.1.0 (`dist/mermaid.min.js`); loaded only when a note has a diagram | MIT |
| `purify.min.js` | DOMPurify 3.4.16 | Apache-2.0 / MPL-2.0 |
| `highlight.min.js`, `hljs-github*.min.css` | highlight.js 11.12.0 | BSD-3-Clause |
| `d3.min.js` | d3 7.9.0 | ISC |
| `emoji.json` | shortcode → emoji map built from gemoji 8.1.0 (`index.js`) | MIT |
