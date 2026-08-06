import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router'

import Button from '@/components/ui/Button'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import Pagination from '@/components/ui/Pagination'
import CreateUserModal from '@/components/users/CreateUserModal'
import UserFilters from '@/components/users/UserFilters'
import UserTable from '@/components/users/UserTable'
import { useCurrentUser } from '@/lib/auth/useCurrentUser'
import {
  emptyFilters,
  hasActiveFilters,
  parseFilters,
  toSearchParams,
  type UserListFilters,
} from '@/lib/users/filters'
import { userListQueryOptions } from '@/lib/users/queries'

/**
 * Admin-only. The router's `<RoleGate>` keeps everyone else off this path, but that is
 * UX: `GET /users` answers `403 forbidden` to a non-admin whatever this file renders
 * (`app/api/users.py`). Nothing here filters an unscoped list client-side — the backend
 * returns exactly the rows the caller may see.
 *
 * Users are disabled, never deleted; there is no `DELETE /users/{id}` to call, because
 * removing a user would orphan their bookings.
 */
export default function UsersPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [creating, setCreating] = useState(false)
  const { data: currentUser } = useCurrentUser()
  const filters = parseFilters(searchParams)

  const { data, isPending, isError, error, isPlaceholderData, refetch } = useQuery(
    userListQueryOptions(filters),
  )

  function applyFilters(next: UserListFilters) {
    // Page 1 on every filter change, as on the other two lists.
    setSearchParams(toSearchParams({ ...next, page: 1 }))
  }

  function clearFilters() {
    setSearchParams(toSearchParams(emptyFilters()))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-900">Users</h1>
        <Button
          onClick={() => {
            setCreating(true)
          }}
        >
          + Add user
        </Button>
      </div>

      <UserFilters filters={filters} onChange={applyFilters} />

      {isPending ? (
        <LoadingState label="Loading users…" rows={6} />
      ) : isError ? (
        <ErrorState
          title="Could not load users"
          error={error}
          onRetry={() => {
            void refetch()
          }}
        />
      ) : data.results.length === 0 ? (
        <UsersEmptyState
          filtered={hasActiveFilters(filters)}
          onClearFilters={clearFilters}
          onAdd={() => {
            setCreating(true)
          }}
        />
      ) : (
        <>
          <UserTable
            users={data.results}
            sort={filters.sort}
            currentUserId={currentUser?.id}
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

      <CreateUserModal
        open={creating}
        onClose={() => {
          setCreating(false)
        }}
        // The mutation already invalidates the list, so closing is all that is left. The
        // new row is not navigated to — there is no user detail screen, and it appears in
        // place under whatever filters are active.
        onCreated={() => {
          setCreating(false)
        }}
      />
    </div>
  )
}

/**
 * Two distinct empty states, as on the other lists. The unfiltered one barely happens —
 * the admin reading it is themselves a row — but it is the honest answer if it does, and
 * "no users match your filters" in front of an admin who set none would point at a
 * control that is not on screen.
 */
function UsersEmptyState({
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
        title="No users match your filters"
        description="Try a different search, or clear the filters to see everyone."
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
      title="No users yet"
      description="Accounts are provisioned here — there is no self-service signup."
      action={<Button onClick={onAdd}>Add a user</Button>}
    />
  )
}
