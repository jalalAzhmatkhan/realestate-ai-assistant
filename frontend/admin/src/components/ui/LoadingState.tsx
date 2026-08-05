export interface LoadingStateProps {
  /** `skeleton` for lists/tables, `spinner` for a single in-flight action (UI/UX §4). */
  variant?: 'skeleton' | 'spinner'
  /** Announced to screen readers; the visual skeleton carries no text of its own. */
  label?: string
  /** Skeleton only. */
  rows?: number
  className?: string
}

export default function LoadingState({
  variant = 'skeleton',
  label = 'Loading…',
  rows = 5,
  className,
}: LoadingStateProps) {
  return (
    <div
      // `status` + `polite`: a list refreshing mid-task should not interrupt what the
      // user is reading, unlike an alert.
      role="status"
      aria-live="polite"
      aria-busy="true"
      className={className}
      data-testid="loading-state"
    >
      <span className="sr-only">{label}</span>
      {variant === 'spinner' ? <Spinner /> : <Skeleton rows={rows} />}
    </div>
  )
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block size-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600 align-[-2px]"
    />
  )
}

function Skeleton({ rows }: { rows: number }) {
  return (
    <div aria-hidden="true" className="space-y-2">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="h-9 animate-pulse rounded bg-slate-200" />
      ))}
    </div>
  )
}
