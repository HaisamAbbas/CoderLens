import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import mermaid from "mermaid";
import { downloadPng, downloadShareCard, downloadSvg } from "../lib/diagramExport";

const isDarkNow = () =>
  document.documentElement.getAttribute("data-theme") === "dark"
  || (document.documentElement.getAttribute("data-theme") !== "light"
      && window.matchMedia("(prefers-color-scheme: dark)").matches);

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Renders a mechanically-generated Mermaid diagram string to SVG —
 *  flowchart, sequenceDiagram, classDiagram, erDiagram, whichever the source
 *  starts with; mermaid.js dispatches on that itself, so nothing here is
 *  diagram-type-specific. The source is always produced by the backend
 *  (never the LLM), so it parses; we still fall back to showing the source
 *  text if a render ever throws, so a diagram can never blank the page.
 *  Click to open a full-screen viewer that zooms and pans. Theme follows the
 *  app's light/dark. */
export default function Mermaid({
  chart, title = "diagram", subtitle = "",
}: {
  chart: string;
  /** Names the downloaded file and titles the share card. */
  title?: string;
  subtitle?: string;
}) {
  const rawId = useId().replace(/[^a-zA-Z0-9]/g, "");
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    mermaid.initialize({
      startOnLoad: false,
      theme: isDarkNow() ? "dark" : "default",
      securityLevel: "loose",
      flowchart: { curve: "basis", htmlLabels: true, padding: 14, nodeSpacing: 40, rankSpacing: 55 },
      fontFamily: "var(--font-sans, system-ui), sans-serif",
    });
    mermaid
      .render(`mmd-${rawId}`, chart)
      .then(({ svg }) => { if (alive) { setSvg(svg); setFailed(false); } })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [chart, rawId]);

  if (failed) return <pre className="wk-mermaid-src">{chart}</pre>;
  return (
    <>
      <div className="wk-mermaid-wrap">
        <div
          className="wk-mermaid"
          role="button"
          title="Click to expand"
          onClick={() => svg && setOpen(true)}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
        {svg && <DiagramExport chart={chart} title={title} subtitle={subtitle} />}
      </div>
      {open && (
        <DiagramLightbox
          svg={svg} chart={chart} title={title} subtitle={subtitle}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

/** Download controls. Kept out of the SVG itself so the diagram exported is
 *  exactly the diagram shown, with no toolbar baked into it. */
function DiagramExport({
  chart, title, subtitle, inverse = false,
}: {
  chart: string; title: string; subtitle: string; inverse?: boolean;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "diagram";

  const run = (kind: string, fn: () => Promise<void>) => async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(kind);
    setError("");
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className={"dgx" + (inverse ? " dgx-inv" : "")} onClick={(e) => e.stopPropagation()}>
      <button disabled={!!busy} onClick={run("svg", () => downloadSvg(chart, slug))}
              title="Download as SVG (vector, scales cleanly)">
        {busy === "svg" ? "…" : "SVG"}
      </button>
      <button disabled={!!busy} onClick={run("png", () => downloadPng(chart, slug))}
              title="Download as PNG (2x resolution)">
        {busy === "png" ? "…" : "PNG"}
      </button>
      <button disabled={!!busy}
              onClick={run("card", () => downloadShareCard(chart, slug, title, subtitle))}
              title="Download a 1200x630 share card for a PR or doc">
        {busy === "card" ? "…" : "Card"}
      </button>
      {error && <span className="dgx-err" title={error}>export failed</span>}
    </div>
  );
}

/** Full-screen diagram viewer: wheel to zoom (toward the cursor), drag to pan,
 *  buttons for zoom/reset, Esc or backdrop click to close. The SVG is scaled
 *  with a CSS transform, so it stays crisp at any zoom. */
function DiagramLightbox({
  svg, chart, title, subtitle, onClose,
}: {
  svg: string; chart: string; title: string; subtitle: string; onClose: () => void;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [t, setT] = useState({ x: 0, y: 0, s: 1 });
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const fit = () => {
    const stage = stageRef.current;
    const el = canvasRef.current?.querySelector("svg");
    if (!stage || !el) return;
    const sr = stage.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const w = r.width / (t.s || 1);
    const h = r.height / (t.s || 1);
    if (w <= 0 || h <= 0) return;
    const s = clamp(Math.min((sr.width * 0.92) / w, (sr.height * 0.9) / h), 0.15, 4);
    setT({ s, x: (sr.width - w * s) / 2, y: (sr.height - h * s) / 2 });
  };

  // Fit once the SVG is in the DOM.
  useLayoutEffect(() => { fit(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [svg]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { document.body.style.overflow = prev; window.removeEventListener("keydown", onKey); };
  }, [onClose]);

  const zoomAt = (factor: number, cx: number, cy: number) =>
    setT((p) => {
      const ns = clamp(p.s * factor, 0.15, 12);
      const k = ns / p.s;
      return { s: ns, x: cx - (cx - p.x) * k, y: cy - (cy - p.y) * k };
    });

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const r = stageRef.current!.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
  };
  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    setT((p) => ({ ...p, x: drag.current!.tx + (e.clientX - drag.current!.x), y: drag.current!.ty + (e.clientY - drag.current!.y) }));
  };
  const onPointerUp = () => { drag.current = null; };
  const btnZoom = (factor: number) => {
    const r = stageRef.current!.getBoundingClientRect();
    zoomAt(factor, r.width / 2, r.height / 2);
  };

  return (
    <div className="dlg-overlay" onClick={onClose}>
      <div className="dlg-controls" onClick={(e) => e.stopPropagation()}>
        <button onClick={() => btnZoom(1.25)} title="Zoom in">+</button>
        <button onClick={() => btnZoom(0.8)} title="Zoom out">−</button>
        <button onClick={fit} title="Fit to screen">Fit</button>
        <span className="dlg-pct">{Math.round(t.s * 100)}%</span>
        <DiagramExport chart={chart} title={title} subtitle={subtitle} inverse />
        <button className="dlg-close" onClick={onClose} title="Close (Esc)">✕</button>
      </div>
      <div
        className="dlg-stage"
        ref={stageRef}
        onClick={(e) => e.stopPropagation()}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
      >
        <div
          className="dlg-canvas"
          ref={canvasRef}
          style={{ transform: `translate(${t.x}px, ${t.y}px) scale(${t.s})`, transformOrigin: "0 0" }}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
      <div className="dlg-hint">Scroll to zoom · drag to pan · Esc to close</div>
    </div>
  );
}
