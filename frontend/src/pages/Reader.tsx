import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { buildSymbolIndex } from "../lib/symbolIndex";
import { emitExplain } from "../lib/explainBus";
import CodeView from "../components/CodeView";
import { CodeSkeleton } from "../components/PageState";
import {
  ArrowIcon, BookIcon, ChevronIcon, CopyIcon, CheckIcon, HistoryIcon, InboundIcon,
  OutboundIcon, SearchIcon, SparkleIcon, SplitIcon, TargetIcon, XIcon,
} from "../components/icons";
import type { FileContent, SymbolDetail, SymbolIndexEntry, SymbolRef, TreeFile } from "../lib/types";

/** A scoped "explain" question grounded in exactly what's on screen — the qualified
 *  name, kind and location — so the investigate engine's retrieval has a precise
 *  anchor instead of a vague symbol name that might collide elsewhere in the repo. */
const explainQuestion = (qualifiedName: string, kind: string, path: string, line: number) =>
  `Explain what \`${qualifiedName}\` (${kind} defined at ${path}:${line}) does, why it's implemented ` +
  `this way, and how it fits into the rest of the codebase — including its direct callers and callees.`;

const short = (p: string) => p.replace(/^src\//, "");
const dirOf = (p: string) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "(root)");
const baseOf = (p: string) => p.slice(p.lastIndexOf("/") + 1);

const KIND_COLOR: Record<string, string> = {
  class: "var(--c-sansio)", method: "var(--c-core)", function: "var(--c-json)",
};
const kindColor = (k: string) => KIND_COLOR[k] ?? "var(--text-3)";

/** "42" or "42-58" → a range; anything else → null. */
function parseLineParam(l: string | null): { start: number; end: number } | null {
  const m = l?.match(/^(\d+)(?:-(\d+))?$/);
  if (!m) return null;
  const start = Number(m[1]);
  return { start, end: m[2] ? Number(m[2]) : start };
}

