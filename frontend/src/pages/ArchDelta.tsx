import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageLoading, ErrorState } from "../components/PageState";
import ArchDiagram from "../components/ArchDiagram";
import type { DiagramEdge, DiagramNode, Tone } from "../components/ArchDiagram";
import type { ArchDelta, ArchRef } from "../lib/types";

/** Turn the receipt into a diagram spec: one card per submodule, toned by what
 *  happened to it, and an arrow for each pair of modules files moved between —
 *  the arrows are the part a flowchart of the folder tree can't show. */
function toDiagram(d: ArchDelta): { nodes: DiagramNode[]; edges: DiagramEdge[] } {
  const changed = new Map(d.delta.submodules_changed.map((c) => [c.submodule, c]));
  const added = new Set(d.delta.submodules_added);
  const removed = new Set(d.delta.submodules_removed);

  // Every submodule, not only the ones that changed — a diagram showing two
  // cards says what moved but not what it moved within. Unchanged modules are
  // drawn muted, so the changes still read at a glance against the whole.
  const names = new Set<string>([
    ...d.after.submodules.map((s) => s.submodule),
    ...removed,
  ]);
  for (const m of d.delta.files_moved) {
    names.add(m.from_submodule || "core");
    names.add(m.to_submodule || "core");
  }
  const filesAfter = new Map(d.after.submodules.map((s) => [s.submodule, s.files.length]));
  const filesBy = new Map(d.after.submodules.map((s) => [s.submodule, s.files]));

  // The files themselves, dot-separated — a card naming client.py and server.py
  // tells you what a module *is*; a card saying "10 files" does not. Files come
  // ordered by weight, so the first few are the ones worth naming.
  //
  // Package markers are skipped and names deduped first. A module spanning
  // several packages has an __init__.py in each, and by weight they can be the
  // top entries — so a card read "__init__.py · __init__.py · cypher_…", which
  // names nothing and wastes the one line that could. They come back only if a
  // module has nothing else in it.
  const detailOf = new Map(d.after.submodules.map((s) => {
    const all = s.files.map((f) => f.split("/").pop() ?? f);
    const marker = /^(__init__\.py|__main__\.py|index\.(t|j)sx?|mod\.rs)$/i;
    const named = [...new Set(all.filter((n) => !marker.test(n)))];
    const names = named.length > 0 ? named : [...new Set(all)];
    const shown = names.slice(0, 3).join(" · ");
    const rest = all.length - Math.min(3, names.length);
    return [s.submodule, rest > 0 ? `${shown} · +${rest}` : shown];
  }));

  const nodes: DiagramNode[] = [...names].sort().map((name) => {
    const c = changed.get(name);
    const n = filesAfter.get(name);
    let tone: Tone = "kept";
    let subtitle = n != null ? `${n} file${n === 1 ? "" : "s"}` : "unchanged";
    if (added.has(name)) {
      tone = "added";
      subtitle = n != null ? `new · ${n} file${n === 1 ? "" : "s"}` : "new submodule";
    } else if (removed.has(name)) {
      tone = "removed";
      subtitle = "no longer present";
    } else if (c) {
      tone = "changed";
      subtitle = `+${c.files_added} / −${c.files_removed} · ${c.file_count_after} files`;
    }
    return {
      id: name, title: name, subtitle, detail: detailOf.get(name) ?? "", tone,
      weight: n ?? 0, files: filesBy.get(name) ?? [],
    };
  });

  // Collapse moves into one arrow per module pair — twelve files moving the
  // same direction is one fact, not twelve overlapping arrows.
  const pairs = new Map<string, { from: string; to: string; n: number }>();
  for (const m of d.delta.files_moved) {
    const from = m.from_submodule || "core";
    const to = m.to_submodule || "core";
    if (from === to) continue;
    const key = `${from}/${to}`;
    const hit = pairs.get(key);
    if (hit) hit.n += 1;
    else pairs.set(key, { from, to, n: 1 });
  }
  const moves: DiagramEdge[] = [...pairs.values()].map((p) => ({
    from: p.from, to: p.to, kind: "move",
    label: p.n === 1 ? "1 file moved" : `${p.n} files moved`,
  }));

  // Dependency edges give the diagram its direction — without them the modules
  // are a set, not an architecture. They come from the symbol graph, so they
  // describe the ingested commit; the page says so when that isn't the head
  // being compared.
  const deps: DiagramEdge[] = (d.module_edges ?? []).map((e) => ({
    from: e.source, to: e.target, weight: e.weight, kind: "dep",
    label: e.weight >= 20 ? `${e.weight}` : "",
  }));

  return { nodes, edges: [...deps, ...moves] };
}

const day = (iso: string) => iso.slice(0, 10);

