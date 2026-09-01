import { useEffect, useRef, useState } from "react";
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
  // Collapsed by default — the raw PLAN/RETRIEVE/GRADE trace is "how it got
  // there," not the answer itself, so it's tucked behind a toggle instead of
  // always taking up space, same as a "Thinking…" disclosure in a chat UI.
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({});
  const scrollRef = useRef<HTMLDivElement>(null);
  // Only auto-scroll to the newest message while the reader is already near
  // the bottom — otherwise a streaming answer would yank them away from
  // earlier history they scrolled up to re-read, the same as any real chat app.
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    if (preset) run(preset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const go = async (question = q) => {
    stickToBottomRef.current = true; // a message the reader just sent always pulls the view down
    await run(question);
    qc.invalidateQueries({ queryKey: ["conversations", "investigate"] });
  };

  const openHistory = async (id: number) => {
    const c = await api.conversation<InvestigateResult>(id);
    setFeedback({});
    stickToBottomRef.current = true;
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

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    go();
  };

  return (
    <div className="page inv-page">
      <div className="inv-chat">
        <div className="inv-chat-head">
          <div>
            <div className="eyebrow">Investigate</div>
            <h1 className="h1" style={{ marginTop: 6 }}>Ask why the code works the way it does</h1>
          </div>
          <div className="inv-head-actions">
            {turns.length > 0 && <button className="btn" onClick={reset}>New conversation</button>}
            <HistoryMenu kind="investigate" onSelect={openHistory} />
          </div>
        </div>

        <div className="inv-chat-scroll" ref={scrollRef} onScroll={onScroll}>
          {turns.length === 0 && !running && (
            <div className="inv-empty">
              <p className="lede">
                The agent plans a strategy, gathers evidence across code, git history, docs and issues,
                checks it's enough, then answers with citations you can trace back to the source. Ask
                follow-ups — it remembers what you already talked about.
              </p>
              <div className="suggest">
                {SUGGESTIONS.map((s) => <button key={s} onClick={() => go(s)}>{s}</button>)}
              </div>
            </div>
          )}

          {turns.map((t) => (
            <div className="inv-exchange" key={t.id}>
              <div className="inv-msg user">
                <div className="inv-bubble-user">{t.displayQuestion ?? t.question}</div>
              </div>

              <div className="inv-msg assistant">
                {(t.status === "running" || t.steps.length > 0) && (
                  <div className="think">
                    <button
                      type="button"
                      className="think-toggle"
                      onClick={() => setExpandedSteps((s) => ({ ...s, [t.id]: !s[t.id] }))}
                      aria-expanded={!!expandedSteps[t.id]}
                    >
                      <span className={"think-plus" + (expandedSteps[t.id] ? " open" : "")}>+</span>
                      {t.status === "running"
                        ? <><span className="spin" />Thinking…</>
                        : `Thought through ${t.steps.length} step${t.steps.length === 1 ? "" : "s"}`}
                    </button>
                    {expandedSteps[t.id] && (
                      <div className="steps">
                        {t.steps.map((s, i) => {
                          const sp = s.indexOf(" ");
                          const head = sp < 0 ? s : s.slice(0, sp);
                          const rest = sp < 0 ? "" : s.slice(sp + 1);
                          const isLast = i === t.steps.length - 1 && t.status === "running";
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
            </div>
          ))}
        </div>

        <form className="inv-composer" onSubmit={submit}>
          <textarea
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(); } }}
            placeholder={turns.length > 0 ? "Ask a follow-up… (Enter to send, Shift+Enter for a new line)" : "e.g. Why is the application context pushed on every request?"}
          />
          <button type="submit" className="btn primary" disabled={running || !q.trim()}>
            {running ? "Investigating…" : turns.length > 0 ? "Ask follow-up" : "Investigate"}
          </button>
        </form>
      </div>
    </div>
  );
}
