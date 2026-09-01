import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import CodemapView from "../components/CodemapView";
import PhysicalFlow from "../components/PhysicalFlow";
import SimulationPanel from "../components/SimulationPanel";
import HistoryMenu from "../components/HistoryMenu";
import { classifyRole } from "../lib/codemapRoles";
import { highlightCode } from "../lib/highlight";
import type { Codemap as CodemapResult, CodemapEdge, CodemapNode, SimStep, SimulationTrace, SymbolRef } from "../lib/types";

const STEP_MS = 3200;

const SUGGESTIONS = [
  "How does Flask dispatch a request to a view function?",
  "How is the application context pushed and popped?",
  "How are blueprints registered onto an app?",
  "How does url_for build a URL for an endpoint?",
];

type Extra = { nodes: CodemapNode[]; edges: CodemapEdge[] };

// Module-level cache — survives navigating away and back (see note in git log).
let cmCache: { question: string; result: CodemapResult | null } = { question: "", result: null };

const mkNode = (r: SymbolRef, step: number): CodemapNode => ({
  id: r.id, qualified_name: r.qualified_name,
  name: r.qualified_name.split(/[./]/).filter(Boolean).pop() ?? r.qualified_name,
  kind: r.kind, file: r.file_path, line: r.start_line, step, note: "",
  ...classifyRole(r.qualified_name, r.file_path, r.kind),
});

