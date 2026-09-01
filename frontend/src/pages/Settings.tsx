import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageLoading, ErrorState } from "../components/PageState";
import type { ConfluenceIntegration, JiraIntegration } from "../lib/types";

/** Each user's own Confluence/Jira connection (Phase 4 of the multi-user
 *  migration) — these used to be global env vars shared by everyone; now
 *  every user connects their own space/project here. The API token field
 *  is always write-only: a saved token is never sent back, and leaving it
 *  blank on an update keeps whatever was saved before. */
export default function Settings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["integrations"], queryFn: api.integrations });

  useEffect(() => { document.title = "Settings — CoderLens"; }, []);

  if (q.isLoading) return <PageLoading tiles={2} />;
  if (!q.data) return <ErrorState message={q.error instanceof Error ? q.error.message : undefined} onRetry={() => q.refetch()} />;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["integrations"] });
    qc.invalidateQueries({ queryKey: ["status"] }); // other pages gate buttons on this
  };

  return (
    <div className="page">
      <div className="eyebrow">Settings</div>
      <h1 className="h1" style={{ marginTop: 6 }}>Integrations</h1>
      <p className="lede">
        Connect your own Confluence space and Jira project. Nothing here is shared with
        other users, and API tokens are encrypted at rest — once saved, they're never
        shown again.
      </p>

      <div className="settings-grid">
        <ConfluenceCard data={q.data.confluence} onSaved={refresh} />
        <JiraCard data={q.data.jira} onSaved={refresh} />
      </div>
    </div>
  );
}

function ConfluenceCard(
  { data, onSaved }: { data: ConfluenceIntegration; onSaved: () => void },
) {
  const [baseUrl, setBaseUrl] = useState(data.base_url);
  const [email, setEmail] = useState(data.email);
  const [token, setToken] = useState("");
  const [spaceKey, setSpaceKey] = useState(data.space_key);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.putConfluenceIntegration({
        base_url: baseUrl, email, api_token: token, space_key: spaceKey,
      });
      setToken("");
      setMsg({ kind: "ok", text: "Saved." });
      onSaved();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.deleteConfluenceIntegration();
      setBaseUrl(""); setEmail(""); setToken(""); setSpaceKey("");
      onSaved();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card">
      <div className="settings-card-head">
        <span className="settings-card-title">Confluence</span>
        <span className={"settings-badge" + (data.configured ? " on" : "")}>
          {data.configured ? "Connected" : "Not connected"}
        </span>
      </div>
      <p className="settings-note">
        Publishes the "Start here" wiki to your space, one page per section.
      </p>

      <label className="settings-field">
        <span>Base URL</span>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://your-domain.atlassian.net/wiki" />
      </label>
      <label className="settings-field">
        <span>Email</span>
        <input value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com" />
      </label>
      <label className="settings-field">
        <span>API token</span>
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
              placeholder={data.has_token ? "•••••••• (leave blank to keep)" : "Paste a new API token"} />
      </label>
      <label className="settings-field">
        <span>Space key</span>
        <input value={spaceKey} onChange={(e) => setSpaceKey(e.target.value)} placeholder="DOC" />
      </label>

      {msg && <div className={"settings-msg " + msg.kind}>{msg.text}</div>}

      <div className="settings-actions">
        <button className="btn primary" onClick={save} disabled={busy || !baseUrl || !email || !spaceKey}>
          Save
        </button>
        {data.configured && (
          <button className="btn" onClick={disconnect} disabled={busy}>Disconnect</button>
        )}
        <a className="settings-help" href="https://id.atlassian.com/manage-profile/security/api-tokens"
          target="_blank" rel="noreferrer">
          Create an API token ↗
        </a>
      </div>
    </div>
  );
}

function JiraCard(
  { data, onSaved }: { data: JiraIntegration; onSaved: () => void },
) {
  const [baseUrl, setBaseUrl] = useState(data.base_url);
  const [email, setEmail] = useState(data.email);
  const [token, setToken] = useState("");
  const [projectKey, setProjectKey] = useState(data.project_key);
  const [issueType, setIssueType] = useState(data.issue_type || "Task");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.putJiraIntegration({
        base_url: baseUrl, email, api_token: token, project_key: projectKey, issue_type: issueType,
      });
      setToken("");
      setMsg({ kind: "ok", text: "Saved." });
      onSaved();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.deleteJiraIntegration();
      setBaseUrl(""); setEmail(""); setToken(""); setProjectKey(""); setIssueType("Task");
      onSaved();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card">
      <div className="settings-card-head">
        <span className="settings-card-title">Jira</span>
        <span className={"settings-badge" + (data.configured ? " on" : "")}>
          {data.configured ? "Connected" : "Not connected"}
        </span>
      </div>
      <p className="settings-note">
        Creates issues from approved Bug Hunter findings in your project.
      </p>

      <label className="settings-field">
        <span>Base URL</span>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://your-domain.atlassian.net" />
      </label>
      <label className="settings-field">
        <span>Email</span>
        <input value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com" />
      </label>
      <label className="settings-field">
        <span>API token</span>
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
              placeholder={data.has_token ? "•••••••• (leave blank to keep)" : "Paste a new API token"} />
      </label>
      <label className="settings-field">
        <span>Project key</span>
        <input value={projectKey} onChange={(e) => setProjectKey(e.target.value)} placeholder="PROJ" />
      </label>
      <label className="settings-field">
        <span>Issue type</span>
        <input value={issueType} onChange={(e) => setIssueType(e.target.value)} placeholder="Task" />
      </label>

      {msg && <div className={"settings-msg " + msg.kind}>{msg.text}</div>}

      <div className="settings-actions">
        <button className="btn primary" onClick={save} disabled={busy || !baseUrl || !email || !projectKey}>
          Save
        </button>
        {data.configured && (
          <button className="btn" onClick={disconnect} disabled={busy}>Disconnect</button>
        )}
        <a className="settings-help" href="https://id.atlassian.com/manage-profile/security/api-tokens"
          target="_blank" rel="noreferrer">
          Create an API token ↗
        </a>
      </div>
    </div>
  );
}
