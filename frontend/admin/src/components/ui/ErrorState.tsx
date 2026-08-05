import { isApiError } from '@/lib/api/errors'
import Button from './Button'

export interface ErrorStateProps {
  title?: string
  /** The thrown value, unnarrowed — screens pass TanStack Query's `error` straight through. */
  error?: unknown
  onRetry?: () => void
  retryLabel?: string
}

const GENERIC_MESSAGE = 'Something went wrong on our side. Please try again.'

export default function ErrorState({
  title = 'Could not load this',
  error,
  onRetry,
  retryLabel = 'Try again',
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-6 py-10 text-center"
      data-testid="error-state"
    >
      <p className="text-sm font-medium text-red-900">{title}</p>
      <p className="max-w-md text-sm text-red-800">{toDisplayMessage(error)}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </div>
  )
}

/**
 * Only an `ApiError`'s message reaches the screen, because only that string came from
 * the backend's human-facing `detail.message` and is meant to be read. Anything else —
 * a TypeError from a bad render, a bundler chunk-load failure — carries internal
 * details, so it degrades to a fixed sentence (frontend design doc §8: never a raw
 * stack trace or JSON dump).
 */
function toDisplayMessage(error: unknown): string {
  return isApiError(error) ? error.message : GENERIC_MESSAGE
}
