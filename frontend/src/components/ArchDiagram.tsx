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
  /** Every file in the module, for the detail panel opened on click. The card
   *  itself only names the first few (see `detail`); this is the full list. */
  files?: string[];
}

/** A module's architectural role, derived from its position in the dependency
 *  graph. Category is carried by the icon, the accent bar and the badge; the
 *  card's fill and border stay with the delta tone, since what changed is what
 *  this view is for. Both read at once without fighting for one channel.
 *
 *  Deliberately NOT lib/codemapRoles.classifyRole. That classifier reads symbol
 *  names (validate_user, parse_config) and does not transfer to module names:
 *  run over this repo's modules it labelled `ui` and `swarm` as "Database" off
 *  a single incidental filename match. A confidently wrong label is worse than
 *  none. Graph position, by contrast, is measured rather than guessed — and it
 *  is the same thing the reference layouts encode when they put callers on the
 *  left and consumers on the right.
 */
const ROLES: Record<string, { hue: string; icon: string }> = {
  Entry:    { hue: "#0ea5e9", icon: "🚪" },   // nothing here depends on it
  Hub:      { hue: "#8b5cf6", icon: "🧠" },   // heavily wired both ways
  Link:     { hue: "#10b981", icon: "🔗" },   // depends on some, depended on by some
  Sink:     { hue: "#f59e0b", icon: "📤" },   // depends on nothing drawn
  Isolated: { hue: "#64748b", icon: "⬚" },    // no drawn dependencies either way
};
const hueOf = (label: string) => ROLES[label]?.hue ?? "#64748b";

function roleOf(id: string, deps: DiagramEdge[]): { icon: string; roleLabel: string } {
  const out = deps.filter((e) => e.from === id).length;
  const inc = deps.filter((e) => e.to === id).length;
  let label: string;
  if (!out && !inc) label = "Isolated";
  else if (!inc) label = "Entry";
  else if (!out) label = "Sink";
  else label = inc + out >= 6 ? "Hub" : "Link";
  return { icon: ROLES[label].icon, roleLabel: label };
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
const NW = 258, NH = 80, COLGAP = 112, GAPY = 24, PAD = 38;
const HEAD_H = 96, LEGEND_H = 74;

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

/** Longest-path layering that survives dependency cycles.
 *
 *  Import cycles are normal in real code, and plain longest-path relaxation
 *  ratchets a cyclic component's depth up on every pass — on a 14-module graph
 *  with cycles it settled at layers 54/55/56, which is not a layout, it is a
 *  bug wearing one. So the cycle is broken first: a depth-first sweep marks
 *  every *back edge* (an edge into a node still open on the current DFS path)
 *  and those edges are excluded from the depth computation. They are still
 *  returned and still drawn — a dependency that closes a loop is real — they
 *  just don't get to define how deep a column sits.
 *
 *  Deterministic: nodes are visited in the given order and neighbours in edge
 *  order, so the same graph always breaks the same edges and lays out the same. */
function layering(
  ids: string[], edges: DiagramEdge[],
): { layer: Map<string, number>; back: Set<string> } {
  const deps = edges.filter((e) => e.kind !== "move" && e.from !== e.to);
  const adj = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const e of deps) if (adj.has(e.from) && adj.has(e.to)) adj.get(e.from)!.push(e.to);

  // DFS three-colour cycle detection. GRAY = on the current path.
  const OPEN = 1, DONE = 2;
  const state = new Map<string, number>();
  const back = new Set<string>();
  const visit = (u: string) => {
    state.set(u, OPEN);
    for (const v of adj.get(u)!) {
      const s = state.get(v);
      if (s === OPEN) back.add(`${u} ${v}`);   // edge closes a loop
      else if (s == null) visit(v);
    }
    state.set(u, DONE);
  };
  for (const id of ids) if (state.get(id) == null) visit(id);

  // Longest path over the acyclic remainder. Capped at ids.length passes, which
  // is now a genuine convergence bound rather than a runaway guard.
  const forward = deps.filter((e) => !back.has(`${e.from} ${e.to}`));
  const layer = new Map(ids.map((id) => [id, 0]));
  for (let pass = 0; pass < ids.length; pass++) {
    let moved = false;
    for (const e of forward) {
      const a = layer.get(e.from)!, b = layer.get(e.to)!;
      if (b < a + 1) { layer.set(e.to, a + 1); moved = true; }
    }
    if (!moved) break;
  }
  return { layer, back };
}

/** Choose which dependency edges to draw under a budget, without orphaning.
 *
 *  A flat "keep the heaviest N" cap silently drops every edge of a lightly-wired
 *  module, so it renders with no arrows and gets mislabelled "Isolated" — a
 *  measured-looking claim that is actually an artefact of the cap. So each node
 *  first reserves its single strongest incident edge; only then is the rest of
 *  the budget filled by weight. Every module that has any dependency at all
 *  keeps at least one line, and the roles read off the drawn edges stay true. */
