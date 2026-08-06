import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'

import BookingFilters from '@/components/bookings/BookingFilters'
import BookingTable from '@/components/bookings/BookingTable'
import Button from '@/components/ui/Button'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import Pagination from '@/components/ui/Pagination'
import { isApiError } from '@/lib/api/errors'
import { useCurrentUser } from '@/lib/auth/useCurrentUser'
import {
  emptyFilters,
  hasActiveFilters,
  parseFilters,
  toSearchParams,
  type BookingListFilters,
} from '@/lib/bookings/filters'
import { bookingListQueryOptions } from '@/lib/bookings/queries'

export default function BookingsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: user } = useCurrentUser()
  const filters = parseFilters(searchParams)

  const { data, isPending, isError, error, isPlaceholderData, refetch } = useQuery(
    bookingListQueryOptions(filters),
  )

  function applyFilters(next: BookingListFilters) {
    // Page 1 on every filter change, for the same reason as the properties list.
    setSearchParams(toSearchParams({ ...next, page: 1 }))
  }

  function clearFilters() {
    setSearchParams(toSearchParams(emptyFilters()))
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Bookings</h1>
        {/* The scoping is server-side and otherwise invisible. Saying so beats an agent
            wondering why a colleague's viewing is missing from a list that presents
            itself as every booking. */}
        {user?.role === 'agent' ? (
          <p className="mt-1 text-sm text-slate-500">Viewings you are conducting.</p>
        ) : null}
      </div>

      <BookingFilters filters={filters} onChange={applyFilters} />

      {isPending ? (
        <LoadingState label="Loading bookings…" rows={6} />
      ) : isError ? (
        <BookingsErrorState
          error={error}
          onClearFilters={clearFilters}
          onRetry={() => {
            void refetch()
          }}
        />
      ) : data.results.length === 0 ? (
        <BookingsEmptyState filtered={hasActiveFilters(filters)} onClearFilters={clearFilters} />
      ) : (
        <>
          <BookingTable
            bookings={data.results}
            sort={filters.sort}
            showAgent={user?.role === 'admin'}
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

/**
 * Two distinct empty states, as on the properties list: "no bookings yet" in front of
 * someone whose date range simply excludes everything reads as an empty database and
 * hides the one control that would fix it.
 *
 * Neither offers a "create booking" action — viewings are booked through the assistant
 * or by a client, and this dashboard exposes no create endpoint for them.
 */
function BookingsEmptyState({
  filtered,
  onClearFilters,
}: {
  filtered: boolean
  onClearFilters: () => void
}) {
  if (filtered) {
    return (
      <EmptyState
        title="No bookings match your filters"
        description="Try a wider date range, or clear the filters to see everything you have access to."
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
      title="No bookings yet"
      description="Viewings booked through the assistant will appear here."
    />
  )
}

/**
 * `422 invalid_date_range` is the one list failure the user caused and can fix, so it
 * gets the action that fixes it. "Try again" would re-send the same impossible range —
 * the backend has already said `date_from` is later than `date_to`.
 */
function BookingsErrorState({
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

  return <ErrorState title="Could not load bookings" error={error} onRetry={onRetry} />
}
