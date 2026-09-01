import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BlameLine, SymbolIndex, SymbolIndexEntry, SymbolSpan } from "../lib/types";
import { highlightCode, splitHighlightedByLine } from "../lib/highlight";
import { CheckIcon, ChevronIcon, CopyIcon, SearchIcon, SparkleIcon, SplitIcon, XIcon } from "./icons";

const LH = 20;      // line-height, must match .cv-line line-height
const PAD_T = 12;   // top padding of code-wrap / gutter
const MIN_FOLD_LINES = 3; // a symbol must hide at least this many body lines to be worth folding

const KIND_COLOR: Record<string, string> = {
  class: "var(--c-sansio)", method: "var(--c-core)", function: "var(--c-json)",
};
const kindColor = (k: string) => KIND_COLOR[k] ?? "var(--text-3)";

function relDate(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso + "T00:00:00Z").getTime()) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

/** A stable, distinct hue per author from their name — for a quiet color cue
 *  in the blame column without a fixed author→color table to maintain. */
function authorHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return h;
}

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

type Row = { kind: "line"; line: number } | { kind: "fold"; symbolId: number; from: number; to: number };

export default function CodeView({
  content, language, range, symbols, index, activeId, onGoto, onGotoBeside, onSelectSymbol, onExplain,
  highlight, onHighlight, blame,
}: {
  content: string;
  language: string | null;
  range?: { start: number; end: number } | null;
  symbols?: SymbolSpan[];
  index?: SymbolIndex;
  activeId?: number | null;
  onGoto?: (path: string, symbolId: number) => void;
  /** Alt/Cmd-click on a go-to-definition target — opens beside instead of replacing. */
  onGotoBeside?: (path: string, symbolId: number) => void;
  onSelectSymbol?: (id: number) => void;
  onExplain?: (en: SymbolIndexEntry) => void;
  highlight?: { start: number; end: number } | null;
  onHighlight?: (range: { start: number; end: number } | null) => void;
  /** Real per-line git blame — only rendered (as a gutter column) when provided. */
  blame?: BlameLine[];
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [ghost, setGhost] = useState<{ start: number; end: number } | null>(null);
  const pinned = useRef(false);   // keep card open while pointer is on it
  const anchorRef = useRef<number | null>(null);   // gutter click anchor for shift-click range extension
  const [folded, setFolded] = useState<Set<number>>(new Set()); // symbol ids currently collapsed
  const [find, setFind] = useState<{ open: boolean; query: string; active: number }>({ open: false, query: "", active: 0 });
  const findInputRef = useRef<HTMLInputElement>(null);

  const html = useMemo(() => highlightCode(content, language), [content, language]);
  const lineHtml = useMemo(() => splitHighlightedByLine(html), [html]);
  const rawLines = useMemo(() => content.split("\n"), [content]);
  const lineCount = rawLines.length;

  const blameByLine = useMemo(() => {
    const m = new Map<number, BlameLine>();
    for (const b of blame ?? []) m.set(b.line, b);
    return m;
  }, [blame]);

  // definitions in this file, keyed by their start line, enriched with fan-in
  const defsByLine = useMemo(() => {
    const m = new Map<number, { sym: SymbolSpan; callers: number }>();
    for (const s of symbols ?? []) {
      const callers = index?.byId.get(s.id)?.callers ?? 0;
      m.set(s.start_line, { sym: s, callers });
    }
    return m;
  }, [symbols, index]);

  // symbols worth a fold toggle (hide at least MIN_FOLD_LINES of body), by their start line
  const foldableByStart = useMemo(() => {
    const m = new Map<number, SymbolSpan>();
    for (const s of symbols ?? []) {
      if (s.end_line - s.start_line >= MIN_FOLD_LINES) m.set(s.start_line, s);
    }
    return m;
  }, [symbols]);

  const toggleFold = useCallback((symbolId: number) => {
    setFolded((f) => {
      const n = new Set(f);
      n.has(symbolId) ? n.delete(symbolId) : n.add(symbolId);
      return n;
    });
  }, []);

  // Auto-expand a fold if navigation (active symbol / highlight) lands inside it —
  // folding shouldn't be able to hide the very thing you just jumped to.
  useEffect(() => {
    if (folded.size === 0 || !symbols) return;
    const targets = [range?.start, highlight?.start].filter((x): x is number => x != null);
    if (targets.length === 0) return;
    setFolded((f) => {
      let changed = false;
      const next = new Set(f);
      for (const s of symbols) {
        if (next.has(s.id) && targets.some((t) => t > s.start_line && t <= s.end_line)) {
          next.delete(s.id);
          changed = true;
        }
      }
      return changed ? next : f;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range?.start, highlight?.start, symbols, folded.size]);

  // The visible row sequence (real lines interleaved with fold placeholders)
  // and a reverse line→row-index map, both rebuilt whenever folds change —
  // every Y-position calculation below (bands, hover, scroll-to) works in
  // row-index space, not raw file-line space, so a fold really does shrink
  // the document instead of just visually hiding text in place.
  const { visibleRows, rowIndexOfLine, foldRowForLine } = useMemo(() => {
    const rows: Row[] = [];
    const idx = new Map<number, number>();
    const hiddenIdx = new Map<number, number>();
    let n = 1;
    while (n <= lineCount) {
      const foldSym = foldableByStart.get(n);
      if (foldSym && folded.has(foldSym.id)) {
        idx.set(n, rows.length);
        rows.push({ kind: "line", line: n });
        const foldRow = rows.length;
        rows.push({ kind: "fold", symbolId: foldSym.id, from: n + 1, to: foldSym.end_line });
        for (let h = n + 1; h <= foldSym.end_line; h++) hiddenIdx.set(h, foldRow);
        n = foldSym.end_line + 1;
      } else {
        idx.set(n, rows.length);
        rows.push({ kind: "line", line: n });
        n++;
      }
    }
    return { visibleRows: rows, rowIndexOfLine: idx, foldRowForLine: hiddenIdx };
  }, [lineCount, foldableByStart, folded]);

  // Row index for ANY line — visible lines resolve directly, a line hidden
  // inside a collapsed fold resolves to that fold's single placeholder row,
  // so a band spanning into folded territory still ends at the right pixel
  // instead of either vanishing or reaching past the shrunk document.
  const rowIndexFor = useCallback(
    (line: number) => rowIndexOfLine.get(line) ?? foldRowForLine.get(line),
    [rowIndexOfLine, foldRowForLine],
  );

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

  const scrollToLine = useCallback((line: number, margin = 90) => {
    const row = rowIndexOfLine.get(line);
    if (row != null && scrollRef.current) {
      scrollRef.current.scrollTop = Math.max(0, PAD_T + row * LH - margin);
    }
  }, [rowIndexOfLine]);

  useEffect(() => {
    if (range) scrollToLine(range.start);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range?.start, rowIndexOfLine]);

  // A line-range highlight (deep-linked or freshly selected) scrolls into view
  // the same way an active symbol does, but only when there's no symbol span
  // already driving the scroll — the two shouldn't fight over position.
  useEffect(() => {
    if (!range && highlight) scrollToLine(highlight.start);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight?.start, rowIndexOfLine]);

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
      const rowIdx = Math.floor((e.clientY - wrap.top - PAD_T) / LH);
      const row = visibleRows[rowIdx];
      const line = row?.kind === "line" ? row.line : null;
      const sp = line != null ? spanAtLine(line) : null;
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
  }, [index, visibleRows, spanAtLine]);

  const onClick = useCallback((e: React.MouseEvent) => {
    if (!index || !onGoto) return;
    if (window.getSelection()?.toString()) return; // don't hijack text selection
    const hit = wordAtPoint(e.clientX, e.clientY);
    if (!hit) return;
    const entries = index.byName.get(hit.word);
    if (!entries || entries.length === 0) return;
    e.preventDefault();
    const target = entries[0];
    if ((e.altKey || e.metaKey) && onGotoBeside) onGotoBeside(target.path, target.id);
    else onGoto(target.path, target.id);
  }, [index, onGoto, onGotoBeside]);

  // ---- find-in-file ----------------------------------------------------
  const findMatches = useMemo(() => {
    const q = find.query.trim().toLowerCase();
    if (!q) return [] as number[];
    const out: number[] = [];
    for (let i = 0; i < rawLines.length; i++) {
      if (rawLines[i].toLowerCase().includes(q)) out.push(i + 1);
    }
    return out;
  }, [find.query, rawLines]);

  const activeMatchLine = findMatches.length ? findMatches[find.active % findMatches.length] : null;

  const stepFind = useCallback((dir: 1 | -1) => {
    setFind((f) => {
      if (findMatches.length === 0) return f;
      const n = (f.active + dir + findMatches.length) % findMatches.length;
      return { ...f, active: n };
    });
  }, [findMatches.length]);

  useEffect(() => {
    if (activeMatchLine != null) scrollToLine(activeMatchLine, 140);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeMatchLine, rowIndexOfLine]);

  useEffect(() => {
    if (find.open) findInputRef.current?.focus();
  }, [find.open]);

  // Gated on mouse hover (not document focus, since nothing here is ever
  // auto-focused) rather than a document.activeElement fallback — the naive
  // "no more specific element is focused" fallback would fire for EVERY
  // mounted CodeView at once when the split pane is open, opening two find
  // bars from one Ctrl+F. Hover scopes it to whichever pane you're over,
  // which is also just the more intuitive behavior with two panes visible.
  const hoveredRef = useRef(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f" && hoveredRef.current) {
        e.preventDefault();
        setFind((f) => ({ ...f, open: true }));
      } else if (e.key === "Escape" && find.open) {
        setFind((f) => ({ ...f, open: false }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [find.open]);

  return (
    <div className="code-scroll cv-root" ref={scrollRef} tabIndex={-1}
      onMouseEnter={() => { hoveredRef.current = true; }}
      onMouseLeave={() => { hoveredRef.current = false; }}
      onScroll={() => { pinned.current = false; setHover(null); setGhost(null); }}>
      {find.open && (
        <FindBar
          value={find.query}
          count={findMatches.length}
          active={findMatches.length ? (find.active % findMatches.length) + 1 : 0}
          onChange={(q) => setFind((f) => ({ ...f, query: q, active: 0 }))}
          onNext={() => stepFind(1)}
          onPrev={() => stepFind(-1)}
          onClose={() => setFind((f) => ({ ...f, open: false }))}
        />
      )}
      <div className="code-inner">
        {blame && (
          <div className="blame-col">
            {visibleRows.map((row) => {
              if (row.kind === "fold") return <div key={`bf${row.symbolId}`} className="bl-row bl-fold" />;
              const b = blameByLine.get(row.line);
              if (!b) return <div key={`b${row.line}`} className="bl-row" />;
              return (
                <div key={`b${row.line}`} className="bl-row"
                  style={{ borderLeftColor: `hsl(${authorHue(b.author)} 55% 55%)` }}
                  title={`${b.author} · ${b.date} · ${b.sha}\n${b.message}`}>
                  <span className="bl-author">{b.author}</span>
                  <span className="bl-date">{relDate(b.date)}</span>
                </div>
              );
            })}
          </div>
        )}
        <div className="gutter" style={blame ? { left: 176 } : undefined}>
          {visibleRows.map((row) => {
            if (row.kind === "fold") {
              return (
                <div key={`f${row.symbolId}`} className="gl cv-fold-gutter" onClick={() => toggleFold(row.symbolId)}
                  title="Expand">
                  <ChevronIcon className="fold-chev" />
                </div>
              );
            }
            const n = row.line;
            const def = defsByLine.get(n);
            const foldSym = foldableByStart.get(n);
            const isFolded = foldSym != null && folded.has(foldSym.id);
            const inRange = activeSpan != null && n >= activeSpan.start_line && n <= activeSpan.end_line;
            const inHighlight = highlight != null && n >= highlight.start && n <= highlight.end;
            const inFind = activeMatchLine === n;
            return (
              <div key={n} className={"gl" + (inRange ? " on" : "") + (inHighlight ? " lit" : "") + (inFind ? " found" : "")}>
                {foldSym && (
                  <button className="fold-btn" onClick={() => toggleFold(foldSym.id)}
                    title={isFolded ? "Expand" : "Collapse"}>
                    <ChevronIcon className={"fold-chev" + (isFolded ? "" : " open")} />
                  </button>
                )}
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
          {range && (() => {
            const top = rowIndexFor(range.start);
            const bottom = rowIndexFor(range.end);
            if (top == null || bottom == null) return null;
            return <div className="band" style={{ top: PAD_T + top * LH, height: (bottom - top + 1) * LH }} />;
          })()}
          {ghost && !(range && range.start === ghost.start && range.end === ghost.end) && (() => {
            const top = rowIndexFor(ghost.start);
            const bottom = rowIndexFor(ghost.end);
            if (top == null || bottom == null) return null;
            return <div className="band ghost" style={{ top: PAD_T + top * LH, height: (bottom - top + 1) * LH }} />;
          })()}
          {highlight && (() => {
            const top = rowIndexFor(highlight.start);
            const bottom = rowIndexFor(highlight.end);
            if (top == null || bottom == null) return null;
            return (
              <>
                <div className="band select" style={{ top: PAD_T + top * LH, height: (bottom - top + 1) * LH }} />
                <HighlightPill top={PAD_T + top * LH} onClear={() => onHighlight?.(null)} />
              </>
            );
          })()}
          {findMatches.map((n) => {
            const row = rowIndexFor(n);
            if (row == null) return null;
            return (
              <div key={`m${n}`} className={"band find" + (n === activeMatchLine ? " active" : "")}
                style={{ top: PAD_T + row * LH, height: LH }} />
            );
          })}
          {hover?.local && (
            <div className="tok-underline"
              style={{ left: hover.local.left, top: hover.local.top + hover.local.height - 1, width: hover.local.width }} />
          )}
          <div className="cv-lines">
            {visibleRows.map((row) => row.kind === "fold"
              ? (
                <div key={`f${row.symbolId}`} className="cv-line cv-fold-row" onClick={() => toggleFold(row.symbolId)}>
                  ⋯ {row.to - row.from + 1} lines hidden — click to expand
                </div>
              )
              : (
                <div key={row.line} className="cv-line hljs"
                  dangerouslySetInnerHTML={{ __html: lineHtml[row.line - 1] || "" }} />
              ))}
          </div>
        </div>
      </div>

      {hover && (
        <PeekCard
          hover={hover}
          onEnter={() => { pinned.current = true; }}
          onLeave={() => { pinned.current = false; setHover(null); }}
          onGoto={(en) => { onGoto?.(en.path, en.id); pinned.current = false; setHover(null); }}
          onGotoBeside={onGotoBeside ? (en) => { onGotoBeside(en.path, en.id); pinned.current = false; setHover(null); } : undefined}
          onExplain={onExplain ? (en) => { onExplain(en); pinned.current = false; setHover(null); } : undefined}
        />
      )}
    </div>
  );
}

function FindBar({ value, count, active, onChange, onNext, onPrev, onClose }: {
  value: string; count: number; active: number;
  onChange: (v: string) => void; onNext: () => void; onPrev: () => void; onClose: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);
  return (
    <div className="cv-find" onKeyDown={(e) => {
      if (e.key === "Enter") { e.preventDefault(); e.shiftKey ? onPrev() : onNext(); }
      if (e.key === "Escape") onClose();
    }}>
      <SearchIcon />
      <input ref={inputRef} value={value} onChange={(e) => onChange(e.target.value)}
        placeholder="Find in file…" aria-label="Find in file" />
      <span className="cv-find-count tnum">{count > 0 ? `${active}/${count}` : value ? "0/0" : ""}</span>
      <button onClick={onPrev} disabled={count === 0} title="Previous match (Shift+Enter)">‹</button>
      <button onClick={onNext} disabled={count === 0} title="Next match (Enter)">›</button>
      <button onClick={onClose} title="Close (Esc)"><XIcon /></button>
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

function PeekCard({ hover, onEnter, onLeave, onGoto, onGotoBeside, onExplain }: {
  hover: Hover;
  onEnter: () => void;
  onLeave: () => void;
  onGoto: (en: SymbolIndexEntry) => void;
  onGotoBeside?: (en: SymbolIndexEntry) => void;
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
            <span className="peek-actions">
              {onGotoBeside && (
                <button className="peek-beside" onClick={() => onGotoBeside(en)} title="Open beside">
                  <SplitIcon style={{ width: 12, height: 12 }} />
                </button>
              )}
              {onExplain && (
                <button className="peek-explain" onClick={() => onExplain(en)}>
                  <SparkleIcon style={{ width: 12, height: 12 }} /> Explain
                </button>
              )}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
