import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import PhysicalFlow from "./PhysicalFlow";
import type { Codemap, CodemapNode, SimStep } from "../lib/types";

const NW = 274, NH = 66, GAPY = 26, COLGAP = 112, PAD = 46;

// Cohesive per-kind colour language, reused for the accent bar, kind tag,
// hover/active stroke and glow — one hue per symbol kind across the whole map.
const KC: Record<string, string> = { class: "#8b5cf6", method: "#0ea5e9", function: "#10b981" };
const kcOf = (k: string) => KC[k] ?? "var(--accent)";
const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const reduceMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

type View = { x: number; y: number; s: number };

export default function CodemapView({
  data, selectedId, onSelect, onOpenReader, onExpand, expanding, onExplainEdge, flowNodes, simByNode,
}: {
  data: Codemap;
  selectedId?: number | null;
  onSelect: (n: CodemapNode) => void;
  onOpenReader: (n: CodemapNode) => void;
  onExpand?: () => void;
  expanding?: boolean;
  onExplainEdge?: (sourceId: number, targetId: number, clientX: number, clientY: number) => void;
  /** Simulation output-summary per node, so the fullscreen flow shows data
   *  moving A→B just like the sidebar copy. */
  simByNode?: Map<number, SimStep>;
  /** The ordered walkthrough (for the "Physical Code" flow panel) — passed
   *  separately from `data` because in fullscreen mode this canvas covers the
   *  whole viewport, hiding the sidebar the panel normally lives in. When
   *  given, an overlay copy renders inside the fullscreen view so it's never
   *  lost behind the graph. */
  flowNodes?: CodemapNode[];
}) {
  const [showFlow, setShowFlow] = useState(true);
  // ---- layout: columns by step, vertically centered per column ----
  const { pos, width, height, stepOf } = useMemo(() => {
    const steps = [...new Set(data.nodes.map((n) => n.step))].sort((a, b) => a - b);
    const stepIdx = new Map(steps.map((s, i) => [s, i]));
    const byStep = new Map<number, CodemapNode[]>();
    for (const n of data.nodes) (byStep.get(n.step) ?? byStep.set(n.step, []).get(n.step)!).push(n);
    const maxRows = Math.max(1, ...steps.map((s) => byStep.get(s)!.length));
    const totalH = maxRows * (NH + GAPY) - GAPY;
    const pos = new Map<number, { x: number; y: number }>();
    const stepOf = new Map<number, number>();
    for (const s of steps) {
      const col = byStep.get(s)!;
      const colH = col.length * (NH + GAPY) - GAPY;
      const offY = PAD + (totalH - colH) / 2;
      const cx = PAD + stepIdx.get(s)! * (NW + COLGAP);
      col.forEach((n, i) => { pos.set(n.id, { x: cx, y: offY + i * (NH + GAPY) }); stepOf.set(n.id, stepIdx.get(s)!); });
    }
    return { pos, stepOf, width: PAD * 2 + steps.length * (NW + COLGAP) - COLGAP, height: PAD * 2 + totalH };
  }, [data]);

  const edges = useMemo(() => data.edges.map((e, i) => {
    const s = pos.get(e.source), t = pos.get(e.target);
    if (!s || !t) return null;
    const x1 = s.x + NW, y1 = s.y + NH / 2, x2 = t.x, y2 = t.y + NH / 2, mx = (x1 + x2) / 2;
    const back = x2 < x1;
    const d = back
      ? `M${x1} ${y1} C ${x1 + 40} ${y1 - 26}, ${x2 - 40} ${y2 - 26}, ${x2} ${y2}`
      : `M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
    // midpoint for the data-flow label (on forward edges the bezier peaks near
    // the horizontal midline; (x1+x2)/2, avg-y is a good, cheap anchor)
    return { key: i, d, fuzzy: e.confidence < 0.8, source: e.source, target: e.target,
             back, lx: (x1 + x2) / 2, ly: (y1 + y2) / 2 };
  }).filter(Boolean) as { key: number; d: string; fuzzy: boolean; source: number; target: number; back: boolean; lx: number; ly: number }[], [data, pos]);

  // ---- neighbours of the selected node → spotlight ----
  const neighbours = useMemo(() => {
    const s = new Set<number>();
    if (selectedId == null) return s;
    s.add(selectedId);
    for (const e of edges) {
      if (e.source === selectedId) s.add(e.target);
      if (e.target === selectedId) s.add(e.source);
    }
    return s;
  }, [edges, selectedId]);

  // ---- pan / zoom view ----
  const containerRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, s: 1 });
  const viewRef = useRef(view); viewRef.current = view;
  const raf = useRef(0);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const [full, setFull] = useState(false);

  const size = () => {
    const el = containerRef.current;
    return el ? { w: el.clientWidth, h: el.clientHeight } : { w: 900, h: 520 };
  };

  const fit = useCallback(() => {
    const { w, h } = size();
    const s = clamp(Math.min((w - 24) / width, (h - 24) / height), 0.2, 1.5);
    setView({ s, x: (w - width * s) / 2, y: (h - height * s) / 2 });
  }, [width, height]);

  const animateTo = useCallback((target: View) => {
    cancelAnimationFrame(raf.current);
    if (reduceMotion()) { setView(target); return; }
    const start = viewRef.current, t0 = performance.now(), dur = 460;
    const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    const tick = (now: number) => {
      const k = Math.min(1, (now - t0) / dur), e = ease(k);
      setView({ x: start.x + (target.x - start.x) * e, y: start.y + (target.y - start.y) * e, s: start.s + (target.s - start.s) * e });
      if (k < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, []);

  // initial fit (and refit on entering/leaving fullscreen)
  useLayoutEffect(() => { const id = requestAnimationFrame(fit); return () => cancelAnimationFrame(id); }, [fit, full]);

  // camera-follow the selected node
  useEffect(() => {
    if (selectedId == null) return;
    const p = pos.get(selectedId); if (!p) return;
    const { w, h } = size();
    const s = Math.max(viewRef.current.s, 0.85);
    animateTo({ s, x: w / 2 - (p.x + NW / 2) * s, y: h / 2 - (p.y + NH / 2) * s });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, pos]);

  // fullscreen: lock scroll + Esc to exit
  useEffect(() => {
    if (!full) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey);
    return () => { document.body.style.overflow = prev; window.removeEventListener("keydown", onKey); };
  }, [full]);

  const zoomAt = (factor: number, cx: number, cy: number) => {
    cancelAnimationFrame(raf.current);
    setView((p) => {
      const ns = clamp(p.s * factor, 0.2, 8), k = ns / p.s;
      return { s: ns, x: cx - (cx - p.x) * k, y: cy - (cy - p.y) * k };
    });
  };
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const r = containerRef.current!.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
  };
  const onPointerDown = (e: React.PointerEvent) => {
    if ((e.target as Element).closest(".cm-node, .cm-edge-hit")) return; // let node/edge clicks through
    cancelAnimationFrame(raf.current);
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, vx: viewRef.current.x, vy: viewRef.current.y };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    setView((p) => ({ ...p, x: drag.current!.vx + (e.clientX - drag.current!.x), y: drag.current!.vy + (e.clientY - drag.current!.y) }));
  };
  const onPointerUp = () => { drag.current = null; };
  const btnZoom = (f: number) => { const { w, h } = size(); zoomAt(f, w / 2, h / 2); };

  return (
    <div ref={containerRef} className={"cm-canvas" + (full ? " cm-full" : "")}
         onWheel={onWheel} onPointerDown={onPointerDown} onPointerMove={onPointerMove}
         onPointerUp={onPointerUp} onPointerLeave={onPointerUp}>
      <div className="cm-tools" onPointerDown={(e) => e.stopPropagation()}>
        {onExpand && (
          <button className="cm-tool cm-tool-wide" onClick={onExpand} disabled={selectedId == null || expanding}
                  title="Expand callers & callees of the selected node">
            {expanding ? "Expanding…" : "＋ Expand"}
          </button>
        )}
        <button className="cm-tool" onClick={() => btnZoom(1.25)} title="Zoom in">+</button>
        <button className="cm-tool" onClick={() => btnZoom(0.8)} title="Zoom out">−</button>
        <button className="cm-tool" onClick={fit} title="Fit to screen">Fit</button>
        <span className="cm-zoom">{Math.round(view.s * 100)}%</span>
        {full && flowNodes && flowNodes.length > 0 && (
          <button className="cm-tool cm-tool-wide" onClick={() => setShowFlow((v) => !v)}
                  title="Toggle the physical-flow panel">
            🎬 {showFlow ? "Hide flow" : "Show flow"}
          </button>
        )}
        <button className="cm-tool" onClick={() => setFull((f) => !f)} title={full ? "Exit fullscreen (Esc)" : "Fullscreen"}>
          {full ? "✕" : "⤢"}
        </button>
      </div>

      {full && flowNodes && flowNodes.length > 0 && showFlow && (
        <div className="cm-full-flow" onPointerDown={(e) => e.stopPropagation()} onWheel={(e) => e.stopPropagation()}>
          <div className="cm-full-flow-head">🎬 Physical flow</div>
          <div className="cm-full-flow-body">
            <PhysicalFlow nodes={flowNodes} activeId={selectedId ?? null} onSelect={(i) => onSelect(flowNodes[i])} onOpen={onOpenReader} simByNode={simByNode} />
          </div>
        </div>
      )}

      <svg className="cm-svg" width="100%" height="100%">
        <defs>
          <marker id="cm-arw" markerWidth="8" markerHeight="8" refX="6.5" refY="3.5" orient="auto">
            <path d="M0 0 L7 3.5 L0 7 z" fill="var(--border-strong)" />
          </marker>
          <marker id="cm-arw-hot" markerWidth="9" markerHeight="9" refX="6.5" refY="3.5" orient="auto">
            <path d="M0 0 L7 3.5 L0 7 z" fill="var(--accent)" />
          </marker>
        </defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.s})`}>
          {edges.map((e) => {
            const hot = selectedId != null && (e.source === selectedId || e.target === selectedId);
            const dim = selectedId != null && !hot;
            return (
              <g key={e.key} className={"cm-edge-g" + (dim ? " dim" : "")}>
                <path d={e.d} className={"cm-edge" + (e.fuzzy ? " fuzzy" : "")}
                      markerEnd={hot ? "url(#cm-arw-hot)" : "url(#cm-arw)"}
                      style={{ stroke: hot ? "var(--accent)" : undefined }} />
                {hot && <path d={e.d} className="cm-edge-flow" />}
                {onExplainEdge && (
                  <path d={e.d} className="cm-edge-hit"
                        onClick={(ev) => { ev.stopPropagation(); onExplainEdge(e.source, e.target, ev.clientX, ev.clientY); }}>
                    <title>Why does this call happen?</title>
                  </path>
                )}
              </g>
            );
          })}
          {simByNode && edges.map((e) => {
            if (e.back) return null;  // labels ride forward (data-flow) edges only
            const out = simByNode.get(e.source)?.output.summary;
            if (!out) return null;
            const hot = selectedId != null && (e.source === selectedId || e.target === selectedId);
            const dim = selectedId != null && !hot;
            const label = trunc(out, 24);
            const w = label.length * 6 + 16;
            return (
              <g key={"lbl" + e.key} className={"cm-edge-lbl" + (dim ? " dim" : "") + (hot ? " hot" : "")}
                 transform={`translate(${e.lx},${e.ly})`}>
                <rect x={-w / 2} y={-9} width={w} height={18} rx={9} className="cm-edge-lbl-bg" />
                <text className="cm-edge-lbl-tx" textAnchor="middle" dy={3.5}>{label}</text>
              </g>
            );
          })}
          {data.nodes.map((n) => {
            const p = pos.get(n.id)!;
            const active = n.id === selectedId;
            const dim = selectedId != null && !neighbours.has(n.id);
            return (
              <g key={n.id} className={"cm-node" + (active ? " active" : "") + (dim ? " dim" : "")}
                 transform={`translate(${p.x},${p.y})`}
                 style={{ ["--kc" as string]: kcOf(n.kind) } as React.CSSProperties}
                 onClick={() => onSelect(n)} onDoubleClick={() => onOpenReader(n)}>
                <g className="cm-in" style={{ animationDelay: `${(stepOf.get(n.id) ?? 0) * 70}ms` }}>
                  <rect width={NW} height={NH} rx="16" className="cm-box" />
                  <rect className="cm-accent" x="0" y="16" width="4" height={NH - 32} rx="2" />
                  <text x="24" y={NH / 2 + 6} className="cm-icon">{n.icon || "⚙️"}</text>
                  <text x="50" y={NH / 2 - 2} className="cm-label">{trunc(n.qualified_name, 26)}</text>
                  <text x="50" y={NH / 2 + 16} className="cm-sub">{n.file.split("/").pop()}:{n.line}</text>
                  <text x={NW - 15} y="20" textAnchor="end" className="cm-kindtag">{n.role_label || n.kind}</text>
                </g>
              </g>
            );
          })}
        </g>
      </svg>
      {full && <div className="cm-full-hint">Scroll to zoom · drag to pan · double-click a node to open it · Esc to close</div>}
    </div>
  );
}