const keyOf = (e: DiagramEdge) => `${e.from} ${e.to}`;
function chooseEdges(
  shownIds: string[], deps: DiagramEdge[],
): { kept: DiagramEdge[]; dropped: number } {
  const byWeight = deps.slice().sort(
    (a, b) => (b.weight ?? 0) - (a.weight ?? 0) || keyOf(a).localeCompare(keyOf(b)));
  const kept = new Map<string, DiagramEdge>();
  for (const id of shownIds) {
    const best = byWeight.find((e) => e.from === id || e.to === id);
    if (best) kept.set(keyOf(best), best);
  }
  for (const e of byWeight) {
    if (kept.size >= MAX_EDGES) break;
    kept.set(keyOf(e), e);
  }
  return { kept: [...kept.values()], dropped: deps.length - kept.size };
}

export default function ArchDiagram({
  title, subtitle, groupLabel, nodes, edges, filename, footnote, onOpenFile,
}: {
  title: string;
  subtitle: string;
  groupLabel: string;
  nodes: DiagramNode[];
  edges: DiagramEdge[];
  filename: string;
  footnote?: string;
  /** Open a file in the Reader. Kept as a callback so the diagram stays
   *  routing-agnostic; the page supplies the actual navigation. */
  onOpenFile?: (path: string) => void;
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
    const depAll = live.filter((e) => e.kind !== "move");
    const { kept: deps, dropped: droppedEdges } = chooseEdges(shown.map((n) => n.id), depAll);
    const moves = live.filter((e) => e.kind === "move");
    const drawn = [...deps, ...moves];

    const { layer } = layering(shown.map((n) => n.id), drawn);
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

    // Roles are read off the edges that are actually drawn, so the badge always
    // agrees with the arrows in front of the reader — a module called "Entry"
    // has no incoming arrow on this diagram, not merely none in the full graph.
    const depsDrawn = drawn.filter((e) => e.kind !== "move");
    const roleBy = new Map(shown.map((n) => [n.id, roleOf(n.id, depsDrawn)]));
    const tally = new Map<string, number>();
    for (const n of shown) {
      const label = roleBy.get(n.id)!.roleLabel;
      tally.set(label, (tally.get(label) ?? 0) + 1);
    }
    const roles = [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

    const groupW = cols.length * (NW + COLGAP) - COLGAP + PAD * 2;
    // The header (title + "ARCH DELTA" label) needs its own minimum width
    // independent of the module boxes — a diagram with few/small modules
    // (few real facts to draw) but a long two-full-sha title used to render
    // narrower than that title, so the right-anchored "ARCH DELTA" label
    // collided with the title's own overflow instead of sitting past it.
    const minHeaderW = PAD + Math.min(title.length, 52) * 13 + 24 + 100;
    return {
      shown, drawn, pos, hidden, roles, roleBy, droppedEdges,
      width: Math.max(groupW + PAD * 2, minHeaderW),
      height: HEAD_H + totalH + PAD * 2 + LEGEND_H,
      groupW, groupH: totalH + PAD * 2,
    };
  }, [nodes, edges, title]);

  const tonesUsed = useMemo(() => {
    const seen = new Set<Tone>(view.shown.map((n) => n.tone));
    return (["added", "removed", "changed", "kept"] as Tone[]).filter((t) => seen.has(t));
  }, [view]);

  // ---- interaction: a clicked module sticks; a hovered one previews ----
  // A click selects and holds; hovering only previews while there is no
  // selection, so moving the mouse doesn't yank a panel you are reading.
  const [sel, setSel] = useState<string | null>(null);
  const [hov, setHov] = useState<string | null>(null);
  const focus = sel ?? hov;

  // The focused module plus everything one edge away — the spotlight set. Edges
  // and cards outside it dim, so a single box and all of its connections read
  // out of the tangle at once.
  const near = useMemo(() => {
    const s = new Set<string>();
    if (!focus) return s;
    s.add(focus);
    for (const e of view.drawn) {
      if (e.from === focus) s.add(e.to);
      if (e.to === focus) s.add(e.from);
    }
    return s;
  }, [focus, view.drawn]);

  // Everything the *panel* shows about the selected module — its full file list
  // and every connection, drawn or not (the diagram caps edges for legibility;
  // the panel is where the complete picture lives).
  const detail = useMemo(() => {
    if (!sel) return null;
    const node = view.shown.find((n) => n.id === sel);
    if (!node) return null;
    const out = edges.filter((e) => e.from === sel && e.to !== sel);
    const inc = edges.filter((e) => e.to === sel && e.from !== sel);
    const byWeight = (a: DiagramEdge, b: DiagramEdge) => (b.weight ?? 0) - (a.weight ?? 0);
    return {
      node,
      role: view.roleBy.get(sel)!,
      out: out.filter((e) => e.kind !== "move").sort(byWeight),
      inc: inc.filter((e) => e.kind !== "move").sort(byWeight),
      moves: [...out, ...inc].filter((e) => e.kind === "move"),
    };
  }, [sel, view.shown, view.roleBy, edges]);

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

          <pattern id="axd-grid" width={26} height={26} patternUnits="userSpaceOnUse">
            <path d="M 26 0 L 0 0 0 26" fill="none" stroke={ui.line} strokeWidth={0.5}
                  opacity={0.5} />
          </pattern>

          <rect x={0} y={0} width={width} height={height} fill={ui.bg} rx={12}
                onClick={() => setSel(null)} />
          <rect x={0} y={HEAD_H - 10} width={width} height={height - HEAD_H - LEGEND_H + 10}
                fill="url(#axd-grid)" onClick={() => setSel(null)} />

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

          {/* One boundary: the package. The reference nests a boundary inside
              another (a security concern drawn inside the runtime package), but
              that split is a real fact about that codebase. Here the data is a
              single package of peer submodules with no sub-grouping signal to
              read, so a nested boundary would be invented — and this codebase's
              rule is that a confidently wrong frame is worse than none. */}
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
            // When a module is focused, its own edges brighten and thicken while
            // the rest fade back, so a single box's wiring stands out of the mesh.
            const hot = focus != null && (e.from === focus || e.to === focus);
            const faded = focus != null && !hot;
            const baseOp = move ? 0.95 : 0.55;
            const op = faded ? 0.1 : hot ? 1 : baseOp;
            return (
              <g key={`${e.kind ?? "dep"}-${e.from}-${e.to}`}>
                <path d={d} fill="none" stroke={colour}
                      strokeWidth={(move ? 1.6 : w) * (hot ? 1.8 : 1)}
                      strokeDasharray={move ? "5 4" : undefined}
                      markerEnd={`url(#${move ? "axd-move" : "axd-dep"})`}
                      opacity={op} />
                {e.label && !faded && (() => {
                  // Pill behind the label, as the codemap does — an unbacked
                  // label sitting on a line is unreadable where edges cross.
                  const txt = trunc(e.label, 22);
                  const lw = txt.length * 5.4 + 12;
                  const ly = (y1 + y2) / 2 - 8;
                  return (
                    <g>
                      <rect x={mx - lw / 2} y={ly - 9} width={lw} height={16} rx={8}
                            fill={ui.bg} stroke={colour} strokeWidth={0.8} opacity={0.95} />
                      <text x={mx} y={ly + 2.5} fill={colour} fontSize={9} textAnchor="middle">
                        {txt}
                      </text>
                    </g>
                  );
                })()}
              </g>
            );
          })}

          {/* Cards follow CodemapView's anatomy: rounded box, inset accent bar,
              leading icon, title over a detail line, category badge in the
              corner — so the two views read as one product. */}
          {view.shown.map((n) => {
            const p = view.pos.get(n.id)!;
            const [fill, stroke] = TONES[n.tone][dark ? "dark" : "light"];
            const role = view.roleBy.get(n.id)!;
            const hue = hueOf(role.roleLabel);
            const active = n.id === sel;
            const faded = focus != null && !near.has(n.id);
            return (
              <g key={n.id}
                 opacity={faded ? 0.28 : 1}
                 style={{ cursor: "pointer" }}
                 onClick={(ev) => { ev.stopPropagation(); setSel((c) => (c === n.id ? null : n.id)); }}
                 onMouseEnter={() => setHov(n.id)}
                 onMouseLeave={() => setHov((c) => (c === n.id ? null : c))}>
                {/* Selection ring — an outer stroke in the module's category hue,
                    outside the card so it reads as a halo, not a thicker border. */}
                {active && (
                  <rect x={p.x - 3} y={p.y - 3} width={NW + 6} height={NH + 6} rx={19}
                        fill="none" stroke={hue} strokeWidth={2.4} opacity={0.9} />
                )}
                <rect x={p.x} y={p.y} width={NW} height={NH} rx={16}
                      fill={fill} stroke={active ? hue : stroke} strokeWidth={active ? 2 : 1.4} />
                <rect x={p.x} y={p.y + 16} width={4} height={NH - 32} rx={2} fill={hue} />
                <text x={p.x + 24} y={p.y + 32} fontSize={15}>{role.icon}</text>
                <text x={p.x + 48} y={p.y + 30} fill={ui.text} fontSize={13.5} fontWeight={700}>
                  {trunc(n.title, 18)}
                </text>
                <text x={p.x + NW - 14} y={p.y + 19} textAnchor="end"
                      fill={hue} fontSize={8.5} letterSpacing={0.7}>
                  {role.roleLabel.toUpperCase()}
                </text>
                <text x={p.x + 24} y={p.y + 52} fill={ui.muted} fontSize={10.5}>
                  {trunc(n.subtitle, 30)}
                </text>
                <text x={p.x + 24} y={p.y + 68} fill={ui.faint} fontSize={9}>
                  {trunc(n.detail, 36)}
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
          {/* Category tally, as the reference legend does — how much of each
              kind of thing the drawn architecture is made of. */}
          {view.roles.map(([label, count], i) => {
            const x = PAD + i * 118;
            const y = HEAD_H + view.groupH + 48;
            return (
              <g key={label}>
                <circle cx={x + 5} cy={y - 4} r={4.5} fill={hueOf(label)} />
                <text x={x + 16} y={y} fill={ui.faint} fontSize={9.5}>
                  {label} {count}
                </text>
              </g>
            );
          })}
          {/* Truncation is disclosed on the diagram itself: a capped picture
              that stays silent reads as "this is everything", which it isn't. */}
          <text x={width - PAD} y={HEAD_H + view.groupH + 45} textAnchor="end"
                fill={ui.faint} fontSize={9}>
            {trunc([
              view.hidden > 0 ? `${view.hidden} smaller module(s) not drawn` : "",
              view.droppedEdges > 0 ? `${view.droppedEdges} lighter edge(s) hidden` : "",
              footnote ?? "",
            ].filter(Boolean).join(" · "), 120)}
          </text>
        </svg>

        {!sel && (
          <div className="axd-hint">Click a module to open its files and connections</div>
        )}
      </div>

      {/* The "expand" surface: a module's full file list and every dependency,
          drawn or capped. Plain HTML, deliberately outside the SVG, so the
          serialized diagram an export produces is unaffected. */}
      {detail && (
        <aside className="axd-panel">
          <div className="axd-panel-head">
            <div className="axd-panel-title">
              <span>{detail.node.title}</span>
              <span className="axd-panel-role" style={{ color: hueOf(detail.role.roleLabel) }}>
                {detail.role.roleLabel}
              </span>
            </div>
            <button className="axd-panel-x" onClick={() => setSel(null)} title="Close">✕</button>
          </div>
          <div className="axd-panel-sub">{detail.node.subtitle}</div>

          <div className="axd-panel-sec">
            <div className="axd-panel-k">
              Files{detail.node.files ? ` (${detail.node.files.length})` : ""}
            </div>
            {detail.node.files && detail.node.files.length > 0 ? (
              <ul className="axd-panel-files">
                {detail.node.files.map((f) => (
                  <li key={f}>
                    {onOpenFile ? (
                      <button className="axd-file-open" title={`Open ${f} in the Reader`}
                              onClick={() => onOpenFile(f)}>
                        <span>{f.split("/").pop()}</span>
                        <span className="axd-file-arrow" aria-hidden>→</span>
                      </button>
                    ) : (
                      <span title={f}>{f.split("/").pop()}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="axd-panel-empty">
                {detail.node.tone === "removed" ? "No longer present at this ref." : "No files listed."}
              </div>
            )}
          </div>

          {detail.out.length > 0 && (
            <div className="axd-panel-sec">
              <div className="axd-panel-k">Depends on →</div>
              <ul className="axd-panel-links">
                {detail.out.map((e) => (
                  <li key={"o" + e.to}>
                    <button onClick={() => setSel(e.to)}>{e.to}</button>
                    {e.weight ? <span className="axd-panel-w">{e.weight}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {detail.inc.length > 0 && (
            <div className="axd-panel-sec">
              <div className="axd-panel-k">← Depended on by</div>
              <ul className="axd-panel-links">
                {detail.inc.map((e) => (
                  <li key={"i" + e.from}>
                    <button onClick={() => setSel(e.from)}>{e.from}</button>
                    {e.weight ? <span className="axd-panel-w">{e.weight}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {detail.moves.length > 0 && (
            <div className="axd-panel-sec">
              <div className="axd-panel-k">Files moved</div>
              <ul className="axd-panel-links">
                {detail.moves.map((e) => (
                  <li key={"m" + e.from + e.to}>
                    <span className="axd-panel-move">{e.from} → {e.to}</span>
                    <span className="axd-panel-w">{e.label}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {detail.out.length === 0 && detail.inc.length === 0 && (
            <div className="axd-panel-empty">
              No module-level dependencies to or from this one.
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
