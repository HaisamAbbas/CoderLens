import type { CSSProperties, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import SectionHead from "../components/SectionHead";
import { ErrorState, PageLoading } from "../components/PageState";
import { usePersona } from "../lib/usePersona";
import {
  BoltIcon, ChatIcon, ClusterIcon, CodeIcon, FileIcon, FlagIcon,
  GhostIcon, HistoryIcon, LayersIcon, LinkIcon, RouteIcon, TargetIcon,
} from "../components/icons";
import type { CouplingPair, Entrypoint } from "../lib/types";

const ENTRY_META: Record<string, { label: string; singular: string; color: string }> = {
  route: { label: "HTTP routes", singular: "route", color: "var(--s-code)" },
  factory: { label: "App factories", singular: "factory", color: "var(--s-issue)" },
  cli: { label: "CLI commands", singular: "command", color: "var(--s-graph)" },
  worker: { label: "Background tasks", singular: "task", color: "var(--s-commit)" },
  main: { label: "main()", singular: "entry", color: "var(--c-core)" },
  module: { label: "Script entries", singular: "script", color: "var(--text-3)" },
};
const ENTRY_ORDER = ["route", "factory", "cli", "worker", "main", "module"];

// Purely decorative rotation for the directory-chip legend — gives the
// architecture card some visual variety instead of every chip sharing one
// flat gray dot, with no change to the underlying data.
const DIR_PALETTE = [
  "var(--accent)", "var(--s-commit)", "var(--good)", "var(--s-issue)", "var(--s-graph)", "var(--s-doc)",
];

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const short = (p: string) => p.replace(/^src\//, "");

function Kpi({ icon, n, l, tone }: { icon: ReactNode; n: number; l: string; tone: string }) {
  return (
    <div className="kpi" style={{ "--kpi-tone": tone } as CSSProperties}>
      <span className="kpi-icon" style={{ background: `color-mix(in srgb, ${tone} 15%, transparent)`, color: tone }}>
        {icon}
      </span>
      <div className="kpi-n tnum">{n.toLocaleString()}</div>
      <div className="kpi-l">{l}</div>
    </div>
  );
}

export default function Overview() {
  const nav = useNavigate();
  const [persona] = usePersona();
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["overview"], queryFn: api.overview });
  const epQ = useQuery({ queryKey: ["entrypoints"], queryFn: api.entrypoints });
  const coQ = useQuery({ queryKey: ["coupling"], queryFn: api.coupling, enabled: persona !== "new" });
  // "Deep dive" only fetches these — everyone else already gets nav links to
  // the same pages, no need to pay for the query if the card isn't shown.
  const deadQ = useQuery({ queryKey: ["dead-code"], queryFn: api.deadCode, enabled: persona === "senior" });
  const commQ = useQuery({ queryKey: ["communities"], queryFn: api.communities, enabled: persona === "senior" });

  if (isLoading) return <PageLoading />;
  if (error || !data)
    return <ErrorState message={error instanceof Error ? error.message : undefined} onRetry={() => refetch()} />;

  const openFile = (path: string) => nav("/reader", { state: { path } });
  const dirs = [...new Set(data.reading_path.map((r) => r.path.split("/").slice(0, -1).join("/")))];

  const archCard = (
    <div className="card">
      <SectionHead icon={<LayersIcon />} title="Architecture at a glance" cap="Computed from the dependency graph" tone="var(--text-3)" />
      <p className="arch">
        {cap(data.name)} spans <b>{data.counts.files} files</b> and{" "}
        <span className="k">{data.counts.symbols.toLocaleString()}</span> symbols, wired together by{" "}
        <b>{data.counts.edges.toLocaleString()} internal dependencies</b>. The files below carry the most
        connections and are the best place to start reading.
      </p>
      <div className="chips">
        {dirs.map((d, i) => (
          <span key={d} className="chip">
            <span className="dot" style={{ background: DIR_PALETTE[i % DIR_PALETTE.length] }} />{d || "/"}
          </span>
        ))}
      </div>
    </div>
  );

  const readingCard = (
    <div className="card">
      <SectionHead icon={<RouteIcon />} title="Start here — a reading path" cap="Ordered by how central each file is to the system" tone="var(--text-3)" />
      <div className="ov-steps">
        {data.reading_path.map((r, i) => (
          <button key={r.path} className="ov-step" onClick={() => openFile(r.path)}>
            <span className="ov-step-n tnum">{i + 1}</span>
            <span className="ov-step-f">{short(r.path)}</span>
            <span className="ov-step-why">{r.reason}</span>
          </button>
        ))}
      </div>
    </div>
  );

  const couplingCard = persona !== "new" && (
    <CouplingCard pairs={coQ.data?.pairs ?? []} windowed={coQ.data?.windowed} windowDays={coQ.data?.window_days} onOpen={openFile} />
  );

  const deepDiveCard = persona === "senior" && (
    <div className="card">
      <SectionHead icon={<ClusterIcon />} title="Deep dive" cap="Extra technical signal, one click from here" tone="var(--text-3)" />
      <div className="dd-grid">
        <button className="dd-tile" onClick={() => nav("/dead-code")}>
          <GhostIcon />
          <div className="n tnum">{deadQ.data?.counts.total ?? "…"}</div>
          <div className="l">Dead code candidates</div>
        </button>
        <button className="dd-tile" onClick={() => nav("/communities")}>
          <ClusterIcon />
          <div className="n tnum">{commQ.data?.total ?? "…"}</div>
          <div className="l">Communities</div>
        </button>
      </div>
    </div>
  );

  const rail = [couplingCard, deepDiveCard].filter(Boolean);

  return (
    <div className="page mat ov-page">
      <div className="ov-hero">
        <div className="ov-hero-glow" aria-hidden />
        <div className="ov-hero-body">
          <div className="eyebrow">Overview</div>
          <h1 className="h1" style={{ marginTop: 6 }}>{cap(data.name)}</h1>
          <p className="lede">
            A map of how this codebase fits together — where to start reading, what's central, and what to
            change carefully. Reconstructed from the code, its git history, and its docs.
          </p>
          {data.url && (
            <a className="ov-repo-link" href={data.url} target="_blank" rel="noreferrer">
              <LinkIcon />{data.url.replace(/^https?:\/\//, "")}
            </a>
          )}
        </div>
        {persona === "new" && (
          <div className="ov-cta">
            <FlagIcon />
            <div>
              <div className="ov-cta-t">New to this codebase?</div>
              <div className="ov-cta-s">The guided tour walks you through it step by step.</div>
            </div>
            <button className="btn primary" onClick={() => nav("/tour")}>Start the tour</button>
          </div>
        )}
      </div>

      <div className="kpi-row">
        <Kpi icon={<FileIcon />} n={data.counts.files} l="Files" tone="var(--accent)" />
        <Kpi icon={<CodeIcon />} n={data.counts.symbols} l="Symbols" tone="var(--s-graph)" />
        <Kpi icon={<LinkIcon />} n={data.counts.edges} l="Dependencies" tone="var(--good)" />
        <Kpi icon={<HistoryIcon />} n={data.counts.commits} l="Commits" tone="var(--s-commit)" />
        <Kpi icon={<ChatIcon />} n={data.counts.issues} l="Issues & PRs" tone="var(--s-issue)" />
      </div>

      <button className="ov-spotlight" onClick={() => openFile(data.reading_path[0]?.path ?? "")}>
        <span className="ov-spotlight-icon"><TargetIcon /></span>
        <div className="ov-spotlight-text">
          <div className="ov-spotlight-l">Most central module</div>
          <div className="ov-spotlight-v">{data.most_central}</div>
        </div>
        <span className="ov-spotlight-cta">Open the reading path below →</span>
      </button>

      {rail.length > 0 ? (
        <div className="ov-grid">
          <div className="ov-col-main">
            {archCard}
            {readingCard}
          </div>
          <div className="ov-col-rail">
            {rail.map((card, i) => <div key={i}>{card}</div>)}
          </div>
        </div>
      ) : (
        <div className="ov-col-main ov-stack-solo">
          {archCard}
          {readingCard}
        </div>
      )}

      <Entrypoints
        items={epQ.data?.entrypoints ?? []}
        onOpen={(e) =>
          e.symbol_id != null
            ? nav("/flow", { state: { symbolId: e.symbol_id, label: e.label } })
            : nav("/reader", { state: { path: e.path } })}
      />
    </div>
  );
}

function CouplingCard({
  pairs, windowed, windowDays, onOpen,
}: { pairs: CouplingPair[]; windowed?: boolean; windowDays: number | null | undefined; onOpen: (path: string) => void }) {
  if (!pairs.length) return null;
  const max = Math.max(...pairs.map((p) => p.co_changes));
  return (
    <div className="card">
      <SectionHead
        icon={<LinkIcon />} tone="var(--text-3)" title="Change coupling"
        cap={`Files that change together in the same commit${windowed && windowDays ? ` · last ${windowDays} days` : " · full history"}`}
      />
      <div className="co-grid">
        {pairs.map((p) => (
          <div className="co-pair" key={p.a + p.b}>
            <div className="co-top">
              <div className="co-files">
                <span className="co-f" onClick={() => onOpen(p.a)} title={p.a}>{short(p.a)}</span>
                <span className="co-f" onClick={() => onOpen(p.b)} title={p.b}><span className="co-x">↔</span>{short(p.b)}</span>
              </div>
              <span className="co-badge tnum">{p.co_changes}×</span>
            </div>
            <div className="co-bar"><span style={{ width: `${Math.max(8, (p.co_changes / max) * 100)}%` }} /></div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Entrypoints({ items, onOpen }: { items: Entrypoint[]; onOpen: (e: Entrypoint) => void }) {
  if (!items.length) return null;
  const groups = ENTRY_ORDER
    .map((k) => [k, items.filter((i) => i.kind === k)] as const)
    .filter(([, v]) => v.length);
  return (
    <div className="card ov-entrypoints">
      <SectionHead icon={<BoltIcon />} title="Entrypoints" cap="Where execution begins, grouped by kind — click a category to trace its call flow" tone="var(--text-3)" />
      <div className="ep-tiles">
        {groups.map(([kind, list]) => {
          const meta = ENTRY_META[kind];
          const sample = list.slice(0, 3).map((e) => e.label).join(", ");
          return (
            <button
              className="ep-tile" key={kind} onClick={() => onOpen(list[0])}
              style={{ "--ep-tone": meta.color } as CSSProperties}
            >
              <span className="ep-tile-top">
                <span className="ep-tile-dot" style={{ background: meta.color }} />
                <span className="ep-tile-n tnum">{list.length}</span>
              </span>
              <div className="ep-tile-l">{meta.label}</div>
              <div className="ep-tile-sample" title={sample}>
                {sample}{list.length > 3 ? `, +${list.length - 3} more` : ""}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
