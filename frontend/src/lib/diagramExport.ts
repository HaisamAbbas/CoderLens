/** Download a Mermaid diagram as SVG, PNG, or a 1200x630 share card.
 *
 *  Everything here re-renders the chart rather than reusing the SVG already on
 *  screen. The on-screen render uses `htmlLabels: true`, which puts label text
 *  inside <foreignObject> — browsers refuse to rasterize that to a canvas, so a
 *  PNG made from the visible SVG comes out with every label blank. Re-rendering
 *  with `htmlLabels: false` produces plain <text>, which both rasterizes and
 *  survives being pasted into tools (Confluence, PR descriptions) that render
 *  SVG without a full HTML engine.
 *
 *  The diagram itself is never modified — this is an export path only, so the
 *  existing diagrams keep rendering exactly as they always have.
 */

import mermaid from "mermaid";

const isDarkNow = () =>
  document.documentElement.getAttribute("data-theme") === "dark"
  || (document.documentElement.getAttribute("data-theme") !== "light"
      && window.matchMedia("(prefers-color-scheme: dark)").matches);

/** Read a themed CSS custom property, with a literal fallback for the canvas
 *  paths — a canvas fill cannot resolve `var(--bg)`. */
function token(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

let exportSeq = 0;

/** Render `chart` to a standalone SVG string with text labels. */
async function renderForExport(chart: string): Promise<string> {
  const dark = isDarkNow();
  mermaid.initialize({
    startOnLoad: false,
    theme: dark ? "dark" : "default",
    securityLevel: "loose",
    flowchart: { curve: "basis", htmlLabels: false, padding: 14, nodeSpacing: 40, rankSpacing: 55 },
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
  });
  const { svg } = await mermaid.render(`mmd-export-${exportSeq++}`, chart);
  return svg;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking synchronously can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Intrinsic pixel size of an SVG string, from width/height or the viewBox. */
function svgSize(svg: string): { w: number; h: number } {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const el = doc.documentElement;
  const vb = (el.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
  const w = parseFloat(el.getAttribute("width") || "") || (vb.length === 4 ? vb[2] : 0);
  const h = parseFloat(el.getAttribute("height") || "") || (vb.length === 4 ? vb[3] : 0);
  return { w: w || 960, h: h || 540 };
}

/** Give the SVG explicit pixel dimensions and an opaque background, so it
 *  rasterizes predictably and does not come out transparent-on-transparent. */
function prepareSvg(svg: string, w: number, h: number, bg: string): string {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const el = doc.documentElement;
  el.setAttribute("width", String(w));
  el.setAttribute("height", String(h));
  if (!el.getAttribute("viewBox")) el.setAttribute("viewBox", `0 0 ${w} ${h}`);
  el.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const rect = doc.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("width", "100%");
  rect.setAttribute("height", "100%");
  rect.setAttribute("fill", bg);
  el.insertBefore(rect, el.firstChild);
  return new XMLSerializer().serializeToString(el);
}

function loadImage(svg: string): Promise<HTMLImageElement> {
  // A data: URL keeps the image same-origin, so the canvas stays untainted and
  // toBlob() is allowed. A blob: URL taints it in some browsers.
  const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("The diagram could not be rasterized."));
    img.src = url;
  });
}

/* ---- Exporting an SVG we drew ourselves (ArchDiagram) ------------------
 * These take a live <svg> node instead of Mermaid source. Nothing needs
 * re-rendering here: the renderer paints with literal colour attributes rather
 * than CSS classes precisely so the serialized markup stands alone, with no
 * stylesheet to carry along and no foreignObject to defeat the canvas.       */

function serialize(node: SVGSVGElement): { svg: string; w: number; h: number } {
  const clone = node.cloneNode(true) as SVGSVGElement;
  const vb = (clone.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
  const w = vb.length === 4 ? vb[2] : node.clientWidth || 960;
  const h = vb.length === 4 ? vb[3] : node.clientHeight || 540;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  return { svg: new XMLSerializer().serializeToString(clone), w, h };
}

export function downloadSvgElement(node: SVGSVGElement, filename: string, bg: string): void {
  const { svg, w, h } = serialize(node);
  saveBlob(new Blob([prepareSvg(svg, w, h, bg)], { type: "image/svg+xml;charset=utf-8" }),
           `${filename}.svg`);
}

export async function downloadPngElement(
  node: SVGSVGElement, filename: string, bg: string, scale = 2,
): Promise<void> {
  const { svg, w, h } = serialize(node);
  const img = await loadImage(prepareSvg(svg, w, h, bg));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  await new Promise<void>((resolve) =>
    canvas.toBlob((b) => { if (b) saveBlob(b, `${filename}.png`); resolve(); }, "image/png"));
}

export async function downloadSvg(chart: string, filename: string): Promise<void> {
  const raw = await renderForExport(chart);
  const { w, h } = svgSize(raw);
  const svg = prepareSvg(raw, w, h, token("--bg", "#ffffff"));
  saveBlob(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }), `${filename}.svg`);
}

export async function downloadPng(chart: string, filename: string, scale = 2): Promise<void> {
  const raw = await renderForExport(chart);
  const { w, h } = svgSize(raw);
  const svg = prepareSvg(raw, w, h, token("--bg", "#ffffff"));
  const img = await loadImage(svg);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = token("--bg", "#ffffff");
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  await new Promise<void>((resolve) =>
    canvas.toBlob((blob) => {
      if (blob) saveBlob(blob, `${filename}.png`);
      resolve();
    }, "image/png"));
}

/** A 1200x630 card — the size link previews expect — with the diagram scaled to
 *  fit and a caption, for dropping into a PR, a doc, or a chat. */
export async function downloadShareCard(
  chart: string, filename: string, title: string, subtitle = "",
): Promise<void> {
  const W = 1200, H = 630, PAD = 48, HEAD = subtitle ? 132 : 104;
  const raw = await renderForExport(chart);
  const { w, h } = svgSize(raw);
  const svg = prepareSvg(raw, w, h, "transparent");
  const img = await loadImage(svg);

  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d")!;
  const bg = token("--bg", "#ffffff");
  const fg = token("--text", "#0f172a");
  const muted = token("--text-2", "#64748b");
  const accent = token("--accent", "#6366f1");

  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = accent;
  ctx.fillRect(0, 0, W, 6);

  ctx.fillStyle = fg;
  ctx.font = "600 38px system-ui, -apple-system, Segoe UI, sans-serif";
  ctx.fillText(title.slice(0, 60), PAD, 74);
  if (subtitle) {
    ctx.fillStyle = muted;
    ctx.font = "400 22px system-ui, -apple-system, Segoe UI, sans-serif";
    ctx.fillText(subtitle.slice(0, 90), PAD, 112);
  }

  const availW = W - PAD * 2;
  const availH = H - HEAD - PAD;
  const s = Math.min(availW / w, availH / h, 1.6);
  const dw = w * s, dh = h * s;
  ctx.drawImage(img, (W - dw) / 2, HEAD + (availH - dh) / 2, dw, dh);

  await new Promise<void>((resolve) =>
    canvas.toBlob((blob) => {
      if (blob) saveBlob(blob, `${filename}-card.png`);
      resolve();
    }, "image/png"));
}
