import { BOOKING_STATUS_LABELS, type BookingStatus } from '@/lib/bookings/types'
import type { EvalRunStatus } from '@/lib/observability/types'
import { PROPERTY_STATUS_LABELS, type PropertyStatus } from '@/lib/properties/types'
import { USER_STATUS_LABELS, type UserStatus } from '@/lib/users/types'

export type BadgeStatus = PropertyStatus | BookingStatus | UserStatus | EvalRunStatus

/**
 * Colors are UI/UX §4's fixed mapping, which defines property and booking statuses in
 * one table precisely so the two screens agree — `active`/`confirmed` green,
 * `sold`/`cancelled` muted red. The label is always rendered alongside, never color
 * alone (§6), so the badge survives a color-blind reader and a greyscale print.
 *
 * The unions overlap on `active` only, where a live listing and an enabled account mean
 * the same thing and read the same green — which is what lets one map serve all three.
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
  // Also outside §4's table. Grey, the same as `draft`: a disabled account is dormant
  // and reversible, not a failure — red would read as an error beside the green rows.
  disabled: 'bg-slate-100 text-slate-700 ring-slate-500/20',
  // An eval run's `failed`, unlike `sold`/`cancelled` above, is a genuine error (the
  // orchestration blew up mid-run — `502 eval_run_failed`), so it gets the saturated red
  // an alert uses rather than the muted "terminal but not wrong" red those two share.
  failed: 'bg-red-100 text-red-800 ring-red-600/20',
}

const LABELS: Record<BadgeStatus, string> = {
  ...PROPERTY_STATUS_LABELS,
  ...BOOKING_STATUS_LABELS,
  ...USER_STATUS_LABELS,
  failed: 'Failed',
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
