import type {
  ArchDelta, ArchRefs,
  Architecture, AskResult, Codemap, CodemapEdge, CodemapNode, Communities, ConfluenceJob,
  ConversationDetail, ConversationKind, ConversationSummary, Coupling, DeadCode, Entrypoint,
  FileContent, FlowData, FolderHeat, GraphData, HistoryTurn, Impact, InvestigateResult,
  JiraTicketJob, Overview, Repo, RepoJob, SearchResponse, SimulationTrace, Status, Stream,
  StreamEvent, SymbolDetail, SymbolIndexEntry, TreeFile, WeaknessList, WeaknessScanJob, Wiki,
} from "./types";

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await errText(r));
  return r.json() as Promise<T>;
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json() as Promise<T>;
}

async function del(url: string): Promise<void> {
  const r = await fetch(url, { method: "DELETE" });
  if (!r.ok) throw new Error(await errText(r));
}

/** Prefer the API's structured `detail` over the raw status line. */
async function errText(r: Response): Promise<string> {
  const text = await r.text();
  try {
    const j = JSON.parse(text);
    if (typeof j.detail === "string" && j.detail) return j.detail;
    if (Array.isArray(j.detail) && j.detail.length) {
      const first = j.detail[0];
      if (first?.msg) return `${first.loc?.join(".") ?? ""}: ${first.msg}`;
    }
  } catch { /* not JSON — fall through */ }
  return `${r.status} ${text.slice(0, 200) || r.statusText}`;
}

const q = (s: string) => encodeURIComponent(s);

export const api = {
  status: () => get<Status>("/api/status"),
  repo: () => get<Repo>("/api/repo"),
  repos: () => get<{ repos: Repo[] }>("/api/repos"),
  addRepo: (url: string, token = "") => post<{ job_id: string; repo_url: string; status: string }>("/api/repos", { url, token }),
  repoJob: (jobId: string) => get<RepoJob>(`/api/repos/jobs/${jobId}`),
  refreshRepo: (token = "") => post<{ job_id: string; repo_url: string; status: string }>("/api/repos/refresh", { token }),
  overview: () => get<Overview>("/api/overview"),
  architecture: () => get<Architecture>("/api/architecture"),
  architectureRefs: () => get<ArchRefs>("/api/architecture/refs"),
  architectureDelta: (base: string, head: string) =>
    get<ArchDelta>(`/api/architecture/delta?base=${q(base)}&head=${q(head)}`),
  tree: () => get<{ files: TreeFile[] }>("/api/tree"),
  file: (path: string) => get<FileContent>(`/api/file?path=${q(path)}`),
  symbol: (id: number) => get<SymbolDetail>(`/api/symbol/${id}`),
  symbolIndex: () => get<{ symbols: SymbolIndexEntry[] }>("/api/symbols/index"),
  graph: (level: "file" | "symbol" = "file", scope?: string, neighbors = false) =>
    get<GraphData>(`/api/graph?level=${level}${scope ? `&scope=${q(scope)}` : ""}${neighbors ? "&neighbors=true" : ""}`),
  entrypoints: () => get<{ entrypoints: Entrypoint[] }>("/api/entrypoints"),
  wiki: () => get<Wiki>("/api/wiki"),
  folders: () => get<FolderHeat>("/api/folders"),
  deadCode: () => get<DeadCode>("/api/dead-code"),
  communities: () => get<Communities>("/api/communities"),
  coupling: () => get<Coupling>("/api/coupling"),
  callgraph: (id: number, depth = 3) => get<FlowData>(`/api/callgraph/${id}?depth=${depth}`),
  impact: (id: number) => get<Impact>(`/api/impact/${id}`),
  search: (query: string, streams?: Stream[]) =>
    get<SearchResponse>(`/api/search?q=${q(query)}${streams?.length ? `&streams=${streams.join(",")}` : ""}`),
  codemap: (question: string) => post<Codemap>("/api/codemap", { question }),
  codemapFile: (path: string, maxNodes = 30) => get<Codemap>(`/api/codemap/file?path=${q(path)}&max_nodes=${maxNodes}`),
  explainEdge: (sourceId: number, targetId: number, question = "") =>
    post<{ text: string; error?: string }>("/api/codemap/explain-edge", { source_id: sourceId, target_id: targetId, question }),
  extendCodemap: (question: string, existingIds: number[], maxNew = 10) =>
    post<{ question: string; note: string; nodes: CodemapNode[]; edges: CodemapEdge[] }>(
      "/api/codemap/extend", { question, existing_ids: existingIds, max_new: maxNew }),
  simulate: (nodeIds: number[], question = "") =>
    post<SimulationTrace>("/api/codemap/simulate", { node_ids: nodeIds, question }),
  ask: (question: string) => post<AskResult>("/api/ask", { question }),
  investigate: (question: string) => post<InvestigateResult>("/api/investigate", { question }),
  conversations: (kind: ConversationKind) =>
    get<{ conversations: ConversationSummary[] }>(`/api/conversations?kind=${kind}`),
  conversation: <T,>(id: number) => get<ConversationDetail<T>>(`/api/conversations/${id}`),
  deleteConversation: (id: number) => del(`/api/conversations/${id}`),
  publishConfluence: (sectionKeys: string[]) =>
    post<{ job_id: string; status: string }>("/api/confluence/publish", { section_keys: sectionKeys }),
  confluenceJob: (jobId: string) => get<ConfluenceJob>(`/api/confluence/jobs/${jobId}`),
  scanWeaknesses: (scanAll = false) =>
    post<{ job_id: string; status: string }>("/api/weaknesses/scan", { scan_all: scanAll }),
  weaknessScanJob: (jobId: string) => get<WeaknessScanJob>(`/api/weaknesses/scan/${jobId}`),
  currentWeaknessScan: () => get<{ job: WeaknessScanJob | null }>("/api/weaknesses/scan"),
  weaknesses: () => get<WeaknessList>("/api/weaknesses"),
  dismissWeakness: (id: number) => post<{ id: number; status: string }>(`/api/weaknesses/${id}/dismiss`, {}),
  createJiraTickets: (findingIds: number[]) =>
    post<{ job_id: string; status: string }>("/api/jira/tickets", { finding_ids: findingIds }),
  jiraJob: (jobId: string) => get<JiraTicketJob>(`/api/jira/jobs/${jobId}`),
};

/**
 * Consume the /api/investigate/stream SSE endpoint via fetch + ReadableStream
 * (POST rules out EventSource). Yields each `data:` event as it arrives.
 */
export async function* investigateStream(
  question: string, maxIterations = 2, history: HistoryTurn[] = [], simple = false,
): AsyncGenerator<StreamEvent> {
  const r = await fetch("/api/investigate/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, max_iterations: maxIterations, history, simple }),
  });
  if (!r.ok || !r.body) throw new Error(await errText(r));
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try { yield JSON.parse(line.slice(6)) as StreamEvent; }
          catch { /* skip malformed event */ }
        }
      }
    }
  }
}

export const STREAM_COLOR: Record<string, string> = {
  code: "var(--s-code)", doc: "var(--s-doc)", commit: "var(--s-commit)",
  issue: "var(--s-issue)", graph: "var(--s-graph)",
};
