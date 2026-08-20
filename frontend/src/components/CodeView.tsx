import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import type { SymbolIndex, SymbolIndexEntry, SymbolSpan } from "../lib/types";
import { CheckIcon, CopyIcon, SparkleIcon, XIcon } from "./icons";

hljs.registerLanguage("python", python);
const LH = 20;      // line-height, must match .code-wrap code line-height
const PAD_T = 12;   // top padding of code-wrap / gutter

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const KIND_COLOR: Record<string, string> = {
  class: "var(--c-sansio)", method: "var(--c-core)", function: "var(--c-json)",
};
const kindColor = (k: string) => KIND_COLOR[k] ?? "var(--text-3)";

/** The identifier under the cursor + its screen rect, via caret hit-testing —
 *  works regardless of the highlight.js span structure and rewrites no DOM. */
function wordAtPoint(x: number, y: number): { word: string; rect: DOMRect } | null {
  let node: Node | null = null;
  let offset = 0;
  // Chromium / WebKit
  const anyDoc = document as unknown as {
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
  };
  if (anyDoc.caretRangeFromPoint) {
    const r = anyDoc.caretRangeFromPoint(x, y);
    if (r) { node = r.startContainer; offset = r.startOffset; }
  } else if (anyDoc.caretPositionFromPoint) {
    const p = anyDoc.caretPositionFromPoint(x, y);
    if (p) { node = p.offsetNode; offset = p.offset; }
  }
  if (!node || node.nodeType !== Node.TEXT_NODE) return null;
  const text = node.textContent ?? "";
  const isW = (c: string) => /[A-Za-z0-9_]/.test(c);
  let s = offset, e = offset;
  while (s > 0 && isW(text[s - 1])) s--;
  while (e < text.length && isW(text[e])) e++;
  if (s === e) return null;
  const word = text.slice(s, e);
  if (!/^[A-Za-z_]/.test(word)) return null;
  const range = document.createRange();
  range.setStart(node, s);
  range.setEnd(node, e);
  return { word, rect: range.getBoundingClientRect() };
}

interface Hover {
  word: string;
  entries: SymbolIndexEntry[];
  rect: DOMRect;         // viewport coords of the word
  local: DOMRect | null; // rect relative to code-wrap, for the underline
}

