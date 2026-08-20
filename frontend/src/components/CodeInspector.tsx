import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import { api } from "../lib/api";
import { emitExplain } from "../lib/explainBus";

hljs.registerLanguage("python", python);
const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const LINE_H = 18; // must match .ci-code .ci-src code's line-height in explorer.css
const CARD_VISIBLE_LINES = 14; // roughly what fits in the small card's 280px window

const explainQuestion = (path: string, line?: number) =>
  line
    ? `Explain what's happening around line ${line} in \`${path}\` — what this code does and why it's written this way.`
    : `Explain what \`${path}\` does and how it fits into the rest of the codebase.`;

export interface OpenFile { key: string; path: string; line?: number; }

export default function CodeInspector({
  files, onClose, onReader, zoomed, onZoom,
}: {
  files: OpenFile[]; onClose: (key: string) => void; onReader: (path: string, line?: number) => void;
  zoomed: OpenFile | null; onZoom: (f: OpenFile | null) => void;
}) {
  return (
    <div className="ci">
      <div className="ci-head">
        <b>Code Inspector</b>
        <span className="ci-count">· {files.length} {files.length === 1 ? "reference" : "references"}</span>
      </div>
      <div className="ci-list">
        {files.length === 0 && (
          <div className="ci-empty">
            Click a node in the graph, or an evidence chip in the explainer, and the code appears here.
          </div>
        )}
        {files.map((f) => <Card key={f.key} file={f} onClose={onClose} onReader={onReader} onZoom={() => onZoom(f)} />)}
      </div>
      {zoomed && <Lightbox file={zoomed} onClose={() => onZoom(null)} onReader={onReader} />}
    </div>
  );
}

/** The FULL file, highlighted once — the small card and the lightbox both
 *  render every line into their own scrollable box (native scroll reveals
 *  the whole file, not just a fixed slice) and only differ in how tall that
 *  box is allowed to grow. */
function useFullFile(data: { content: string; language: string | null } | undefined) {
  return useMemo(() => {
    if (!data?.content) return { html: "", count: 0 };
    const lines = data.content.split("\n");
    const code = data.content;
    let html: string;
    try { html = data.language === "python" ? hljs.highlight(code, { language: "python" }).value : escapeHtml(code); }
    catch { html = escapeHtml(code); }
    return { html, count: lines.length };
  }, [data]);
}

/** Lands the scroll position on the reference line (a few lines of lead-in
 *  above it) the moment the content is ready, instead of always starting at
 *  line 1 — the whole file is there to scroll through, but you land where
 *  the reference actually pointed. */
function useScrollToLine(ref: React.RefObject<HTMLDivElement | null>, line: number | undefined, ready: boolean) {
  useEffect(() => {
    if (!ready || !ref.current) return;
    const target = line ? Math.max(0, line - 5) : 0;
    ref.current.scrollTop = target * LINE_H;
  }, [ref, line, ready]);
}

function Card({ file, onClose, onReader, onZoom }: {
  file: OpenFile; onClose: (key: string) => void; onReader: (p: string, l?: number) => void; onZoom: () => void;
}) {
  const { data } = useQuery({ queryKey: ["file", file.path], queryFn: () => api.file(file.path) });
  const full = useFullFile(data);
  const codeRef = useRef<HTMLDivElement>(null);
  useScrollToLine(codeRef, file.line, !!data);
  // Inline growth (stays in the list, next to the AI Explainer) is a
  // DIFFERENT control from the header's ⌄ zoom-to-lightbox — that one covers
  // the whole screen, which is exactly wrong when you want the code and the
  // explainer's answer visible at the same time. This one just gives the
  // card itself more room without leaving the split-panel layout.
  const [tall, setTall] = useState(false);

  const gutter = useMemo(() => Array.from({ length: full.count }, (_, i) => i + 1).join("\n"), [full.count]);
  const hasMore = full.count > CARD_VISIBLE_LINES;

  return (
    <div className="ci-card">
      <div className="ci-card-head">
        <span className="ci-name">{file.path.split("/").pop()}</span>
        <span className="ci-path">{file.path}{data ? `  ·  ${data.loc} lines` : ""}</span>
        {hasMore && (
          <button className="ci-expand-btn" onClick={onZoom} title={`Zoom in · ${data?.loc ?? "?"} lines`}>
            ⌄
          </button>
        )}
        <button
          className="ci-explain"
          onClick={() => emitExplain({ question: explainQuestion(file.path, file.line), display: "Explain this" })}
          title="Ask the AI Explainer about this code"
        >
          ✦ Explain
        </button>
        <span className="ci-x" onClick={() => onClose(file.key)} title="Close">×</span>
      </div>
      {!data ? (
        <div className="ci-loading"><span className="spin" /></div>
      ) : (
        <div className={"ci-code" + (tall ? " tall" : "")} ref={codeRef}>
          <pre className="ci-gutter"><code>{gutter}</code></pre>
          <pre className="ci-src"><code className="hljs" dangerouslySetInnerHTML={{ __html: full.html }} /></pre>
        </div>
      )}
      <div className="ci-foot-row">
        {hasMore && (
          <span className="ci-inline-expand" onClick={() => setTall((v) => !v)}>
            {tall ? "⌃ Collapse" : "⌄ Expand"}
          </span>
        )}
        <span className="ci-foot" onClick={() => onReader(file.path, file.line)}>Open full file in Reader →</span>
      </div>
    </div>
  );
}

/** The expand button brings the file forward, over everything — like clicking
 *  a thumbnail to view it full-size — instead of growing taller in place among
 *  other stacked cards, where it was easy to lose track of with several files
 *  open at once. */
function Lightbox({ file, onClose, onReader }: {
  file: OpenFile; onClose: () => void; onReader: (p: string, l?: number) => void;
}) {
  const { data } = useQuery({ queryKey: ["file", file.path], queryFn: () => api.file(file.path) });
  const full = useFullFile(data);
  const codeRef = useRef<HTMLDivElement>(null);
  useScrollToLine(codeRef, file.line, !!data);

  const gutter = useMemo(() => Array.from({ length: full.count }, (_, i) => i + 1).join("\n"), [full.count]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="ci-lightbox-backdrop" onClick={onClose}>
      <div className="ci-lightbox" onClick={(e) => e.stopPropagation()}>
        <div className="ci-lightbox-head">
          <span className="ci-name">{file.path.split("/").pop()}</span>
          <span className="ci-path">{file.path}{data ? `  ·  ${data.loc} lines` : ""}</span>
          <span className="ci-lightbox-open" onClick={() => { onReader(file.path, file.line); onClose(); }}>
            Open full file in Reader →
          </span>
          <button className="ci-x" onClick={onClose} title="Close (Esc)">×</button>
        </div>
        <div className="ci-lightbox-body" ref={codeRef}>
          {!data ? (
            <div className="ci-loading"><span className="spin" /></div>
          ) : (
            <div className="ci-code">
              <pre className="ci-gutter"><code>{gutter}</code></pre>
              <pre className="ci-src"><code className="hljs" dangerouslySetInnerHTML={{ __html: full.html }} /></pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
