import { BOOKING_STATUS_LABELS, type BookingStatus } from '@/lib/bookings/types'
import { PROPERTY_STATUS_LABELS, type PropertyStatus } from '@/lib/properties/types'

export type BadgeStatus = PropertyStatus | BookingStatus

/**
 * Colors are UI/UX §4's fixed mapping, which defines property and booking statuses in
 * one table precisely so the two screens agree — `active`/`confirmed` green,
 * `sold`/`cancelled` muted red. The label is always rendered alongside, never color
 * alone (§6), so the badge survives a color-blind reader and a greyscale print.
 *
 * The two status unions are disjoint, which is what lets one map serve both.
 */
const STYLES: Record<BadgeStatus, string> = {
  active: 'bg-green-100 text-green-800 ring-green-600/20',
  confirmed: 'bg-green-100 text-green-800 ring-green-600/20',
  draft: 'bg-slate-100 text-slate-700 ring-slate-500/20',
  under_offer: 'bg-amber-100 text-amber-800 ring-amber-600/20',
  // Muted red: terminal, not an error.
  sold: 'bg-red-50 text-red-800 ring-red-600/20',
  cancelled: 'bg-red-50 text-red-800 ring-red-600/20',
  // Not in §4's table, which predates the booking contract. Blue rather than the
  // terminal red `cancelled` uses: a completed viewing is a success, and painting the
  // two the same would make a scanned column read as if every past booking fell through.
  completed: 'bg-blue-50 text-blue-800 ring-blue-600/20',
}

const LABELS: Record<BadgeStatus, string> = {
  ...PROPERTY_STATUS_LABELS,
  ...BOOKING_STATUS_LABELS,
}

export default function StatusBadge({ status }: { status: BadgeStatus }) {
  return (
    <span
      data-testid="status-badge"
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STYLES[status]}`}
    >
      {LABELS[status]}
    </span>
  )
}
