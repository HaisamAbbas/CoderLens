export interface User {
  id: number; github_login: string; email: string | null; avatar_url: string | null;
  is_guest: boolean;
}

export interface Counts { files: number; symbols: number; commits: number; issues: number; edges: number; }
export interface Repo {
  id: number; name: string; url: string; default_branch: string | null;
  head_sha: string | null; ingested_at: string | null; counts: Counts;
}

export interface Status {
  llm: { provider: string | null; model: string; available: boolean };
  embedding: { provider: string; model: string | null; active: boolean };
  confluence?: { configured: boolean };
  jira?: { configured: boolean };
}

export interface ConfluenceIntegration {
  configured: boolean; base_url: string; email: string; space_key: string; has_token: boolean;
}
export interface JiraIntegration {
  configured: boolean; base_url: string; email: string; project_key: string;
  issue_type: string; has_token: boolean;
}
export interface Integrations { confluence: ConfluenceIntegration; jira: JiraIntegration; }

export interface ConfluenceResult {
  key: string; title: string; status: "ok" | "error"; url?: string; error?: string;
}
export interface ConfluenceJob {
  id: string; status: "running" | "done" | "error";
  parent_url: string | null; results: ConfluenceResult[]; error: string;
}

export interface RepoJob {
  id: string; repo_url: string; status: "running" | "done" | "error";
  step: string; message: string; stats: Record<string, number> | null; error: string;
}

export interface ArchRef { ref: string; kind: "tag" | "commit"; sha: string; date: string; subject: string; }
export interface ArchRefs { tags: ArchRef[]; commits: ArchRef[]; head: string; }
export interface ArchMove {
  file: string; from: string; to: string; from_submodule: string; to_submodule: string;
}
export interface ArchSubChange {
  submodule: string; files_added: number; files_removed: number;
  file_count_before: number; file_count_after: number;
}
export interface ArchCounts { code_files: number; submodules: number }
export interface ArchSubmodule { submodule: string; files: string[]; weight: number }
/** The mechanical skeleton at one ref — see analysis/architecture.py's
 *  shape_from_paths, which both the live and historical views go through. */
export interface ArchShape {
  package: string;
  submodules: ArchSubmodule[];
  counts: ArchCounts;
}
/** Facts read off two git trees — never an inference about whether a change was
 *  good, risky or intentional. See analysis/arch_delta.py. */
export interface ArchDelta {
  base: { ref: string; sha: string; date: string; subject: string };
  head: { ref: string; sha: string; date: string; subject: string };
  before: ArchShape; after: ArchShape;
  /** Module -> module dependencies aggregated from the symbol graph. They
   *  describe the INGESTED commit, not necessarily the head being compared —
   *  `edges_live` says whether those are the same. */
  module_edges: { source: string; target: string; weight: number }[];
  edges_live: boolean;
  mermaid: string | null;
  delta: {
    package: { before: string; after: string } | null;
    package_relocated: boolean;
    submodules_added: string[]; submodules_removed: string[];
    submodules_changed: ArchSubChange[];
    files_added: string[]; files_removed: string[]; files_moved: ArchMove[];
    top_level_added: string[]; top_level_removed: string[];
    counts: {
      before: ArchCounts; after: ArchCounts;
      files_added: number; files_removed: number; files_moved: number;
    };
    truncated: boolean; unchanged: boolean;
  };
}

export interface ReadingItem { path: string; degree: number; reason: string; }
export interface Hotspot { path: string; score: number; churn: number; coupling: number; }
export interface Overview {
  name: string; url: string; counts: Counts; most_central: string;
  reading_path: ReadingItem[]; hotspots: Hotspot[];
}

export interface TreeFile { path: string; category: string; language: string | null; loc: number; symbols: number; }
export interface SymbolSpan {
  id: number; name: string; qualified_name: string; kind: string;
  start_line: number; end_line: number; docstring: string | null;
}
export interface FileContent {
  path: string; language: string | null; category: string; loc: number;
  content: string; symbols: SymbolSpan[];
}
export interface SymbolRef {
  id: number; qualified_name: string; kind: string; file_path: string; start_line: number;
  edge: string; confidence: number;
}
export interface SymbolDetail {
  id: number; name: string; qualified_name: string; kind: string; file_path: string;
  start_line: number; end_line: number; signature: string | null; docstring: string | null;
  callers: SymbolRef[]; callees: SymbolRef[];
}