export default function Reader() {
  const loc = useLocation();
  const nav = useNavigate();
  const [search, setSearch] = useSearchParams();
  const st0 = loc.state as { path?: string; symbolId?: number; line?: number } | null;
  const [path, setPath] = useState<string | undefined>(st0?.path ?? search.get("path") ?? undefined);
  const [symId, setSymId] = useState<number | null>(st0?.symbolId ?? null);
  const [filter, setFilter] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // A second, independent pane opened "beside" the main one (Alt/Cmd-click a
  // go-to-definition target, or the peek card's split button) — for comparing
  // a caller and callee, or a symbol's two definitions, without losing place.
  const [split, setSplit] = useState<{ path: string; symbolId: number | null } | null>(null);
  // Blame is real per-line git history, live-computed on demand — off by
  // default (this is a reading view, not a history tool) and toggled per file.
  const [blameOn, setBlameOn] = useState(false);
  // A manually-selected (or deep-linked) line range — GitHub-style permalink
  // gesture: click a gutter number, shift-click another, get a shareable URL.
  const [highlight, setHighlightState] = useState<{ start: number; end: number } | null>(
    () => (st0?.symbolId == null ? parseLineParam(search.get("L")) ?? (st0?.line != null ? { start: st0.line, end: st0.line } : null) : null),
  );

  const setHighlight = (range: { start: number; end: number } | null) => {
    setHighlightState(range);
    const next = new URLSearchParams(search);
    if (range && path) {
      next.set("path", path);
      next.set("L", range.start === range.end ? String(range.start) : `${range.start}-${range.end}`);
    } else {
      // No highlight → no reason for the URL to carry file state; back to
      // the normal in-app-navigation-only address bar.
      next.delete("L");
      next.delete("path");
    }
    setSearch(next, { replace: true });
  };

  // Apply deep-links (from Graph / Flow / Overview) on each navigation.
  useEffect(() => {
    const s = loc.state as { path?: string; symbolId?: number; line?: number } | null;
    if (s?.path) setPath(s.path);
    if (s?.symbolId != null) { setSymId(s.symbolId); setHighlightState(null); }
    else if (s?.line != null) setHighlightState({ start: s.line, end: s.line });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loc.key]);

  const treeQ = useQuery({ queryKey: ["tree"], queryFn: api.tree });
  const fileQ = useQuery({ queryKey: ["file", path], queryFn: () => api.file(path!), enabled: !!path });
  const symQ = useQuery({ queryKey: ["symbol", symId], queryFn: () => api.symbol(symId!), enabled: symId != null });
  const idxQ = useQuery({ queryKey: ["symbolIndex"], queryFn: api.symbolIndex, staleTime: Infinity });
  const fileQ2 = useQuery({
    queryKey: ["file", split?.path], queryFn: () => api.file(split!.path), enabled: !!split?.path,
  });
  const blameQ = useQuery({
    queryKey: ["blame", path], queryFn: () => api.blame(path!), enabled: blameOn && !!path,
  });

  // Client-side resolution maps (name→def, id→def) for hover-peek + go-to-def.
  const index = useMemo(
    () => (idxQ.data ? buildSymbolIndex(idxQ.data.symbols) : undefined),
    [idxQ.data],
  );

  useEffect(() => {
    if (!path && treeQ.data) {
      const f = treeQ.data.files;
      // Default to a source file in the primary package (src/…), not a test or
      // a hardcoded repo path — so any ingested repo opens sensibly.
      const pref = f.find((x) => x.category === "code" && x.path.startsWith("src/"))
        ?? f.find((x) => x.category === "code") ?? f[0];
      if (pref) setPath(pref.path);
    }
  }, [treeQ.data, path]);

  const groups = useMemo(() => {
    const files = (treeQ.data?.files ?? []).filter((f) => f.category !== "other");
    const q = filter.trim().toLowerCase();
    const matched = q ? files.filter((f) => f.path.toLowerCase().includes(q)) : files;
    const map = new Map<string, TreeFile[]>();
    for (const f of matched) {
      const d = dirOf(f.path);
      (map.get(d) ?? map.set(d, []).get(d)!).push(f);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [treeQ.data, filter]);

  const toggleDir = (d: string) =>
    setCollapsed((c) => { const n = new Set(c); n.has(d) ? n.delete(d) : n.add(d); return n; });

  const selectFile = (p: string) => { setPath(p); setSymId(null); setHighlight(null); };
  const gotoRef = (r: SymbolRef) => { if (r.file_path !== path) setPath(r.file_path); setSymId(r.id); setHighlight(null); };
  // go-to-definition from an inline click / peek card (may cross files)
  const goto = (p: string, id: number) => { if (p !== path) setPath(p); setSymId(id); setHighlight(null); };
  // same, but into the secondary pane instead of replacing the primary one
  const openBeside = (p: string, id: number) => setSplit({ path: p, symbolId: id });
  const gotoInSplit = (p: string, id: number) => setSplit({ path: p, symbolId: id });
  const explainEntry = (en: SymbolIndexEntry) =>
    emitExplain(explainQuestion(en.qualified_name, en.kind, en.path, en.line));
  const explainSymbol = (s: SymbolDetail) =>
    emitExplain(explainQuestion(s.qualified_name, s.kind, s.file_path, s.start_line));

  // Prefer the file's own symbol span (instant, no fetch) so scroll + highlight
  // land immediately; fall back to the fetched symbol detail for cross-file jumps.
  const localSpan = symId != null ? fileQ.data?.symbols.find((x) => x.id === symId) : undefined;
  const range = localSpan
    ? { start: localSpan.start_line, end: localSpan.end_line }
    : symQ.data && symQ.data.file_path === path
      ? { start: symQ.data.start_line, end: symQ.data.end_line }
      : null;

  const splitSpan = split?.symbolId != null ? fileQ2.data?.symbols.find((x) => x.id === split.symbolId) : undefined;
  const splitRange = splitSpan ? { start: splitSpan.start_line, end: splitSpan.end_line } : null;

  return (
    <div className="reader-grid">
      <div className="rd-tree">
        <div className="rd-filter">
          <SearchIcon />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter files…"
            aria-label="Filter files"
          />
          {filter && (
            <button className="rd-filter-x" onClick={() => setFilter("")} aria-label="Clear filter">
              <XIcon />
            </button>
          )}
        </div>
        <div className="rd-tree-scroll">
          {groups.length === 0 && <div className="rd-empty-sm">No files match “{filter}”.</div>}
          {groups.map(([dir, files]) => {
            const closed = collapsed.has(dir);
            return (
              <div key={dir} className="rd-dir">
                <button className="rd-grp" onClick={() => toggleDir(dir)}>
                  <ChevronIcon className={"rd-chev" + (closed ? "" : " open")} />
                  <span>{short(dir)}</span>
                  <span className="rd-grp-n tnum">{files.length}</span>
                </button>
                {!closed && files.map((f) => (
                  <div
                    key={f.path}
                    className={"rd-file" + (f.path === path ? " on" : "")}
                    onClick={() => selectFile(f.path)}
                  >
                    <span className="rd-file-name">{baseOf(f.path)}</span>
                    {f.symbols > 0 && <span className="rd-file-n tnum">{f.symbols}</span>}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>

      <div className="rd-panes">
        <div className="rd-code-pane">
          <div className="rd-toolbar">
            <span className="crumb">
              {path
                ? <>
                    <span className="cdim">{dirOf(path).replace(/^src\//, "src/")}</span>
                    <span className="csep">/</span>
                    <span className="cfile">{baseOf(path)}</span>
                    {symId != null && localSpan && (
                      <><span className="csep">›</span><span className="csym">{localSpan.name}</span></>
                    )}
                  </>
                : "—"}
            </span>
            <div className="rd-toolbar-actions">
              {fileQ.data && <span className="chip tnum">{fileQ.data.symbols.length} symbols · {fileQ.data.loc} loc</span>}
              {path && (
                <button
                  className={"rd-iconbtn" + (blameOn ? " on" : "")}
                  onClick={() => setBlameOn((v) => !v)}
                  title={blameOn ? "Hide git blame" : "Show git blame"}
                  aria-label="Toggle git blame"
                >
                  {blameQ.isFetching && blameOn ? <span className="spin" style={{ width: 12, height: 12 }} /> : <HistoryIcon />}
                </button>
              )}
              {path && <CopyPathButton path={path} />}
            </div>
          </div>
          {fileQ.isLoading && <CodeSkeleton />}
          {fileQ.data && (
            <CodeView
              content={fileQ.data.content ?? ""}
              language={fileQ.data.language}
              range={range}
              symbols={fileQ.data.symbols}
              index={index}
              activeId={symId}
              onGoto={goto}
              onGotoBeside={openBeside}
              onSelectSymbol={setSymId}
              onExplain={explainEntry}
              highlight={highlight}
              onHighlight={setHighlight}
              blame={blameOn ? blameQ.data?.lines : undefined}
            />
          )}
        </div>

        {split && (
          <div className="rd-code-pane split">
            <div className="rd-toolbar">
              <span className="crumb">
                <SplitIcon style={{ width: 12, height: 12, color: "var(--text-3)", marginRight: 6, verticalAlign: -1 }} />
                <span className="cdim">{dirOf(split.path).replace(/^src\//, "src/")}</span>
                <span className="csep">/</span>
                <span className="cfile">{baseOf(split.path)}</span>
              </span>
              <div className="rd-toolbar-actions">
                {fileQ2.data && <span className="chip tnum">{fileQ2.data.symbols.length} symbols · {fileQ2.data.loc} loc</span>}
                <button className="rd-iconbtn" onClick={() => setSplit(null)} title="Close split pane" aria-label="Close split pane">
                  <XIcon />
                </button>
              </div>
            </div>
            {fileQ2.isLoading && <CodeSkeleton />}
            {fileQ2.data && (
              <CodeView
                content={fileQ2.data.content ?? ""}
                language={fileQ2.data.language}
                range={splitRange}
                symbols={fileQ2.data.symbols}
                index={index}
                activeId={split.symbolId}
                onGoto={gotoInSplit}
                onSelectSymbol={(id) => setSplit((s) => (s ? { ...s, symbolId: id } : s))}
                onExplain={explainEntry}
              />
            )}
          </div>
        )}
      </div>

      <div className="rd-ctx">
        <Fade contentKey={symQ.data && symQ.data.file_path === path ? `s${symQ.data.id}` : "outline"}>
          {symQ.data && symQ.data.file_path === path
            ? <SymbolPanel s={symQ.data} onBack={() => setSymId(null)} onRef={gotoRef}
                onFlow={(id, label) => nav("/flow", { state: { symbolId: id, label } })}
                onImpact={(id) => nav("/impact", { state: { symbolId: id } })}
                onExplain={() => explainSymbol(symQ.data)} />
            : fileQ.data
              ? <Outline file={fileQ.data} activeId={symId} onPick={setSymId} />
              : (
                <div className="rd-empty">
                  <BookIcon />
                  <p>Select a file to read.</p>
                </div>
              )}
        </Fade>
      </div>
    </div>
  );
}

/** Cross-fades its children whenever `contentKey` changes — the Outline↔Symbol
 *  swap should feel like a soft transition, not an instant jump-cut. */
function Fade({ contentKey, children }: { contentKey: string; children: React.ReactNode }) {
  const [key, setKey] = useState(contentKey);
  const [phase, setPhase] = useState<"in" | "out">("in");
  const pending = useRef(contentKey);
  pending.current = contentKey;

  useEffect(() => {
    if (contentKey === key) return;
    setPhase("out");
    const t = setTimeout(() => { setKey(pending.current); setPhase("in"); }, 110);
    return () => clearTimeout(t);
  }, [contentKey, key]);

  return <div className={"rd-fade " + phase} key={key}>{children}</div>;
}

function CopyPathButton({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(path); } catch { /* clipboard unavailable */ }
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <button className="rd-iconbtn" onClick={copy} title="Copy file path" aria-label="Copy file path">
      {copied ? <CheckIcon className="ok" /> : <CopyIcon />}
    </button>
  );
}

function Outline({ file, activeId, onPick }: {
  file: FileContent; activeId: number | null; onPick: (id: number) => void;
}) {
  return (
    <div>
      <div className="rd-panel-head">
        <span className="eyebrow">Outline</span>
        <span className="rd-count tnum">{file.symbols.length}</span>
      </div>
      {file.symbols.length === 0 && <div className="rd-empty-sm">No symbols in this file.</div>}
      <div className="rd-outline-list">
        {file.symbols.map((s) => (
          <div
            key={s.id}
            className={"rd-row" + (s.id === activeId ? " on" : "")}
            onClick={() => onPick(s.id)}
            title={s.qualified_name}
          >
            <KindChip kind={s.kind} />
            <span className="rd-row-name">{s.qualified_name}</span>
            <span className="rd-row-loc tnum">L{s.start_line}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SymbolPanel({ s, onBack, onRef, onFlow, onImpact, onExplain }: {
  s: SymbolDetail; onBack: () => void; onRef: (r: SymbolRef) => void; onFlow: (id: number, label: string) => void;
  onImpact: (id: number) => void; onExplain: () => void;
}) {
  return (
    <div>
      <button className="rd-back" onClick={onBack}>
        <ArrowIcon style={{ transform: "rotate(180deg)", width: 13, height: 13 }} /> Outline
      </button>

      <div className="rd-sym-head">
        <KindChip kind={s.kind} big />
        <div className="rd-sym-titles">
          <div className="rd-sym-name">{s.qualified_name}</div>
          <div className="rd-sym-meta">{s.kind} · line {s.start_line}</div>
        </div>
      </div>

      {s.signature && <pre className="rd-sig">{s.signature}</pre>}

      {s.docstring && (
        <div className="rd-doc-box">
          <p>{firstSentence(s.docstring)}</p>
        </div>
      )}

      <div className="rd-sym-actions">
        <button className="btn rd-explain-btn" onClick={onExplain}>
          <SparkleIcon style={{ width: 14, height: 14 }} /> Explain this
        </button>
        <button className="btn rd-impact-btn" onClick={() => onImpact(s.id)}>
          <TargetIcon style={{ width: 14, height: 14 }} /> Is it safe to change this?
        </button>
        {s.callees.length > 0 && (
          <button className="btn primary rd-flow-btn" onClick={() => onFlow(s.id, s.qualified_name)}>
            View call flow <ArrowIcon style={{ width: 14, height: 14 }} />
          </button>
        )}
      </div>

      <RefSection
        icon={<InboundIcon />}
        label="Called by"
        hint="breaks if removed"
        count={s.callers.length}
        empty="Nothing internal calls this."
        refs={s.callers}
        onRef={onRef}
      />
      <RefSection
        icon={<OutboundIcon />}
        label="Calls into"
        count={s.callees.length}
        empty="Nothing resolved internally."
        refs={s.callees}
        onRef={onRef}
      />
    </div>
  );
}

function RefSection({ icon, label, hint, count, empty, refs, onRef }: {
  icon: React.ReactNode; label: string; hint?: string; count: number; empty: string;
  refs: SymbolRef[]; onRef: (r: SymbolRef) => void;
}) {
  return (
    <div className="rd-section">
      <div className="rd-section-head">
        {icon}
        <span>{label}</span>
        {hint && <span className="rd-hint">{hint}</span>}
        <span className="rd-count tnum">{count}</span>
      </div>
      {refs.length
        ? refs.map((r) => <RefRow key={r.id + label} r={r} onRef={onRef} />)
        : <div className="rd-empty-sm">{empty}</div>}
    </div>
  );
}

function RefRow({ r, onRef }: { r: SymbolRef; onRef: (r: SymbolRef) => void }) {
  const fuzzy = r.confidence < 0.8;
  return (
    <div className="rd-row" onClick={() => onRef(r)} title={`${r.file_path}:${r.start_line}`}>
      <KindChip kind={r.kind} />
      <span className="rd-row-name">{r.qualified_name}</span>
      {fuzzy && <span className="rd-fuzzy-dot" title="Ambiguous name match" />}
      <span className="rd-row-loc">{baseOf(r.file_path)}</span>
    </div>
  );
}

function KindChip({ kind, big }: { kind: string; big?: boolean }) {
  return (
    <span
      className={"rd-kind-chip" + (big ? " big" : "")}
      style={{ background: `color-mix(in srgb, ${kindColor(kind)} 16%, transparent)`, color: kindColor(kind) }}
    >
      {kind[0].toUpperCase()}
    </span>
  );
}

function firstSentence(doc: string) {
  const clean = doc.trim().replace(/\s+/g, " ");
  const m = clean.match(/^.*?[.!?](\s|$)/);
  return (m ? m[0] : clean).slice(0, 240);
}
