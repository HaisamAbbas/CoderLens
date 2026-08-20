import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphData, GraphNode } from "../lib/types";

interface SimNode extends GraphNode { x: number; y: number; vx: number; vy: number; fx: number | null; fy: number | null; }

const GVARS = ["--c-core", "--c-sansio", "--c-json", "--c-other"];
const edgeKey = (a: GraphNode["id"], b: GraphNode["id"]) => [String(a), String(b)].sort().join("|");

/** Plain BFS shortest path over the (undirected) visible graph — same data
 *  the canvas already renders, no new endpoint needed. */
function shortestPath(data: GraphData, start: GraphNode["id"], end: GraphNode["id"]): GraphNode["id"][] | null {
  if (start === end) return [start];
  const adj = new Map<GraphNode["id"], GraphNode["id"][]>();
  for (const l of data.links) {
    (adj.get(l.source) ?? adj.set(l.source, []).get(l.source)!).push(l.target);
    (adj.get(l.target) ?? adj.set(l.target, []).get(l.target)!).push(l.source);
  }
  const prev = new Map<GraphNode["id"], GraphNode["id"]>();
  const visited = new Set([start]);
  const queue = [start];
  while (queue.length) {
    const cur = queue.shift()!;
    if (cur === end) break;
    for (const nb of adj.get(cur) ?? []) {
      if (!visited.has(nb)) { visited.add(nb); prev.set(nb, cur); queue.push(nb); }
    }
  }
  if (!visited.has(end)) return null;
  const path: GraphNode["id"][] = [end];
  let cur = end;
  while (cur !== start) { cur = prev.get(cur)!; path.push(cur); }
  return path.reverse();
}

