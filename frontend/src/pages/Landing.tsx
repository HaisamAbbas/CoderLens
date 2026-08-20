import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "../lib/useTheme";
import { api } from "../lib/api";
import { LogoMark } from "../components/Logo";
import type { RepoJob } from "../lib/types";
import { CompassIcon, FlagIcon, GraphIcon, MapIcon, MoonIcon, SearchIcon, SunIcon, WorkspaceIcon } from "../components/icons";

const JOB_STEP_LABEL: Record<string, string> = {
  clone: "Cloning repository and walking code, docs, git history",
  symbols: "Extracting code symbols (tree-sitter)",
  "code-index": "Indexing symbols into OpenSearch",
  "evidence-index": "Indexing docs, commits and issues",
  graph: "Building the dependency graph",
};

function useJob(jobId: string | null, onDone: () => void) {
  const [job, setJob] = useState<RepoJob | null>(null);
  const [error, setError] = useState("");
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    const poll = async () => {
      try {
        const j = await api.repoJob(jobId);
        if (stopped) return;
        setJob(j);
        setError("");
        if (j.status === "done") { doneRef.current(); return; }
        if (j.status === "error") return;
        timer = window.setTimeout(poll, 1500);
      } catch (e) {
        if (stopped) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = window.setTimeout(poll, 2500);
      }
    };
    let timer = window.setTimeout(poll, 400);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [jobId]);

  return { job, error, setError };
}

export default function Landing() {
  const nav = useNavigate();
  const { isDark, toggle } = useTheme();

  useEffect(() => {
    document.title = "CoderLens — understand any codebase";
  }, []);
  const [url, setUrl] = useState("");
  const [starting, setStarting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const reposQ = useQuery({ queryKey: ["repos"], queryFn: api.repos });

  const { job, error: jobError, setError: setJobError } = useJob(jobId, () => nav("/overview"));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const u = url.trim();
    if (!u || starting) return;
    setStarting(true);
    setSubmitError("");
    setJobError("");
    try {
      const res = await api.addRepo(u);
      setJobId(res.job_id);
      reposQ.refetch();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
      setStarting(false);
    }
  };

  const busy = starting || (job != null && job.status === "running");
  const failed = job?.status === "error";

  return (
    <div className="lp">
      <header className="lp-top">
        <div className="lp-brand"><LogoMark size={26} /><span>CoderLens</span></div>
        <div className="lp-actions">
          <button className="lp-round" onClick={toggle} aria-label="Toggle theme">
            {isDark ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>
      </header>

      <main className="lp-hero">
        <h1 className="lp-title">CoderLens</h1>
        <p className="lp-tag">
          Understand unfamiliar code, fast.<br />
          Evidence-backed maps of how any repository actually works.
        </p>

        <form className="lp-search" onSubmit={submit}>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Paste a GitHub repository URL — e.g. https://github.com/pallets/flask"
            aria-label="Repository URL"
            disabled={busy}
          />
          <button type="submit" aria-label="Ingest repository" disabled={busy}>
            {busy ? <span className="spin" /> : <SearchIcon />}
          </button>
        </form>

        {submitError && <div className="lp-msg err">{submitError}</div>}

        {busy && job && (
          <div className="lp-job">
            <div className="lp-job-step">
              <span className="spin" />
              {JOB_STEP_LABEL[job.step] ?? job.message ?? "Working…"}
            </div>
            <div className="lp-job-sub">
              {job.step === "clone" && job.stats.files != null && `files: ${job.stats.files}`}
              {job.status === "running" && "This can take a few minutes for a large repository."}
            </div>
          </div>
        )}
        {failed && (
          <div className="lp-msg err">
            Ingest failed: {job?.error || "unknown error"}
            <button className="lp-retry" onClick={() => { setJobId(null); setUrl(job?.repo_url ?? ""); }}>Try again</button>
          </div>
        )}
        {jobError && <div className="lp-msg err">Lost connection while checking progress — {jobError}</div>}

        {reposQ.data && reposQ.data.repos.length > 0 && (
          <div className="lp-suggest">
            <span className="lp-suggest-label">Indexed:</span>
            {reposQ.data.repos.slice(0, 5).map((r) => (
              <button key={r.id} onClick={() => nav("/overview")} title={r.url}>
                {r.name}
                {r.ingested_at ? ` · ${timeAgo(r.ingested_at)}` : ""}
              </button>
            ))}
          </div>
        )}

        <nav className="lp-quick" aria-label="Jump to a feature">
          <Link to="/tour"><FlagIcon /> Start here</Link>
          <Link to="/explorer"><WorkspaceIcon /> Explore</Link>
          <Link to="/overview"><CompassIcon /> Overview</Link>
          <Link to="/graph"><GraphIcon /> Graph</Link>
          <Link to="/codemap"><MapIcon /> Codemap</Link>
          <Link to="/investigate"><SearchIcon /> Investigate</Link>
        </nav>
      </main>
    </div>
  );
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
