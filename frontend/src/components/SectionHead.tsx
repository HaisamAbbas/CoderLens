import type { ReactNode } from "react";

/** Icon-led card/section header — leading tonal icon chip + title + caption.
 *  The Material pattern used consistently across Overview and Tour. */
export default function SectionHead({
  icon, title, cap, tone = "var(--accent)",
}: { icon: ReactNode; title: string; cap?: string; tone?: string }) {
  return (
    <div className="sec-head">
      <span className="sec-icon" style={{ background: `color-mix(in srgb, ${tone} 15%, transparent)`, color: tone }}>
        {icon}
      </span>
      <div className="sec-text">
        <h3>{title}</h3>
        {cap && <p className="cap">{cap}</p>}
      </div>
    </div>
  );
}
