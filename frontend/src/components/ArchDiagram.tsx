/** A layered architecture diagram: typed cards laid out in dependency order,
 *  wired with real edges, inside a labelled boundary.
 *
 *  Same layout idea as CodemapView — columns by depth, each column centred
 *  vertically, bezier edges between them — because that is what turns a set of
 *  boxes into something you can read a direction out of. The difference is
 *  where the depth comes from: the codemap gets an explicit `step` per node,
 *  while here it is derived by longest-path layering over module dependencies
 *  aggregated from the symbol graph.
 *
 *  Not Mermaid, and not a grid. A grid shows which modules exist; it cannot
 *  show that `channels` sits upstream of `tools` and `state`.
 *
 *  Every colour is a literal attribute rather than a CSS class, so the
 *  serialized SVG stands alone for export: no stylesheet to carry, and no
 *  foreignObject to stop a canvas rasterizing it.
 */

import { useMemo, useRef, useState } from "react";
import { downloadPngElement, downloadSvgElement } from "../lib/diagramExport";

export type Tone = "added" | "removed" | "changed" | "kept";

export interface DiagramNode {
  id: string;
  title: string;
  subtitle: string;
  /** Dot-separated specifics — the actual file names in the module. */
  detail: string;
  tone: Tone;
  /** Ranking weight, used to decide what to draw when there are too many. */
  weight: number;
}
export interface DiagramEdge {
  from: string;
  to: string;
  label: string;
  /** Thicker line for a heavier dependency. */
  weight?: number;
  /** Files that moved between modules, drawn distinctly from dependencies. */
  kind?: "dep" | "move";
}

const TONES: Record<Tone, { light: [string, string, string]; dark: [string, string, string] }> = {
  added:   { light: ["#f0fdf4", "#16a34a", "#22c55e"], dark: ["#0e2a1a", "#22c55e", "#4ade80"] },
  removed: { light: ["#fef2f2", "#dc2626", "#ef4444"], dark: ["#2b1113", "#ef4444", "#f87171"] },
  changed: { light: ["#fffbeb", "#d97706", "#f59e0b"], dark: ["#2c210a", "#f59e0b", "#fbbf24"] },
  kept:    { light: ["#f8fafc", "#64748b", "#94a3b8"], dark: ["#141a24", "#475569", "#64748b"] },
};
const TONE_LABEL: Record<Tone, string> = {
  added: "added", removed: "removed", changed: "files added or removed", kept: "unchanged",
};

// Fixed metrics keep layout deterministic: the same delta must always draw the
// same diagram, or two exports of one comparison would differ.
const NW = 216, NH = 82, COLGAP = 104, GAPY = 22, PAD = 34;
const HEAD_H = 92, LEGEND_H = 54;

/** Cap on drawn cards. Past roughly this many the diagram stops being readable
 *  and becomes a wall — the fact lists below the diagram carry the complete
 *  data, so truncating the picture loses nothing that isn't stated elsewhere. */
const MAX_NODES = 14;
const MAX_EDGES = 20;

const isDarkNow = () =>
  document.documentElement.getAttribute("data-theme") === "dark"
  || (document.documentElement.getAttribute("data-theme") !== "light"
      && window.matchMedia("(prefers-color-scheme: dark)").matches);

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/** Longest-path layering. Modules that depend on nothing visible start at 0;
 *  every other module sits one column right of its deepest dependency. Cyclic
 *  imports are normal in real code, so the relaxation is capped rather than
 *  assuming a DAG — a cycle settles instead of looping forever. */
function layerOf(ids: string[], edges: DiagramEdge[]): Map<string, number> {
  const layer = new Map(ids.map((id) => [id, 0]));
  const deps = edges.filter((e) => e.kind !== "move");
  for (let pass = 0; pass < ids.length; pass++) {
    let moved = false;
    for (const e of deps) {
      const a = layer.get(e.from), b = layer.get(e.to);
      if (a == null || b == null) continue;
      if (b < a + 1) { layer.set(e.to, a + 1); moved = true; }
    }
    if (!moved) break;
  }
  return layer;
}

