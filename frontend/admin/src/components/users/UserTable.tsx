import StatusBadge from '@/components/ui/StatusBadge'
import { formatDate } from '@/lib/datetime'
import { readSort, toggleSort, type SortableField } from '@/lib/users/filters'
import type { User } from '@/lib/users/types'
import SelfAccountNote from './SelfAccountNote'
import UserRoleSelect from './UserRoleSelect'
import UserStatusToggle from './UserStatusToggle'

export interface UserTableProps {
  users: User[]
  sort: string
  onSortChange: (sort: string) => void
  /** Drives the self-lockout guards; absent only while `/auth/me` is still in flight. */
  currentUserId: string | undefined
}

const CELL = 'px-3 py-2 text-sm text-slate-700'

/**
 * No row click-through: UI/UX §5.6's wireframe has no user detail screen, and every
 * action a user row offers is already in the row. A row that navigated somewhere would
 * also fight the two controls inside it, as the bookings table's click guard shows.
 *
 * "Created" is shown rather than only sorted on: `created_at` is in the backend's sort
 * whitelist, and offering a sort by a value the table cannot display reads as a bug.
 */
export default function UserTable({ users, sort, onSortChange, currentUserId }: UserTableProps) {
  const active = readSort(sort)

  const columns: { key: string; label: string; sortBy?: SortableField }[] = [
    { key: 'name', label: 'Name', sortBy: 'name' },
    { key: 'email', label: 'Email', sortBy: 'email' },
    { key: 'role', label: 'Role', sortBy: 'role' },
    { key: 'status', label: 'Status' },
    { key: 'created_at', label: 'Created', sortBy: 'created_at' },
    { key: 'actions', label: 'Actions' },
  ]

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[52rem] border-collapse">
        <thead className="border-b border-slate-200 bg-slate-50">
          <tr>
            {columns.map((column) => {
              const sortBy = column.sortBy
              const isActive = sortBy === active.field
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={sortBy ? (isActive ? ariaSort(active.direction) : 'none') : undefined}
                  className="px-3 py-2 text-left text-xs font-semibold tracking-wide text-slate-600 uppercase"
                >
                  {sortBy ? (
                    <button
                      type="button"
                      onClick={() => {
                        onSortChange(toggleSort(sort, sortBy))
                      }}
                      className="inline-flex items-center gap-1 rounded uppercase hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                    >
                      {column.label}
                      <span aria-hidden="true" className="text-slate-400">
                        {isActive ? (active.direction === 'asc' ? '▲' : '▼') : '↕'}
                      </span>
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              )
            })}
          </tr>
        </thead>

        <tbody className="divide-y divide-slate-100">
          {users.map((user) => {
            const isSelf = user.id === currentUserId
            return (
              <tr key={user.id} className={isSelf ? 'bg-blue-50/40' : undefined}>
                <td className={`${CELL} font-medium text-slate-900`}>
                  {user.name}
                  {isSelf ? (
                    <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-800">
                      You
                    </span>
                  ) : null}
                </td>
                <td className={CELL}>{user.email}</td>
                <td className={CELL}>
                  <UserRoleSelect user={user} isSelf={isSelf} />
                </td>
                <td className={CELL}>
                  <StatusBadge status={user.status} />
                </td>
                <td className={`${CELL} whitespace-nowrap`}>{formatDate(user.created_at)}</td>
                <td className={CELL}>
                  <UserStatusToggle user={user} isSelf={isSelf} />
                  {isSelf ? <SelfAccountNote /> : null}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ariaSort(direction: 'asc' | 'desc'): 'ascending' | 'descending' {
  return direction === 'asc' ? 'ascending' : 'descending'
}
