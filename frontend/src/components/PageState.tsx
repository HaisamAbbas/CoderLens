/** Shared page states — skeleton loading, real error + retry, and a code-line
 *  skeleton — so every page behaves the same way under load and failure. */

/** Skeleton page layout: eyebrow/h1/lede bars, stat tiles, then two cards. */
export function PageLoading({ header = true, tiles = 4 }: { header?: boolean; tiles?: number }) {
  return (
    <div className="page" aria-busy="true" aria-label="Loading">
      {header && (
        <>
          <div className="sk" style={{ width: 96, height: 11 }} />
          <div className="sk" style={{ width: 320, maxWidth: "70%", height: 26, marginTop: 12 }} />
          <div className="sk" style={{ width: 540, maxWidth: "90%", height: 13, marginTop: 14 }} />
        </>
      )}
      {tiles > 0 && (
        <div className="tiles" style={{ marginTop: 24 }}>
          {Array.from({ length: tiles }, (_, i) => (
            <div key={i} className="sk" style={{ height: 84 }} />
          ))}
        </div>
      )}
      <div className="sk" style={{ height: 168, marginTop: 16 }} />
      <div className="sk" style={{ height: 96, marginTop: 14 }} />
    </div>
  );
}

/** Error state showing the real failure with an optional retry. */
export function ErrorState({
  message, onRetry, title = "Something went wrong.",
}: {
  message?: string; onRetry?: () => void; title?: string;
}) {
  return (
    <div className="page">
      <div className="state err">
        <b>{title}</b>
        {message && <div className="err-detail">{message}</div>}
        {onRetry && (
          <button className="btn" style={{ marginTop: 12 }} onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

/** Skeleton for a block of source code (Reader peek / Tour stage). */
export function CodeSkeleton({ lines = 14 }: { lines?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading source" style={{ padding: 12 }}>
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          className="sk"
          style={{ height: 12, marginBottom: 10, width: `${38 + ((i * 41) % 55)}%` }}
        />
      ))}
    </div>
  );
}
