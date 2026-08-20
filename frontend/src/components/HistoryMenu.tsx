import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { ChevronIcon, XIcon } from "./icons";
import type { ConversationKind } from "../lib/types";

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/** A "History" dropdown backed by the conversations table — every completed
 *  Investigate/Codemap result is saved automatically server-side, this just
 *  lists and reopens them. Shared between the two full pages. */
export default function HistoryMenu({ kind, onSelect }: {
  kind: ConversationKind; onSelect: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["conversations", kind],
    queryFn: () => api.conversations(kind),
    enabled: open,
    staleTime: 15_000,
  });

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [open]);

  const remove = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    await api.deleteConversation(id);
    qc.invalidateQueries({ queryKey: ["conversations", kind] });
  };

  const items = q.data?.conversations ?? [];

  return (
    <div className="hm" ref={ref}>
      <button className="btn hm-toggle" onClick={() => setOpen((v) => !v)}>
        History{items.length > 0 && !q.isLoading ? ` · ${items.length}` : ""}
        <ChevronIcon className={"hm-chev" + (open ? " open" : "")} />
      </button>
      {open && (
        <div className="hm-panel">
          {q.isLoading && <div className="hm-empty">Loading…</div>}
          {!q.isLoading && items.length === 0 && <div className="hm-empty">No previous questions yet.</div>}
          {items.map((c) => (
            <div key={c.id} className="hm-row" onClick={() => { onSelect(c.id); setOpen(false); }}>
              <div className="hm-q" title={c.question}>{c.question}</div>
              <div className="hm-meta">
                <span>{timeAgo(c.created_at)}</span>
                <button className="hm-del" onClick={(e) => remove(e, c.id)} aria-label="Delete this entry" title="Delete">
                  <XIcon />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
