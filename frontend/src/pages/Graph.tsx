import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import GraphCanvas from "../components/GraphCanvas";
import { ErrorState, PageLoading } from "../components/PageState";

export default function Graph() {
  const nav = useNavigate();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["graph", "file"],
    queryFn: () => api.graph("file"),
  });

  if (isLoading || !data) return <PageLoading />;
  if (error)
    return <ErrorState message={error instanceof Error ? error.message : undefined} onRetry={() => refetch()} />;

  return (
    <GraphCanvas
      data={data}
      // Drilling into a file used to switch this canvas to a generic
      // force-directed graph of its symbols — no real execution order, no
      // code, no walkthrough. It now opens the actual Codemap experience
      // (step columns, confidence edges, "Physical Code" flow, expand,
      // explain-edge) scoped to that file's real functions/methods/classes.
      onDrill={(n) => nav("/codemap", { state: { filePath: String(n.id) } })}
      onOpenFile={(p) => nav("/reader", { state: { path: p } })}
      onOpenSymbol={(id, file) => nav("/reader", { state: { path: file, symbolId: id } })}
    />
  );
}
