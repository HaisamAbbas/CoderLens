// Our mark: three stacked isometric strata — "layers of a codebase," excavated.
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id="am-top" x1="4" y1="3" x2="28" y2="17" gradientUnits="userSpaceOnUse">
          <stop stopColor="#4c8dff" />
          <stop offset="1" stopColor="#7aa8ff" />
        </linearGradient>
        <linearGradient id="am-mid" x1="4" y1="9" x2="28" y2="23" gradientUnits="userSpaceOnUse">
          <stop stopColor="#8b5cf6" />
          <stop offset="1" stopColor="#a78bfa" />
        </linearGradient>
        <linearGradient id="am-bot" x1="4" y1="15" x2="28" y2="29" gradientUnits="userSpaceOnUse">
          <stop stopColor="#f97316" />
          <stop offset="1" stopColor="#fb9a4b" />
        </linearGradient>
      </defs>
      <path d="M16 15 L29 22 L16 29 L3 22 Z" fill="url(#am-bot)" />
      <path d="M16 9 L29 16 L16 23 L3 16 Z" fill="url(#am-mid)" />
      <path d="M16 3 L29 10 L16 17 L3 10 Z" fill="url(#am-top)" />
    </svg>
  );
}