/** A ref as it should read in a heading. Tags come back as their own name, but
 *  a commit's ref is the full 40-char sha — printing that raw turned a diagram
 *  title into "v1.0.1 vs d5db2492890d63ab901027b6cb93d379cd6130ec". */
const refLabel = (r: { ref: string; sha: string }) =>
  (r.ref.length >= 20 && /^[0-9a-f]+$/i.test(r.ref) ? r.sha : r.ref);

/** One ref picker. Tags are listed before commits because "v1 vs v2" is the
 *  question people actually have; a raw SHA pair almost never is. */
function RefSelect({
  label, value, onChange, refs, disabled,
}: {
  label: string; value: string; onChange: (v: string) => void;
  refs: { tags: ArchRef[]; commits: ArchRef[] }; disabled: boolean;
}) {
  return (
    <label className="ad-pick">
      <span className="cap">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        {refs.tags.length > 0 && (
          <optgroup label="Tags">
            {refs.tags.map((r) => (
              <option key={`t-${r.ref}`} value={r.ref}>{r.ref} · {day(r.date)}</option>
            ))}
          </optgroup>
        )}
        <optgroup label="Recent commits">
          {refs.commits.map((r) => (
            <option key={`c-${r.ref}`} value={r.ref}>{r.sha} · {day(r.date)} · {r.subject}</option>
          ))}
        </optgroup>
      </select>
    </label>
  );
}