export interface GraphNode {
  id: string | number; label: string; meta: string; group: string; degree: number;
  stats: [string, string | number][]; file?: string;
}
export interface GraphLink { source: string | number; target: string | number; weight: number; }
export interface GraphGroup { key: string; label: string; }
export interface GraphData { nodes: GraphNode[]; links: GraphLink[]; groups: GraphGroup[]; subtitle: string; }

export interface Entrypoint {
  kind: string; label: string; detail: string; path: string; line: number; symbol_id: number | null;
}
export interface FlowNode {
  id: number; qualified_name: string; name: string; kind: string; file: string; line: number; depth: number;
}
export interface FlowEdge { source: number; target: number; confidence: number; }
export interface FlowData { root: number; nodes: FlowNode[]; edges: FlowEdge[]; }

export interface ImpactNode {
  id: number; qualified_name: string; kind: string; file: string; line: number; depth: number;
}
export interface CoupledFile { a: string; b: string; co_changes: number; strength: number; }
export interface ImpactRisk { level: "low" | "medium" | "high"; reason: string; }
export interface Impact {
  symbol: { id: number; qualified_name: string; kind: string; file: string; line: number };
  direct_callers: ImpactNode[];
  transitive_callers: ImpactNode[];
  test_callers: ImpactNode[];
  coupled_files: CoupledFile[];
  is_entrypoint: boolean;
  fan_in: number;
  risk: ImpactRisk;
}

export interface Chip { kind: string; text: string; path: string; }
export interface StructureItem { label: string; chips: Chip[]; }
export interface ArchLayer { submodule: string; responsibility: string; evidence: Chip[]; }
export interface Architecture {
  repo: string; package: string; summary: string; style: string;
  structure: StructureItem[]; layers: ArchLayer[];
  counts: { code_files: number; submodules: number };
}

export interface CodemapNode {
  id: number; qualified_name: string; name: string; kind: string;
  file: string; line: number; step: number; note: string;
  /** "Physical Code" — a mechanically classified real-world role (validator,
   *  database, cache, ...) with an icon; fallback when no `concept` is set.
   *  Optional: nodes added client-side (via symbol expand) compute their own
   *  with `classifyRole` in Codemap.tsx. */
  role?: string; icon?: string; role_label?: string;
  /** The LLM's grounded domain-concept card for this step (e.g. "Chunking" —
   *  "Splits the document into overlapping windows before embedding.") —
   *  takes priority over the mechanical role/icon when present. */
  concept?: string; explainer?: string;
}
export interface CodemapEdge { source: number; target: number; confidence: number; }
export interface Codemap {
  question: string; title: string; narrative: string;
  nodes: CodemapNode[]; edges: CodemapEdge[]; curated: boolean;
}

/** ▶ Play simulation — an illustrative data-flow trace over a codemap
 *  walkthrough. The STRUCTURE is real (each `node_id` is a real symbol on the
 *  map); the DATA is representative, generated by the LLM from the actual code
 *  (or a mechanical, signature-grounded fallback). See analysis/simulation.py. */
export interface SimState { summary: string; fields: Record<string, unknown>; }
export interface SimStep {
  node_id: number;
  /** Plain-English explanation of what this component contributes to the
   *  project and how it fits the overall flow — read from the real code. */
  contribution: string;
  input: SimState;
  transformation: string;
  output: SimState;
  important_variables: Record<string, unknown>;
  branch_taken: string | null;
  confidence: "high" | "representative";
  notes: string[];
}
export interface SimulationTrace {
  scenario: string;
  simulated: boolean;
  /** "llm-simulated" = model-generated values · "mechanical" = grounded from
   *  signatures only (no LLM). Kept distinct so a future sandbox "Run" can set
   *  its own source without a UI rebuild. */
  source: "llm-simulated" | "mechanical";
  truncated: boolean;
  steps: SimStep[];
}