export default function GraphCanvas({
  data, onOpenFile, onNode, onDrill, onBack, crumb, onOpenSymbol,
}: {
  data: GraphData;
  onOpenFile?: (path: string) => void;
  onNode?: (node: GraphNode) => void;
  onDrill?: (node: GraphNode) => void;
  onBack?: () => void;
  crumb?: string;
  onOpenSymbol?: (id: number, file: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const S = useRef<any>({});
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [focus, setFocus] = useState<GraphNode["id"] | null>(null);
  const selRef = useRef<GraphNode | null>(null);
  const onNodeRef = useRef(onNode);
  onNodeRef.current = onNode;
  const pick = (n: GraphNode | null) => { selRef.current = n; setSelected(n); if (n) onNodeRef.current?.(n); };

  // Path finder: a separate mode from normal selection — "armed" means the
  // next canvas click sets that endpoint instead of selecting a node.
  const [pathOpen, setPathOpen] = useState(false);
  const [armed, setArmed] = useState<"start" | "end" | null>(null);
  const [pathEnds, setPathEnds] = useState<{ start: GraphNode | null; end: GraphNode | null }>({ start: null, end: null });
  const armedRef = useRef(armed); armedRef.current = armed;
  const setEndpointRef = useRef((_n: GraphNode) => {});
  setEndpointRef.current = (n: GraphNode) => {
    const kind = armedRef.current;
    if (!kind) return;
    setPathEnds((pe) => ({ ...pe, [kind]: n }));
    setArmed(null);
  };
  const path = useMemo(() => {
    const { start, end } = pathEnds;
    if (!start || !end) return null;
    return shortestPath(data, start.id, end.id);
  }, [data, pathEnds]);
  const pathRef = useRef<GraphNode["id"][] | null>(null);
  useEffect(() => { pathRef.current = path; }, [path]);
  const clearPath = () => { setPathEnds({ start: null, end: null }); setArmed(null); };

  const byId = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data]);
  const groupIdx = (k: string) => { const i = data.groups.findIndex((g) => g.key === k); return i < 0 ? 3 : Math.min(i, 3); };

  useEffect(() => {
    const cv = canvasRef.current!;
    const ctx = cv.getContext("2d")!;
    let raf = 0;

    let srcNodes = data.nodes, srcLinks = data.links;
    if (focus != null) {
      const keep = new Set<GraphNode["id"]>([focus]);
      data.links.forEach((l) => { if (l.source === focus) keep.add(l.target); if (l.target === focus) keep.add(l.source); });
      srcNodes = data.nodes.filter((n) => keep.has(n.id));
      srcLinks = data.links.filter((l) => keep.has(l.source) && keep.has(l.target));
    }
    const nodes: SimNode[] = srcNodes.map((n, i) => ({
      ...n, x: Math.cos(i) * 240, y: Math.sin(i * 1.7) * 240, vx: 0, vy: 0, fx: null, fy: null,
    }));
    const map = new Map(nodes.map((n) => [n.id, n]));
    const links = srcLinks.map((l) => ({ s: map.get(l.source)!, t: map.get(l.target)!, w: l.weight || 1 }))
      .filter((l) => l.s && l.t);
    const maxDeg = Math.max(1, ...nodes.map((n) => n.degree));
    const radius = (n: SimNode) => 4 + Math.sqrt(n.degree) * 1.8;

    let W = 0, H = 0, DPR = 1;
    const view = { scale: 1, px: 0, py: 0 };
    let colors = { list: ["#888", "#888", "#888", "#888"], ink: "#000", surface: "#fff", hair: "#ccc", accent: "#4c8dff" };
    const readColors = () => {
      const cs = getComputedStyle(document.documentElement);
      colors = {
        list: GVARS.map((v) => cs.getPropertyValue(v).trim()),
        ink: cs.getPropertyValue("--text").trim(),
        surface: cs.getPropertyValue("--surface").trim(),
        hair: cs.getPropertyValue("--border-strong").trim(),
        accent: cs.getPropertyValue("--accent").trim(),
      };
    };
    const colorOf = (n: GraphNode) => colors.list[groupIdx(n.group)];

    const size = () => {
      const r = cv.parentElement!.getBoundingClientRect();
      DPR = Math.min(2, window.devicePixelRatio || 1); W = r.width; H = r.height;
      cv.width = W * DPR; cv.height = H * DPR; ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    };
    const fit = () => {
      let a = 1e9, b = 1e9, c = -1e9, d = -1e9;
      nodes.forEach((n) => { a = Math.min(a, n.x); b = Math.min(b, n.y); c = Math.max(c, n.x); d = Math.max(d, n.y); });
      const pad = 90, w = c - a || 1, h = d - b || 1;
      view.scale = Math.min((W - pad) / w, (H - pad) / h, 2.4);
      view.px = W / 2 - (a + c) / 2 * view.scale; view.py = H / 2 - (b + d) / 2 * view.scale;
    };
    const toScreen = (n: SimNode) => ({ x: n.x * view.scale + view.px, y: n.y * view.scale + view.py });
    const toWorld = (sx: number, sy: number) => ({ x: (sx - view.px) / view.scale, y: (sy - view.py) / view.scale });

    let alpha = 1;
    const tick = () => {
      for (const n of nodes) { n.vx += -n.x * 0.015 * alpha; n.vy += -n.y * 0.015 * alpha; }
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const p = nodes[i], qn = nodes[j]; let dx = p.x - qn.x, dy = p.y - qn.y, d2 = dx * dx + dy * dy + 0.01;
        const f = 4200 / d2 * alpha, d = Math.sqrt(d2); dx /= d; dy /= d;
        p.vx += dx * f; p.vy += dy * f; qn.vx -= dx * f; qn.vy -= dy * f;
      }
      for (const l of links) {
        let dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        const f = (d - 78) * 0.012 * alpha * Math.min(3, l.w); dx /= d; dy /= d;
        l.s.vx += dx * f; l.s.vy += dy * f; l.t.vx -= dx * f; l.t.vy -= dy * f;
      }
      for (const n of nodes) {
        if (n.fx !== null) { n.x = n.fx; n.y = n.fy!; n.vx = 0; n.vy = 0; continue; }
        n.vx *= 0.82; n.vy *= 0.82;
        n.x += Math.max(-20, Math.min(20, n.vx)); n.y += Math.max(-20, Math.min(20, n.vy));
      }
      if (alpha > 0.03) alpha *= 0.992;
    };

    const neighbors = (id: GraphNode["id"]) => {
      const s = new Set([id]);
      links.forEach((l) => { if (l.s.id === id) s.add(l.t.id); if (l.t.id === id) s.add(l.s.id); });
      return s;
    };
    const draw = () => {
      tick();
      ctx.clearRect(0, 0, W, H);
      const sel = selRef.current;
      const nb = sel ? neighbors(sel.id) : null;
      const pathIds = pathRef.current;
      const onPath = pathIds ? new Set(pathIds) : null;
      const pathEdges = onPath
        ? new Set(pathIds!.slice(0, -1).map((id, i) => edgeKey(id, pathIds![i + 1])))
        : null;
      for (const l of links) {
        const a = toScreen(l.s), b = toScreen(l.t);
        let col = colors.hair, wdt = 1, al = 0.7;
        if (pathEdges) {
          if (pathEdges.has(edgeKey(l.s.id, l.t.id))) { col = colors.accent; wdt = 2.6; al = 1; } else al = 0.1;
        } else if (sel) {
          const on = l.s.id === sel.id || l.t.id === sel.id; if (on) { col = colorOf(sel as SimNode); wdt = 1.6; al = 1; } else al = 0.12;
        }
        ctx.globalAlpha = al; ctx.strokeStyle = col; ctx.lineWidth = wdt;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
      ctx.globalAlpha = 1;
      const screenPos = new Map<GraphNode["id"], { x: number; y: number; r: number }>();
      for (const n of nodes) {
        const p = toScreen(n), r = radius(n) * Math.max(0.7, Math.min(1.4, view.scale));
        screenPos.set(n.id, { x: p.x, y: p.y, r });
        const dim = onPath ? !onPath.has(n.id) : (sel && nb && !nb.has(n.id));
        ctx.globalAlpha = dim ? 0.25 : 1;
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); ctx.fillStyle = colorOf(n); ctx.fill();
        const onP = onPath?.has(n.id);
        ctx.lineWidth = n.id === sel?.id ? 2.5 : onP ? 2.2 : 1.2;
        ctx.strokeStyle = n.id === sel?.id ? colors.ink : onP ? colors.accent : colors.surface; ctx.stroke();
      }

      // Labels: drawn in a second pass, in priority order (selected first, then
      // by degree), and skipped if they'd overlap a higher-priority label
      // already placed this frame — a tight cluster of hub nodes (common on
      // real repos with a few heavily-depended-on files) previously rendered
      // as an illegible pile of overlapping text; now the busiest, most
      // useful labels win and the rest quietly step aside rather than collide.
      ctx.font = "11px 'IBM Plex Mono', monospace"; ctx.textAlign = "center"; ctx.textBaseline = "top";
      const candidates = nodes.filter((n) => n.id === sel?.id || onPath?.has(n.id) || n.degree > maxDeg * 0.35);
      candidates.sort((a, b) =>
        (b.id === sel?.id ? 1 : 0) - (a.id === sel?.id ? 1 : 0)
        || ((onPath?.has(b.id) ? 1 : 0) - (onPath?.has(a.id) ? 1 : 0))
        || b.degree - a.degree);
      const placed: { x: number; y: number; w: number; h: number }[] = [];
      const overlaps = (a: { x: number; y: number; w: number; h: number }, b: typeof a) =>
        a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
      for (const n of candidates) {
        const priority = n.id === sel?.id || onPath?.has(n.id);
        const pos = screenPos.get(n.id)!;
        const w = ctx.measureText(n.label).width, h = 13;
        const box = { x: pos.x - w / 2 - 2, y: pos.y + pos.r + 3, w: w + 4, h };
        if (!priority && placed.some((p) => overlaps(box, p))) continue;
        placed.push(box);
        const dim = onPath ? !onPath.has(n.id) : (sel && nb && !nb.has(n.id));
        ctx.globalAlpha = dim ? 0.4 : 1;
        ctx.lineWidth = 3; ctx.strokeStyle = colors.surface; ctx.strokeText(n.label, pos.x, box.y);
        ctx.fillStyle = onPath?.has(n.id) ? colors.accent : colors.ink; ctx.fillText(n.label, pos.x, box.y);
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(draw);
    };

    const pickAt = (sx: number, sy: number) => {
      let best: SimNode | null = null, bd = 1e9;
      for (const n of nodes) {
        const p = toScreen(n), r = radius(n) * Math.max(0.7, Math.min(1.4, view.scale)) + 3;
        const d = (p.x - sx) ** 2 + (p.y - sy) ** 2; if (d < r * r && d < bd) { bd = d; best = n; }
      }
      return best;
    };

    let drag: SimNode | null = null, panning = false, last = { x: 0, y: 0 }, moved = false;
    const onDown = (e: MouseEvent) => {
      const n = pickAt(e.offsetX, e.offsetY); moved = false;
      if (n) { drag = n; n.fx = n.x; n.fy = n.y; } else { panning = true; last = { x: e.offsetX, y: e.offsetY }; }
    };
    const onMove = (e: MouseEvent) => {
      const rect = cv.getBoundingClientRect(), sx = e.clientX - rect.left, sy = e.clientY - rect.top;
      if (drag) { moved = true; const w = toWorld(sx, sy); drag.fx = w.x; drag.fy = w.y; alpha = Math.max(alpha, 0.3); return; }
      if (panning) { moved = true; view.px += sx - last.x; view.py += sy - last.y; last = { x: sx, y: sy }; return; }
      cv.style.cursor = armedRef.current ? "crosshair" : pickAt(sx, sy) ? "pointer" : "default";
    };
    const onUp = () => {
      if (drag && !moved) { if (armedRef.current) setEndpointRef.current(drag); else pick(drag); }
      else if (panning && !moved) pick(null);
      if (drag) { drag.fx = null; drag.fy = null; } drag = null; panning = false;
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault(); const f = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      const wx = (e.offsetX - view.px) / view.scale, wy = (e.offsetY - view.py) / view.scale;
      view.scale = Math.max(0.2, Math.min(5, view.scale * f));
      view.px = e.offsetX - wx * view.scale; view.py = e.offsetY - wy * view.scale;
    };

    readColors(); size();
    for (let i = 0; i < 260; i++) tick();
    fit();
    S.current.fit = () => { alpha = Math.max(alpha, 0.25); fit(); };

    cv.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    cv.addEventListener("wheel", onWheel, { passive: false });
    const onResize = () => { size(); fit(); };
    window.addEventListener("resize", onResize);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", readColors);
    const mo = new MutationObserver(readColors);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    draw();
    return () => {
      cancelAnimationFrame(raf);
      cv.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      cv.removeEventListener("wheel", onWheel);
      window.removeEventListener("resize", onResize);
      mq.removeEventListener("change", readColors);
      mo.disconnect();
    };
  }, [data, focus]);

  // A new graph (drill / back) clears any isolation focus.
  useEffect(() => { setFocus(null); }, [data]);

  const detail = selected && (() => {
    const callers = data.links.filter((l) => l.target === selected.id).map((l) => byId.get(l.source)!).filter(Boolean);
    const callees = data.links.filter((l) => l.source === selected.id).map((l) => byId.get(l.target)!).filter(Boolean);
    return { callers, callees };
  })();

  return (
    <div className="graph-stage">
      <canvas ref={canvasRef} />
      <div className="g-toolbar">
        {onBack && <button className="btn" onClick={onBack}>← {crumb ?? "Back"}</button>}
        <button className="btn" onClick={() => S.current.fit?.()}>Fit</button>
        {focus != null && <button className="btn" onClick={() => setFocus(null)}>← Whole graph</button>}
        <button
          className={"btn" + (pathOpen ? " primary" : "")}
          onClick={() => { setPathOpen((v) => !v); if (!pathOpen) setArmed("start"); else clearPath(); }}
        >
          ⤳ Find path
        </button>
      </div>

      {pathOpen && (
        <div className="g-path">
          <span className="close" onClick={() => { setPathOpen(false); clearPath(); }}>×</span>
          <div className="eyebrow">Path finder</div>
          <div className="gp-ends">
            <button className={"gp-end" + (armed === "start" ? " on" : "")} onClick={() => setArmed("start")}>
              <span className="gp-dot a" />{pathEnds.start ? pathEnds.start.label : "Click a start node…"}
            </button>
            <button className={"gp-end" + (armed === "end" ? " on" : "")} onClick={() => setArmed("end")}>
              <span className="gp-dot b" />{pathEnds.end ? pathEnds.end.label : "Click an end node…"}
            </button>
          </div>
          {pathEnds.start && pathEnds.end && (
            path
              ? (
                <>
                  <div className="gp-hops">{path.length - 1} hop{path.length - 1 === 1 ? "" : "s"}</div>
                  {path.map((id) => {
                    const n = byId.get(id);
                    return n ? <div key={String(id)} className="linkrow" onClick={() => pick(n)}><span>{n.label}</span></div> : null;
                  })}
                </>
              )
              : <div className="gp-none">No path — not connected in this graph.</div>
          )}
          {(pathEnds.start || pathEnds.end) && (
            <button className="btn" style={{ width: "100%", marginTop: 10 }} onClick={clearPath}>Clear</button>
          )}
        </div>
      )}
      <div className="g-legend">
        {data.groups.map((g, i) => (
          <span className="k" key={g.key}><i style={{ background: `var(${GVARS[Math.min(i, 3)]})` }} />{g.label}</span>
        ))}
        <span>● larger = more connected</span>
      </div>

      {selected && detail && (
        <div className="g-detail">
          <span className="close" onClick={() => pick(null)}>×</span>
          <div className="k">{selected.label}</div>
          <div className="mono" style={{ fontSize: 11, color: "var(--text-3)", marginTop: 4, wordBreak: "break-all" }}>{selected.meta}</div>
          <div style={{ display: "flex", gap: 16, marginTop: 12 }}>
            {selected.stats.map(([l, v]) => (
              <div key={l}><div className="tnum" style={{ fontSize: 19 }}>{v}</div><div style={{ fontSize: 10.5, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: ".05em" }}>{l}</div></div>
            ))}
          </div>
          <button className="btn" style={{ width: "100%", marginTop: 12 }}
            onClick={() => setFocus(focus == null ? selected.id : null)}>
            {focus == null ? "Isolate neighborhood" : "Show whole graph"}
          </button>
          {onDrill && !selected.file && (
            <button className="btn" style={{ width: "100%", marginTop: 8 }} onClick={() => onDrill(selected)}>⤢ Zoom into symbols</button>
          )}
          {onOpenFile && !selected.file && typeof selected.id === "string" && (
            <button className="btn primary" style={{ width: "100%", marginTop: 8 }} onClick={() => onOpenFile(String(selected.id))}>Open in Reader →</button>
          )}
          {onOpenSymbol && selected.file && (
            <button className="btn primary" style={{ width: "100%", marginTop: 8 }} onClick={() => onOpenSymbol(Number(selected.id), selected.file!)}>Open in Reader →</button>
          )}
          <h5>Breaks if removed · {detail.callers.length}</h5>
          {detail.callers.slice(0, 8).map((n) => <div key={String(n.id)} className="linkrow" onClick={() => pick(n)}><span>{n.label}</span></div>)}
          {!detail.callers.length && <div style={{ fontSize: 12, color: "var(--text-3)" }}>— none —</div>}
          <h5>Depends on · {detail.callees.length}</h5>
          {detail.callees.slice(0, 8).map((n) => <div key={String(n.id)} className="linkrow" onClick={() => pick(n)}><span>{n.label}</span></div>)}
          {!detail.callees.length && <div style={{ fontSize: 12, color: "var(--text-3)" }}>— none —</div>}
        </div>
      )}
    </div>
  );
}
