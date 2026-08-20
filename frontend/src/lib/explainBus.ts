export interface ExplainRequest {
  /** The full, precisely-anchored question actually sent to the investigate engine. */
  question: string;
  /** What shows up as the user's message in the chat — short and human, defaults
   *  to `question` itself if omitted (e.g. Reader's per-symbol "Explain this"). */
  display?: string;
}

type Listener = (req: ExplainRequest) => void;
let listeners: Listener[] = [];

/** Fires an "explain this" request from anywhere (Reader, peek cards, the
 *  Explorer's Code Inspector) to whichever ask surface is listening — the
 *  floating Ask widget (mounted once by Shell, outlives navigation) or the
 *  Explorer's inline AI Explainer chat (only listens while it's mounted, i.e.
 *  only on the Explorer page). Avoids threading a callback through every
 *  intermediate layer. */
export function emitExplain(req: ExplainRequest | string) {
  const payload = typeof req === "string" ? { question: req } : req;
  listeners.forEach((l) => l(payload));
}

export function onExplain(cb: Listener) {
  listeners.push(cb);
  return () => { listeners = listeners.filter((l) => l !== cb); };
}
