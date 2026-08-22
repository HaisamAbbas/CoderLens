import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageLoading, ErrorState } from "../components/PageState";
import WikiCode from "../components/WikiCode";
import type {
  JiraTicketJob, JiraTicketResult, WeaknessFinding, WeaknessScanJob,
} from "../lib/types";

const short = (p: string) => p.replace(/^src\//, "");

const SEV_COLOR: Record<string, string> = {
  high: "#e06c75", medium: "#e5c07b", low: "#3fb27f",
};
const CAT_COLOR: Record<string, string> = {
  logic: "#61afef", security: "#e06c75", style: "#56b6c2",
};

type CatFilter = "all" | WeaknessFinding["category"];
type SevFilter = "all" | WeaknessFinding["severity"];

/** Weakness scan → review → Jira tickets. Own page (no host route to piggyback
 *  on): its own persisted list, scan job, and approval batch — the review-
 *  before-anything-external rule the Confluence dialog established. */
export default function Weaknesses() {
  const nav = useNavigate();
  const statusQ = useQuery({ queryKey: ["status"], queryFn: api.status, staleTime: 60_000 });
  const listQ = useQuery({ queryKey: ["weaknesses"], queryFn: api.weaknesses });

  const [scanJobId, setScanJobId] = useState<string | null>(null);
  const [scanJob, setScanJob] = useState<WeaknessScanJob | null>(null);
  const [ticketJobId, setTicketJobId] = useState<string | null>(null);
  const [ticketJob, setTicketJob] = useState<JiraTicketJob | null>(null);
  const [scanAll, setScanAll] = useState(false);
  const [cat, setCat] = useState<CatFilter>("all");
  const [sev, setSev] = useState<SevFilter>("all");
  const [showDismissed, setShowDismissed] = useState(false);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");

  // Same inline recursive-setTimeout polling as ConfluencePublishDialog.
  useEffect(() => {
    if (!scanJobId) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const j = await api.weaknessScanJob(scanJobId);
        if (stopped) return;
        setScanJob(j);
        if (j.status === "running") { timer = window.setTimeout(poll, 1500); return; }
        listQ.refetch();
        setChecked(new Set());
      } catch (e) {
        if (stopped) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = window.setTimeout(poll, 2500);
      }
    };
    timer = window.setTimeout(poll, 400);
    return () => { stopped = true; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanJobId]);

  useEffect(() => {
    if (!ticketJobId) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const j = await api.jiraJob(ticketJobId);
        if (stopped) return;
        setTicketJob(j);
        if (j.status === "running") { timer = window.setTimeout(poll, 1500); return; }
        listQ.refetch();
      } catch (e) {
        if (stopped) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = window.setTimeout(poll, 2500);
      }
    };
    timer = window.setTimeout(poll, 400);
    return () => { stopped = true; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketJobId]);

  if (listQ.isLoading) return <PageLoading tiles={3} />;
  if (!listQ.data) return <ErrorState message={listQ.error instanceof Error ? listQ.error.message : undefined} onRetry={() => listQ.refetch()} />;

  const llmOk = statusQ.data?.llm?.available ?? false;
  const jiraOk = statusQ.data?.jira?.configured ?? false;
  const scanning = scanJob?.status === "running";
  const ticketing = ticketJob?.status === "running";

  const all = listQ.data.weaknesses;
  const news = all.filter((w) => w.status === "new");
  const dismissed = all.filter((w) => w.status === "dismissed");
  const ticketed = all.filter((w) => w.status === "ticketed");

  const matches = (w: WeaknessFinding) =>
    (cat === "all" || w.category === cat) && (sev === "all" || w.severity === sev);

  const shownNew = news.filter(matches);
  const shownTicketed = ticketed.filter(matches);
  const shownDismissed = dismissed.filter(matches);

  const startScan = async () => {
    setError("");
    try {
      const r = await api.scanWeaknesses(scanAll);
      setScanJobId(r.job_id);
      setScanJob(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const createTickets = async () => {
    if (!checked.size) return;
    setError("");
    try {
      const r = await api.createJiraTickets([...checked]);
      setTicketJobId(r.job_id);
      setTicketJob(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const dismiss = async (id: number) => {
    setError("");
    try {
      await api.dismissWeakness(id);
      setChecked((prev) => { const n = new Set(prev); n.delete(id); return n; });
      listQ.refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const toggleCheck = (id: number) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const resultsById = new Map<number, JiraTicketResult>((ticketJob?.results ?? []).map((r) => [r.finding_id, r]));

  return (
    <div className="page">
      <div className="eyebrow">Bug Hunter</div>
      <h1 className="h1" style={{ marginTop: 6 }}>Hunt for potential bugs</h1>
      <p className="lede">
        LLM-flagged logic, security and style issues, prioritized by commit churn.
        Findings are model-assessed — review each one here; nothing leaves this app
        unless you select it and create tickets explicitly. Re-scanning replaces new
        and dismissed findings; anything already ticketed is never touched.
      </p>

      <div style={{ display: "flex", alignItems: "center", gap: 14, margin: "18px 0 6px", flexWrap: "wrap" }}>
        <button className="btn primary" onClick={startScan} disabled={scanning || !llmOk}>
          {scanning ? <span className="spin" /> : "Start bug hunt"}
        </button>
        <label style={{ fontSize: 12.5, color: "var(--text-2)", display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={scanAll} disabled={scanning}
            onChange={(e) => setScanAll(e.target.checked)} />
          Scan all files (slower, no cap)
        </label>
        {!llmOk && <span className="cap">Configure an LLM provider in .env to enable scanning.</span>}
        {scanJob && !scanning && scanJob.notes.length > 0 && (
          <span className="cap">{scanJob.notes.join(" ")}</span>
        )}
      </div>
      {scanning && scanJob && (
        <div className="cap tnum">
          Scanned {scanJob.files_scanned}/{scanJob.files_total || "?"} files…
        </div>
      )}
      {ticketing && (
        <div className="cap tnum">
          Creating Jira tickets… {(ticketJob?.results ?? []).filter((r) => r.status).length}/
          {(ticketJob?.finding_ids ?? []).length}
        </div>
      )}

      <div className="tiles" style={{ marginTop: 14 }}>
        <div className="tile"><div className="n">{news.length}</div><div className="l">New</div></div>
        <div className="tile"><div className="n">{dismissed.length}</div><div className="l">Dismissed</div></div>
        <div className="tile"><div className="n">{ticketed.length}</div><div className="l">Ticketed</div></div>
      </div>

      <div className="chip-row" role="group" aria-label="Filter findings">
        {(["all", "logic", "security", "style"] as const).map((k) => (
          <button key={k} className={"chip" + (cat === k ? " on" : "")} onClick={() => setCat(k)}>{k}</button>
        ))}
        <span style={{ width: 10 }} />
        {(["all", "high", "medium", "low"] as const).map((k) => (
          <button key={k} className={"chip" + (sev === k ? " on" : "")} onClick={() => setSev(k)}>{k}</button>
        ))}
      </div>

      {error && <div className="lp-msg err" style={{ marginBottom: 12 }}>{error}</div>}

      {jiraOk && news.length > 0 && (
        <div style={{ display: "flex", justifyContent: "flex-end", margin: "4px 0 10px" }}>
          <button className="btn primary" onClick={createTickets} disabled={checked.size === 0 || ticketing}>
            Create {checked.size} Jira ticket{checked.size === 1 ? "" : "s"}
          </button>
        </div>
      )}

      <FindingList
        items={shownNew} checked={checked} jiraOk={jiraOk} resultsById={resultsById}
        onToggle={toggleCheck} onDismiss={dismiss}
        onOpen={(w) => nav("/reader", { state: { path: w.file_path, line: w.start_line } })}
        emptyText={all.length ? "No findings match the current filters." :
          "Nothing scanned yet — run a scan to populate this list."}
      />

      {ticketed.filter(matches).length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>Ticketed</h3>
          <p className="cap">Already filed in Jira — survives re-scans.</p>
          <FindingList items={shownTicketed} resultsById={resultsById}
            onOpen={(w) => nav("/reader", { state: { path: w.file_path, line: w.start_line } })}
            emptyText="" />
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h3>Dismissed</h3>
            <p className="cap">Hidden from review — a full re-scan brings them back</p>
          </div>
          <button className="btn" onClick={() => setShowDismissed((v) => !v)}>
            {showDismissed ? "Hide" : `Show ${dismissed.length}`}
          </button>
        </div>
        {showDismissed &&
          <FindingList items={shownDismissed}
            onOpen={(w) => nav("/reader", { state: { path: w.file_path, line: w.start_line } })}
            emptyText="No dismissed findings." muted />}
      </div>
    </div>
  );
}

function FindingList(
  { items, checked, jiraOk, resultsById, onToggle, onDismiss, onOpen, emptyText, muted }:
  {
    items: WeaknessFinding[];
    checked?: Set<number>;
    jiraOk?: boolean;
    resultsById?: Map<number, JiraTicketResult>;
    onToggle?: (id: number) => void;
    onDismiss?: (id: number) => void;
    onOpen: (w: WeaknessFinding) => void;
    emptyText: string;
    muted?: boolean;
  },
) {
  if (!items.length) return emptyText ? <div className="doc" style={{ padding: "14px 0" }}>{emptyText}</div> : null;
  return (
    <div className="dc-list" style={{ marginTop: 12 }}>
      {items.map((w) => {
        const res = resultsById?.get(w.id);
        return (
          <div key={w.id} className={"wk-find" + (muted ? " muted" : "")}>
            <div className="dc-row" onClick={() => onOpen(w)}>
              {w.status === "new" && checked && jiraOk && (
                <input type="checkbox" readOnly checked={checked.has(w.id)}
                  onClick={(e) => e.stopPropagation()} onChange={() => onToggle!(w.id)}
                  aria-label={`Select ${w.title}`} />
              )}
              <span className="dc-dot" style={{ background: CAT_COLOR[w.category] ?? "var(--text-3)" }} />
              <div className="dc-main">
                <div className="dc-name">{w.title}</div>
                <div className="dc-sig" style={{ whiteSpace: "normal" }}>{w.description}</div>
                {res?.status === "error" && <div className="wk-err">{res.error}</div>}
              </div>
              <span className="wk-sev" style={{ borderColor: SEV_COLOR[w.severity], color: SEV_COLOR[w.severity] }}>
                {w.severity}
              </span>
              <div className="dc-loc tnum">{short(w.file_path)}:{w.start_line}</div>
              {onDismiss && w.status === "new" && (
                <button className="btn" onClick={(e) => { e.stopPropagation(); onDismiss(w.id); }}
                  title="Hide this finding (a re-scan brings it back)">
                  Dismiss
                </button>
              )}
              {w.status === "ticketed" && w.jira_url && (
                <a className="btn" href={w.jira_url} target="_blank" rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}>
                  View in Jira
                </a>
              )}
            </div>
            {w.snippet && (
              <div className="wk-snippet">
                <WikiCode title={w.title} path={w.file_path} line={w.start_line}
                  lang={w.lang} code={w.snippet} onOpen={(p, l) => onOpen({ ...w, file_path: p, start_line: l })} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
