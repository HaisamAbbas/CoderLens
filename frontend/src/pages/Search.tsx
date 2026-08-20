import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, STREAM_COLOR } from "../lib/api";
import type { Evidence, Stream } from "../lib/types";

const STREAMS: { key: Stream | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "code", label: "Code" },
  { key: "doc", label: "Docs" },
  { key: "commit", label: "Commits" },
  { key: "issue", label: "Issues" },
];

export default function Search() {
  const loc = useLocation();
  const nav = useNavigate();
  const preset = (loc.state as { q?: string } | null)?.q ?? "";
  const [q, setQ] = useState(preset);
  const [debounced, setDebounced] = useState(preset);
  const [streams, setStreams] = useState<Stream[]>([]);

  // Debounce typing; chips re-query instantly.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const res = useQuery({
    queryKey: ["search", debounced, streams.join(",")],
    queryFn: () => api.search(debounced, streams.length ? streams : undefined),
    enabled: debounced.length > 0,
  });

  const toggle = (key: Stream | "all") => {
    if (key === "all") setStreams([]);
    else setStreams((cur) => (cur.includes(key) ? cur.filter((s) => s !== key) : [...cur, key]));
  };

  const open = (h: Evidence) => {
    if (h.stream === "code" && h.path) {
      nav("/reader", { state: { path: h.path, symbolId: h.symbol_id } });
    } else if (h.stream === "doc" && (h.path ?? h.citation)) {
      nav("/reader", { state: { path: h.path ?? h.citation.split(" (")[0] } });
    }
  };

  return (
    <div className="page search-page">
      <div className="eyebrow">Search</div>
      <h1 className="h1" style={{ marginTop: 6 }}>Search every stream at once</h1>
      <p className="lede">
        One query across <b>code symbols</b>, <b>docs</b>, <b>git commits</b> and <b>issues/PRs</b> — ranked by BM25 + semantic similarity.
      </p>

      <div className="ask-box">
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. how is the request context pushed, or: async support, or: url routing"
          aria-label="Search query"
        />
      </div>

      <div className="chip-row" role="group" aria-label="Filter by stream">
        {STREAMS.map(({ key, label }) => {
          const active = key === "all" ? streams.length === 0 : streams.includes(key);
          return (
            <button key={key} className={"chip" + (active ? " on" : "")} onClick={() => toggle(key)}>
              {label}
            </button>
          );
        })}
        <span className="chip-count">{res.data?.hits.length ?? 0} results</span>
      </div>

      {res.isPending && <div className="state"><span className="spin" />Searching…</div>}
      {res.isError && (
        <div className="state err">
          Search failed — {res.error instanceof Error ? res.error.message : String(res.error)}
        </div>
      )}
      {res.data && res.data.hits.length === 0 && (
        <div className="state">No matches for “{debounced}”. Try a different phrasing or drop a stream filter.</div>
      )}

      {res.data && res.data.hits.length > 0 && (
        <div className="hit-list">
          {res.data.hits.map((h, i) => (
            <div key={i} className={"hit" + (h.stream === "code" || h.stream === "doc" ? " clickable" : "")} onClick={() => open(h)}>
              <div className="hit-head">
                <i className="dot" style={{ background: STREAM_COLOR[h.stream] ?? "var(--text-3)" }} />
                <span className="hit-cit">{h.citation}</span>
                <span className="hit-title">{h.title}</span>
                <span className="hit-score tnum">{h.score.toFixed(3)}</span>
              </div>
              {h.snippet && <div className="hit-snip">{h.snippet}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
