import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router'

import PropertyFilters from '@/components/properties/PropertyFilters'
import PropertyTable from '@/components/properties/PropertyTable'
import Button from '@/components/ui/Button'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import Pagination from '@/components/ui/Pagination'
import RoleGate from '@/components/ui/RoleGate'
import {
  emptyFilters,
  hasActiveFilters,
  parseFilters,
  toSearchParams,
  type PropertyListFilters,
} from '@/lib/properties/filters'
import { propertyListQueryOptions } from '@/lib/properties/queries'

export default function PropertiesPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = parseFilters(searchParams)

  const { data, isPending, isError, error, isPlaceholderData, refetch } = useQuery(
    propertyListQueryOptions(filters),
  )

  function applyFilters(next: PropertyListFilters) {
    // Page 1 on every filter change: staying on page 4 of a narrower result set lands on
    // an empty page the user has no reason to expect.
    setSearchParams(toSearchParams({ ...next, page: 1 }))
  }

  function goToPage(page: number) {
    setSearchParams(toSearchParams({ ...filters, page }))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-900">Properties</h1>
        {/* A client never reaches this shell at all (AppLayout's staff gate), so this
            gate is belt-and-braces; the backend answers 403 to a create either way. */}
        <RoleGate allow={['admin', 'agent']}>
          <Button
            onClick={() => {
              void navigate('/properties/new')
            }}
          >
            + Add property
          </Button>
        </RoleGate>
      </div>

      <PropertyFilters filters={filters} onChange={applyFilters} />

      {isPending ? (
        <LoadingState label="Loading properties…" rows={6} />
      ) : isError ? (
        <ErrorState
          title="Could not load properties"
          error={error}
          onRetry={() => {
            void refetch()
          }}
        />
      ) : data.results.length === 0 ? (
        <PropertiesEmptyState
          filtered={hasActiveFilters(filters)}
          onClearFilters={() => {
            setSearchParams(toSearchParams(emptyFilters()))
          }}
          onAdd={() => {
            void navigate('/properties/new')
          }}
        />
      ) : (
        <>
          <PropertyTable
            properties={data.results}
            sort={filters.sort}
            onSortChange={(sort) => {
              setSearchParams(toSearchParams({ ...filters, sort, page: 1 }))
            }}
          />
          <Pagination meta={data} onPageChange={goToPage} isPending={isPlaceholderData} />
        </>
      )}
    </div>
  )
}

/**
 * Two distinct empty states (UI/UX §5.2). "No properties yet" in front of someone whose
 * filters simply exclude everything is actively misleading — it reads as an empty
 * database and hides the one control that would fix it.
 */
function PropertiesEmptyState({
  filtered,
  onClearFilters,
  onAdd,
}: {
  filtered: boolean
  onClearFilters: () => void
  onAdd: () => void
}) {
  if (filtered) {
    return (
      <EmptyState
        title="No properties match your filters"
        description="Try a broader search, or clear the filters to see everything you have access to."
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
      title="No properties yet"
      description="Listings you create will appear here."
      action={
        <RoleGate allow={['admin', 'agent']}>
          <Button onClick={onAdd}>Add a property</Button>
        </RoleGate>
      }
    />
  )
}
