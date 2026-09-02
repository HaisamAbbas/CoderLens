import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import GraphCanvas from "../components/GraphCanvas";
import ArchPanel from "../components/ArchPanel";
import CodeInspector, { type OpenFile } from "../components/CodeInspector";
import { ErrorState } from "../components/PageState";

const MAX_OPEN = 6;

export default function Explorer() {
  const nav = useNavigate();
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([]);
  const [zoomed, setZoomed] = useState<OpenFile | null>(null);

  const { data: graph, isLoading, error, refetch } = useQuery({
    queryKey: ["graph", "file"],
    queryFn: () => api.graph({ level: "file" }),
  });

  const open = (path: string, line?: number) => {
    const key = `${path}:${line ?? ""}`;
    setOpenFiles((prev) => (prev.some((f) => f.key === key) ? prev : [{ key, path, line }, ...prev].slice(0, MAX_OPEN)));
  };
  const close = (key: string) => {
    setOpenFiles((prev) => prev.filter((f) => f.key !== key));
    setZoomed((z) => (z?.key === key ? null : z));
  };

  return (
    <div className="xp">
      <CodeInspector
        files={openFiles} onClose={close}
        onReader={(path, line) => nav("/reader", { state: { path, ...(line ? { line } : {}) } })}
        zoomed={zoomed} onZoom={setZoomed}
      />

      <div className="xp-center">
        {isLoading
          ? <div className="sk" style={{ height: "100%", minHeight: 420, borderRadius: 0 }} aria-busy="true" />
          : error
            ? <ErrorState message={error instanceof Error ? error.message : undefined} onRetry={() => refetch()} />
            : graph && <GraphCanvas
              data={graph}
              onNode={(n) => { if (n.file) open(n.file); else if (typeof n.id === "string") open(n.id); }}
              // Zooming into a file's symbols used to switch this same canvas
              // to a generic force-directed graph of them — no execution
              // order, no code, no walkthrough. It now opens the real
              // Call-flow experience (step columns, confidence edges,
              // simulate, expand, explain-edge, code + input/output side by
              // side) for every symbol in that file at once, via Flow's file
              // mode — the same mechanical data Codemap's file view uses,
              // just rendered on /flow instead of leaving this page.
              onDrill={(n) => nav("/flow", { state: { filePath: String(n.id), label: String(n.id).split("/").pop() } })}
              onOpenFile={(p) => open(p)}
              onOpenSymbol={(_id, file) => open(file)}
            />}
      </div>

      <ArchPanel onOpen={open} />
    </div>
  );
}
