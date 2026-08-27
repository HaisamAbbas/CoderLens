/** An architecture diagram drawn as real SVG — typed cards with a title and a
 *  subtitle, grouped inside a labelled boundary, with labelled edges and a
 *  legend.
 *
 *  Not Mermaid. Mermaid lays out flowcharts, and a flowchart node is a shape
 *  with a string in it; there is nowhere to put a second line of detail, a
 *  category colour, a dashed boundary around a subset of nodes, or a legend
 *  explaining what the colours mean. Drawing the SVG directly costs a layout
 *  pass and buys all of that.
 *
 *  Every colour is written as a literal attribute rather than a CSS class, so
 *  the serialized markup is self-contained: export needs no stylesheet, and
 *  there is no <foreignObject> to stop a canvas rasterizing it.
 */

import { useMemo, useRef, useState } from "react";
import { downloadPngElement, downloadSvgElement } from "../lib/diagramExport";

export type Tone = "added" | "removed" | "changed" | "kept";

export interface DiagramNode {
  id: string;
  title: string;
  subtitle: string;
  tone: Tone;
}
export interface DiagramEdge {
  from: string;
  to: string;
  label: string;
}

/** Palette per tone: card fill, border, and the accent chip down the left edge.
 *  Two variants so the diagram reads on either theme rather than being a dark
 *  rectangle punched into a light page. */
const TONES: Record<Tone, { light: [string, string, string]; dark: [string, string, string] }> = {
  added:   { light: ["#f0fdf4", "#16a34a", "#22c55e"], dark: ["#0e2a1a", "#22c55e", "#4ade80"] },
  removed: { light: ["#fef2f2", "#dc2626", "#ef4444"], dark: ["#2b1113", "#ef4444", "#f87171"] },
  changed: { light: ["#fffbeb", "#d97706", "#f59e0b"], dark: ["#2c210a", "#f59e0b", "#fbbf24"] },
  kept:    { light: ["#f8fafc", "#94a3b8", "#cbd5e1"], dark: ["#141a24", "#475569", "#64748b"] },
};

const TONE_LABEL: Record<Tone, string> = {
  added: "added", removed: "removed", changed: "files added or removed", kept: "unchanged",
};

// Card metrics. Fixed sizes keep the layout deterministic — the same delta must
// always draw the same diagram, or an export taken twice would differ.
const CARD_W = 186, CARD_H = 58, GAP_X = 26, GAP_Y = 24;
const GROUP_PAD = 26, HEAD_H = 74, LEGEND_H = 46, MARGIN = 22;

const isDarkNow = () =>
  document.documentElement.getAttribute("data-theme") === "dark"
  || (document.documentElement.getAttribute("data-theme") !== "light"
      && window.matchMedia("(prefers-color-scheme: dark)").matches);

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

