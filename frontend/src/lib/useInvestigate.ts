import { useRef, useState } from "react";
import { investigateStream } from "./api";
import type { Evidence } from "./types";

export type TurnStatus = "running" | "done" | "error";

export interface Turn {
  id: number;
  question: string;
  /** What's shown as the user's message, if different from `question` (e.g.
   *  an "Explain this" click shows that short label while the real, precisely
   *  anchored question is what's actually sent to the engine). */
  displayQuestion?: string;
  steps: string[];
  answer: string;
  evidence: Evidence[];
  status: TurnStatus;
  error: string;
}

// Turns for a given `persistKey` survive the owning component unmounting and
// remounting (e.g. navigating away from Explorer and back) — same pattern as
// Codemap's module-level cache. Keyed so unrelated callers never collide.
const persisted: Record<string, Turn[]> = {};

/** Drives a multi-turn investigation conversation. Each `run()` call appends a
 *  new turn (a follow-up) and streams the answer into it token-by-token, while
 *  sending every prior turn as `history` so the agent can resolve references
 *  ("what about its callers?") against the real preceding conversation instead
 *  of planning/answering cold. Shared by the full Investigate page and the
 *  floating Ask widget so both stay in sync with the same behavior.
 *
 *  `simple`: ask the synthesizer for plain, jargon-explained wording instead
 *  of its normal concise/technical register — used by pages (e.g. Explorer)
 *  where the reader is looking at unfamiliar code side by side with the
 *  answer and needs terms explained, not assumed.
 *
 *  `persistKey`: keep this conversation alive across unmounts (e.g. Explorer,
 *  which tears its chat panel down every time you navigate away) instead of
 *  losing it like a fresh page load would. */
export function useInvestigate(opts?: { simple?: boolean; persistKey?: string }) {
  const simple = opts?.simple ?? false;
  const persistKey = opts?.persistKey;
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>(() => (persistKey ? persisted[persistKey] ?? [] : []));
  const runRef = useRef(0);
  const idRef = useRef(turns.reduce((m, t) => Math.max(m, t.id), 0));
  const runningRef = useRef(false);
  const turnsRef = useRef<Turn[]>([]);
  turnsRef.current = turns;

  const setTurnsPersist = (fn: (ts: Turn[]) => Turn[]) => {
    setTurns((ts) => {
      const next = fn(ts);
      if (persistKey) persisted[persistKey] = next;
      return next;
    });
  };

  const patchTurn = (id: number, fn: (t: Turn) => Turn) =>
    setTurnsPersist((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));

  const run = async (question: string, runOpts?: { displayQuestion?: string }) => {
    const trimmed = question.trim();
    if (!trimmed || runningRef.current) return;
    runningRef.current = true;
    setQ("");
    const id = ++idRef.current;
    // Only completed, answered turns are useful conversational context.
    const history = turnsRef.current
      .filter((t) => t.status === "done" && t.answer)
      .map((t) => ({ question: t.question, answer: t.answer }));
    setTurnsPersist((ts) => [...ts, {
      id, question: trimmed, displayQuestion: runOpts?.displayQuestion,
      steps: [], answer: "", evidence: [], status: "running", error: "",
    }]);
    const myRun = ++runRef.current;
    try {
      for await (const ev of investigateStream(trimmed, 2, history, simple)) {
        if (runRef.current !== myRun) return;
        if (ev.type === "step") patchTurn(id, (t) => ({ ...t, steps: [...t.steps, ev.message] }));
        else if (ev.type === "answer_delta") patchTurn(id, (t) => ({ ...t, answer: t.answer + ev.text }));
        else if (ev.type === "answer") patchTurn(id, (t) => ({ ...t, answer: ev.answer }));
        else if (ev.type === "evidence") patchTurn(id, (t) => ({ ...t, evidence: ev.evidence }));
        else if (ev.type === "error") {
          patchTurn(id, (t) => ({ ...t, status: "error", error: ev.message }));
          runningRef.current = false;
          return;
        }
      }
      if (runRef.current === myRun) {
        patchTurn(id, (t) => ({ ...t, status: "done" }));
        runningRef.current = false;
      }
    } catch (e) {
      if (runRef.current !== myRun) return;
      patchTurn(id, (t) => ({ ...t, status: "error", error: e instanceof Error ? e.message : String(e) }));
      runningRef.current = false;
    }
  };

  /** Re-run the given turn (or the last one) as a fresh follow-up — same
   *  question, current conversation as history, discarding its old answer. */
  const retry = (id?: number) => {
    const target = id != null
      ? turnsRef.current.find((t) => t.id === id)
      : turnsRef.current[turnsRef.current.length - 1];
    if (!target || runningRef.current) return;
    setTurnsPersist((ts) => ts.filter((t) => t.id !== target.id));
    run(target.question, { displayQuestion: target.displayQuestion });
  };

  const reset = () => {
    runRef.current++; // orphan any in-flight stream so it can't write into a fresh conversation
    runningRef.current = false;
    setQ(""); setTurnsPersist(() => []);
  };

  /** Hydrate directly from a saved (single-turn) conversation — no streaming, no re-run. */
  const loadStored = (question: string, saved: { answer: string; evidence: Evidence[]; trace?: string[] }) => {
    runRef.current++; // orphan any in-flight stream so it can't clobber this
    runningRef.current = false;
    const id = ++idRef.current;
    setQ("");
    setTurnsPersist(() => [{ id, question, steps: saved.trace ?? [], answer: saved.answer, evidence: saved.evidence, status: "done", error: "" }]);
  };

  const running = turns.length > 0 && turns[turns.length - 1].status === "running";

  return { q, setQ, turns, running, run, retry, reset, loadStored };
}