export default function ArchDiagram({
  title, subtitle, groupLabel, nodes, edges, filename, footnote,
}: {
  title: string;
  subtitle: string;
  groupLabel: string;
  nodes: DiagramNode[];
  edges: DiagramEdge[];
  filename: string;
  footnote?: string;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [busy, setBusy] = useState("");
  const dark = isDarkNow();

  const ui = dark
    ? { bg: "#0b0f16", panel: "#0e131c", line: "#243244", text: "#e2e8f0",
        muted: "#94a3b8", faint: "#64748b", dep: "#38bdf8", move: "#f472b6" }
    : { bg: "#ffffff", panel: "#fafbfd", line: "#dbe3ec", text: "#0f172a",
        muted: "#64748b", faint: "#94a3b8", dep: "#0284c7", move: "#db2777" };

  const view = useMemo(() => {
    // Keep every module the delta has something to say about, then fill the
    // remaining budget with the heaviest ones so the picture still shows the
    // system the change sits inside.
    const story = nodes.filter((n) => n.tone !== "kept");
    const rest = nodes.filter((n) => n.tone === "kept").sort((a, b) => b.weight - a.weight);
    const shown = [...story, ...rest].slice(0, MAX_NODES);
    const keep = new Set(shown.map((n) => n.id));
    const hidden = nodes.length - shown.length;

    const live = edges.filter((e) => keep.has(e.from) && keep.has(e.to) && e.from !== e.to);
    const deps = live.filter((e) => e.kind !== "move")
      .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0)).slice(0, MAX_EDGES);
    const moves = live.filter((e) => e.kind === "move");
    const drawn = [...deps, ...moves];

    const layer = layerOf(shown.map((n) => n.id), drawn);
    const byLayer = new Map<number, DiagramNode[]>();
    for (const n of shown) {
      const l = layer.get(n.id) ?? 0;
      (byLayer.get(l) ?? byLayer.set(l, []).get(l)!).push(n);
    }
    const cols = [...byLayer.keys()].sort((a, b) => a - b);
    const maxRows = Math.max(1, ...cols.map((c) => byLayer.get(c)!.length));
    const totalH = maxRows * (NH + GAPY) - GAPY;

    const pos = new Map<string, { x: number; y: number }>();
    cols.forEach((c, ci) => {
      const col = byLayer.get(c)!.slice().sort((a, b) => b.weight - a.weight);
      const colH = col.length * (NH + GAPY) - GAPY;
      const offY = HEAD_H + PAD + (totalH - colH) / 2;
      col.forEach((n, i) => {
        pos.set(n.id, { x: PAD + ci * (NW + COLGAP), y: offY + i * (NH + GAPY) });
      });
    });

    const groupW = cols.length * (NW + COLGAP) - COLGAP + PAD * 2;
    return {
      shown, drawn, pos, hidden,
      width: groupW + PAD * 2,
      height: HEAD_H + totalH + PAD * 2 + LEGEND_H,
      groupW, groupH: totalH + PAD * 2,
    };
  }, [nodes, edges]);

  const tonesUsed = useMemo(() => {
    const seen = new Set<Tone>(view.shown.map((n) => n.tone));
    return (["added", "removed", "changed", "kept"] as Tone[]).filter((t) => seen.has(t));
  }, [view]);

  const run = (kind: string, fn: () => void | Promise<void>) => async () => {
    if (busy || !svgRef.current) return;
    setBusy(kind);
    try { await fn(); } finally { setBusy(""); }
  };

  const { width, height } = view;

  return (
    <div className="axd">
      <div className="axd-bar">
        <button disabled={!!busy}
                onClick={run("svg", () => downloadSvgElement(svgRef.current!, filename, ui.bg))}>
          {busy === "svg" ? "…" : "SVG"}
        </button>
        <button disabled={!!busy}
                onClick={run("png", () => downloadPngElement(svgRef.current!, filename, ui.bg))}>
          {busy === "png" ? "…" : "PNG"}
        </button>
      </div>
      <div className="axd-scroll">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          role="img"
          aria-label={`${title} — ${subtitle}`}
          fontFamily="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
        >
          <defs>
            <marker id="axd-dep" viewBox="0 0 10 10" refX={9} refY={5}
                    markerWidth={5.5} markerHeight={5.5} orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={ui.dep} />
            </marker>
            <marker id="axd-move" viewBox="0 0 10 10" refX={9} refY={5}
                    markerWidth={5.5} markerHeight={5.5} orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={ui.move} />
            </marker>
          </defs>

          <rect x={0} y={0} width={width} height={height} fill={ui.bg} rx={12} />

          <text x={PAD} y={38} fill={ui.text} fontSize={21} fontWeight={700}>
            {trunc(title, 52)}
          </text>
          <text x={PAD} y={62} fill={ui.muted} fontSize={12}>
            {trunc(subtitle, 104)}
          </text>
          <text x={width - PAD} y={36} fill={ui.faint} fontSize={10}
                textAnchor="end" letterSpacing={1.6}>
            ARCH DELTA
          </text>

          <rect x={PAD - 12} y={HEAD_H} width={view.groupW} height={view.groupH}
                rx={14} fill={ui.panel} stroke={ui.line} strokeWidth={1} strokeDasharray="6 5" />
          <text x={PAD} y={HEAD_H + 18} fill={ui.faint} fontSize={10}>
            {trunc(groupLabel, 64)}
          </text>

          {view.drawn.map((e) => {
            const a = view.pos.get(e.from), b = view.pos.get(e.to);
            if (!a || !b) return null;
            const move = e.kind === "move";
            const colour = move ? ui.move : ui.dep;
            const forward = b.x > a.x;
            // Leave from the right edge going forward, from the left when the
            // edge points back up the layering — a cycle drawn as a straight
            // line through the cards is unreadable.
            const x1 = forward ? a.x + NW : a.x, y1 = a.y + NH / 2;
            const x2 = forward ? b.x : b.x + NW, y2 = b.y + NH / 2;
            const mx = (x1 + x2) / 2;
            const d = forward
              ? `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`
              : `M ${x1} ${y1} C ${x1 - 46} ${y1 - 34}, ${x2 + 46} ${y2 - 34}, ${x2} ${y2}`;
            const w = Math.min(3, 1 + Math.log2(1 + (e.weight ?? 1)) / 2.6);
            return (
              <g key={`${e.kind ?? "dep"}-${e.from}-${e.to}`}>
                <path d={d} fill="none" stroke={colour} strokeWidth={move ? 1.6 : w}
                      strokeDasharray={move ? "5 4" : undefined}
                      markerEnd={`url(#${move ? "axd-move" : "axd-dep"})`}
                      opacity={move ? 0.95 : 0.55} />
                {e.label && (
                  <text x={mx} y={(y1 + y2) / 2 - 7} fill={colour} fontSize={9}
                        textAnchor="middle" opacity={0.95}>
                    {trunc(e.label, 22)}
                  </text>
                )}
              </g>
            );
          })}

          {view.shown.map((n) => {
            const p = view.pos.get(n.id)!;
            const [fill, stroke, chip] = TONES[n.tone][dark ? "dark" : "light"];
            return (
              <g key={n.id}>
                <rect x={p.x} y={p.y} width={NW} height={NH} rx={9}
                      fill={fill} stroke={stroke} strokeWidth={1.3} />
                <rect x={p.x} y={p.y} width={4} height={NH} rx={2} fill={chip} />
                <rect x={p.x + 15} y={p.y + 14} width={15} height={15} rx={4}
                      fill="none" stroke={chip} strokeWidth={1.3} />
                <rect x={p.x + 19} y={p.y + 18} width={7} height={2.6} rx={1.3} fill={chip} />
                <text x={p.x + 38} y={p.y + 27} fill={ui.text} fontSize={13} fontWeight={700}>
                  {trunc(n.title, 18)}
                </text>
                <text x={p.x + 15} y={p.y + 50} fill={ui.muted} fontSize={10}>
                  {trunc(n.subtitle, 30)}
                </text>
                <text x={p.x + 15} y={p.y + 67} fill={ui.faint} fontSize={9}>
                  {trunc(n.detail, 34)}
                </text>
              </g>
            );
          })}

          {tonesUsed.map((t, i) => {
            const x = PAD + i * 150;
            const y = HEAD_H + view.groupH + 26;
            const [fill, stroke] = TONES[t][dark ? "dark" : "light"];
            return (
              <g key={t}>
                <rect x={x} y={y - 9} width={11} height={11} rx={3}
                      fill={fill} stroke={stroke} strokeWidth={1.1} />
                <text x={x + 18} y={y} fill={ui.muted} fontSize={10}>{TONE_LABEL[t]}</text>
              </g>
            );
          })}
          <text x={PAD} y={HEAD_H + view.groupH + 45} fill={ui.faint} fontSize={9}>
            {trunc([
              view.hidden > 0 ? `${view.hidden} smaller module(s) not drawn` : "",
              footnote ?? "",
            ].filter(Boolean).join(" · "), 120)}
          </text>
        </svg>
      </div>
    </div>
  );
}
