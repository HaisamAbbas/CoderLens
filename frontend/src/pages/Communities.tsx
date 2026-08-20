import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageLoading, ErrorState } from "../components/PageState";
import type { CommunityMember } from "../lib/types";

const short = (p: string) => p.replace(/^src\//, "");
const KIND_COLOR: Record<string, string> = {
  class: "var(--c-sansio)", method: "var(--c-core)", function: "var(--c-json)",
};

export default function Communities() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["communities"], queryFn: api.communities });

  if (q.isLoading) return <PageLoading tiles={0} />;
  if (!q.data) return <ErrorState message={q.error instanceof Error ? q.error.message : undefined} onRetry={() => q.refetch()} />;

  const { clusters, total } = q.data;
  const openMember = (m: CommunityMember) => nav("/reader", { state: { path: m.path, symbolId: m.id } });

  return (
    <div className="page">
      <div className="eyebrow">Communities</div>
      <h1 className="h1" style={{ marginTop: 6 }}>Functional clusters</h1>
      <p className="lede">
        Groups of symbols that call each other densely, discovered from the dependency graph rather
        than folder structure — sometimes a class and its cooperating mixins cluster together even
        when they live in different files, or two files in the same folder split apart because they
        don't actually talk to each other.
      </p>

      {clusters.length === 0 ? (
        <div className="state" style={{ marginTop: 24 }}>Not enough internal structure to cluster yet.</div>
      ) : (
        <>
          <p className="cap" style={{ marginTop: 20 }}>{total} cluster{total === 1 ? "" : "s"} found · showing the {clusters.length} largest</p>
          <div className="cl-grid">
            {clusters.map((c) => (
              <div className="card cl-card" key={c.label + c.primary_dir}>
                <div className="cl-head">
                  <h3>{c.label}</h3>
                  <span className="chip solid" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                    {c.size} symbols
                  </span>
                </div>
                <p className="cap">
                  {short(c.primary_dir)}{c.dir_spread > 1 ? ` · spans ${c.dir_spread} directories` : ""}
                </p>
                <div className="cl-members">
                  {c.members.map((m) => (
                    <div className="cl-member" key={m.id} onClick={() => openMember(m)} title={`${m.path}:${m.line}`}>
                      <span className="cl-dot" style={{ background: KIND_COLOR[m.kind] ?? "var(--text-3)" }} />
                      <span className="cl-name">{m.qualified_name}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
