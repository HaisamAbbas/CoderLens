import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import Treemap, { type TreemapItem } from "../components/Treemap";
import SectionHead from "../components/SectionHead";
import { ErrorState, PageLoading } from "../components/PageState";
import { usePersona } from "../lib/usePersona";
import {
  BoltIcon, ClusterIcon, FlagIcon, FlameIcon, GhostIcon,
  LayersIcon, LinkIcon, RouteIcon,
} from "../components/icons";
import type { CouplingPair, Entrypoint } from "../lib/types";

const ENTRY_META: Record<string, { label: string; color: string }> = {
  route: { label: "HTTP routes", color: "var(--s-code)" },
  factory: { label: "App factories", color: "var(--s-issue)" },
  cli: { label: "CLI commands", color: "var(--s-graph)" },
  worker: { label: "Background tasks", color: "var(--s-commit)" },
  main: { label: "main()", color: "var(--c-core)" },
  module: { label: "Script entries", color: "var(--text-3)" },
};
const ENTRY_ORDER = ["route", "factory", "cli", "worker", "main", "module"];

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const short = (p: string) => p.replace(/^src\//, "");

function Stat({ n, l, mono, accent }: { n: number | string; l: string; mono?: boolean; accent?: boolean }) {
  return (
    <div className={"ov-stat" + (accent ? " accent" : "")}>
      <div className={"n" + (mono ? " mono" : " tnum")}>{typeof n === "number" ? n.toLocaleString() : n}</div>
      <div className="l">{l}</div>
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
        <b>{data.counts.edges.toLocaleString()} internal dependencies</b>. Its most connected module is{" "}
        <span className="k">{data.most_central}</span> — the files below carry the most connections and
        are the best place to start reading.
      </p>
      <div className="chips">
        {dirs.map((d) => (
          <span key={d} className="chip"><span className="dot" style={{ background: "var(--text-3)" }} />{d}</span>
        ))}
      </div>
    </div>
  );

  const readingCard = (
    <div className="card">
      <SectionHead icon={<RouteIcon />} title="Start here — a reading path" cap="Ordered by how central each file is to the system" tone="var(--text-3)" />
      <ol className="reading">
        {data.reading_path.map((r) => (
          <li key={r.path} onClick={() => openFile(r.path)}>
            <div>
              <div className="f">{short(r.path)}</div>
              <div className="why">{r.reason}</div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );

  const hotspotsCard = persona !== "new" && (
    <div className="card">
      <SectionHead icon={<FlameIcon />} title="Hotspots" cap="High churn × coupling — box size = risk, click to open" tone="var(--warn)" />
      <Treemap
        height={168}
        items={data.hotspots.map((h): TreemapItem => ({
          key: h.path,
          label: short(h.path),
          sub: `${h.churn} changes · ${h.coupling} deps`,
          value: Math.max(h.score, 0.04),
          tint: `color-mix(in srgb, var(--warn) ${Math.round(24 + h.score * 56)}%, var(--surface))`,
          title: `${h.path} — ${h.churn} changes · ${h.coupling} dependencies`,
          onClick: () => openFile(h.path),
        }))}
      />
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

  const rail = [hotspotsCard, couplingCard, deepDiveCard].filter(Boolean);

  return (
    <div className="page mat ov-page">
      <div className="ov-hero">
        <div className="ov-hero-info">
          <div className="eyebrow">Overview</div>
          <h1 className="h1" style={{ marginTop: 6 }}>{cap(data.name)}</h1>
          <p className="lede">
            A map of how this codebase fits together — where to start reading, what's central, and what to
            change carefully. Reconstructed from the code, its git history, and its docs.
          </p>
        </div>
        <div className="ov-hero-stats">
          <Stat n={data.counts.files} l="Files" />
          <Stat n={data.counts.symbols} l="Symbols" />
          <Stat n={data.counts.edges} l="Dependencies" />
          <Stat n={data.most_central} l="Most central" mono accent />
        </div>
      </div>

      {persona === "new" && (
        <div className="ov-banner">
          <FlagIcon />
          <span>New to this codebase? The guided tour walks you through it step by step.</span>
          <button className="btn primary" onClick={() => nav("/tour")}>Start the tour</button>
        </div>
      )}

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
  return (
    <div className="card">
      <SectionHead
        icon={<LinkIcon />} tone="var(--text-3)" title="Change coupling"
        cap={`Files that change together in the same commit${windowed && windowDays ? ` · last ${windowDays} days` : " · full history"} — a signal independent of imports`}
      />
      <div className="co-list">
        {pairs.map((p) => (
          <div className="co-row" key={p.a + p.b}>
            <div className="co-files">
              <span className="co-f" onClick={() => onOpen(p.a)}>{short(p.a)}</span>
              <span className="co-x">×</span>
              <span className="co-f" onClick={() => onOpen(p.b)}>{short(p.b)}</span>
            </div>
            <span className="co-meta tnum">{p.co_changes} commits together</span>
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
      <SectionHead icon={<BoltIcon />} title="Entrypoints" cap="Where execution begins — click one to trace its call flow" tone="var(--text-3)" />
      <div className="ep-groups">
        {groups.map(([kind, list]) => {
          const meta = ENTRY_META[kind];
          return (
            <div className="ep-group" key={kind}>
              <div className="gh"><b>{meta.label}</b> · {list.length}</div>
              <div className="ep-list">
                {list.slice(0, 12).map((e, i) => (
                  <div className="ep" key={kind + i} onClick={() => onOpen(e)}>
                    <span className="tag" style={{ background: `color-mix(in srgb, ${meta.color} 16%, transparent)`, color: meta.color }}>{kind}</span>
                    <span className="lab">{e.label}</span>
                    <span className="loc">{e.path.split("/").pop()}:{e.line}</span>
                  </div>
                ))}
                {list.length > 12 && <div style={{ fontSize: 12, color: "var(--text-3)", padding: "4px 10px" }}>+{list.length - 12} more</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
