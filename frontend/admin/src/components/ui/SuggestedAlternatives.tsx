import Button from './Button'

/**
 * One entry of the `suggested_alternatives` array the backend attaches to a `409`
 * (`slot_unavailable` / `booking_slot_conflict`) — identical shape from both
 * `schedule_viewing` and `POST /bookings/{id}/reschedule`, which is why this renders
 * for both (backend README; frontend design doc §8).
 */
export interface AlternativeSlot {
  availability_slot_id: string
  /** ISO-8601 with the listing's own UTC offset. */
  slot_time: string
}

export interface SuggestedAlternativesProps {
  alternatives: readonly AlternativeSlot[]
  onSelect: (slot: AlternativeSlot) => void
  /** Why the original slot failed — the backend's `detail.message` belongs here. */
  message?: string
  /** `availability_slot_id` currently being submitted, if any. */
  pendingSlotId?: string | null
  disabled?: boolean
}

export default function SuggestedAlternatives({
  alternatives,
  onSelect,
  message = 'That time is no longer available.',
  pendingSlotId = null,
  disabled = false,
}: SuggestedAlternativesProps) {
  return (
    <div
      role="alert"
      className="rounded-md border border-amber-200 bg-amber-50 p-4"
      data-testid="suggested-alternatives"
    >
      <p className="text-sm font-medium text-amber-900">{message}</p>

      {alternatives.length === 0 ? (
        <p className="mt-1 text-sm text-amber-800">
          No nearby times are open either — pick another date.
        </p>
      ) : (
        <>
          <p className="mt-1 text-sm text-amber-800">Pick one of these instead:</p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {alternatives.map((slot) => (
              <li key={slot.availability_slot_id}>
                <Button
                  variant="secondary"
                  disabled={disabled || pendingSlotId !== null}
                  onClick={() => {
                    onSelect(slot)
                  }}
                >
                  {formatSlotTime(slot.slot_time)}
                  {pendingSlotId === slot.availability_slot_id ? ' …' : ''}
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

const DATE_FORMAT = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  timeZone: 'UTC',
})

/**
 * Renders the wall-clock time as written in the payload, deliberately *not* converted
 * to the viewer's timezone: the offset in `slot_time` is the listing's local time, and
 * an agent in another timezone reading "13:00" for a 10:00 viewing would be a booking
 * error, not a display quirk. Parsing the components out and formatting as UTC is what
 * pins the displayed value to the string.
 */
function formatSlotTime(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  if (!match) return iso

  const [, year, month, day, hour, minute] = match
  const wallClock = new Date(`${year}-${month}-${day}T${hour}:${minute}:00Z`)
  if (Number.isNaN(wallClock.getTime())) return iso

  return `${DATE_FORMAT.format(wallClock)} — ${hour}:${minute}`
}
