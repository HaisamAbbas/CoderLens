import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import CodemapView from "../components/CodemapView";
import PhysicalFlow from "../components/PhysicalFlow";
import SimulationPanel from "../components/SimulationPanel";
import { classifyRole } from "../lib/codemapRoles";
import { ErrorState, PageLoading } from "../components/PageState";
import { highlightCode } from "../lib/highlight";
import type { Codemap, CodemapNode, FlowData, SimStep } from "../lib/types";

const STEP_MS = 3200;

/** The real dependency-graph call flow (`/api/callgraph/{id}`) is, and always
 *  has been, 100% mechanical — nodes/edges from tree-sitter + the resolved
 *  call graph, zero LLM involvement anywhere in this path. What used to be
 *  weak was the RENDERING: a static, pre-upgrade SVG with no pan/zoom, no
 *  walkthrough, no interactivity. This reshapes the same real data into the
 *  Codemap shape and renders it through the same interactive engine
 *  (CodemapView + PhysicalFlow) already built for the question-based
 *  Codemap — mechanical role/icon classification only, no AI concept cards
 *  or generated images (this view never calls into that pipeline). */
function toCodemap(data: FlowData, title: string): Codemap {
  const nodes: CodemapNode[] = data.nodes.map((n) => ({
    id: n.id, qualified_name: n.qualified_name, name: n.name, kind: n.kind,
    file: n.file, line: n.line, step: n.depth, note: "",
    ...classifyRole(n.qualified_name, n.file, n.kind),
  }));
  return { question: title, title, narrative: "", nodes, edges: data.edges, curated: false };
}

