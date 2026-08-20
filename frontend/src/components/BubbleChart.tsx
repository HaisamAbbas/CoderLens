import { useLayoutEffect, useMemo, useRef, useState } from "react";

export interface BubbleItem {
  key: string;
  label: string;
  sub: string;
  value: number;   // area basis
  tint: string;    // fill/ring color
  title?: string;
  badge?: string;
  onClick?: () => void;
}

interface Circle extends BubbleItem { r: number; x: number; y: number }

/** Force-relaxed circle packing: seed on a spiral, then iteratively push
 *  overlapping circles apart while pulling everything gently toward center.
 *  Converges to a tight, roughly-circular cluster without any hard grid. */
function pack(items: BubbleItem[], w: number, h: number): Circle[] {
  if (!items.length || w <= 0 || h <= 0) return [];
  const total = items.reduce((s, d) => s + d.value, 0);
  const targetArea = w * h * 0.5;
  const scale = Math.sqrt(targetArea / (Math.PI * total));
  const minR = Math.max(16, Math.min(w, h) * 0.03);
  const maxR = Math.min(w, h) * 0.42;

  const circles: Circle[] = items
    .map((d) => ({ ...d, r: Math.min(maxR, Math.max(minR, Math.sqrt(d.value) * scale)), x: 0, y: 0 }))
    .sort((a, b) => b.r - a.r);

  const cx = w / 2, cy = h / 2;
  circles.forEach((c, i) => {
    const angle = i * 2.4;
    const rad = Math.sqrt(i) * (Math.min(w, h) * 0.09);
    c.x = cx + Math.cos(angle) * rad;
    c.y = cy + Math.sin(angle) * rad;
  });

  for (let it = 0; it < 320; it++) {
    for (let i = 0; i < circles.length; i++) {
      for (let j = i + 1; j < circles.length; j++) {
        const a = circles[i], b = circles[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        let dist = Math.hypot(dx, dy);
        const minDist = a.r + b.r + 3;
        if (dist < minDist) {
          if (dist < 0.001) dist = 0.001;
          const overlap = (minDist - dist) / 2;
          const nx = dx / dist, ny = dy / dist;
          a.x -= nx * overlap; a.y -= ny * overlap;
          b.x += nx * overlap; b.y += ny * overlap;
        }
      }
    }
    for (const c of circles) {
      c.x += (cx - c.x) * 0.008;
      c.y += (cy - c.y) * 0.008;
      c.x = Math.min(Math.max(w - c.r, c.r), Math.max(c.r, Math.min(c.x, w - c.r)));
      c.y = Math.min(Math.max(h - c.r, c.r), Math.max(c.r, Math.min(c.y, h - c.r)));
    }
  }
  return circles;
}

export default function BubbleChart({ items, height = 320 }: { items: BubbleItem[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  const [hover, setHover] = useState<string | null>(null);

  useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(e[0].contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const circles = useMemo(() => {
    const data = items.filter((i) => i.value > 0);
    return pack(data, w, height);
  }, [items, w, height]);

  return (
    <div ref={ref} className="bbl" style={{ height }}>
      {circles.map((c) => {
        const showLabel = c.r >= 24;
        const showSub = c.r >= 40;
        return (
          <div
            key={c.key}
            className={"bbl-c" + (c.onClick ? " click" : "") + (hover === c.key ? " hot" : "")}
            style={{
              left: c.x - c.r, top: c.y - c.r, width: c.r * 2, height: c.r * 2,
              background: `color-mix(in srgb, ${c.tint} 26%, var(--surface))`,
              boxShadow: `inset 0 0 0 1.5px color-mix(in srgb, ${c.tint} 60%, transparent)`,
              zIndex: hover === c.key ? 5 : 1,
            }}
            title={c.title ?? `${c.label} — ${c.sub}`}
            onClick={c.onClick}
            onMouseEnter={() => setHover(c.key)}
            onMouseLeave={() => setHover((h) => (h === c.key ? null : h))}
          >
            {showLabel && (
              <div className="bbl-in">
                <div className="bbl-label">{c.label}</div>
                {showSub && <div className="bbl-sub">{c.sub}</div>}
                {showSub && c.badge && (
                  <div className="bbl-badge" style={{ color: c.tint }}>{c.badge}</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