export default function CodeView({
  content, language, range, symbols, index, activeId, onGoto, onSelectSymbol, onExplain,
  highlight, onHighlight,
}: {
  content: string;
  language: string | null;
  range?: { start: number; end: number } | null;
  symbols?: SymbolSpan[];
  index?: SymbolIndex;
  activeId?: number | null;
  onGoto?: (path: string, symbolId: number) => void;
  onSelectSymbol?: (id: number) => void;
  onExplain?: (en: SymbolIndexEntry) => void;
  highlight?: { start: number; end: number } | null;
  onHighlight?: (range: { start: number; end: number } | null) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [ghost, setGhost] = useState<{ start: number; end: number } | null>(null);
  const pinned = useRef(false);   // keep card open while pointer is on it
  const anchorRef = useRef<number | null>(null);   // gutter click anchor for shift-click range extension

  const html = useMemo(() => {
    if (language === "python") {
      try { return hljs.highlight(content, { language: "python" }).value; } catch { /* fall through */ }
    }
    return escapeHtml(content);
  }, [content, language]);

  const lineCount = useMemo(() => content.split("\n").length, [content]);

  // definitions in this file, keyed by their start line, enriched with fan-in
  const defsByLine = useMemo(() => {
    const m = new Map<number, { sym: SymbolSpan; callers: number }>();
    for (const s of symbols ?? []) {
      const callers = index?.byId.get(s.id)?.callers ?? 0;
      m.set(s.start_line, { sym: s, callers });
    }
    return m;
  }, [symbols, index]);

  // The selected symbol's full span — drives the whole-function highlight.
  const activeSpan = useMemo(
    () => (activeId != null ? (symbols ?? []).find((s) => s.id === activeId) ?? null : null),
    [activeId, symbols],
  );

  // Symbol whose span contains a given line (for hover-to-highlight).
  const spanAtLine = useCallback(
    (line: number) => (symbols ?? []).find((s) => line >= s.start_line && line <= s.end_line) ?? null,
    [symbols],
  );

  useEffect(() => {
    if (range && scrollRef.current) {
      scrollRef.current.scrollTop = Math.max(0, (range.start - 1) * LH - 90);
    }
  }, [range]);

  // A line-range highlight (deep-linked or freshly selected) scrolls into view
  // the same way an active symbol does, but only when there's no symbol span
  // already driving the scroll — the two shouldn't fight over position.
  useEffect(() => {
    if (!range && highlight && scrollRef.current) {
      scrollRef.current.scrollTop = Math.max(0, (highlight.start - 1) * LH - 90);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight?.start, highlight?.end]);

  // Click a line number to select it; shift-click another to extend the range
  // — GitHub's line-permalink gesture. The gutter is the natural hit target
  // since it's already a dedicated, unambiguous per-line column.
  const onGutterClick = useCallback((n: number, shiftKey: boolean) => {
    if (!onHighlight) return;
    if (shiftKey && anchorRef.current != null) {
      const a = anchorRef.current;
      onHighlight({ start: Math.min(a, n), end: Math.max(a, n) });
      return;
    }
    anchorRef.current = n;
    onHighlight({ start: n, end: n });
  }, [onHighlight]);

  const clearHover = useCallback(() => {
    if (!pinned.current) setHover(null);
  }, []);

  // Hovering anywhere inside a function lights up its whole span — the code
  // reader reacts to where you are, not just to explicit clicks.
  const onMove = useCallback((e: React.MouseEvent) => {
    const wrap = wrapRef.current?.getBoundingClientRect();
    if (wrap) {
      const line = Math.floor((e.clientY - wrap.top - PAD_T) / LH) + 1;
      const sp = spanAtLine(line);
      setGhost((g) => {
        const next = sp ? { start: sp.start_line, end: sp.end_line } : null;
        if (g?.start === next?.start && g?.end === next?.end) return g;
        return next;
      });
    }
    if (!index) return;
    const hit = wordAtPoint(e.clientX, e.clientY);
    if (!hit) { setHover((h) => (h && !pinned.current ? null : h)); return; }
    const entries = index.byName.get(hit.word);
    if (!entries || entries.length === 0) {
      setHover((h) => (h && !pinned.current ? null : h));
      return;
    }
    setHover((h) => {
      if (h && h.word === hit.word && Math.abs(h.rect.left - hit.rect.left) < 1
        && Math.abs(h.rect.top - hit.rect.top) < 1) return h;
      const wrap = wrapRef.current?.getBoundingClientRect();
      const local = wrap
        ? new DOMRect(hit.rect.left - wrap.left, hit.rect.top - wrap.top, hit.rect.width, hit.rect.height)
        : null;
      return { word: hit.word, entries, rect: hit.rect, local };
    });
  }, [index]);

  const onClick = useCallback((e: React.MouseEvent) => {
    if (!index || !onGoto) return;
    if (window.getSelection()?.toString()) return; // don't hijack text selection
    const hit = wordAtPoint(e.clientX, e.clientY);
    if (!hit) return;
    const entries = index.byName.get(hit.word);
    if (!entries || entries.length === 0) return;
    e.preventDefault();
    const target = entries[0];
    onGoto(target.path, target.id);
  }, [index, onGoto]);

  return (
    <div className="code-scroll" ref={scrollRef} onScroll={() => { pinned.current = false; setHover(null); setGhost(null); }}>
      <div className="code-inner">
        <div className="gutter">
          {Array.from({ length: lineCount }, (_, i) => {
            const n = i + 1;
            const def = defsByLine.get(n);
            const inRange = activeSpan != null && n >= activeSpan.start_line && n <= activeSpan.end_line;
            const inHighlight = highlight != null && n >= highlight.start && n <= highlight.end;
            return (
              <div key={n} className={"gl" + (inRange ? " on" : "") + (inHighlight ? " lit" : "")}>
                {def && (
                  <button
                    className="rb"
                    title={`${def.sym.qualified_name} — used by ${def.callers} place${def.callers === 1 ? "" : "s"}`}
                    onClick={() => onSelectSymbol?.(def.sym.id)}
                  >
                    <span className="rb-count tnum">{def.callers}</span>
                    <span className="rb-dot" style={{ background: kindColor(def.sym.kind) }} />
                  </button>
                )}
                {onHighlight
                  ? <button className="ln ln-btn" onClick={(e) => onGutterClick(n, e.shiftKey)}>{n}</button>
                  : <span className="ln">{n}</span>}
              </div>
            );
          })}
        </div>

        <div className="code-wrap" ref={wrapRef}
          onMouseMove={onMove} onMouseLeave={() => { clearHover(); setGhost(null); }} onClick={onClick}>
          {range && (
            <div className="band" style={{ top: PAD_T + (range.start - 1) * LH, height: (range.end - range.start + 1) * LH }} />
          )}
          {ghost && !(range && range.start === ghost.start && range.end === ghost.end) && (
            <div className="band ghost" style={{ top: PAD_T + (ghost.start - 1) * LH, height: (ghost.end - ghost.start + 1) * LH }} />
          )}
          {highlight && (
            <>
              <div className="band select" style={{ top: PAD_T + (highlight.start - 1) * LH, height: (highlight.end - highlight.start + 1) * LH }} />
              <HighlightPill top={PAD_T + (highlight.start - 1) * LH} onClear={() => onHighlight?.(null)} />
            </>
          )}
          {hover?.local && (
            <div className="tok-underline"
              style={{ left: hover.local.left, top: hover.local.top + hover.local.height - 1, width: hover.local.width }} />
          )}
          <pre><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>
        </div>
      </div>

      {hover && (
        <PeekCard
          hover={hover}
          onEnter={() => { pinned.current = true; }}
          onLeave={() => { pinned.current = false; setHover(null); }}
          onGoto={(en) => { onGoto?.(en.path, en.id); pinned.current = false; setHover(null); }}
          onExplain={onExplain ? (en) => { onExplain(en); pinned.current = false; setHover(null); } : undefined}
        />
      )}
    </div>
  );
}

function HighlightPill({ top, onClear }: { top: number; onClear: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try { await navigator.clipboard.writeText(window.location.href); } catch { /* clipboard unavailable */ }
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="hl-pill" style={{ top }} onClick={(e) => e.stopPropagation()}>
      <button onClick={copy} title="Copy link to these lines">
        {copied ? <CheckIcon className="ok" /> : <CopyIcon />}
        {copied ? "Copied" : "Copy link"}
      </button>
      <button onClick={onClear} title="Clear highlight" className="hl-pill-x"><XIcon /></button>
    </div>
  );
}

function PeekCard({ hover, onEnter, onLeave, onGoto, onExplain }: {
  hover: Hover;
  onEnter: () => void;
  onLeave: () => void;
  onGoto: (en: SymbolIndexEntry) => void;
  onExplain?: (en: SymbolIndexEntry) => void;
}) {
  const { rect, entries } = hover;
  const width = 360;
  const left = Math.min(rect.left, window.innerWidth - width - 14);
  const below = rect.bottom + 8;
  const flipUp = below > window.innerHeight - 160;
  const style: React.CSSProperties = flipUp
    ? { left, bottom: window.innerHeight - rect.top + 8, width }
    : { left, top: below, width };

  return (
    <div className="peek" style={style} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      {entries.length > 1 && <div className="peek-multi">{entries.length} definitions</div>}
      {entries.slice(0, 4).map((en) => (
        <div key={en.id} className="peek-item">
          <div className="peek-head" onClick={() => onGoto(en)}>
            <span className="peek-dot" style={{ background: kindColor(en.kind) }} />
            <span className="peek-qn">{en.qualified_name}</span>
            <span className="peek-kind">{en.kind}</span>
          </div>
          {en.signature && <div className="peek-sig" onClick={() => onGoto(en)}>{en.signature}</div>}
          {en.doc && <div className="peek-doc" onClick={() => onGoto(en)}>{en.doc}</div>}
          <div className="peek-foot">
            <span onClick={() => onGoto(en)}>
              <span className="peek-loc">{en.path.replace(/^src\//, "")}:{en.line}</span>
              {" · "}
              <span className="peek-refs">{en.callers} ref{en.callers === 1 ? "" : "s"} → click to open</span>
            </span>
            {onExplain && (
              <button className="peek-explain" onClick={() => onExplain(en)}>
                <SparkleIcon style={{ width: 12, height: 12 }} /> Explain
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
