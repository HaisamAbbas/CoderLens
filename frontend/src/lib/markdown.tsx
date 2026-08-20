import { Fragment, type ReactNode } from "react";
import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import xml from "highlight.js/lib/languages/xml";
import css from "highlight.js/lib/languages/css";
import go from "highlight.js/lib/languages/go";
import rust from "highlight.js/lib/languages/rust";
import java from "highlight.js/lib/languages/java";
import sql from "highlight.js/lib/languages/sql";
import yaml from "highlight.js/lib/languages/yaml";

hljs.registerLanguage("python", python);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("shell", bash);
hljs.registerLanguage("json", json);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("css", css);
hljs.registerLanguage("go", go);
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("java", java);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("yaml", yaml);

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Inline tokens: **bold**, *italic*, a `code` symbol followed by "— path:line"
 *  (the LLM's own convention for citing where something lives — rendered as
 *  a code chip + a dimmed mono location, not left as plain punctuation-glued
 *  text), plain `code`, [n] citations, [text](url). The ref form must come
 *  before the plain backtick form in the alternation so it wins the match. */
const REF_RE = /`[^`]+`\s+—\s+[\w./-]+:\d+/;
const INLINE_RE = new RegExp(
  `(${REF_RE.source}|\\*\\*[^*]+\\*\\*|\\*[^*]+\\*|\`[^\`]+\`|\\[(\\d+)\\]|\\[[^\\]]+\\]\\([^)]+\\))`, "g",
);

export function renderInline(text: string, onCite?: (n: number) => void, keyBase = ""): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(INLINE_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(text.slice(last, idx));
    const tok = m[0];
    const k = `${keyBase}-${i++}`;
    if (REF_RE.test(tok) && tok.startsWith("`")) {
      const sep = tok.indexOf("—");
      const codePart = tok.slice(0, sep).trim().replace(/^`|`$/g, "");
      const loc = tok.slice(sep + 1).trim();
      out.push(
        <span key={k} className="md-ref">
          <code>{codePart}</code>
          <span className="md-loc">{loc}</span>
        </span>,
      );
    }
    else if (tok.startsWith("**") && tok.endsWith("**")) out.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("*") && tok.endsWith("*")) out.push(<em key={k}>{tok.slice(1, -1)}</em>);
    else if (tok.startsWith("`")) out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else if (m[2] !== undefined) {
      const n = parseInt(m[2], 10);
      out.push(
        onCite ? (
          <button key={k} className="cite-btn" onClick={() => onCite(n)} title={`Jump to evidence [${n}]`}>
            [{n}]
          </button>
        ) : (
          <span key={k} className="cite-mute">[{n}]</span>
        ),
      );
    } else {
      const inner = tok.slice(1, -1);
      const sep = inner.indexOf("](");
      const label = inner.slice(0, sep);
      const url = inner.slice(sep + 2);
      out.push(
        /^https?:\/\//.test(url) ? (
          <a key={k} href={url} target="_blank" rel="noreferrer">{label}</a>
        ) : (
          <Fragment key={k}>{label}</Fragment>
        ),
      );
    }
    last = idx + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  let html: string;
  try {
    const res = lang && hljs.getLanguage(lang) ? hljs.highlight(code, { language: lang }) : hljs.highlightAuto(code);
    html = res.value;
  } catch {
    html = escapeHtml(code);
  }
  return (
    <pre className="md-pre">
      {lang && <div className="md-lang">{lang}</div>}
      <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
    </pre>
  );
}

/** Renders LLM answers as markdown; `[n]` markers become citation chips. */
export default function Markdown({ text, onCite }: { text: string; onCite?: (n: number) => void }) {
  const blocks = text.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  const out: ReactNode[] = [];
  let bi = 0;

  for (const block of blocks) {
    const key = `b${bi++}`;
    // Fenced code block
    if (block.startsWith("```")) {
      const first = block.indexOf("\n");
      const lang = first === -1 ? "" : block.slice(3, first).trim();
      const code = first === -1 ? block.slice(3).replace(/```$/, "") : block.slice(first + 1).replace(/```\s*$/, "");
      out.push(<CodeBlock key={key} lang={lang} code={code} />);
      continue;
    }
    // Heading
    const hm = block.match(/^(#{1,3})\s+(.*)$/);
    if (hm) {
      const level = hm[1].length;
      const content = renderInline(hm[2], onCite, key);
      out.push(
        level === 1 ? <h3 key={key} className="md-h3">{content}</h3>
          : level === 2 ? <h4 key={key} className="md-h4">{content}</h4>
            : <h5 key={key} className="md-h5">{content}</h5>,
      );
      continue;
    }
    // List
    const lines = block.split("\n");
    if (lines.some((l) => /^\s*[-*]\s+/.test(l) || /^\s*\d+\.\s+/.test(l))) {
      const ordered = /^\s*\d+\.\s+/.test(lines[0]);
      out.push(
        ordered ? (
          <ol key={key} className="md-ol">{lines.map((l, i) => (
            <li key={i}>{renderInline(l.replace(/^\s*\d+\.\s+/, ""), onCite, `${key}-${i}`)}</li>
          ))}</ol>
        ) : (
          <ul key={key} className="md-ul">{lines.map((l, i) => (
            <li key={i}>{renderInline(l.replace(/^\s*[-*]\s+/, ""), onCite, `${key}-${i}`)}</li>
          ))}</ul>
        ),
      );
      continue;
    }
    // Paragraph
    out.push(<p key={key} className="md-p">{renderInline(block, onCite, key)}</p>);
  }
  return <div className="md">{out}</div>;
}
