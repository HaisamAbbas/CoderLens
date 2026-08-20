import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, STREAM_COLOR } from "../lib/api";
import { useInvestigate } from "../lib/useInvestigate";
import { onExplain } from "../lib/explainBus";
import Markdown from "../lib/markdown";
import type { Chip as ChipT, Evidence } from "../lib/types";

function Chip({ c, onOpen }: { c: ChipT; onOpen: (path: string, line?: number) => void }) {
  const isFile = c.kind === "file";
  return (
    <button className={"chipx " + c.kind} title={c.path} onClick={() => isFile && onOpen(c.path)} disabled={!isFile}>
      <span className="ck">{isFile ? "{ }" : "▸"}</span>{c.text}
    </button>
  );
}

const SUGGESTIONS = ["What does this file do?", "Why is this structured this way?"];
const EV_CAP = 6;

/** The right-hand "AI Explainer" panel — architecture summary up top, a real
 *  ask box below it. Uses the SAME streaming/citations/multi-turn engine as
 *  the floating Ask widget and the full Investigate page (not a separate,
 *  weaker one-shot call), so it actually works and behaves consistently —
 *  just tuned toward plainer wording (`simple: true`), since the reader here
 *  is looking at unfamiliar code side by side and needs jargon explained,
 *  not assumed. The floating Ask widget is hidden on this page (see
 *  AskWidget.tsx) so there's exactly one ask box here, not two overlapping. */
export default function ArchPanel({ onOpen }: { onOpen: (path: string, line?: number) => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["architecture"], queryFn: api.architecture });
  const { q, setQ, turns, running, run, retry } = useInvestigate({ simple: true, persistKey: "explorer" });
  const bodyRef = useRef<HTMLDivElement>(null);
  const [showAll, setShowAll] = useState<Record<number, boolean>>({});

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  const go = (question = q) => run(question);

  // "✦ Explain" on an open file in the Code Inspector (to the left, on this
  // same page) fires here directly — the question drops straight into this
  // chat and starts streaming, no copy-paste. Only listens while this panel
  // is mounted (i.e. only on Explorer), so it never steals a Reader-page
  // "Explain this" click meant for the floating widget.
  useEffect(() => onExplain((req) => { run(req.question, { displayQuestion: req.display }); }), [run]);

  const openEvidence = (h: Evidence) => {
    if ((h.stream === "code" || h.stream === "graph") && h.path) onOpen(h.path, h.symbol_id ?? undefined);
    else if (h.stream === "doc" && (h.path ?? h.citation)) onOpen(h.path ?? h.citation.split(" (")[0]);
  };

  return (
    <div className="ax">
      <div className="ax-head">
        <span className="ax-spark">✦</span> AI Explainer
        <span className="ax-sub">· how this codebase is built</span>
      </div>

      <div className="ax-body" ref={bodyRef}>
        {isLoading && <div className="state"><span className="spin" />Reading the structure…</div>}
        {data && (
          <>
            <p className="ax-summary">{data.summary}</p>

            <div className="ax-struct">
              {data.structure.map((s) => (
                <div className="ax-srow" key={s.label}>
                  <span className="ax-slabel">{s.label}</span>
                  <div className="ax-chips">{s.chips.map((c) => <Chip key={c.path} c={c} onOpen={onOpen} />)}</div>
                </div>
              ))}
            </div>

            <h4 className="ax-h">Architectural layers</h4>
            <div className="ax-style"><span className="ax-badge">style</span>{data.style}</div>

            <div className="ax-layer">
              <div className="ax-ltitle">Core library <code>{data.package}</code></div>
              <table className="ax-table">
                <thead><tr><th>Submodule</th><th>Responsibility</th><th>Evidence</th></tr></thead>
                <tbody>
                  {data.layers.map((l) => (
                    <tr key={l.submodule}>
                      <td className="mono">{l.submodule}</td>
                      <td>{l.responsibility}</td>
                      <td><div className="ax-chips">{l.evidence.map((e) => <Chip key={e.path} c={e} onOpen={onOpen} />)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {turns.length === 0 && !isLoading && (
          <div className="ax-chat-intro">
            <div className="ax-h" style={{ marginTop: 20 }}>Ask about this code</div>
            <p className="ax-note" style={{ padding: 0 }}>
              Answers here use plain wording and explain jargon as it comes up — ask about
              whatever you have open on the left.
            </p>
            <div className="ax-suggest">
              {SUGGESTIONS.map((s) => <button key={s} onClick={() => go(s)}>{s}</button>)}
            </div>
          </div>
        )}

        {turns.map((t, ti) => (
          <div className={"ax-turn" + (ti > 0 ? " followup" : "")} key={t.id}>
            <div className="ax-q">{t.displayQuestion ?? t.question}</div>
            {t.status === "running" && (
              <div className="state" style={{ padding: "10px 0" }}>
                <span className="spin" />
                {t.steps.length ? t.steps[t.steps.length - 1] : "planning…"}
              </div>
            )}
            {t.status === "error" && (
              <div className="ax-note err">
                {t.error || "The investigation failed."}
                <button className="btn" style={{ marginLeft: 10 }} onClick={() => retry(t.id)}>Retry</button>
              </div>
            )}
            {(t.answer || (t.status === "done" && !t.error)) && (
              <div className="ax-answer" style={ti > 0 ? { marginTop: 6, paddingTop: 0, borderTop: "none" } : undefined}>
                {t.answer
                  ? <Markdown text={t.answer} onCite={(n) => { const e = t.evidence[n - 1]; if (e) openEvidence(e); }} />
                  : <div className="state"><span className="spin" />Synthesizing…</div>}
                {t.evidence.length > 0 && (
                  <div className="ax-sources">
                    {(showAll[t.id] ? t.evidence : t.evidence.slice(0, EV_CAP)).map((e, i) => (
                      <button key={i} className="ax-src" onClick={() => openEvidence(e)} title={e.title}>
                        <i style={{ background: STREAM_COLOR[e.stream] ?? "var(--text-3)" }} />
                        <span className="cit">{e.citation}</span>
                      </button>
                    ))}
                    {!showAll[t.id] && t.evidence.length > EV_CAP && (
                      <button className="ax-src ax-src-more" onClick={() => setShowAll((s) => ({ ...s, [t.id]: true }))}>
                        +{t.evidence.length - EV_CAP} more
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <form className="ax-ask" onSubmit={(e) => { e.preventDefault(); if (q.trim() && !running) go(); }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={turns.length > 0 ? "Ask a follow-up…" : "Ask about the codebase…"}
          disabled={running}
        />
        <button className="ax-send" disabled={running || !q.trim()} aria-label="Ask">↑</button>
      </form>
    </div>
  );
}