export interface SymbolIndexEntry {
  id: number; name: string; qualified_name: string; kind: string;
  path: string; line: number; end_line: number;
  signature: string; doc: string; callers: number; callers_confident: number;
}
/** Client-side resolution maps built from /api/symbols/index. */
export interface SymbolIndex {
  list: SymbolIndexEntry[];
  byName: Map<string, SymbolIndexEntry[]>;
  byId: Map<number, SymbolIndexEntry>;
}

export interface WikiChip { kind: string; text: string; path: string; line?: number }
export type WikiBlock =
  | { kind: "p"; text: string }
  | { kind: "md"; text: string }
  | { kind: "h2"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "chips"; chips: WikiChip[] }
  | { kind: "table"; columns: string[]; rows: string[][] }
  | { kind: "code"; title: string; path: string; line: number; lang: string; code: string }
  | { kind: "diagram"; title: string; mermaid: string };
export interface WikiSection { key: string; title: string; subtitle: string; blocks: WikiBlock[] }
export interface Wiki { repo: string; counts: { files: number; symbols: number; edges: number }; sections: WikiSection[] }

export interface FolderRow {
  dir: string; files: number; symbols: number;
  fan_in: number; fan_out: number; role: string; heat: number;
}
export interface FolderHeat { folders: FolderRow[]; max_fan_in: number; }

export interface Evidence {
  stream: string; title: string; citation: string; snippet: string;
  body?: string; score: number; symbol_id?: number; path?: string;
}
export interface AskResult { question: string; answer: string; evidence: Evidence[]; }
export interface InvestigateResult extends AskResult { trace: string[]; }
export interface SearchResponse { query: string; hits: Evidence[]; }

/** Events from the /api/investigate/stream SSE endpoint. */
export type StreamEvent =
  | { type: "step"; message: string }
  | { type: "answer_delta"; text: string }
  | { type: "answer"; answer: string }
  | { type: "evidence"; evidence: Evidence[] }
  | { type: "error"; message: string };

/** One prior turn, sent back to the agent so a follow-up question can resolve
 *  references ("it", "that function") against the real preceding answer. */
export interface HistoryTurn { question: string; answer: string; }

export type Stream = "code" | "doc" | "commit" | "issue";

export interface DeadCodeCandidate {
  id: number; qualified_name: string; kind: string; path: string; line: number;
  signature: string; visibility: "private" | "public"; reason: string;
}
export interface DeadCode {
  candidates: DeadCodeCandidate[];
  counts: { total: number; private: number; public: number };
}

export interface CommunityMember {
  id: number; qualified_name: string; kind: string; path: string; line: number;
}
export interface Community {
  label: string; size: number; primary_dir: string; dir_spread: number;
  members: CommunityMember[];
}
export interface Communities { clusters: Community[]; total: number; }

export interface CouplingPair { a: string; b: string; co_changes: number; strength: number; }
export interface Coupling { pairs: CouplingPair[]; windowed: boolean; window_days: number | null; }

export type ConversationKind = "investigate" | "codemap";
export interface ConversationSummary { id: number; question: string; created_at: string; }
export interface ConversationDetail<T = unknown> {
  id: number; kind: ConversationKind; question: string; result: T; created_at: string;
}

export interface WeaknessFinding {
  id: number; file_path: string; start_line: number; end_line: number;
  category: "logic" | "security" | "style"; severity: "high" | "medium" | "low";
  title: string; description: string; suggested_fix: string | null;
  status: "new" | "dismissed" | "ticketed"; jira_url: string | null;
  head_sha: string | null; lang: string; snippet: string;
}
export interface WeaknessList { repo: string; head_sha: string | null; weaknesses: WeaknessFinding[] }
export interface WeaknessScanJob {
  id: string; repo_id: number; status: "running" | "done" | "error";
  files_scanned: number; files_total: number; message: string; notes: string[]; error: string;
}
export interface JiraTicketResult { finding_id: number; status: "ok" | "error"; url?: string; key?: string; error?: string }
export interface JiraTicketJob {
  id: string; repo_id: number; status: "running" | "done" | "error";
  finding_ids: number[]; results: JiraTicketResult[]; error: string;
}
