import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageLoading, ErrorState } from "../components/PageState";
import type { DeadCodeCandidate } from "../lib/types";

const short = (p: string) => p.replace(/^src\//, "");
const KIND_COLOR: Record<string, string> = {
  class: "var(--c-sansio)", method: "var(--c-core)", function: "var(--c-json)",
};

export default function DeadCode() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["dead-code"], queryFn: api.deadCode });
  const [showPublic, setShowPublic] = useState(false);

  if (q.isLoading) return <PageLoading tiles={3} />;
  if (!q.data) return <ErrorState message={q.error instanceof Error ? q.error.message : undefined} onRetry={() => q.refetch()} />;

  const { candidates, counts } = q.data;
  const priv = candidates.filter((c) => c.visibility === "private");
  const pub = candidates.filter((c) => c.visibility === "public");
  const openFile = (c: DeadCodeCandidate) => nav("/reader", { state: { path: c.path, symbolId: c.id } });

  return (
    <div className="page">
      <div className="eyebrow">Dead code</div>
      <h1 className="h1" style={{ marginTop: 6 }}>Unreachable code candidates</h1>
      <p className="lede">
        Functions and methods with no internal caller found — framework-aware, so entrypoints,
        dunder methods, decorated definitions, and tests are never flagged. For a library, a public
        method can look "unreferenced" simply because it's called by external users, not by the
        library itself — those are labeled separately below.
      </p>

      <div className="tiles" style={{ marginTop: 24 }}>
        <div className="tile"><div className="n">{counts.total}</div><div className="l">Total candidates</div></div>
        <div className="tile"><div className="n">{counts.private}</div><div className="l">Private (real signal)</div></div>
        <div className="tile"><div className="n">{counts.public}</div><div className="l">Public API (likely external use)</div></div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Internal, unreferenced</h3>
        <p className="cap">Private by convention (leading underscore) — no caller anywhere in the codebase</p>
        {priv.length === 0
          ? <div className="doc" style={{ padding: "14px 0" }}>No unreferenced private helpers found.</div>
          : <DeadList items={priv} onOpen={openFile} />}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h3>Public API, no internal caller</h3>
            <p className="cap">Often expected for a library — shown for completeness, not as confirmed dead code</p>
          </div>
          <button className="btn" onClick={() => setShowPublic((v) => !v)}>
            {showPublic ? "Hide" : `Show ${pub.length}`}
          </button>
        </div>
        {showPublic && <DeadList items={pub} onOpen={openFile} muted />}
      </div>
    </div>
  );
}

function DeadList({ items, onOpen, muted }: { items: DeadCodeCandidate[]; onOpen: (c: DeadCodeCandidate) => void; muted?: boolean }) {
  return (
    <div className="dc-list" style={{ marginTop: 12 }}>
      {items.map((c) => (
        <div className={"dc-row" + (muted ? " muted" : "")} key={c.id} onClick={() => onOpen(c)}>
          <span className="dc-dot" style={{ background: KIND_COLOR[c.kind] ?? "var(--text-3)" }} />
          <div className="dc-main">
            <div className="dc-name">{c.qualified_name}</div>
            {c.signature && <div className="dc-sig">{c.signature}</div>}
          </div>
          <div className="dc-loc tnum">{short(c.path)}:{c.line}</div>
        </div>
      ))}
    </div>
  );
}
