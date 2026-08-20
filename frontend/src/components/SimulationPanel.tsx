import { useState } from "react";
import type { CodemapNode, SimStep, SimulationTrace } from "../lib/types";

/** The ▶ data-movement view for the ACTIVE step: what data enters, what the
 *  step does to it, and what comes out — with the delta (what actually changed)
 *  called out so you can SEE the transformation, not just read two blobs. Every
 *  value is SIMULATED (representative, generated from the real code), never a
 *  real run — the banner makes that explicit. */
export default function SimulationPanel({
  trace, step, node, nextNode, onOpenReader,
}: {
  trace: SimulationTrace;
  step: SimStep | null;
  node: CodemapNode | null;
  nextNode: CodemapNode | null;
  onOpenReader?: (n: CodemapNode) => void;
}) {
  const mechanical = trace.source === "mechanical";
  const changes = step ? diff(step.input.fields, step.output.fields) : null;
  // Only treat this as an in-place transform (highlight new/changed fields +
  // show a delta) when the input and output actually share a field key — i.e.
  // some data persists and is worth diffing. Otherwise the step reshapes the
  // data entirely and everything would spuriously read as "new".
  const overlap = !!step && Object.keys(step.output.fields ?? {}).some((k) => k in (step.input.fields ?? {}));
  return (
    <div className="sim">
      <div className={"sim-banner" + (mechanical ? " mech" : "")}>
        <span className="sim-dot" aria-hidden>●</span>
        <span>
          <b>SIMULATED</b> —{" "}
          {mechanical
            ? "representative shapes from the code's signatures (no LLM)."
            : "representative data generated from the real code. Not a real run."}
        </span>
      </div>

      {trace.scenario && <div className="sim-scenario">🎬 {trace.scenario}</div>}

      {!node || !step ? (
        <div className="sim-empty">Click a node to see the data flow through it.</div>
      ) : (
        <div className="sim-step">
          <div className="sim-node-head">
            <span className="sim-node-name mono">{node.qualified_name}</span>
            <span className={"sim-conf " + step.confidence}>
              {step.confidence === "high" ? "grounded" : "representative"}
            </span>
            {onOpenReader && <button className="sim-open" onClick={() => onOpenReader(node)}>Open in Reader →</button>}
          </div>

          {step.contribution && (
            <div className="sim-contribution">
              <span className="sim-contribution-label">What it contributes</span>
              <p>{step.contribution}</p>
            </div>
          )}

          <DataCard label="INPUT" tone="in" summary={step.input.summary} fields={step.input.fields} />

          <div className="sim-transform">
            <span className="sim-transform-icon" aria-hidden>⚙️</span>
            <div className="sim-transform-main">
              <div className="sim-transform-text">{step.transformation || "processes the input"}</div>
              {changes && overlap && (changes.changed.length > 0 || changes.added.length > 0) && (
                <div className="sim-delta">
                  {changes.changed.map((c) => (
                    <span className="d-chg" key={"c" + c.k}>
                      <span className="d-k">{c.k}</span> {fmtVal(c.from)} <span className="d-arw">→</span> {fmtVal(c.to)}
                    </span>
                  ))}
                  {changes.added.map((c) => (
                    <span className="d-add" key={"a" + c.k}><span className="d-k">+ {c.k}</span> {fmtVal(c.v)}</span>
                  ))}
                </div>
              )}
            </div>
          </div>

          <DataCard label="OUTPUT" tone="out" summary={step.output.summary} fields={step.output.fields}
                    prev={overlap ? step.input.fields : undefined} />

          {step.branch_taken && <div className="sim-branch">🚦 <b>Branch:</b> {step.branch_taken}</div>}

          {step.important_variables && Object.keys(step.important_variables).length > 0 && (
            <VarsBlock vars={step.important_variables} />
          )}

          {step.notes.length > 0 && (
            <ul className="sim-notes">{step.notes.map((nt, i) => <li key={i}>{nt}</li>)}</ul>
          )}

          {nextNode && (
            <div className="sim-next">
              <span className="sim-next-label">flows into</span>
              <span className="sim-next-arrow" aria-hidden>▾</span>
              <span className="sim-next-name mono">{nextNode.name || nextNode.qualified_name}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- value formatting: make data read like data ----
function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "number") return Number.isFinite(v) ? v.toLocaleString() : String(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return `${v.length} item${v.length === 1 ? "" : "s"}`;
  if (typeof v === "object") { const k = Object.keys(v as object).length; return `{ ${k} field${k === 1 ? "" : "s"} }`; }
  const s = String(v);
  return s.length > 48 ? s.slice(0, 47) + "…" : s;
}
function typeClass(v: unknown): string {
  if (v === null || v === undefined) return "nul";
  if (typeof v === "number") return "num";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "object") return "coll";
  return "str";
}
const eq = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function diff(inF: Record<string, unknown>, outF: Record<string, unknown>) {
  const ip = inF ?? {}, op = outF ?? {};
  const changed: { k: string; from: unknown; to: unknown }[] = [];
  const added: { k: string; v: unknown }[] = [];
  for (const [k, v] of Object.entries(op)) {
    if (k in ip) { if (!eq(ip[k], v)) changed.push({ k, from: ip[k], to: v }); }
    else added.push({ k, v });
  }
  return { changed, added };
}

/** One data state as typed pills; OUTPUT pills highlight what's new/changed
 *  vs. the input (`prev`). Raw JSON is one click away. */
function DataCard({
  label, tone, summary, fields, prev,
}: {
  label: string; tone: "in" | "out";
  summary: string; fields: Record<string, unknown>; prev?: Record<string, unknown>;
}) {
  const [raw, setRaw] = useState(false);
  const entries = Object.entries(fields ?? {});
  return (
    <div className={"sim-data " + tone}>
      <div className="sim-data-head">
        <span className="sim-data-label">{label}</span>
        {entries.length > 0 && (
          <button className="sim-data-toggle" onClick={() => setRaw((r) => !r)}>{raw ? "pills" : "raw JSON"}</button>
        )}
      </div>
      {summary && <div className="sim-data-summary">{summary}</div>}
      {entries.length > 0 && !raw && (
        <div className="sim-pills">
          {entries.map(([k, v]) => {
            const chg = prev ? (!(k in prev) ? " new" : !eq(prev[k], v) ? " chg" : "") : "";
            return (
              <span className={"sim-pill " + typeClass(v) + chg} key={k}>
                <span className="sim-pill-k">{k}</span>
                <span className="sim-pill-v">{fmtVal(v)}</span>
              </span>
            );
          })}
        </div>
      )}
      {raw && <pre className="sim-raw"><code>{JSON.stringify(fields, null, 2)}</code></pre>}
    </div>
  );
}

function VarsBlock({ vars }: { vars: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sim-vars">
      <button className="sim-vars-head" onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} important variables ({Object.keys(vars).length})
      </button>
      {open && (
        <div className="sim-pills">
          {Object.entries(vars).map(([k, v]) => (
            <span className={"sim-pill " + typeClass(v)} key={k}>
              <span className="sim-pill-k">{k}</span><span className="sim-pill-v">{fmtVal(v)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