export default function Codemap() {
  const nav = useNavigate();
  const loc = useLocation();
  const qc = useQueryClient();
  const presetFile = (loc.state as { filePath?: string } | null)?.filePath;
  const [q, setQ] = useState(cmCache.question);
  const [result, setResult] = useState<CodemapResult | null>(cmCache.result);
  const [fileMode, setFileMode] = useState(false);
  const [extra, setExtra] = useState<Extra>({ nodes: [], edges: [] });
  const [step, setStepState] = useState(-1);   // index into the ordered walkthrough; -1 = none
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [expanding, setExpanding] = useState(false);
  const [edgePopup, setEdgePopup] = useState<
    { source: number; target: number; x: number; y: number; loading: boolean; text: string; error: string } | null
  >(null);
  const [extendQ, setExtendQ] = useState("");
  const [extending, setExtending] = useState(false);
  const [extendNote, setExtendNote] = useState("");
  // The full, coherent data-flow simulation (state flows node→node). Generated
  // ONCE, lazily — triggered when the user first clicks a node, NOT by Play, so
  // Play stays independent and instant. Cached for the rest of the session.
  const [trace, setTrace] = useState<SimulationTrace | null>(null);
  const [simLoading, setSimLoading] = useState(false);
  const [simError, setSimError] = useState("");
  // Which of the two stacked panes (the diagram, the output) is expanded.
  // "split" = both visible; "diagram" = diagram full (output collapsed to its
  // header); "output" = output full (diagram collapsed). Lets the client blow
  // either screen up to full and back.
  const [mainPane, setMainPane] = useState<"split" | "diagram" | "output">("split");
  // Output and source used to render stacked, always both, in the same
  // already-cramped dock — now a tab, same idea as the sidebar's own tabs.
  const [dockTab, setDockTab] = useState<"output" | "code">("output");
  const [sidebarTab, setSidebarTab] = useState<"steps" | "about">("steps");
  const traceRef = useRef<SimulationTrace | null>(trace); traceRef.current = trace;

  const m = useMutation({
    mutationFn: (question: string) => api.codemap(question),
    onSuccess: (data, question) => {
      cmCache = { question, result: data };
      setResult(data);
      qc.invalidateQueries({ queryKey: ["conversations", "codemap"] });
    },
  });

  // Drilling into a file from Graph lands here instead of a generic
  // force-directed symbol graph — a real walkthrough of what that file
  // defines. Separate mutation from `m` (a file path isn't a question, and
  // shouldn't get saved into the question-conversation history).
  const fileM = useMutation({
    mutationFn: (path: string) => api.codemapFile(path),
    onSuccess: (data) => { cmCache = { question: "", result: data }; setResult(data); },
  });

  // Generate the whole coherent trace once (backend caches it too). Called when
  // a node is first clicked; never blocks Play. No-op if already built/building.
  const ensureSim = useCallback(async (ids: number[]) => {
    if (traceRef.current || simLoading || !ids.length) return;
    setSimLoading(true); setSimError("");
    try {
      const r = await api.simulate(ids, cmCache.question);
      setTrace(r);
    } catch (e) {
      setSimError(e instanceof Error ? e.message : String(e));
    } finally {
      setSimLoading(false);
    }
  }, [simLoading]);

  useEffect(() => {
    if (presetFile) { setFileMode(true); fileM.mutate(presetFile); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ordered = result?.nodes ?? [];
  const n = ordered.length;
  const stepRef = useRef(step); stepRef.current = step;

  // The merged graph the canvas draws: base map + anything the user expanded.
  const graph = useMemo<CodemapResult | null>(() => {
    if (!result) return null;
    const nodes = [...result.nodes];
    const ids = new Set(nodes.map((x) => x.id));
    for (const nd of extra.nodes) if (!ids.has(nd.id)) { nodes.push(nd); ids.add(nd.id); }
    const eset = new Set(result.edges.map((e) => `${e.source}-${e.target}`));
    const edges = [...result.edges];
    for (const e of extra.edges) { const k = `${e.source}-${e.target}`; if (!eset.has(k)) { edges.push(e); eset.add(k); } }
    return { ...result, nodes, edges };
  }, [result, extra]);

  const selectedNode = useMemo(
    () => graph?.nodes.find((x) => x.id === selectedId) ?? null, [graph, selectedId]);

  // reset walkthrough + exploration whenever a new map arrives
  useEffect(() => {
    setExtra({ nodes: [], edges: [] }); setStepState(-1); setSelectedId(null); setPlaying(false);
    setEdgePopup(null); setExtendQ(""); setExtendNote("");
    setTrace(null); setSimLoading(false); setSimError(""); setMainPane("split"); setDockTab("output"); setSidebarTab("steps");
  }, [result]);

  // dismiss the edge-explain popover on Esc or a click elsewhere
  useEffect(() => {
    if (!edgePopup) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setEdgePopup(null); };
    const onDown = (e: MouseEvent) => {
      if (!(e.target as Element).closest(".cm-edge-popup, .cm-edge-hit")) setEdgePopup(null);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [edgePopup]);

  const goStep = useCallback((i: number) => {
    if (!n) return;
    const c = Math.max(0, Math.min(n - 1, i));
    setPlaying(false); setStepState(c); setSelectedId(ordered[c]?.id ?? null);
  }, [n, ordered]);

  // auto-advance while playing
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

  // Build the simulation when a node is first selected — but NOT while
  // auto-playing, so playback stays instant. Clicking a node (or pausing on
  // one) triggers it; the panel shows a spinner until the trace lands.
  useEffect(() => {
    if (selectedId == null || playing) return;
    ensureSim(ordered.map((x) => x.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, playing]);

  // keyboard: ←/→ to step, space to play/pause (ignored while typing)
  useEffect(() => {
    if (!result) return;
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "TEXTAREA" || tag === "INPUT") return;
      if (e.key === "ArrowRight") { e.preventDefault(); goStep((stepRef.current < 0 ? -1 : stepRef.current) + 1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); goStep(stepRef.current - 1); }
      else if (e.key === " ") { e.preventDefault(); play(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [result, goStep, play]);

  const setQPersist = (v: string) => { setQ(v); cmCache.question = v; };
  const run = (question = q) => { const t = question.trim(); if (t) { setFileMode(false); setQPersist(t); m.mutate(t); } };
  const open = (node: CodemapNode) => nav("/reader", { state: { path: node.file, symbolId: node.id } });
  const openFlow = (node: CodemapNode) => nav("/flow", { state: { symbolId: node.id, label: node.qualified_name } });
  const clear = () => { cmCache = { question: "", result: null }; setQ(""); setResult(null); setFileMode(false); m.reset(); fileM.reset(); };

  // click a node → focus it (camera + spotlight); sync the walkthrough if it's a base step
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
      const newNodes: CodemapNode[] = [], newEdges: CodemapEdge[] = [];
      for (const c of (d.callers ?? []).slice(0, 6)) {
        if (!have.has(c.id)) { newNodes.push(mkNode(c, sel.step - 1)); have.add(c.id); }
        newEdges.push({ source: c.id, target: sel.id, confidence: c.confidence ?? 1 });
      }
      for (const c of (d.callees ?? []).slice(0, 6)) {
        if (!have.has(c.id)) { newNodes.push(mkNode(c, sel.step + 1)); have.add(c.id); }
        newEdges.push({ source: sel.id, target: c.id, confidence: c.confidence ?? 1 });
      }
      setExtra((p) => ({ nodes: [...p.nodes, ...newNodes], edges: [...p.edges, ...newEdges] }));
    } finally { setExpanding(false); }
  };

  const explainEdge = async (sourceId: number, targetId: number, x: number, y: number) => {
    setEdgePopup({ source: sourceId, target: targetId, x, y, loading: true, text: "", error: "" });
    try {
      const r = await api.explainEdge(sourceId, targetId, q);
      setEdgePopup((p) => (p && p.source === sourceId && p.target === targetId
        ? { ...p, loading: false, text: r.text, error: r.error ?? "" } : p));
    } catch (e) {
      setEdgePopup((p) => (p && p.source === sourceId && p.target === targetId
        ? { ...p, loading: false, error: e instanceof Error ? e.message : String(e) } : p));
    }
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

  const openHistory = async (id: number) => {
    const c = await api.conversation<CodemapResult>(id);
    cmCache = { question: c.question, result: c.result };
    setQ(c.question); setResult(c.result); m.reset();
  };

  const pending = m.isPending || fileM.isPending;
  const runError = m.error ?? fileM.error;

  // active simulation step + the node its output flows into next
  const curOrderIdx = selectedNode ? ordered.findIndex((x) => x.id === selectedNode.id) : -1;
  const nextNode = curOrderIdx >= 0 ? ordered[curOrderIdx + 1] ?? null : null;
  const simByNode = useMemo<Map<number, SimStep>>(
    () => new Map((trace?.steps ?? []).map((s) => [s.node_id, s])), [trace]);
  const activeStep = selectedId != null ? simByNode.get(selectedId) ?? null : null;

  return (
    <div className="page cm-page">
      <div className="cm-head">
        <div>
          <div className="eyebrow">{fileMode && result ? `Codemap · ${result.title}` : "Codemap"}</div>
          <h1 className="h1" style={{ marginTop: 6 }}>
            {fileMode && result ? "Functions defined in this file" : "Map how something works"}
          </h1>
          {!result && (
            <p className="lede">
              Ask about a feature or flow. We assemble a focused map from the <b>real</b> dependency graph,
              then <b>play a walkthrough</b> — highlighting each step and revealing its code in order.
            </p>
          )}
        </div>
        <HistoryMenu kind="codemap" onSelect={openHistory} />
      </div>

      <div className={"cm-ask" + (result ? " compact" : "")}>
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey || result)) run(); }}
          placeholder={result ? "Ask a follow-up question to build a new, LLM-curated map…" : "e.g. How does Flask dispatch a request to a view function?"}
          rows={result ? 1 : undefined}
        />
        <button className="btn primary" onClick={() => run()} disabled={pending}>
          {m.isPending ? "Mapping…" : "Build codemap"}
        </button>
        {result && !pending && <button className="btn" onClick={clear}>New question</button>}
      </div>

      {!result && !pending && !runError && !presetFile && (
        <div className="cm-suggest">{SUGGESTIONS.map((s) => <button key={s} onClick={() => run(s)}>{s}</button>)}</div>
      )}
      {pending && (
        <div className="state"><span className="spin" />
          {fileM.isPending ? "Reading the file's real functions and call graph…" : "Retrieving code · building the map…"}
        </div>
      )}
      {runError && (
        <div className="state err">
          Couldn't build the codemap — {runError instanceof Error ? runError.message : String(runError)}
          <button className="btn" style={{ marginLeft: 10 }} onClick={() => (fileMode && presetFile ? fileM.mutate(presetFile) : run())}>Retry</button>
        </div>
      )}

      {result && graph && (n ? (
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
              <button className="cm-info-btn" aria-label="About this map">ⓘ</button>
              <div className="cm-info-pop">
                <p>Click a node to focus it, simulate its data flow, and <b>Expand</b> its real callers/callees. Click an edge to ask why the call happens.</p>
                <p><span className="cm-dot solid" /> confidently resolved call &nbsp; <span className="cm-dot dashed" /> ambiguous name match</p>
                {!result.curated && !fileMode && (
                  <p className="muted">Structure is exact — nodes/edges come straight from the dependency graph. Step order is mechanical; add an LLM key for curated ordering and a narrative.</p>
                )}
              </div>
            </div>
          </div>

          <div className="cm-layout">
            <div className={"cm-main pane-" + mainPane}>
              {/* Diagram pane — its own header with minimize / maximize so the
                  map can be blown up full or tucked away, mirroring the output. */}
              <div className="cm-pane cm-pane-diagram">
                <div className="cm-dock-head">
                  <span className="cm-dock-title">◇ <span>Codeflow diagram</span></span>
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
                    onExplainEdge={explainEdge}
                    flowNodes={ordered} simByNode={simByNode}
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
                      <CodeStrip node={selectedNode} onReader={open} onFlow={openFlow} />
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
                <PhysicalFlow nodes={ordered} activeId={selectedId} onSelect={goStep} onOpen={open}
                              simByNode={simByNode} />
              ) : (
                <div className="cm-tab-panel">
                  <h3>{result.title}</h3>
                  {result.narrative
                    ? <div className="prose">{result.narrative}</div>
                    : <p className="muted-sm">No narrative for this map — see the ⓘ above the diagram for how it was built.</p>}

                  <div className="cm-extend">
                    <div className="cm-extend-head">Ask a follow-up to grow the map</div>
                    <div className="cm-extend-row">
                      <input
                        value={extendQ}
                        onChange={(e) => setExtendQ(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !extending) extendMap(); }}
                        placeholder="e.g. What calls into the coupling analysis?"
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
        </>
      ) : <div className="state">{fileMode ? "No functions, methods, or classes found in that file." : "No matching code found for that question."}</div>)}

      {edgePopup && <EdgePopup popup={edgePopup} onClose={() => setEdgePopup(null)} />}
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

function CodeStrip({ node, onReader, onFlow }: { node: CodemapNode; onReader: (n: CodemapNode) => void; onFlow: (n: CodemapNode) => void }) {
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
          <span className="cm-strip-open" onClick={() => onFlow(node)}>Call flow →</span>
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
