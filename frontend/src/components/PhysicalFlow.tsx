import type { CodemapNode, SimStep } from "../lib/types";

/** The walkthrough rendered as a simple vertical chain of compact cards — a
 *  numbered step, a title, a one-line caption, and (once simulated) the step's
 *  output. No big illustrations or per-step icons: it stays a lightweight
 *  outline of how execution moves through the system. The connector below each
 *  card fills in as the walkthrough passes it. */
export default function PhysicalFlow({
  nodes, activeId, onSelect, onOpen, simByNode,
}: {
  nodes: CodemapNode[];
  activeId: number | null;
  onSelect: (i: number) => void;
  onOpen: (n: CodemapNode) => void;
  /** When simulating, each card shows its representative OUTPUT summary, so the
   *  chain reads as data literally moving from one step to the next. */
  simByNode?: Map<number, SimStep>;
}) {
  const activeIdx = activeId == null ? -1 : nodes.findIndex((n) => n.id === activeId);

  return (
    <div className="cm-flow">
      {nodes.map((n, i) => {
        const state = activeIdx < 0 ? "pending" : i < activeIdx ? "done" : i === activeIdx ? "active" : "pending";
        const out = simByNode?.get(n.id)?.output.summary;
        return (
          <div className="cm-flow-item" key={n.id}>
            <button
              className={"cm-flow-card" + (state === "active" ? " active" : "")}
              onClick={() => onSelect(i)}
              onDoubleClick={() => onOpen(n)}
              title={n.qualified_name}
            >
              <div className="cm-flow-body">
                <div className="cm-flow-toprow">
                  <span className="cm-flow-num">{i + 1}</span>
                  <span className="cm-flow-title">{n.concept || n.role_label || n.kind}</span>
                </div>
                <span className="cm-flow-explainer">{n.explainer || n.note || n.qualified_name}</span>
                <span className="cm-flow-loc mono">{n.file.split("/").pop()}:{n.line}</span>
                {out && <span className="cm-flow-out"><b>out</b>{out}</span>}
              </div>
            </button>
            {i < nodes.length - 1 && (
              <div className={"cm-flow-connector" + (state === "done" ? " done" : state === "active" ? " active" : "")}>
                <span className="cm-flow-line" />
                <span className="cm-flow-arrow">▾</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
