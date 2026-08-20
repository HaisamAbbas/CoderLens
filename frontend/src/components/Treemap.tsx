import { useLayoutEffect, useMemo, useRef, useState } from "react";

export interface TreemapItem {
  key: string;
  label: string;
  sub: string;
  value: number;   // area
  tint: string;    // fill background
  title?: string;
  onClick?: () => void;
}

interface Node extends TreemapItem { area: number }
interface Rect { x: number; y: number; w: number; h: number; data: Node }

/** Squarified treemap (Bruls, Huizing & van Wijk) — lays items into a rect while
 *  keeping cell aspect ratios close to square, so the map reads cleanly. */
function squarify(items: Node[], W: number, H: number): Rect[] {
  const rects: Rect[] = [];
  const worst = (row: Node[], side: number) => {
    const sum = row.reduce((s, r) => s + r.area, 0);
    const mx = Math.max(...row.map((r) => r.area));
    const mn = Math.min(...row.map((r) => r.area));
    return Math.max((side * side * mx) / (sum * sum), (sum * sum) / (side * side * mn));
  };
  const layoutRow = (row: Node[], r: { x: number; y: number; w: number; h: number }) => {
    const sum = row.reduce((s, n) => s + n.area, 0);
    if (r.w >= r.h) {
      const rw = sum / r.h;
      let cy = r.y;
      for (const n of row) { const rh = n.area / rw; rects.push({ x: r.x, y: cy, w: rw, h: rh, data: n }); cy += rh; }
      return { x: r.x + rw, y: r.y, w: r.w - rw, h: r.h };
    }
    const rh = sum / r.w;
    let cx = r.x;
    for (const n of row) { const rw = n.area / rh; rects.push({ x: cx, y: r.y, w: rw, h: rh, data: n }); cx += rw; }
    return { x: r.x, y: r.y + rh, w: r.w, h: r.h - rh };
  };

  let rect = { x: 0, y: 0, w: W, h: H };
  let row: Node[] = [];
  const queue = [...items];
  while (queue.length) {
    const side = Math.min(rect.w, rect.h);
    const next = queue[0];
    if (row.length === 0 || worst(row, side) >= worst([...row, next], side)) {
      row.push(next); queue.shift();
    } else {
      rect = layoutRow(row, rect); row = [];
    }
  }
  if (row.length) layoutRow(row, rect);
  return rects;
}

export default function Treemap({ items, height = 248 }: { items: TreemapItem[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);

  useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(e[0].contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const rects = useMemo(() => {
    const data = items.filter((i) => i.value > 0);
    if (!data.length || w <= 0) return [];
    const total = data.reduce((s, d) => s + d.value, 0);
    const scale = (w * height) / total;
    const nodes: Node[] = data
      .map((d) => ({ ...d, area: d.value * scale }))
      .sort((a, b) => b.area - a.area);
    return squarify(nodes, w, height);
  }, [items, w, height]);

  return (
    <div ref={ref} className="tm" style={{ height }}>
      {rects.map((r) => {
        const n = r.data;
        const tight = r.w < 66 || r.h < 34;
        const tiny = r.w < 30 || r.h < 20;
        return (
          <div
            key={n.key}
            className={"tm-cell" + (n.onClick ? " click" : "")}
            style={{ left: r.x, top: r.y, width: r.w, height: r.h, background: n.tint }}
            title={n.title ?? `${n.label} — ${n.sub}`}
            onClick={n.onClick}
          >
            {!tiny && (
              <div className="tm-in">
                <div className="tm-label">{n.label}</div>
                {!tight && <div className="tm-sub">{n.sub}</div>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
