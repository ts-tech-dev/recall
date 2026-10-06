// Extra Markdown syntax for marked, on top of GitHub-flavored Markdown (tables, task lists, ~~strike~~, autolinks):
//
//   ==highlight==          <mark>
//   %%comment%%            hidden (Obsidian comments, inline or across lines)
//   [ ] / [x] at a line start, outside a list: a checkbox (like a task list item)
//   Term + ": definition"  definition lists
//   :smile:                emoji shortcodes (GitHub names)
//
// Footnotes ([^1]) and math ($…$, $$…$$) come from the marked-footnote and marked-katex extensions (see markdown.js).

const highlight = {
  name: "highlight",
  level: "inline",
  start: src => src.indexOf("=="),
  tokenizer(src) {
    const m = /^==(?=\S)([^\n]*?\S)==(?!=)/.exec(src);
    if (m) return { type: "highlight", raw: m[0], tokens: this.lexer.inlineTokens(m[1]) };
  },
  renderer(t) { return `<mark>${this.parser.parseInline(t.tokens)}</mark>`; },
};

const commentInline = {
  name: "commentInline",
  level: "inline",
  start: src => src.indexOf("%%"),
  tokenizer(src) {
    const m = /^%%[\s\S]*?%%/.exec(src);
    if (m) return { type: "commentInline", raw: m[0] };
  },
  renderer: () => "",
};

const commentBlock = {
  name: "commentBlock",
  level: "block",
  start: src => src.match(/^%%/m)?.index,
  tokenizer(src) {
    const m = /^%%[\s\S]*?%%[ \t]*(?:\n|$)/.exec(src);
    if (m) return { type: "commentBlock", raw: m[0] };
  },
  renderer: () => "",
};

// Must match the bare-task pattern in tasks.js.
const bareTask = {
  name: "bareTask",
  level: "block",
  start: src => src.match(/^[ \t]*\[[ xX]\][ \t]+\S/m)?.index,
  tokenizer(src) {
    const m = /^[ \t]*\[([ xX])\][ \t]+([^\n]*)(?:\n|$)/.exec(src);
    if (m) return { type: "bareTask", raw: m[0], checked: m[1] !== " ", tokens: this.lexer.inlineTokens(m[2]) };
  },
  renderer(t) {
    return `<p class="task-line"><input type="checkbox" disabled${t.checked ? " checked" : ""}> ${this.parser.parseInline(t.tokens)}</p>\n`;
  },
};

// A term line: not a heading, quote, list item, table row, fence or definition itself.
const TERM = String.raw`(?![#>|*+\-\`~:]|\d+[.)]\s|\s{4})[^\n]+`;
const DL_START = new RegExp(String.raw`(?:^|\n)${TERM}\n:[ \t]+\S`);
const DL_BLOCK = new RegExp(String.raw`^((?:${TERM}\n(?::[ \t]+[^\n]*(?:\n|$))+\n?)+)`);

const definitionList = {
  name: "definitionList",
  level: "block",
  start: src => { const m = DL_START.exec(src); return m ? m.index + (m[0][0] === "\n" ? 1 : 0) : undefined; },
  tokenizer(src) {
    const m = DL_BLOCK.exec(src);
    if (!m) return;
    const items = [];
    for (const line of m[1].split("\n")) {
      if (!line.trim()) continue;
      const def = /^:[ \t]+(.*)$/.exec(line);
      items.push(def ? { dd: true, tokens: this.lexer.inlineTokens(def[1]) } : { dd: false, tokens: this.lexer.inlineTokens(line) });
    }
    return { type: "definitionList", raw: m[0], items };
  },
  renderer(t) {
    return "<dl>" + t.items.map(i => i.dd ? `<dd>${this.parser.parseInline(i.tokens)}</dd>` : `<dt>${this.parser.parseInline(i.tokens)}</dt>`).join("") + "</dl>\n";
  },
};

export function emojiExtension(map) {
  return {
    name: "emoji",
    level: "inline",
    start: src => src.match(/:[+\w-]+:/)?.index,
    tokenizer(src) {
      const m = /^:([+\w-]+):/.exec(src);
      if (m && map[m[1]]) return { type: "emoji", raw: m[0], emoji: map[m[1]], name: m[1] };
    },
    renderer: t => `<span class="emoji" title=":${t.name}:">${t.emoji}</span>`,
  };
}

export const extensions = [highlight, commentInline, commentBlock, bareTask, definitionList];
