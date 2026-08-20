import { useMemo, useState } from "react";
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
import { CheckIcon, CopyIcon } from "./icons";

const REG: Record<string, unknown> = {
  python, javascript, typescript, bash, json, xml, css, go, rust, java, sql, yaml,
};
for (const [name, lang] of Object.entries(REG)) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (!hljs.getLanguage(name)) hljs.registerLanguage(name, lang as any);
}
hljs.registerLanguage("shell", bash);
hljs.registerLanguage("html", xml);

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** A real source snippet from the indexed repo — DeepWiki-style: a filename bar
 *  with a copy button, then syntax-highlighted, copy-pasteable code. Clicking
 *  the path opens the file in the reader at the right line. */
export default function WikiCode({
  title, path, line, lang, code, onOpen,
}: {
  title: string; path: string; line: number; lang: string; code: string;
  onOpen?: (path: string, line: number) => void;
}) {
  const [copied, setCopied] = useState(false);

  const html = useMemo(() => {
    try {
      const res = lang && hljs.getLanguage(lang)
        ? hljs.highlight(code, { language: lang })
        : hljs.highlightAuto(code);
      return res.value;
    } catch {
      return escapeHtml(code);
    }
  }, [code, lang]);

  const copy = async () => {
    try { await navigator.clipboard.writeText(code); } catch { /* clipboard unavailable */ }
    setCopied(true);
    setTimeout(() => setCopied(false), 1300);
  };

  return (
    <figure className="wk-code">
      <div className="wk-code-bar">
        <button
          className="wk-code-loc"
          onClick={() => onOpen?.(path, line)}
          title={`Open ${path}:${line}`}
        >
          <span className="wk-code-name">{title}</span>
          <span className="wk-code-path">{path}:{line}</span>
        </button>
        <button className="wk-code-copy" onClick={copy} title="Copy code">
          {copied ? <CheckIcon className="ok" /> : <CopyIcon />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="wk-code-pre"><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>
    </figure>
  );
}
