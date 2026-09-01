import { useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import Mermaid from "../components/Mermaid";
import WikiCode from "../components/WikiCode";
import ConfluencePublishDialog from "../components/ConfluencePublishDialog";
import Markdown, { renderInline } from "../lib/markdown";
import { ErrorState } from "../components/PageState";
import type { WikiBlock, WikiChip } from "../lib/types";

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");

/** Plain-text list/table cells go through the same inline renderer as full
 *  prose (bold, italic, `code`, and the `symbol` — path:line reference
 *  pattern) — they used to only understand **bold**, so a cell like
 *  `` `create_app` — src/axon/web/app.py:41 `` rendered its backticks as
 *  literal punctuation instead of a code chip. */
function Prose({ text }: { text: string }) {
  return <>{renderInline(text)}</>;
}

function Block({ block, onOpen }: { block: WikiBlock; onOpen: (c: WikiChip) => void }) {
  if (block.kind === "md") return <Markdown text={block.text} />;
  if (block.kind === "p") return <p><Prose text={block.text} /></p>;
  if (block.kind === "h2") return <h2 id={slug(block.text)} className="wk-h2">{block.text}</h2>;
  if (block.kind === "list")
    return <ul className="wk-list">{block.items.map((i, k) => <li key={k}><Prose text={i} /></li>)}</ul>;
  if (block.kind === "table")
    return (
      <div className="wk-table-wrap">
        <table className="wk-table">
          <thead><tr>{block.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
          <tbody>
            {block.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><Prose text={cell} /></td>)}</tr>)}
          </tbody>
        </table>
      </div>
    );
  if (block.kind === "code")
    return (
      <WikiCode
        title={block.title} path={block.path} line={block.line} lang={block.lang} code={block.code}
        onOpen={(path, line) => onOpen({ kind: "file", text: block.title, path, line })}
      />
    );
  if (block.kind === "diagram")
    return (
      <figure id={slug(block.title)} className="wk-diagram">
        <figcaption>{block.title}</figcaption>
        <div className="wk-diagram-canvas"><Mermaid chart={block.mermaid} /></div>
      </figure>
    );
  if (block.kind === "stats")
    return (
      <div className="tiles wk-stats">
        {block.items.map((s, k) => (
          <div key={k} className="tile"><div className="n tnum">{s.n}</div><div className="l">{s.label}</div></div>
        ))}
      </div>
    );
  if (block.kind === "callout")
    return (
      <div className={"wk-callout " + block.tone}>
        <Prose text={block.text} />
      </div>
    );
  return (
    <div className="wk-chips">
      {block.chips.map((c, k) => (
        <button key={k} className="wk-chip" onClick={() => onOpen(c)} title={c.path}>
          <span className="ck">{"{ }"}</span>{c.text}
        </button>
      ))}
    </div>
  );
}

export default function Tour() {
  const nav = useNavigate();
  const { sectionKey } = useParams<{ sectionKey?: string }>();
  const wikiQ = useQuery({ queryKey: ["wiki"], queryFn: api.wiki, staleTime: Infinity });
  // Shared cache with Shell's poll — no extra request once status is warm.
  const statusQ = useQuery({ queryKey: ["status"], queryFn: api.status, staleTime: 60_000 });
  const [publishOpen, setPublishOpen] = useState(false);

  const openChip = (c: WikiChip) => nav("/reader", { state: { path: c.path, line: c.line } });
  // Always offered — a user with no Confluence connected yet gets sent to
  // Settings (with that card called out) instead of never seeing the button
  // at all, so publishing is discoverable before it's usable.
  const clickPublish = () => {
    if (statusQ.data?.confluence?.configured) setPublishOpen(true);
    else nav("/settings", { state: { highlight: "confluence", reason: "publish" } });
  };

  if (wikiQ.isLoading) {
    return (
      <div className="page wk">
        <div className="eyebrow">Start here</div>
        <div className="state">
          <span className="spin" />
          Generating your wiki — the page structure and each section's writeup are being
          decided live for this repo. Cached after this first visit.
        </div>
      </div>
    );
  }
  if (!wikiQ.data)
    return <ErrorState message={wikiQ.error instanceof Error ? wikiQ.error.message : undefined} onRetry={() => wikiQ.refetch()} />;

  const sections = wikiQ.data.sections;
  if (!sectionKey) return <Navigate to={`/tour/${sections[0]?.key}`} replace />;

  const idx = sections.findIndex((s) => s.key === sectionKey);
  const sec = sections[idx];
  if (!sec) return <Navigate to={`/tour/${sections[0]?.key}`} replace />;

  // "Relevant source files" — every distinct path this section's evidence
  // chips point at, surfaced up top the way DeepWiki does, before the prose.
  const relevantFiles = [...new Map(
    sec.blocks.filter((b): b is Extract<WikiBlock, { kind: "chips" }> => b.kind === "chips")
      .flatMap((b) => b.chips)
      .map((c) => [c.path, c] as const)
  ).values()];

  // "On this page" — every H2 heading and diagram title in reading order.
  const toc = sec.blocks
    .filter((b): b is Extract<WikiBlock, { kind: "h2" | "diagram" }> => b.kind === "h2" || b.kind === "diagram")
    .map((b) => ({ text: b.kind === "h2" ? b.text : b.title, id: slug(b.kind === "h2" ? b.text : b.title) }));

  const prev = sections[idx - 1];
  const next = sections[idx + 1];

  return (
    <div className="page wk">
      <div className="eyebrow" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <span>Start here · {wikiQ.data.repo}</span>
        <button className="btn" onClick={clickPublish}>
          Publish to Confluence
        </button>
      </div>
      <h1 className="h1">{sec.title}</h1>
      <p className="lede">{sec.subtitle}</p>

      {relevantFiles.length > 0 && (
        <div className="wk-relevant">
          <div className="wk-relevant-head">Relevant source files</div>
          <ul>
            {relevantFiles.map((c, i) => (
              <li key={i}>
                <button onClick={() => openChip(c)}>{c.path}{c.line ? `:${c.line}` : ""}</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="wk-layout">
        <article className="wk-article">
          {sec.blocks.map((b, i) => <Block key={i} block={b} onOpen={openChip} />)}
        </article>

        {toc.length > 0 && (
          <aside className="wk-toc">
            <div className="wk-toc-head">On this page</div>
            {toc.map((t) => (
              <a key={t.id} href={`#${t.id}`}>{t.text}</a>
            ))}
          </aside>
        )}
      </div>

      <div className="wk-pager">
        {prev ? <button className="btn" onClick={() => nav(`/tour/${prev.key}`)}>← {prev.title}</button> : <span />}
        {next && <button className="btn primary" onClick={() => nav(`/tour/${next.key}`)}>{next.title} →</button>}
      </div>

      {publishOpen && (
        <ConfluencePublishDialog sections={sections} onClose={() => setPublishOpen(false)} />
      )}
    </div>
  );
}
