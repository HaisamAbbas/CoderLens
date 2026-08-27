import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageLoading, ErrorState } from "../components/PageState";
import Mermaid from "../components/Mermaid";
import type { ArchDelta, ArchRef } from "../lib/types";

const day = (iso: string) => iso.slice(0, 10);

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
  const subtitle = useMemo(
    () => (d ? `${d.base.ref} → ${d.head.ref}` : ""), [d]);

  if (refsQ.isLoading) return <PageLoading tiles={0} />;
  if (refsQ.isError) {
    return <ErrorState
      title="Can't read this repository's history."
      message={refsQ.error instanceof Error ? refsQ.error.message : undefined}
      onRetry={() => refsQ.refetch()} />;
  }

  return (
    <div className="page">
      <div className="eyebrow">Architecture Delta</div>
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

          {d.delta.unchanged ? (
            <div className="state" style={{ marginTop: 20 }}>
              Structurally identical. Files changed between these refs, but no package, submodule or
              file-placement differences — the architecture held still.
            </div>
          ) : (
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

              {d.mermaid && (
                <div className="card" style={{ marginTop: 16 }}>
                  <Mermaid chart={d.mermaid} title={`architecture-delta-${d.base.ref}-${d.head.ref}`}
                           subtitle={subtitle} />
                  <p className="cap ad-legend">
                    <span className="ad-key ad-added" /> added
                    <span className="ad-key ad-removed" /> removed
                    <span className="ad-key ad-changed" /> files added or removed
                    <span className="ad-key ad-kept" /> unchanged
                  </p>
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
