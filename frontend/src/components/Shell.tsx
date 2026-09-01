import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import { useTheme } from "../lib/useTheme";
import { usePersona } from "../lib/usePersona";
import { LogoMark } from "./Logo";
import AskWidget from "./AskWidget";
import type { RepoJob } from "../lib/types";
import {
  BookIcon, ClusterIcon, CompassIcon, FlameIcon, FlagIcon, GearIcon, GhostIcon, GraphIcon, MapIcon,
  MoonIcon, SearchIcon, SunIcon, WorkspaceIcon,
} from "./icons";

const TITLES: Record<string, string> = {
  "/tour": "Start here", "/explorer": "Explorer", "/overview": "Overview",
  "/graph": "Dependency graph", "/reader": "Reader",
  "/codemap": "Codemap", "/flow": "Call flow", "/investigate": "Investigate",
  "/search": "Search", "/dead-code": "Dead code", "/weaknesses": "Bug Hunter",
  "/communities": "Communities", "/settings": "Settings",
};

export default function Shell() {
  const { isDark, toggle } = useTheme();
  const { user } = useAuth();
  const [persona] = usePersona();
  const loc = useLocation();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: repo, error: repoError, isPending: repoPending } = useQuery({ queryKey: ["repo"], queryFn: api.repo });
  // Only for the switcher below — every OTHER query in this app (wiki,
  // overview, graph, ...) has no repo_id of its own and just trusts the
  // backend's "most recently ingested" pick, so switching repos has to
  // invalidate the whole cache, not just ["repo"]/["repos"].
  const { data: reposList } = useQuery({ queryKey: ["repos"], queryFn: api.repos });
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: api.status, staleTime: 60_000 });
  // Fetched here (not just in Tour.tsx) so the sidebar can list "Start here"'s
  // sub-pages — same cache entry, so navigating into Tour is instant.
  const { data: wiki } = useQuery({ queryKey: ["wiki"], queryFn: api.wiki });
  const onTour = loc.pathname.startsWith("/tour");
  const [q, setQ] = useState("");
  const [syncing, setSyncing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // `/` focuses search from anywhere (unless already typing somewhere).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Per-route document title (browser tab) so you always know where you are.
  useEffect(() => {
    const sectionKey = loc.pathname.startsWith("/tour/") ? loc.pathname.slice("/tour/".length) : null;
    const sectionTitle = sectionKey && wiki?.sections.find((s) => s.key === sectionKey)?.title;
    const title = sectionTitle ?? TITLES[loc.pathname] ?? "Overview";
    document.title = repo ? `${cap(repo.name)} · ${title}` : `CoderLens · ${title}`;
  }, [loc.pathname, repo, wiki]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (q.trim()) nav("/search", { state: { q: q.trim() } });
  };

  const switchRepo = async (repoId: number) => {
    await api.activateRepo(repoId);
    qc.clear();  // every other query trusts "most recent" with no repo_id of its own
  };

  const signOut = async () => {
    await api.logout();
    qc.clear();               // drop every cached query — the next user must not see this one's data
    window.location.href = "/login";  // hard navigation, not SPA nav — guarantees a clean reload
  };

  const refresh = async () => {
    if (syncing) return;
    setSyncing(true);
    try {
      const res = await api.refreshRepo();
      await pollJob(res.job_id);
      qc.invalidateQueries({ queryKey: ["repo"] });
      qc.invalidateQueries({ queryKey: ["repos"] });
    } catch {
      /* surface via the syncing state clearing */
    } finally {
      setSyncing(false);
    }
  };

  const pollJob = (jobId: string) =>
    new Promise<void>((resolve) => {
      const tick = async () => {
        let job: RepoJob;
        try { job = await api.repoJob(jobId); } catch { resolve(); return; }
        if (job.status === "running") setTimeout(tick, 1500);
        else resolve();
      };
      tick();
    });

  // No repository yet — a friendly full-page empty state instead of broken pages.
  if (repoError && !repoPending && !repo) {
    return (
      <div className="empty-state">
        <LogoMark size={40} />
        <h1>No repository indexed yet</h1>
        <p>Add a GitHub repository URL on the landing page and CoderLens will clone it, walk its git history and issues, and build the symbol graph — then every view here comes alive.</p>
        <Link className="btn primary" to="/">Add a repository</Link>
      </div>
    );
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <Link to="/" className="brand" title="Home">
          <LogoMark size={26} />
          <span className="name">CoderLens<small>codebase explorer</small></span>
        </Link>

        <nav className="nav">
          <NavLink to="/tour" className={() => "nav-parent" + (onTour ? " on" : "")}>
            <FlagIcon /> Start here
          </NavLink>
          {onTour && wiki && (
            <div className="nav-sub">
              {wiki.sections.map((s) => (
                <NavLink key={s.key} to={`/tour/${s.key}`}>{s.title}</NavLink>
              ))}
            </div>
          )}
          <NavLink to="/overview"><CompassIcon /> Overview</NavLink>
          <NavLink to="/reader"><BookIcon /> Reader</NavLink>
          <NavLink to="/investigate"><SearchIcon /> Investigate</NavLink>
          {/* "New here" keeps the nav to the essentials — everything below is
              still reachable by switching persona, nothing is removed. */}
          {persona !== "new" && (
            <>
              <NavLink to="/explorer"><WorkspaceIcon /> Explorer</NavLink>
              <NavLink to="/graph"><GraphIcon /> Graph</NavLink>
              <NavLink to="/codemap"><MapIcon /> Codemap</NavLink>
              <NavLink to="/dead-code"><GhostIcon /> Dead code</NavLink>
              <NavLink to="/weaknesses"><FlameIcon /> Bug Hunter</NavLink>
              <NavLink to="/communities"><ClusterIcon /> Communities</NavLink>
              <NavLink to="/arch-delta"><CompassIcon /> Arch delta</NavLink>
            </>
          )}
          <NavLink to="/settings"><GearIcon /> Settings</NavLink>
        </nav>

        <div className="foot">
          <div className="lbl">Investigating</div>
          {reposList && reposList.repos.length > 1 ? (
            <select
              className="repo-switcher"
              value={reposList.repos.find((r) => r.name === repo?.name)?.id ?? ""}
              onChange={(e) => switchRepo(Number(e.target.value))}
              title="Switch between your ingested repositories"
            >
              {reposList.repos.map((r) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
          ) : (
            <div className="repo">{repo ? repo.url.replace(/^https?:\/\//, "") : "…"}</div>
          )}
          {repo && (
            <>
              <div className="stats">
                <span><b className="tnum">{repo.counts.files}</b> files</span>
                <span><b className="tnum">{repo.counts.symbols}</b> symbols</span>
                <span><b className="tnum">{repo.counts.commits}</b> commits</span>
                <span><b className="tnum">{repo.counts.issues}</b> issues</span>
              </div>
              {status && (
                <div className="ai-chip" title={status.llm.available
                  ? `LLM: ${status.llm.provider} · ${status.llm.model}`
                  : "No LLM connected — offline retrieval mode"}>
                  <span className="ai-dot" />
                  {status.llm.available ? `AI · ${status.llm.provider}` : "AI · offline"}
                </div>
              )}
              <div className="meta">
                {repo.ingested_at && <span>synced {timeAgo(repo.ingested_at)}</span>}
                {repo.head_sha && <span className="sha tnum">{repo.head_sha.slice(0, 7)}</span>}
                <button className="refresh" onClick={refresh} disabled={syncing} title="Re-ingest this repository">
                  {syncing ? <span className="spin" /> : "↻"}
                </button>
              </div>
              {syncing && <div className="syncing">Re-ingesting…</div>}
              <a
                className="export-link"
                href="/api/export/snapshot.html"
                title="Download a self-contained HTML snapshot — architecture, tour, entrypoints, dead code, communities, coupling, and a mini graph. Opens with no backend, no Docker, no LLM key."
              >
                ⇩ Export shareable snapshot
              </a>
            </>
          )}
          {user && (
            <div className="user-chip">
              {user.avatar_url && <img src={user.avatar_url} alt="" className="user-avatar" />}
              <span className="user-name" title={user.email ?? undefined}>{user.github_login}</span>
              <button className="user-signout" onClick={signOut} title="Sign out">Sign out</button>
            </div>
          )}
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <div className="crumbs">
            <b>{repo ? cap(repo.name) : "Codebase"}</b> · {TITLES[loc.pathname] ?? "Overview"}
          </div>
          <form className="searchbar" onSubmit={submit}>
            <SearchIcon />
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search code, docs, commits, issues…"
              aria-label="Search"
            />
          </form>
          <button className="iconbtn" onClick={toggle} aria-label="Toggle theme" title="Toggle theme">
            {isDark ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>
        <div className="scroll">
          <Outlet />
        </div>
      </div>

      <AskWidget />
    </div>
  );
}

function cap(s: string) { return s.charAt(0).toUpperCase() + s.slice(1); }

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}
