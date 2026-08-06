import { Link, useNavigate } from 'react-router'

import StatusBadge from '@/components/ui/StatusBadge'
import { readSort, toggleSort, type SortableField } from '@/lib/bookings/filters'
import type { Booking } from '@/lib/bookings/types'
import { formatSlotTime } from '@/lib/datetime'
import CancelBookingButton from './CancelBookingButton'

export interface BookingTableProps {
  bookings: Booking[]
  sort: string
  onSortChange: (sort: string) => void
  /** An `agent`'s rows all name the same agent — their own — so that column is dropped. */
  showAgent: boolean
}

const CELL = 'px-3 py-2 text-sm text-slate-700'

/**
 * `created_at` is in the backend's sort whitelist but not on `BookingResponse`, so it is
 * not offered here: a column header that reorders rows by a value the table cannot show
 * looks like a bug from the outside.
 */
export default function BookingTable({
  bookings,
  sort,
  onSortChange,
  showAgent,
}: BookingTableProps) {
  const navigate = useNavigate()
  const active = readSort(sort)

  const columns: { key: string; label: string; sortBy?: SortableField }[] = [
    { key: 'property', label: 'Property' },
    { key: 'client', label: 'Client' },
    ...(showAgent ? [{ key: 'agent', label: 'Agent' }] : []),
    { key: 'slot_time', label: 'Slot', sortBy: 'slot_time' },
    { key: 'status', label: 'Status', sortBy: 'status' },
    { key: 'actions', label: 'Actions' },
  ]

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[48rem] border-collapse">
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
          {bookings.map((booking) => (
            <tr
              key={booking.booking_id}
              // Convenience only — the property cell is a real link, so the row is
              // keyboard-reachable without this. The guard covers the cancel button and
              // its `<dialog>`, both of which live inside this row: without it, clicking
              // "Keep booking" would dismiss the modal *and* navigate away.
              onClick={(event) => {
                if (event.target instanceof HTMLElement && event.target.closest('a, button, dialog')) {
                  return
                }
                void navigate(`/bookings/${booking.booking_id}`)
              }}
              className="cursor-pointer hover:bg-slate-50"
            >
              <td className={CELL}>
                <Link
                  to={`/bookings/${booking.booking_id}`}
                  className="font-medium text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                >
                  {booking.property_title}
                </Link>
              </td>
              <td className={CELL}>{booking.client_name}</td>
              {showAgent ? <td className={CELL}>{booking.agent_name}</td> : null}
              <td className={`${CELL} whitespace-nowrap`}>{formatSlotTime(booking.slot_time)}</td>
              <td className={CELL}>
                <StatusBadge status={booking.status} />
              </td>
              <td className={CELL}>
                <CancelBookingButton booking={booking} compact />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ariaSort(direction: 'asc' | 'desc'): 'ascending' | 'descending' {
  return direction === 'asc' ? 'ascending' : 'descending'
}