export default function Flow() {
  const loc = useLocation();
  const nav = useNavigate();
  const st = loc.state as { symbolId?: number; label?: string; filePath?: string } | null;
  const symbolId = st?.symbolId;
  const filePath = st?.filePath;

  // Two entry points into the same rendering engine: a single symbol's real
  // downstream call graph (`/api/callgraph`), or — from Explorer's "zoom into
  // symbols" — every symbol in a whole file plus the real edges between them
  // (`/api/codemap/file`, the same mechanical builder Codemap's file mode
  // uses). Both are 100% real/mechanical; this page never distinguishes them
  // past this point, they just feed the same `base` shape.
  const symbolQ = useQuery({
    queryKey: ["callgraph", symbolId],
    queryFn: () => api.callgraph(symbolId!, 3),
    enabled: symbolId != null,
  });
  const fileQ = useQuery({
    queryKey: ["codemap-file-flow", filePath],
    queryFn: () => api.codemapFile(filePath!),
    enabled: filePath != null,
  });

  const isLoading = filePath != null ? fileQ.isLoading : symbolQ.isLoading;
  const error = filePath != null ? fileQ.error : symbolQ.error;
  const refetch = filePath != null ? fileQ.refetch : symbolQ.refetch;

  const base = useMemo<Codemap | null>(() => {
    if (filePath != null) return fileQ.data ?? null;
    if (symbolId != null && symbolQ.data) return toCodemap(symbolQ.data, st?.label ?? `Symbol #${symbolId}`);
    return null;
  }, [filePath, fileQ.data, symbolId, symbolQ.data, st?.label]);

  const [extra, setExtra] = useState<{ nodes: CodemapNode[]; edges: { source: number; target: number; confidence: number }[] }>({ nodes: [], edges: [] });
  const [step, setStepState] = useState(-1);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [expanding, setExpanding] = useState(false);
  const [edgePopup, setEdgePopup] = useState<
    { source: number; target: number; x: number; y: number; loading: boolean; text: string; error: string } | null
  >(null);
  // ── simulation (mirrors Codemap): the coherent trace is built ONCE, lazily,
  // when a node is first clicked; Play stays instant. Panes let either the
  // diagram or the output fill the screen.
  const [trace, setTrace] = useState<import("../lib/types").SimulationTrace | null>(null);
  const [simLoading, setSimLoading] = useState(false);
  const [simError, setSimError] = useState("");
  const [mainPane, setMainPane] = useState<"split" | "diagram" | "output">("split");
  // Output and source used to render stacked, always both, in the same
  // already-cramped dock — now a tab, same idea as the sidebar's own tabs.
  const [dockTab, setDockTab] = useState<"output" | "code">("output");
  const [sidebarTab, setSidebarTab] = useState<"steps" | "about">("steps");
  const [extendQ, setExtendQ] = useState("");
  const [extending, setExtending] = useState(false);
  const [extendNote, setExtendNote] = useState("");
  const traceRef = useRef<import("../lib/types").SimulationTrace | null>(null); traceRef.current = trace;

  const graph = useMemo<Codemap | null>(() => {
    if (!base) return null;
    const nodes = [...base.nodes];
    const ids = new Set(nodes.map((x) => x.id));
    for (const n of extra.nodes) if (!ids.has(n.id)) { nodes.push(n); ids.add(n.id); }
    const eset = new Set(base.edges.map((e) => `${e.source}-${e.target}`));
    const edges = [...base.edges];
    for (const e of extra.edges) { const k = `${e.source}-${e.target}`; if (!eset.has(k)) { edges.push(e); eset.add(k); } }
    return { ...base, nodes, edges };
  }, [base, extra]);

  const ordered = base?.nodes ?? [];
  const n = ordered.length;
  const stepRef = useRef(step); stepRef.current = step;
  const selectedNode = useMemo(() => graph?.nodes.find((x) => x.id === selectedId) ?? null, [graph, selectedId]);

  useEffect(() => {
    setExtra({ nodes: [], edges: [] }); setStepState(-1); setSelectedId(null); setPlaying(false); setEdgePopup(null);
    setTrace(null); setSimLoading(false); setSimError(""); setMainPane("split"); setDockTab("output");
    setSidebarTab("steps"); setExtendQ(""); setExtendNote("");
  }, [base]);

  // Arriving from the Reader on a single symbol, land ON the root symbol you
  // came in on — so the code + input/output panel open immediately, instead
  // of an empty diagram waiting for a click. A whole-file map (from
  // Explorer's zoom) has no single "root" to land on — it opens unselected,
  // same as Codemap's own file mode.
  useEffect(() => {
    if (!base || !base.nodes.length || filePath != null) return;
    const rootId = symbolQ.data?.root ?? base.nodes[0].id;
    setSelectedId((cur) => (cur == null ? rootId : cur));
    setStepState((cur) => (cur < 0 ? Math.max(0, base.nodes.findIndex((x) => x.id === rootId)) : cur));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, filePath]);

  useEffect(() => {
    if (!edgePopup) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setEdgePopup(null); };
    const onDown = (e: MouseEvent) => { if (!(e.target as Element).closest(".cm-edge-popup, .cm-edge-hit")) setEdgePopup(null); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [edgePopup]);

  const goStep = useCallback((i: number) => {
    if (!n) return;
    const c = Math.max(0, Math.min(n - 1, i));
    setPlaying(false); setStepState(c); setSelectedId(ordered[c]?.id ?? null);
  }, [n, ordered]);

  useEffect(() => {
    if (!playing) return;
    if (step >= n - 1) { setPlaying(false); return; }
    const t = setTimeout(() => { const c = Math.min(n - 1, (step < 0 ? 0 : step) + 1); setStepState(c); setSelectedId(ordered[c]?.id ?? null); }, STEP_MS);
    return () => clearTimeout(t);
  }, [playing, step, n, ordered]);

  const play = useCallback(() => {
    setPlaying((p) => {
      if (!p && step < 0) { setStepState(0); setSelectedId(ordered[0]?.id ?? null); }
      return !p;
    });
  }, [step, ordered]);

  const replay = useCallback(() => {
    if (!n) return;
    setStepState(0); setSelectedId(ordered[0]?.id ?? null); setPlaying(true);
  }, [n, ordered]);

  // Build the coherent trace once when a node is first clicked (never blocks Play).
  const ensureSim = useCallback(async (ids: number[]) => {
    if (traceRef.current || simLoading || !ids.length) return;
    setSimLoading(true); setSimError("");
    try {
      const r = await api.simulate(ids, st?.label ?? base?.title ?? "");
      setTrace(r);
    } catch (e) {
      setSimError(e instanceof Error ? e.message : String(e));
    } finally {
      setSimLoading(false);
    }
  }, [simLoading, st?.label, base?.title]);

  useEffect(() => {
    if (selectedId == null || playing) return;
    ensureSim(ordered.map((x) => x.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, playing]);

  const simByNode = useMemo<Map<number, SimStep>>(
    () => new Map((trace?.steps ?? []).map((s) => [s.node_id, s])), [trace]);
  const activeStep = selectedId != null ? simByNode.get(selectedId) ?? null : null;
  const curOrderIdx = selectedNode ? ordered.findIndex((x) => x.id === selectedNode.id) : -1;
  const nextNode = curOrderIdx >= 0 ? ordered[curOrderIdx + 1] ?? null : null;

  useEffect(() => {
    if (!base) return;
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "TEXTAREA" || tag === "INPUT") return;
      if (e.key === "ArrowRight") { e.preventDefault(); goStep((stepRef.current < 0 ? -1 : stepRef.current) + 1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); goStep(stepRef.current - 1); }
      else if (e.key === " ") { e.preventDefault(); play(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [base, goStep, play]);

  const open = (node: CodemapNode) => nav("/reader", { state: { path: node.file, symbolId: node.id } });
  const openFileMap = (node: CodemapNode) => nav("/codemap", { state: { filePath: node.file } });
  const selectNode = (node: CodemapNode) => {
    setPlaying(false); setSelectedId(node.id);
    const i = ordered.findIndex((x) => x.id === node.id);
    if (i >= 0) setStepState(i);
  };

  const expand = async () => {
    if (selectedId == null || !graph) return;
    const sel = graph.nodes.find((x) => x.id === selectedId); if (!sel) return;
    setExpanding(true);
    try {
      const d = await api.symbol(selectedId);
      const have = new Set(graph.nodes.map((x) => x.id));
      const newNodes: CodemapNode[] = [], newEdges: { source: number; target: number; confidence: number }[] = [];
      for (const c of (d.callers ?? []).slice(0, 6)) {
        if (!have.has(c.id)) {
          newNodes.push({ id: c.id, qualified_name: c.qualified_name, name: c.qualified_name.split(/[./]/).filter(Boolean).pop() ?? c.qualified_name,
            kind: c.kind, file: c.file_path, line: c.start_line, step: sel.step - 1, note: "", ...classifyRole(c.qualified_name, c.file_path, c.kind) });
          have.add(c.id);
        }
        newEdges.push({ source: c.id, target: sel.id, confidence: c.confidence ?? 1 });
      }
      for (const c of (d.callees ?? []).slice(0, 6)) {
        if (!have.has(c.id)) {
          newNodes.push({ id: c.id, qualified_name: c.qualified_name, name: c.qualified_name.split(/[./]/).filter(Boolean).pop() ?? c.qualified_name,
            kind: c.kind, file: c.file_path, line: c.start_line, step: sel.step + 1, note: "", ...classifyRole(c.qualified_name, c.file_path, c.kind) });
          have.add(c.id);
        }
        newEdges.push({ source: sel.id, target: c.id, confidence: c.confidence ?? 1 });
      }
      setExtra((p) => ({ nodes: [...p.nodes, ...newNodes], edges: [...p.edges, ...newEdges] }));
    } finally { setExpanding(false); }
  };

  const extendMap = async () => {
    const question = extendQ.trim();
    if (!question || !graph) return;
    setExtending(true); setExtendNote("");
    try {
      const existingIds = graph.nodes.map((x) => x.id);
      const r = await api.extendCodemap(question, existingIds);
      if (!r.nodes.length) { setExtendNote("Nothing new found for that follow-up."); return; }
      const rightmost = Math.max(0, ...graph.nodes.map((x) => x.step)) + 1;
      const newNodes = r.nodes.map((nd) => ({ ...nd, step: rightmost }));
      setExtra((p) => ({ nodes: [...p.nodes, ...newNodes], edges: [...p.edges, ...r.edges] }));
      setExtendNote(r.note || `Added ${r.nodes.length} related symbol${r.nodes.length === 1 ? "" : "s"}.`);
      setExtendQ("");
    } catch (e) {
      setExtendNote(e instanceof Error ? e.message : String(e));
    } finally {
      setExtending(false);
    }
  };

  const explainEdge = async (sourceId: number, targetId: number, x: number, y: number) => {
    setEdgePopup({ source: sourceId, target: targetId, x, y, loading: true, text: "", error: "" });
    try {
      const r = await api.explainEdge(sourceId, targetId);
      setEdgePopup((p) => (p && p.source === sourceId && p.target === targetId ? { ...p, loading: false, text: r.text, error: r.error ?? "" } : p));
    } catch (e) {
      setEdgePopup((p) => (p && p.source === sourceId && p.target === targetId ? { ...p, loading: false, error: e instanceof Error ? e.message : String(e) } : p));
    }
  };

  if (symbolId == null && filePath == null)
    return (
      <div className="page">
        <div className="eyebrow">Call flow</div>
        <div className="state">
          Open a call flow from a symbol in the <b>Reader</b>, an entrypoint on the <b>Overview</b>,
          or "Zoom into symbols" on a file in the <b>Explorer</b>.
        </div>
      </div>
    );

  const title = filePath != null ? (base?.title ?? filePath.split("/").pop()) : (st?.label ?? `Symbol #${symbolId}`);

  return (
    <div className="page cm-page">
      <div className="cm-head">
        <div>
          <div className="eyebrow">{filePath != null ? "Call flow · file" : "Call flow · downstream"}</div>
          <h1 className="h1" style={{ marginTop: 6 }}>{title}</h1>
        </div>
      </div>

      {isLoading && <PageLoading header={false} tiles={0} />}
      {error && <ErrorState message={error instanceof Error ? error.message : undefined} onRetry={() => refetch()} />}

      {graph && (n ? (
        <>
          <div className="cm-player">
            <div className="cm-transport">
              <button className="cm-play" onClick={play} aria-label={playing ? "Pause" : "Play"}>{playing ? "⏸" : "▶"}</button>
              <button className="cm-nav" onClick={() => goStep(step - 1)} disabled={step <= 0} aria-label="Previous">‹</button>
              <button className="cm-nav" onClick={() => goStep(step + 1)} disabled={step >= n - 1} aria-label="Next">›</button>
              <button className="cm-nav" onClick={replay} disabled={!n} aria-label="Replay" title="Replay from the start">↺</button>
            </div>
            <input
              className="cm-scrub" type="range" min={0} max={Math.max(0, n - 1)} value={Math.max(0, step)}
              onChange={(e) => goStep(Number(e.target.value))} aria-label="Walkthrough step"
            />
            <span className="cm-progress">{step >= 0 ? `${step + 1} / ${n}` : `${n} steps`}</span>
            <div className="cm-info">
              <button className="cm-info-btn" aria-label="About this view">ⓘ</button>
              <div className="cm-info-pop">
                <p>Every real, resolved call {filePath != null ? "between these functions" : "this makes"}, in true
                  execution/discovery order — nothing here is AI-generated, it's the real dependency graph.</p>
                <p>Click a node to focus it, simulate its data flow, and <b>Expand</b> its real callers/callees.
                  Click an edge to ask why the call happens.</p>
                <p><span className="cm-dot solid" /> confidently resolved call &nbsp; <span className="cm-dot dashed" /> ambiguous name match</p>
              </div>
            </div>
          </div>

          <div className="cm-layout">
            <div className={"cm-main pane-" + mainPane}>
              <div className="cm-pane cm-pane-diagram">
                <div className="cm-dock-head">
                  <span className="cm-dock-title">◇ <span>Call-flow diagram</span></span>
                  <div className="cm-dock-tools">
                    <button className="cm-dock-btn" title={mainPane === "output" ? "Show diagram" : "Minimize diagram"}
                            disabled={!selectedNode}
                            onClick={() => setMainPane((p) => (p === "output" ? "split" : "output"))}>
                      {mainPane === "output" ? "▸" : "▾"}
                    </button>
                    <button className="cm-dock-btn" title={mainPane === "diagram" ? "Restore split" : "Maximize diagram"}
                            onClick={() => setMainPane((p) => (p === "diagram" ? "split" : "diagram"))}>
                      {mainPane === "diagram" ? "❐" : "⤢"}
                    </button>
                  </div>
                </div>
                <div className="cm-pane-body">
                  <CodemapView
                    data={graph} selectedId={selectedId}
                    onSelect={selectNode} onOpenReader={open} onExpand={expand} expanding={expanding}
                    onExplainEdge={explainEdge} flowNodes={ordered} simByNode={simByNode}
                  />
                </div>
              </div>

              {selectedNode && (
                <div className="cm-dock">
                  <div className="cm-dock-head">
                    <span className="cm-dock-title">
                      ⚡ <span className="mono">{selectedNode.name}</span>
                    </span>
                    <div className="cm-dock-subtabs">
                      <button
                        className={"cm-dock-subtab" + (dockTab === "output" ? " active" : "")}
                        onClick={() => setDockTab("output")}
                      >
                        Output {simLoading && !trace && <span className="spin" />}
                      </button>
                      <button
                        className={"cm-dock-subtab" + (dockTab === "code" ? " active" : "")}
                        onClick={() => setDockTab("code")}
                      >
                        Code
                      </button>
                    </div>
                    <div className="cm-dock-tools">
                      <button className="cm-dock-btn" title={mainPane === "diagram" ? "Show output" : "Minimize output"}
                              onClick={() => setMainPane((p) => (p === "diagram" ? "split" : "diagram"))}>
                        {mainPane === "diagram" ? "▸" : "▾"}
                      </button>
                      <button className="cm-dock-btn" title={mainPane === "output" ? "Restore split" : "Maximize output"}
                              onClick={() => setMainPane((p) => (p === "output" ? "split" : "output"))}>
                        {mainPane === "output" ? "❐" : "⤢"}
                      </button>
                      <button className="cm-dock-btn" title="Close" onClick={() => { setSelectedId(null); setMainPane("split"); }}>✕</button>
                    </div>
                  </div>
                  <div className="cm-dock-body">
                    {dockTab === "output" ? (
                      simLoading && !trace ? (
                        <div className="sim"><div className="sim-empty"><span className="spin" /> Simulating data flow through the walkthrough…</div></div>
                      ) : trace && activeStep ? (
                        <SimulationPanel trace={trace} step={activeStep} node={selectedNode}
                                         nextNode={nextNode} onOpenReader={open} />
                      ) : simError && !trace ? (
                        <div className="sim"><div className="sim-empty cm-sim-state err">
                          Couldn't simulate — {simError}
                          <button className="btn" style={{ marginLeft: 10 }} onClick={() => ensureSim(ordered.map((x) => x.id))}>Retry</button>
                        </div></div>
                      ) : (
                        <div className="sim"><div className="sim-empty">No simulated output for this node.</div></div>
                      )
                    ) : (
                      <CodeStrip node={selectedNode} onReader={open} onFileMap={openFileMap} />
                    )}
                  </div>
                </div>
              )}
            </div>
            <aside className="cm-narr">
              <div className="cm-tabs">
                <button className={"cm-tab" + (sidebarTab === "steps" ? " active" : "")} onClick={() => setSidebarTab("steps")}>
                  Steps <span className="cm-tab-count">{n}</span>
                </button>
                <button className={"cm-tab" + (sidebarTab === "about" ? " active" : "")} onClick={() => setSidebarTab("about")}>
                  Overview
                </button>
              </div>

              {sidebarTab === "steps" ? (
                <PhysicalFlow nodes={ordered} activeId={selectedId} onSelect={goStep} onOpen={open} simByNode={simByNode} />
              ) : (
                <div className="cm-tab-panel">
                  <h3>{title}</h3>
                  {base?.narrative
                    ? <div className="prose">{base.narrative}</div>
                    : <p className="muted-sm">No narrative for this view — see the ⓘ above the diagram for how it was built.</p>}

                  <div className="cm-extend">
                    <div className="cm-extend-head">Ask a follow-up to grow the map</div>
                    <div className="cm-extend-row">
                      <input
                        value={extendQ}
                        onChange={(e) => setExtendQ(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !extending) extendMap(); }}
                        placeholder="e.g. What calls into this?"
                        disabled={extending}
                      />
                      <button className="btn" onClick={extendMap} disabled={extending || !extendQ.trim()}>
                        {extending ? "…" : "Extend"}
                      </button>
                    </div>
                    {extendNote && <div className="cm-extend-note">{extendNote}</div>}
                  </div>
                </div>
              )}
            </aside>
          </div>

          {edgePopup && <EdgePopup popup={edgePopup} onClose={() => setEdgePopup(null)} />}
        </>
      ) : <div className="state">{filePath != null ? "No functions, methods, or classes found in that file." : "This symbol doesn't call any resolved internal functions."}</div>)}
    </div>
  );
}

function EdgePopup({
  popup, onClose,
}: {
  popup: { x: number; y: number; loading: boolean; text: string; error: string };
  onClose: () => void;
}) {
  const width = 300;
  const left = Math.min(popup.x - width / 2, window.innerWidth - width - 14);
  const style: React.CSSProperties = { left: Math.max(10, left), top: popup.y + 14 };
  return (
    <div className="cm-edge-popup" style={style}>
      <div className="cm-edge-popup-head">
        Why this call?
        <button className="cm-edge-popup-x" onClick={onClose} aria-label="Close">✕</button>
      </div>
      {popup.loading
        ? <div className="cm-edge-popup-body"><span className="spin" /> Thinking…</div>
        : popup.error
          ? <div className="cm-edge-popup-body err">{popup.error}</div>
          : <div className="cm-edge-popup-body">{popup.text}</div>}
    </div>
  );
}

function CodeStrip({ node, onReader, onFileMap }: { node: CodemapNode; onReader: (n: CodemapNode) => void; onFileMap: (n: CodemapNode) => void }) {
  const { data } = useQuery({ queryKey: ["file", node.file], queryFn: () => api.file(node.file) });
  const snip = useMemo(() => {
    if (!data?.content) return { html: "", from: 1, count: 0, hl: 0 };
    const lines = data.content.split("\n");
    const from = Math.max(0, node.line - 4);
    const slice = lines.slice(from, from + 20);
    const html = highlightCode(slice.join("\n"), data.language);
    return { html, from: from + 1, count: slice.length, hl: node.line - from - 1 };
  }, [data, node.line]);

  const gutter = useMemo(() => Array.from({ length: snip.count }, (_, i) => snip.from + i).join("\n"), [snip]);

  return (
    <div className="cm-strip">
      <div className="cm-strip-head">
        <span className="ci-name">{node.qualified_name}</span>
        <span className="ci-path">{node.file}:{node.line}</span>
        <div className="cm-strip-actions">
          <span className="cm-strip-open" onClick={() => onFileMap(node)}>File map →</span>
          <span className="cm-strip-open" onClick={() => onReader(node)}>Open in Reader →</span>
        </div>
      </div>
      <div className="cm-strip-code">
        <div className="band" style={{ top: 10 + snip.hl * 18 }} />
        <pre className="ci-gutter"><code>{gutter}</code></pre>
        <pre className="cm-strip-src"><code className="hljs" dangerouslySetInnerHTML={{ __html: snip.html }} /></pre>
      </div>
    </div>
  );
}
