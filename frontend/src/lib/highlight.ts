import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import go from "highlight.js/lib/languages/go";

/** One shared hljs registration for every "read a real source file" surface
 *  (Reader, Flow/Codemap's code strip, the graph's code inspector) — matches
 *  the backend's actual SUPPORTED language set (indexing/languages.py), so a
 *  JS/TS/Go file gets real highlighting instead of the old Python-only path
 *  that silently fell back to plain escaped text for every other language. */
if (!hljs.getLanguage("python")) hljs.registerLanguage("python", python);
if (!hljs.getLanguage("javascript")) hljs.registerLanguage("javascript", javascript);
if (!hljs.getLanguage("typescript")) hljs.registerLanguage("typescript", typescript);
if (!hljs.getLanguage("go")) hljs.registerLanguage("go", go);
// hljs ships no distinct "tsx" grammar — the backend's "tsx" language value
// (tree-sitter's language_tsx) is close enough to typescript's JSX-aware
// grammar to alias rather than duplicate.
hljs.registerAliases(["tsx"], { languageName: "typescript" });

export const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Highlight `code` as `language` when hljs knows it, else fall back to
 *  plain escaped text — never throws, never guesses with the wrong grammar. */
export function highlightCode(code: string, language: string | null | undefined): string {
  if (language && hljs.getLanguage(language)) {
    try { return hljs.highlight(code, { language }).value; } catch { /* fall through */ }
  }
  return escapeHtml(code);
}

/** hljs highlights a whole file as one HTML string — a <span class="hljs-x">
 *  wrapping a multi-line construct (a docstring, a block comment) can span
 *  several source lines with no tag at each newline. Folding and per-line
 *  find-match rendering both need each line addressable as its own,
 *  independently-valid HTML fragment, so this walks the highlighted output
 *  once, tracking which tags are currently open, and re-splits it at every
 *  real newline — closing every open tag at the end of a line and reopening
 *  the same stack at the start of the next, so each resulting line renders
 *  correctly on its own with no unbalanced spans.
 *
 *  hljs's HTML renderer only ever emits `<span class="...">`/`</span>` around
 *  escaped text — no other elements, no attributes besides class — so a
 *  simple tag/text tokenizer is sufficient; this isn't a general HTML parser. */
export function splitHighlightedByLine(html: string): string[] {
  const lines: string[] = [];
  const stack: string[] = [];
  let current = "";
  const tokenRe = /(<span class="[^"]*">|<\/span>)/g;
  let lastIndex = 0;
  let m: RegExpExecArray | null;

  const flushText = (text: string) => {
    const parts = text.split("\n");
    for (let i = 0; i < parts.length; i++) {
      current += parts[i];
      if (i < parts.length - 1) {
        for (let j = stack.length - 1; j >= 0; j--) current += "</span>";
        lines.push(current);
        current = stack.join("");
      }
    }
  };

  while ((m = tokenRe.exec(html))) {
    const text = html.slice(lastIndex, m.index);
    if (text) flushText(text);
    const tag = m[0];
    if (tag === "</span>") stack.pop();
    else stack.push(tag);
    current += tag;
    lastIndex = tokenRe.lastIndex;
  }
  flushText(html.slice(lastIndex));
  lines.push(current);
  return lines;
}