export default function ArchDiagram({
  title, subtitle, groupLabel, nodes, edges, filename,
}: {
  title: string;
  subtitle: string;
  groupLabel: string;
  nodes: DiagramNode[];
  edges: DiagramEdge[];
  filename: string;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [busy, setBusy] = useState("");
  const dark = isDarkNow();

  const ui = dark
    ? { bg: "#0b0f16", panel: "#0e131c", line: "#1e293b", text: "#e2e8f0",
        muted: "#94a3b8", faint: "#64748b", edge: "#5eead4" }
    : { bg: "#ffffff", panel: "#fbfcfe", line: "#e2e8f0", text: "#0f172a",
        muted: "#64748b", faint: "#94a3b8", edge: "#0d9488" };

  const layout = useMemo(() => {
    // Near-square grid, capped at 4 columns so cards stay readable and the
    // diagram stays a sensible aspect ratio for a share card.
    const cols = Math.max(1, Math.min(4, Math.ceil(Math.sqrt(nodes.length || 1))));
    const rows = Math.max(1, Math.ceil(nodes.length / cols));
    const pos = new Map<string, { x: number; y: number }>();
    nodes.forEach((n, i) => {
      pos.set(n.id, {
        x: MARGIN + GROUP_PAD + (i % cols) * (CARD_W + GAP_X),
        y: HEAD_H + GROUP_PAD + Math.floor(i / cols) * (CARD_H + GAP_Y),
      });
    });
    const gridW = cols * CARD_W + (cols - 1) * GAP_X;
    const gridH = rows * CARD_H + (rows - 1) * GAP_Y;
    const groupW = gridW + GROUP_PAD * 2;
    const groupH = gridH + GROUP_PAD * 2;
    return {
      pos, cols,
      groupX: MARGIN, groupY: HEAD_H, groupW, groupH,
      width: groupW + MARGIN * 2,
      height: HEAD_H + groupH + LEGEND_H + MARGIN,
    };
  }, [nodes]);

  const tonesUsed = useMemo(() => {
    const seen = new Set<Tone>(nodes.map((n) => n.tone));
    return (["added", "removed", "changed", "kept"] as Tone[]).filter((t) => seen.has(t));
  }, [nodes]);

  const run = (kind: string, fn: () => void | Promise<void>) => async () => {
    if (busy || !svgRef.current) return;
    setBusy(kind);
    try { await fn(); } finally { setBusy(""); }
  };

  const { width, height } = layout;

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
          <rect x={0} y={0} width={width} height={height} fill={ui.bg} rx={10} />

          {/* header */}
          <text x={MARGIN} y={30} fill={ui.text} fontSize={17} fontWeight={700}>
            {trunc(title, 58)}
          </text>
          <text x={MARGIN} y={50} fill={ui.muted} fontSize={11.5}>
            {trunc(subtitle, 96)}
          </text>
          <text x={width - MARGIN} y={30} fill={ui.faint} fontSize={10}
                textAnchor="end" letterSpacing={1.4}>
            ARCH DELTA
          </text>

          {/* the package boundary */}
          <rect
            x={layout.groupX} y={layout.groupY}
            width={layout.groupW} height={layout.groupH}
            rx={12} fill={ui.panel} stroke={ui.line} strokeWidth={1}
            strokeDasharray="5 4"
          />
          <text x={layout.groupX + 12} y={layout.groupY + 16} fill={ui.faint} fontSize={10}>
            {trunc(groupLabel, 60)}
          </text>

          {/* edges first, so cards paint over their ends */}
          <defs>
            <marker id="axd-arrow" viewBox="0 0 10 10" refX={9} refY={5}
                    markerWidth={6} markerHeight={6} orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={ui.edge} />
            </marker>
          </defs>
          {edges.map((e) => {
            const a = layout.pos.get(e.from);
            const b = layout.pos.get(e.to);
            if (!a || !b) return null;
            const ax = a.x + CARD_W / 2, ay = a.y + CARD_H / 2;
            const bx = b.x + CARD_W / 2, by = b.y + CARD_H / 2;
            // Bowed away from the straight line so two cards in the same row
            // don't have their connector hidden under the cards between them.
            const mx = (ax + bx) / 2, my = (ay + by) / 2 - Math.max(26, Math.abs(bx - ax) * 0.16);
            return (
              <g key={`${e.from}->${e.to}`}>
                <path d={`M ${ax} ${ay} Q ${mx} ${my} ${bx} ${by}`}
                      fill="none" stroke={ui.edge} strokeWidth={1.4}
                      strokeDasharray="4 3" markerEnd="url(#axd-arrow)" opacity={0.85} />
                <text x={mx} y={my - 5} fill={ui.edge} fontSize={9.5} textAnchor="middle">
                  {trunc(e.label, 26)}
                </text>
              </g>
            );
          })}

          {/* cards */}
          {nodes.map((n) => {
            const p = layout.pos.get(n.id)!;
            const [fill, stroke, chip] = TONES[n.tone][dark ? "dark" : "light"];
            return (
              <g key={n.id}>
                <rect x={p.x} y={p.y} width={CARD_W} height={CARD_H} rx={8}
                      fill={fill} stroke={stroke} strokeWidth={1.2} />
                <rect x={p.x} y={p.y} width={4} height={CARD_H} rx={2} fill={chip} />
                <text x={p.x + 16} y={p.y + 24} fill={ui.text} fontSize={12.5} fontWeight={700}>
                  {trunc(n.title, 20)}
                </text>
                <text x={p.x + 16} y={p.y + 42} fill={ui.muted} fontSize={10}>
                  {trunc(n.subtitle, 26)}
                </text>
              </g>
            );
          })}

          {/* legend */}
          {tonesUsed.map((t, i) => {
            const x = MARGIN + i * 150;
            const y = HEAD_H + layout.groupH + 26;
            const [fill, stroke] = TONES[t][dark ? "dark" : "light"];
            return (
              <g key={t}>
                <rect x={x} y={y - 9} width={11} height={11} rx={3}
                      fill={fill} stroke={stroke} strokeWidth={1.1} />
                <text x={x + 18} y={y} fill={ui.muted} fontSize={10}>{TONE_LABEL[t]}</text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
