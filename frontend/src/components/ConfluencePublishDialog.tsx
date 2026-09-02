import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { safeHref } from "../lib/safeHref";
import type { ConfluenceJob, WikiSection } from "../lib/types";

type Phase = "preview" | "publishing" | "done";

/** Review-then-publish dialog for the wiki → Confluence flow. Nothing is sent
 *  until "Publish" is clicked with explicit section checkboxes — the same
 *  nothing-reaches-an-external-system-silently rule the backend enforces. */
export default function ConfluencePublishDialog(
  { sections, onClose }: { sections: WikiSection[]; onClose: () => void },
) {
  const [phase, setPhase] = useState<Phase>("preview");
  const [checked, setChecked] = useState<Set<string>>(() => new Set(sections.map((s) => s.key)));
  const [publishedKeys, setPublishedKeys] = useState<string[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<ConfluenceJob | null>(null);
  const [error, setError] = useState("");

  // Same inline recursive-setTimeout cadence as Landing's useJob — one more
  // caller doesn't justify extracting a shared hook.
  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const j = await api.confluenceJob(jobId);
        if (stopped) return;
        setJob(j);
        if (j.status === "running") { timer = window.setTimeout(poll, 1500); return; }
        setPhase("done");
        if (j.status === "error") setError(j.error || "Publish failed.");
      } catch (e) {
        if (stopped) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = window.setTimeout(poll, 2500);
      }
    };
    timer = window.setTimeout(poll, 400);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [jobId]);

  // Esc closes — but never while a publish is in flight.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && phase !== "publishing") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, onClose]);

  const toggle = (key: string) => {
    if (phase !== "preview") return;
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const publish = async () => {
    const keys = sections.filter((s) => checked.has(s.key)).map((s) => s.key);
    if (!keys.length) return;
    setPublishedKeys(keys);
    setPhase("publishing");
    setError("");
    try {
      const res = await api.publishConfluence(keys);
      setJobId(res.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("preview");
    }
  };

  const resultsByKey = new Map((job?.results ?? []).map((r) => [r.key, r]));
  const running = phase === "publishing";

  return (
    <div className="dlg-overlay" onClick={running ? undefined : onClose}>
      <div className="dlg-panel" onClick={(e) => e.stopPropagation()}>
        <div className="cfd-head">
          <span className="cfd-title">Publish to Confluence</span>
          {!running && <button className="btn" onClick={onClose}>Close</button>}
        </div>

        {phase === "preview" && (
          <>
            <p className="cfd-note">
              One parent page plus one child page per section will be created in your
              Confluence space (existing pages of the same title are updated). Only the
              sections you check are sent.
            </p>
            <div className="cfd-list">
              {sections.map((s) => (
                <button key={s.key} type="button" className="cfd-row" onClick={() => toggle(s.key)}>
                  <input type="checkbox" readOnly checked={checked.has(s.key)} />
                  <span>{s.title}</span>
                  <span className="cfd-key">{s.key}</span>
                </button>
              ))}
            </div>
            {error && <div className="lp-msg err">{error}</div>}
            <div className="cfd-actions">
              <button className="btn" onClick={onClose}>Cancel</button>
              <button className="btn primary" onClick={publish} disabled={checked.size === 0}>
                Publish {checked.size} section{checked.size === 1 ? "" : "s"}
              </button>
            </div>
          </>
        )}

        {(phase === "publishing" || phase === "done") && (
          <>
            {job?.parent_url && (
              <p className="cfd-note">
                Parent page:{" "}
                {safeHref(job.parent_url)
                  ? <a href={safeHref(job.parent_url)} target="_blank" rel="noreferrer">{job.parent_url}</a>
                  : job.parent_url}
              </p>
            )}
            {publishedKeys.map((key) => {
              const sec = sections.find((s) => s.key === key);
              const r = resultsByKey.get(key);
              return (
                <div key={key} className="cfd-status">
                  {!r && <span className="spin" />}
                  {r?.status === "ok" && (
                    <>
                      <span className="cfd-ok">✓</span>
                      <span>
                        {r.title}
                        {safeHref(r.url) && <> · <a href={safeHref(r.url)} target="_blank" rel="noreferrer">View in Confluence</a></>}
                        {r.error && <div className="cfd-error-text">{r.error}</div>}
                      </span>
                    </>
                  )}
                  {r?.status === "error" && (
                    <>
                      <span className="cfd-err">✕</span>
                      <span>
                        {r.title}
                        <div className="cfd-error-text">{r.error}</div>
                      </span>
                    </>
                  )}
                  {!r && <span>Publishing “{sec?.title ?? key}” …</span>}
                </div>
              );
            })}
            {running && !job && <div className="cfd-note">Starting the publish job …</div>}
            {error && phase === "done" && <div className="lp-msg err">{error}</div>}
          </>
        )}
      </div>
    </div>
  );
}