function FactList({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  if (items.length === 0) return null;
  return (
    <div className="card ad-facts">
      <h3>{title} <span className={"chip solid ad-" + tone}>{items.length}</span></h3>
      <ul>{items.map((p) => <li key={p} className="mono">{p}</li>)}</ul>
    </div>
  );
}

export default function ArchDeltaPage() {
  const nav = useNavigate();
  const refsQ = useQuery({ queryKey: ["arch-refs"], queryFn: api.architectureRefs });
  const [base, setBase] = useState("");
  const [head, setHead] = useState("");

  // Default to the widest comparison the repo offers — oldest tag to newest, or
  // the oldest listed commit to HEAD. The interesting drift is rarely in the
  // last two commits, and an empty page teaches nothing about what this does.
  useEffect(() => {
    const d = refsQ.data;
    if (!d || base || head) return;
    if (d.tags.length >= 2) {
      setBase(d.tags[d.tags.length - 1].ref);
      setHead(d.tags[0].ref);
    } else if (d.commits.length >= 2) {
      setBase(d.commits[d.commits.length - 1].ref);
      setHead(d.commits[0].ref);
    }
  }, [refsQ.data, base, head]);

  const deltaQ = useQuery({
    queryKey: ["arch-delta", base, head],
    queryFn: () => api.architectureDelta(base, head),
    enabled: Boolean(base && head),
  });

  const d: ArchDelta | undefined = deltaQ.data;
  const counts = d?.delta.counts;
  const diagram = useMemo(
    () => (d ? toDiagram(d) : { nodes: [], edges: [] }), [d]);
  const gapDays = useMemo(() => {
    if (!d) return null;
    const ms = new Date(d.head.date).getTime() - new Date(d.base.date).getTime();
    return Number.isFinite(ms) ? Math.max(0, Math.round(ms / 86_400_000)) : null;
  }, [d]);

  if (refsQ.isLoading) return <PageLoading tiles={0} />;
  if (refsQ.isError) {
    return <ErrorState
      title="Can't read this repository's history."
      message={refsQ.error instanceof Error ? refsQ.error.message : undefined}
      onRetry={() => refsQ.refetch()} />;
  }

  return (
    <div className="page">
      <div className="eyebrow">Commitsmap</div>
      <h1 className="h1" style={{ marginTop: 6 }}>How the structure changed</h1>
      <p className="lede">
        Every other page describes this codebase as it stands today. This one compares two points in
        its history — two releases, a tag and HEAD, any two commits — and reports the structural
        facts that differ. Read straight from the git trees: no re-ingest, no model, nothing inferred.
      </p>

      <div className="ad-controls">
        <RefSelect label="From" value={base} onChange={setBase}
                   refs={refsQ.data!} disabled={deltaQ.isFetching} />
        <span className="ad-arrow">→</span>
        <RefSelect label="To" value={head} onChange={setHead}
                   refs={refsQ.data!} disabled={deltaQ.isFetching} />
        {deltaQ.isFetching && <span className="spin" />}
      </div>

      {deltaQ.isError && (
        <ErrorState title="That comparison failed."
                    message={deltaQ.error instanceof Error ? deltaQ.error.message : undefined}
                    onRetry={() => deltaQ.refetch()} />
      )}

      {d && counts && (
        <>
          <p className="cap ad-range">
            {d.base.sha} ({day(d.base.date)}) → {d.head.sha} ({day(d.head.date)})
          </p>

          {d.delta.unchanged && (
            <div className="state" style={{ marginTop: 20 }}>
              <b>Structurally identical.</b> File contents changed between these refs, but nothing
              moved: no package, submodule or file-placement differences.{" "}
              {gapDays !== null && gapDays <= 21
                ? `These refs are only ${gapDays} day${gapDays === 1 ? "" : "s"} apart, which is usually too close for structure to shift — pick a wider range (an early tag against a recent one) to see drift.`
                : "The diagram below shows the architecture as it stands at the later ref."}
            </div>
          )}

          {/* The diagram renders either way. "Nothing changed" is a finding, and
              a finding still deserves the picture of what held still. */}
          {diagram.nodes.length > 0 && (
            <div className="card" style={{ marginTop: 16, padding: 0, overflow: "hidden" }}>
              <ArchDiagram
                title={`${refLabel(d.head)} vs ${refLabel(d.base)}`}
                subtitle={`${d.after.package || "repository"} · ${counts.before.code_files} → ${counts.after.code_files} code files · ${counts.files_moved} moved between modules`}
                groupLabel={d.after.package || d.before.package || "package"}
                nodes={diagram.nodes}
                edges={diagram.edges}
                onOpenFile={(path) => nav("/reader", { state: { path } })}
                footnote={
                  diagram.edges.some((e) => e.kind === "dep")
                    ? (d.edges_live
                        ? "arrows: module dependencies from the symbol graph"
                        : "arrows: module dependencies as they are at the ingested commit, not at this ref")
                    : undefined
                }
                filename={`arch-delta-${refLabel(d.base)}-${refLabel(d.head)}`.replace(/[^a-zA-Z0-9._-]+/g, "-")}
              />
            </div>
          )}

          {!d.delta.unchanged && (
            <>
              <div className="tiles" style={{ marginTop: 20 }}>
                <div className="tile">
                  <div className="tile-v tnum">{counts.before.code_files} → {counts.after.code_files}</div>
                  <div className="tile-k">code files</div>
                </div>
                <div className="tile">
                  <div className="tile-v tnum">{counts.before.submodules} → {counts.after.submodules}</div>
                  <div className="tile-k">submodules</div>
                </div>
                <div className="tile">
                  <div className="tile-v tnum">+{counts.files_added} / −{counts.files_removed}</div>
                  <div className="tile-k">files added / removed</div>
                </div>
                <div className="tile">
                  <div className="tile-v tnum">{counts.files_moved}</div>
                  <div className="tile-k">files moved between modules</div>
                </div>
              </div>

              {d.delta.package && (
                <div className="card ad-note">
                  <b>The package itself moved.</b>{" "}
                  <span className="mono">{d.delta.package.before}</span> →{" "}
                  <span className="mono">{d.delta.package.after}</span>.{" "}
                  {d.delta.package_relocated
                    ? "Same package at a new path, so file changes below are compared relative to it — a layout migration stays one fact instead of marking every file as moved."
                    : "A different package became the primary one, so there is no shared frame of reference; file paths below are absolute and are not directly comparable."}
                </div>
              )}

              {d.delta.submodules_changed.length > 0 && (
                <div className="card ad-facts">
                  <h3>Submodules that gained or lost files</h3>
                  <table className="tbl">
                    <thead><tr><th>Submodule</th><th>Added</th><th>Removed</th><th>Files</th></tr></thead>
                    <tbody>
                      {d.delta.submodules_changed.map((c) => (
                        <tr key={c.submodule}>
                          <td className="mono">{c.submodule}</td>
                          <td className="tnum">+{c.files_added}</td>
                          <td className="tnum">−{c.files_removed}</td>
                          <td className="tnum">{c.file_count_before} → {c.file_count_after}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {d.delta.files_moved.length > 0 && (
                <div className="card ad-facts">
                  <h3>Files that moved between submodules
                    <span className="chip solid ad-moved">{counts.files_moved}</span></h3>
                  <ul>
                    {d.delta.files_moved.map((m) => (
                      <li key={m.from} className="mono">
                        {m.from} → {m.to}
                        <span className="cap"> ({m.from_submodule || "core"} → {m.to_submodule || "core"})</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <FactList title="Submodules added" items={d.delta.submodules_added} tone="added" />
              <FactList title="Submodules removed" items={d.delta.submodules_removed} tone="removed" />
              <FactList title="Top-level folders added" items={d.delta.top_level_added} tone="added" />
              <FactList title="Top-level folders removed" items={d.delta.top_level_removed} tone="removed" />
              <FactList title="Files added" items={d.delta.files_added} tone="added" />
              <FactList title="Files removed" items={d.delta.files_removed} tone="removed" />

              {d.delta.truncated && (
                <p className="cap ad-range">
                  Long lists are capped for readability — the counts above are complete.
                </p>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
