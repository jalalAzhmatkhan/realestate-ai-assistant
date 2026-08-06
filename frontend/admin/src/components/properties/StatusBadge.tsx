import { PROPERTY_STATUS_LABELS, type PropertyStatus } from '@/lib/properties/types'

/**
 * Colors are UI/UX §4's fixed mapping. The label is always rendered alongside, never
 * color alone (§6) — the badge has to survive a color-blind reader and a greyscale print.
 */
const STYLES: Record<PropertyStatus, string> = {
  active: 'bg-green-100 text-green-800 ring-green-600/20',
  draft: 'bg-slate-100 text-slate-700 ring-slate-500/20',
  under_offer: 'bg-amber-100 text-amber-800 ring-amber-600/20',
  // Muted red: `sold` is terminal, not an error.
  sold: 'bg-red-50 text-red-800 ring-red-600/20',
}

export default function StatusBadge({ status }: { status: PropertyStatus }) {
  return (
    <span
      data-testid="status-badge"
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STYLES[status]}`}
    >
      {PROPERTY_STATUS_LABELS[status]}
    </span>
  )
}
