import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { STREAM_COLOR } from "../lib/api";
import { useInvestigate } from "../lib/useInvestigate";
import { onExplain } from "../lib/explainBus";
import Markdown from "../lib/markdown";
import { ArrowIcon, ChatIcon, SparkleIcon, XIcon } from "./icons";
import type { Evidence } from "../lib/types";

const SUGGESTIONS = [
  "Why does this work the way it does?",
  "What would break if I removed this?",
];

const EV_CAP = 5; // this panel is narrow — a long raw citation dump reads as noise here even faster than on the full page

/** A persistent "ask anything" launcher, available on every feature page —
 *  same engine as the full Investigate page (streaming, citations, evidence,
 *  multi-turn follow-ups), just reachable without leaving where you are.
 *  Rendered once by Shell, so its conversation survives navigating between
 *  features; only a hard refresh or "New conversation" clears it. "Open full
 *  view" hands the question to the full page for the wide layout. */
export default function AskWidget() {
  const nav = useNavigate();
  const loc = useLocation();
  const [open, setOpen] = useState(false);
  const { q, setQ, turns, running, run, retry, reset } = useInvestigate();
  const [flash, setFlash] = useState<string | null>(null);
  const [showAll, setShowAll] = useState<Record<number, boolean>>({});
  const bodyRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  // "Explain this" buttons elsewhere in the app (e.g. Reader) fire a question
  // in here directly, so explaining a symbol never requires leaving the page.
  useEffect(() => onExplain((req) => { setOpen(true); run(req.question, { displayQuestion: req.display }); }), [run]);

  const go = (question = q) => run(question);

  const cite = (turnId: number, n: number) => {
    const key = `${turnId}-${n}`;
    setFlash(key);
    setTimeout(() => setFlash(null), 1400);
    panelRef.current?.querySelector(`#aw-ev-${key}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const openEvidence = (h: Evidence) => {
    setOpen(false); // stepping away to look at code — collapse back to the bubble
    if ((h.stream === "code" || h.stream === "graph") && h.path) nav("/reader", { state: { path: h.path, symbolId: h.symbol_id } });
    else if (h.stream === "doc" && (h.path ?? h.citation)) nav("/reader", { state: { path: h.path ?? h.citation.split(" (")[0] } });
  };

  const openFull = () => { setOpen(false); nav("/investigate", { state: { q: turns.length ? turns[turns.length - 1].question : q } }); };

  const hasContent = turns.length > 0 || q;

  // Explorer has its own always-visible, equally-capable ask box (the "AI
  // Explainer" panel) sitting flush against the same corner of the screen —
  // showing the floating launcher on top of it too just overlaps the two and
  // confuses which one to use, so it sits this one page out.
  if (loc.pathname.startsWith("/explorer")) return null;

  return (
    <>
      <button
        className={"aw-fab" + (open ? " open" : "")}
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close ask assistant" : "Ask a question about this codebase"}
        title="Ask about this codebase"
      >
        {open ? <XIcon /> : <ChatIcon />}
      </button>

      {open && (
        <div className="aw-panel" ref={panelRef}>
          <div className="aw-head">
            <SparkleIcon />
            <span>Ask</span>
            {hasContent && (
              <>
                <button className="aw-full" onClick={openFull}>Open full view <ArrowIcon /></button>
                {turns.length > 0 && <button className="aw-new" onClick={reset} title="New conversation">New</button>}
              </>
            )}
          </div>

          <div className="aw-body" ref={bodyRef}>
            {!hasContent && (
              <>
                <p className="aw-intro">Ask why the code works the way it does — from anywhere in the app.</p>
                <div className="aw-suggest">
                  {SUGGESTIONS.map((s) => <button key={s} onClick={() => go(s)}>{s}</button>)}
                </div>
              </>
            )}

            {turns.map((t, ti) => (
              <div className={"aw-turn" + (ti > 0 ? " aw-followup" : "")} key={t.id}>
                <div className="aw-q">{t.displayQuestion ?? t.question}</div>

                {t.status === "running" && (
                  <div className="aw-steps">
                    {t.steps.map((s, i) => {
                      const sp = s.indexOf(" ");
                      const head = sp < 0 ? s : s.slice(0, sp);
                      const rest = sp < 0 ? "" : s.slice(sp + 1);
                      return (
                        <div className={"aw-step" + (i === t.steps.length - 1 ? " on" : "")} key={i}>
                          <b>{head}</b>{rest && <span> {rest}</span>}
                        </div>
                      );
                    })}
                    {t.steps.length === 0 && <div className="aw-step on"><span className="spin" /> planning…</div>}
                  </div>
                )}

                {t.status === "error" && (
                  <div className="aw-error">
                    {t.error || "The investigation failed."}
                    <button className="btn" onClick={() => retry(t.id)}>Retry</button>
                  </div>
                )}

                {(t.answer || (t.status === "done" && !t.error)) && (
                  <div className="aw-answer">
                    {t.answer
                      ? <Markdown text={t.answer} onCite={(n) => cite(t.id, n)} />
                      : <div className="state"><span className="spin" />Synthesizing…</div>}
                  </div>
                )}

                {t.evidence.length > 0 && (
                  <div className="aw-evidence">
                    {(showAll[t.id] ? t.evidence : t.evidence.slice(0, EV_CAP)).map((e, i) => {
                      const n = i + 1;
                      const key = `${t.id}-${n}`;
                      const clickable = e.stream === "code" || e.stream === "doc" || e.stream === "graph";
                      return (
                        <div
                          key={i}
                          id={`aw-ev-${key}`}
                          className={"aw-ev" + (clickable ? " click" : "") + (flash === key ? " flash" : "")}
                          onClick={() => clickable && openEvidence(e)}
                        >
                          <span className="n">[{n}]</span>
                          <i style={{ background: STREAM_COLOR[e.stream] ?? "var(--text-3)" }} />
                          <span className="cit">{e.citation}</span>
                        </div>
                      );
                    })}
                    {!showAll[t.id] && t.evidence.length > EV_CAP && (
                      <button className="aw-ev-more" onClick={() => setShowAll((s) => ({ ...s, [t.id]: true }))}>
                        Show {t.evidence.length - EV_CAP} more
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="aw-ask">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !running) go(); }}
              placeholder={turns.length > 0 ? "Ask a follow-up…" : "Ask a question…"}
              disabled={running}
            />
            <button className="aw-send" onClick={() => go()} disabled={running || !q.trim()} aria-label="Ask">
              <ArrowIcon />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
