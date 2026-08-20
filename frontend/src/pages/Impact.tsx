import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { ErrorState, PageLoading } from "../components/PageState";
import { BoltIcon, InboundIcon, LinkIcon, TargetIcon } from "../components/icons";
import type { CoupledFile, ImpactNode, ImpactRisk } from "../lib/types";

const short = (p: string) => p.replace(/^src\//, "");
const RISK_COLOR: Record<ImpactRisk["level"], string> = {
  low: "var(--good)", medium: "var(--c-sansio)", high: "var(--bad, #e5484d)",
};

export default function Impact() {
  const loc = useLocation();
  const nav = useNavigate();
  const st = loc.state as { symbolId?: number } | null;
  const symbolId = st?.symbolId;

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["impact", symbolId],
    queryFn: () => api.impact(symbolId!),
    enabled: symbolId != null,
  });

  const open = (n: ImpactNode) => nav("/reader", { state: { path: n.file, symbolId: n.id } });
  const openFile = (p: string) => nav("/reader", { state: { path: p } });

  if (symbolId == null)
    return (
      <div className="page">
        <div className="eyebrow">Impact analysis</div>
        <div className="state">Open impact analysis from a symbol in the <b>Reader</b>.</div>
      </div>
    );

  return (
    <div className="page">
      <div className="eyebrow">Impact analysis</div>
      {isLoading && <PageLoading header={false} tiles={0} />}
      {error && <ErrorState message={error instanceof Error ? error.message : undefined} onRetry={() => refetch()} />}
      {data && (
        <>
          <h1 className="h1" style={{ marginTop: 6 }}>{data.symbol.qualified_name}</h1>
          <p className="lede">
            Is it safe to change this? {short(data.symbol.file)}:{data.symbol.line}
          </p>

          <div className="imp-verdict" style={{ borderColor: RISK_COLOR[data.risk.level] }}>
            <span className="imp-level" style={{ background: RISK_COLOR[data.risk.level] }}>
              {data.risk.level} risk
            </span>
            <p>{data.risk.reason}</p>
          </div>

          {data.is_entrypoint && (
            <div className="imp-flag">
              <BoltIcon style={{ width: 14, height: 14 }} /> This is an entrypoint — something outside
              the visible call graph (a route, CLI command, or framework) invokes it directly.
            </div>
          )}

          <div className="imp-grid">
            <ImpactSection
              icon={<InboundIcon />}
              title="Direct callers"
              cap={`${data.direct_callers.length} place${data.direct_callers.length === 1 ? "" : "s"} call this directly`}
              nodes={data.direct_callers}
              empty="Nothing internal calls this directly."
              onOpen={open}
            />
            <ImpactSection
              icon={<TargetIcon />}
              title="Test coverage"
              cap={data.test_callers.length ? "Tests that exercise this path" : "No test path found"}
              nodes={data.test_callers}
              empty="No test calls this — changes here won't be caught automatically."
              onOpen={open}
              tone={data.test_callers.length ? undefined : "warn"}
            />
          </div>

          {data.transitive_callers.length > 0 && (
            <ImpactSection
              icon={<InboundIcon />}
              title="Further upstream"
              cap={`${data.transitive_callers.length} more, 2+ hops away`}
              nodes={data.transitive_callers}
              empty=""
              onOpen={open}
              collapsedByDefault
            />
          )}

          {data.coupled_files.length > 0 && (
            <div className="imp-section">
              <div className="imp-section-head">
                <LinkIcon />
                <span>Change coupling</span>
                <span className="rd-hint">files that changed alongside this one historically</span>
              </div>
              <div className="co-list">
                {data.coupled_files.map((p: CoupledFile) => {
                  const other = p.a === data.symbol.file ? p.b : p.a;
                  return (
                    <div className="co-row" key={p.a + p.b}>
                      <span className="co-f" onClick={() => openFile(other)}>{short(other)}</span>
                      <span className="co-meta tnum">{p.co_changes} commits together</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ImpactSection({ icon, title, cap, nodes, empty, onOpen, tone, collapsedByDefault }: {
  icon: React.ReactNode; title: string; cap: string; nodes: ImpactNode[]; empty: string;
  onOpen: (n: ImpactNode) => void; tone?: "warn"; collapsedByDefault?: boolean;
}) {
  return (
    <details className="imp-section" open={!collapsedByDefault}>
      <summary className="imp-section-head">
        {icon}
        <span>{title}</span>
        <span className={"rd-hint" + (tone === "warn" ? " warn" : "")}>{cap}</span>
      </summary>
      {nodes.length === 0
        ? <div className="rd-empty-sm">{empty}</div>
        : nodes.map((n) => (
            <div key={n.id} className="rd-row" onClick={() => onOpen(n)} title={n.file}>
              <span className="rd-row-name">{n.qualified_name}</span>
              <span className="rd-row-loc">{short(n.file)}:{n.line}</span>
            </div>
          ))}
    </details>
  );
}
