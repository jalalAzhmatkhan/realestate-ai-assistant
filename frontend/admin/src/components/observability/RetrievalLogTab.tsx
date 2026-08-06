import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'

import Button from '@/components/ui/Button'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import Pagination from '@/components/ui/Pagination'
import { isApiError } from '@/lib/api/errors'
import { retrievalLogsQueryOptions } from '@/lib/observability/queries'
import {
  emptyFilters,
  hasActiveFilters,
  parseFilters,
  toSearchParams,
  type RetrievalLogFilters as Filters,
} from '@/lib/observability/retrievalLogFilters'
import RetrievalLogFilters from './RetrievalLogFilters'
import RetrievalLogTable from './RetrievalLogTable'

/**
 * Every FAQ retrieval the agent made, one row per `search_faq` call, with what it
 * retrieved shown inline (there is no `GET /retrieval-logs/{id}` to drill into —
 * `results[]` already travels on the list item).
 */
export default function RetrievalLogTab() {
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = parseFilters(searchParams)

  const { data, isPending, isError, error, isPlaceholderData, refetch } = useQuery(
    retrievalLogsQueryOptions(filters),
  )

  function applyFilters(next: Filters) {
    setSearchParams(toSearchParams({ ...next, page: 1 }))
  }

  function clearFilters() {
    setSearchParams(toSearchParams(emptyFilters()))
  }

  return (
    <div className="space-y-4">
      <RetrievalLogFilters filters={filters} onChange={applyFilters} />

      {isPending ? (
        <LoadingState label="Loading retrieval log…" rows={6} />
      ) : isError ? (
        <RetrievalLogErrorState error={error} onClearFilters={clearFilters} onRetry={() => void refetch()} />
      ) : data.results.length === 0 ? (
        <RetrievalLogEmptyState filtered={hasActiveFilters(filters)} onClearFilters={clearFilters} />
      ) : (
        <>
          <RetrievalLogTable
            logs={data.results}
            sort={filters.sort}
            onSortChange={(sort) => {
              setSearchParams(toSearchParams({ ...filters, sort, page: 1 }))
            }}
          />
          <Pagination
            meta={data}
            isPending={isPlaceholderData}
            onPageChange={(page) => {
              setSearchParams(toSearchParams({ ...filters, page }))
            }}
          />
        </>
      )}
    </div>
  )
}

function RetrievalLogEmptyState({
  filtered,
  onClearFilters,
}: {
  filtered: boolean
  onClearFilters: () => void
}) {
  if (filtered) {
    return (
      <EmptyState
        title="No retrievals match your filters"
        description="Try a wider date range or a different search, or clear the filters to see everything."
        action={
          <Button variant="secondary" onClick={onClearFilters}>
            Clear filters
          </Button>
        }
      />
    )
  }

  return (
    <EmptyState
      title="No retrievals logged yet"
      description="Every FAQ search the agent makes through search_faq will appear here."
    />
  )
}

/** `422 invalid_date_range` is the one failure the user caused and can fix. */
function RetrievalLogErrorState({
  error,
  onClearFilters,
  onRetry,
}: {
  error: unknown
  onClearFilters: () => void
  onRetry: () => void
}) {
  if (isApiError(error) && error.code === 'invalid_date_range') {
    return (
      <ErrorState
        title="That date range cannot be searched"
        error={error}
        retryLabel="Clear filters"
        onRetry={onClearFilters}
      />
    )
  }

  return <ErrorState title="Could not load the retrieval log" error={error} onRetry={onRetry} />
}
