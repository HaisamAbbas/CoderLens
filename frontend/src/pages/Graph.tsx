import { useMemo } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import GraphCanvas from "../components/GraphCanvas";
import { ErrorState, PageLoading } from "../components/PageState";

type Level = "file" | "symbol";
type GroupBy = "dir" | "community";

interface DeepLinkState { focus?: string | number }

const dirOf = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "(root)");

export default function Graph() {
  const nav = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();

  // Every filter lives in the URL — refreshing, or sending the link to a
  // teammate, reproduces the exact same view instead of losing it back to
  // the whole-repo default.
  const level = (params.get("level") === "symbol" ? "symbol" : "file") as Level;
  const scope = params.get("scope") ?? "";
  const tests = params.get("tests") === "1";
  const minWeight = Math.min(10, Math.max(1, Number(params.get("min_weight") ?? 1) || 1));
  const groupBy = (params.get("group_by") === "community" ? "community" : "dir") as GroupBy;

  const setFilters = (patch: Partial<{ level: Level; scope: string; tests: boolean; minWeight: number; groupBy: GroupBy }>) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      const merged = { level, scope, tests, minWeight, groupBy, ...patch };
      // Changing level or scope invalidates the other's assumptions less
      // than it seems, but a directory picked for a symbol-scoped view can
      // stop making sense for the file view (or vice versa) — clearest to
      // just keep it; the query simply comes back with everything in scope
      // if the directory doesn't apply, so nothing breaks either way.
      if (merged.level === "file") next.set("level", "file"); else next.set("level", "symbol");
      if (merged.scope) next.set("scope", merged.scope); else next.delete("scope");
      if (merged.tests) next.set("tests", "1"); else next.delete("tests");
      if (merged.minWeight !== 1) next.set("min_weight", String(merged.minWeight)); else next.delete("min_weight");
      if (merged.groupBy === "community") next.set("group_by", "community"); else next.delete("group_by");
      return next;
    }, { replace: true });
  };

  const opts = { level, scope: scope || undefined, tests, minWeight, groupBy };
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["graph", opts],
    queryFn: () => api.graph(opts),
  });

  // A separate, always-unscoped-and-unfiltered fetch purely to enumerate
  // directories for the scope picker — independent of whatever the main
  // query above is currently filtered to, and cheap to keep around since
  // it never refetches once loaded.
  const dirsQ = useQuery({
    queryKey: ["graph", "alldirs"],
    queryFn: () => api.graph({ level: "file" }),
    staleTime: Infinity,
  });
  const dirs = useMemo(() => {
    if (!dirsQ.data) return [];
    return [...new Set(dirsQ.data.nodes.map((n) => dirOf(String(n.id))))].sort();
  }, [dirsQ.data]);

  // "Open in Graph" from Reader/Search lands here with the target node id
  // in router state — a real deep link, not just a cold whole-repo view.
  const deepFocus = (location.state as DeepLinkState | null)?.focus ?? null;

  if (isLoading || !data) return <PageLoading />;
  if (error)
    return <ErrorState message={error instanceof Error ? error.message : undefined} onRetry={() => refetch()} />;

  return (
    <GraphCanvas
      data={data}
      initialFocus={deepFocus}
      // Drilling into a file used to switch this canvas to a generic
      // force-directed graph of its symbols — no real execution order, no
      // code, no walkthrough. It now opens the actual Codemap experience
      // (step columns, confidence edges, "Physical Code" flow, expand,
      // explain-edge) scoped to that file's real functions/methods/classes.
      onDrill={(n) => nav("/codemap", { state: { filePath: String(n.id) } })}
      onOpenFile={(p) => nav("/reader", { state: { path: p } })}
      onOpenSymbol={(id, file) => nav("/reader", { state: { path: file, symbolId: id } })}
      filters={{ level, tests, scope, minWeight, groupBy, dirs, onChange: setFilters }}
    />
  );
}
