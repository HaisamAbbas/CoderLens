import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, STREAM_COLOR } from "../lib/api";
import { useInvestigate } from "../lib/useInvestigate";
import Markdown from "../lib/markdown";
import HistoryMenu from "../components/HistoryMenu";
import type { Evidence, InvestigateResult } from "../lib/types";

const EV_CAP = 6; // sources beyond this are gated behind "Show N more" — a raw dump of 20+ rows reads as noise, not rigor

const SUGGESTIONS = [
  "Why does Flask use an application context?",
  "What would break if I removed Flask.dispatch_request?",
  "How are before_request handlers registered and run?",
  "Why did Flask move away from LocalStack for context handling?",
];

export default function Investigate() {
  const loc = useLocation();
  const nav = useNavigate();
  const qc = useQueryClient();
  const preset = (loc.state as { q?: string } | null)?.q;
  const { q, setQ, turns, running, run, retry, reset, loadStored } = useInvestigate();
  const [feedback, setFeedback] = useState<Record<number, "up" | "down">>({});
  const [copied, setCopied] = useState<number | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [showAll, setShowAll] = useState<Record<number, boolean>>({});

  useEffect(() => {
    if (preset) run(preset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const go = async (question = q) => {
    await run(question);
    qc.invalidateQueries({ queryKey: ["conversations", "investigate"] });
  };

  const openHistory = async (id: number) => {
    const c = await api.conversation<InvestigateResult>(id);
    setFeedback({});
    loadStored(c.question, c.result);
  };

  const cite = (turnId: number, n: number) => {
    const key = `${turnId}-${n}`;
    setFlash(key);
    setTimeout(() => setFlash(null), 1600);
    document.getElementById(`ev-${key}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const openEvidence = (h: Evidence) => {
    if ((h.stream === "code" || h.stream === "graph") && h.path) {
      nav("/reader", { state: { path: h.path, symbolId: h.symbol_id } });
    } else if (h.stream === "doc" && (h.path ?? h.citation)) {
      nav("/reader", { state: { path: h.path ?? h.citation.split(" (")[0] } });
    }
  };

  const copy = async (turnId: number, answer: string) => {
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(turnId);
      setTimeout(() => setCopied(null), 1500);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <div className="page inv-page">
      <div className="inv">
        <div className="inv-head">
          <div>
            <div className="eyebrow">Investigate</div>
            <h1 className="h1" style={{ marginTop: 6 }}>Ask why the code works the way it does</h1>
            <p className="lede">
              The agent plans a strategy, gathers evidence across code, git history, docs and issues, checks
              it's enough, then answers with citations you can trace back to the source. Ask follow-ups —
              the agent remembers what you already talked about.
            </p>
          </div>
          <div className="inv-head-actions">
            {turns.length > 0 && <button className="btn" onClick={reset}>New conversation</button>}
            <HistoryMenu kind="investigate" onSelect={openHistory} />
          </div>
        </div>

        <div className="ask-box">
          <textarea
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) go(); }}
            placeholder={turns.length > 0 ? "Ask a follow-up… e.g. What about its callers?" : "e.g. Why is the application context pushed on every request?"}
          />
          <button className="btn primary" onClick={() => go()} disabled={running}>
            {running ? "Investigating…" : turns.length > 0 ? "Ask follow-up" : "Investigate"}
          </button>
        </div>

        {turns.length === 0 && !running && (
          <div className="suggest">
            {SUGGESTIONS.map((s) => <button key={s} onClick={() => go(s)}>{s}</button>)}
          </div>
        )}

        {turns.map((t, ti) => (
          <div className={"inv-turn" + (ti > 0 ? " inv-followup" : "")} key={t.id}>
            {ti > 0 && <div className="inv-q"><span className="inv-q-badge">Follow-up</span>{t.question}</div>}

            {t.status === "running" && (
              <div className="steps">
                {t.steps.map((s, i) => {
                  const sp = s.indexOf(" ");
                  const head = sp < 0 ? s : s.slice(0, sp);
                  const rest = sp < 0 ? "" : s.slice(sp + 1);
                  const isLast = i === t.steps.length - 1;
                  return (
                    <div className={"step" + (isLast ? " running" : "")} key={i}>
                      <div className="t"><b>{head}</b></div>
                      {rest && <div className="d">{rest}</div>}
                    </div>
                  );
                })}
                {t.steps.length === 0 && <div className="step running"><div className="t"><b>PLAN</b></div><div className="d">Deciding how to investigate…</div></div>}
              </div>
            )}

            {t.status === "error" && (
              <div className="state err">
                {t.error || "The investigation failed."}
                <button className="btn" style={{ marginLeft: 10 }} onClick={() => retry(t.id)}>Retry</button>
              </div>
            )}

            {(t.answer || t.status === "done") && (
              <div className="answer">
                <div className="answer-head">
                  <h5>Answer</h5>
                  <div className="ans-actions">
                    <button className="iconbtn" onClick={() => copy(t.id, t.answer)} title="Copy answer" aria-label="Copy answer">
                      {copied === t.id ? "✓" : "⧉"}
                    </button>
                    <button className="iconbtn" onClick={() => retry(t.id)} title="Regenerate" aria-label="Regenerate" disabled={running}>
                      ↻
                    </button>
                    <span className="fb">
                      <button className={"iconbtn" + (feedback[t.id] === "up" ? " on" : "")} onClick={() => setFeedback((f) => ({ ...f, [t.id]: "up" }))} title="Helpful" aria-label="Helpful">▲</button>
                      <button className={"iconbtn" + (feedback[t.id] === "down" ? " on" : "")} onClick={() => setFeedback((f) => ({ ...f, [t.id]: "down" }))} title="Not helpful" aria-label="Not helpful">▼</button>
                    </span>
                  </div>
                </div>
                {t.answer ? (
                  <Markdown text={t.answer} onCite={(n) => cite(t.id, n)} />
                ) : (
                  <div className="state"><span className="spin" />Synthesizing the answer…</div>
                )}
              </div>
            )}

            {t.evidence.length > 0 && (
              <div>
                <div className="eyebrow" style={{ marginBottom: 8 }}>
                  Evidence · {t.evidence.length} sources — click to open in the Reader
                </div>
                <div className="evidence">
                  {(showAll[t.id] ? t.evidence : t.evidence.slice(0, EV_CAP)).map((e, i) => {
                    const n = i + 1;
                    const key = `${t.id}-${n}`;
                    const clickable = e.stream === "code" || e.stream === "doc" || e.stream === "graph";
                    return (
                      <div
                        key={i}
                        id={`ev-${key}`}
                        className={"ev-row" + (clickable ? " clickable" : "") + (flash === key ? " flash" : "")}
                        onClick={() => clickable && openEvidence(e)}
                        title={clickable ? "Open in Reader" : undefined}
                      >
                        <span className="n">[{n}]</span>
                        <i style={{ background: STREAM_COLOR[e.stream] ?? "var(--text-3)" }} />
                        <span className="cit">{e.citation}</span>
                        {e.title && !e.citation.startsWith(e.title) && <span className="ti">{e.title}</span>}
                      </div>
                    );
                  })}
                  {!showAll[t.id] && t.evidence.length > EV_CAP && (
                    <button className="ev-more" onClick={() => setShowAll((s) => ({ ...s, [t.id]: true }))}>
                      Show {t.evidence.length - EV_CAP} more sources
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
