import { useEffect, useState, type FormEvent } from 'react'

import Button from '@/components/ui/Button'
import SuggestedAlternatives, { type AlternativeSlot } from '@/components/ui/SuggestedAlternatives'
import { isApiError } from '@/lib/api/errors'
import { useRescheduleBooking } from '@/lib/bookings/mutations'
import {
  MAX_RESCHEDULE_REASON_LENGTH,
  type Booking,
  type RescheduleRequest,
} from '@/lib/bookings/types'
import { formatSlotTimeLong, fromDateTimeLocalValue, toDateTimeLocalValue } from '@/lib/datetime'

const INPUT_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 ' +
  'focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-blue-600'

/**
 * A viewing can only be moved onto a slot that already exists in the agent's
 * availability, and this screen has no endpoint that enumerates those slots. That is
 * not a gap to work around client-side: the two `409`s carry up to three
 * `suggested_alternatives`, so a wrong guess answers with the real openings rather than
 * a dead end. The free datetime input plus that response *is* the slot picker.
 */
export default function RescheduleForm({ booking }: { booking: Booking }) {
  const [slot, setSlot] = useState(() => toDateTimeLocalValue(booking.slot_time))
  const [reason, setReason] = useState('')
  const [emptySlot, setEmptySlot] = useState(false)
  /** Which alternative is mid-submit, so its own button shows the progress. */
  const [pendingSlotId, setPendingSlotId] = useState<string | null>(null)

  const reschedule = useRescheduleBooking(booking.booking_id)

  // Re-syncs after a successful move, and after anything else that changes the booking
  // underneath this form — otherwise the input still holds the slot it just left, and
  // resubmitting would report `slot_unchanged` against a time that did change.
  useEffect(() => {
    setSlot(toDateTimeLocalValue(booking.slot_time))
  }, [booking.slot_time])

  /**
   * `reschedule.error` is cleared by TanStack Query the instant a retry's `mutate` call
   * starts, which is exactly when picking an alternative fires one — so deriving the
   * panel straight from `reschedule.error` made it unmount mid-submit, taking
   * `pendingSlotId`'s "…" progress indicator and the disabled-other-options state with
   * it. Frozen here instead: only updates once the mutation has settled, so the panel
   * (and its pending affordance) stays mounted for the retry it was itself the cause of.
   */
  const [frozenConflict, setFrozenConflict] = useState<{
    alternatives: AlternativeSlot[]
    message: string
  } | null>(null)

  useEffect(() => {
    if (reschedule.isPending) return
    const next = readAlternatives(reschedule.error)
    setFrozenConflict(next ? { alternatives: next, message: conflictMessage(reschedule.error) } : null)
  }, [reschedule.error, reschedule.isPending])

  function submit(requestedSlotTime: string, slotId: string | null) {
    setEmptySlot(false)
    setPendingSlotId(slotId)
    const trimmed = reason.trim()
    const payload: RescheduleRequest = trimmed
      ? { requested_slot_time: requestedSlotTime, reason: trimmed }
      : { requested_slot_time: requestedSlotTime }

    reschedule.mutate(payload, {
      onSettled: () => {
        setPendingSlotId(null)
      },
    })
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const requested = fromDateTimeLocalValue(slot)
    if (!requested) {
      setEmptySlot(true)
      return
    }
    submit(requested, null)
  }

  return (
    <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-base font-semibold text-slate-900">Reschedule</h2>

      <form onSubmit={handleSubmit} aria-label="Reschedule booking" className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <label htmlFor="reschedule-slot" className="block text-sm font-medium text-slate-700">
              New date and time
            </label>
            <input
              id="reschedule-slot"
              type="datetime-local"
              value={slot}
              // Deliberately not `required`: the browser would block submit behind a
              // native bubble, which is neither styled nor announced like the rest of
              // this app's validation (UI/UX §5.3, errors render inline).
              aria-invalid={emptySlot || undefined}
              aria-describedby="reschedule-slot-hint"
              onChange={(event) => {
                setSlot(event.target.value)
              }}
              className={INPUT_CLASS}
            />
            <p id="reschedule-slot-hint" className="text-xs text-slate-500">
              Must match an open slot in {booking.agent_name}&rsquo;s availability. If it does
              not, the nearest open times are offered below.
            </p>
            {emptySlot ? (
              <p role="alert" className="text-sm text-red-800">
                Pick a date and time to move this viewing to.
              </p>
            ) : null}
          </div>

          <div className="space-y-1">
            <label htmlFor="reschedule-reason" className="block text-sm font-medium text-slate-700">
              Reason <span className="font-normal text-slate-500">(optional)</span>
            </label>
            <textarea
              id="reschedule-reason"
              value={reason}
              rows={3}
              maxLength={MAX_RESCHEDULE_REASON_LENGTH}
              placeholder="Included in the notification sent to the client."
              onChange={(event) => {
                setReason(event.target.value)
              }}
              className={INPUT_CLASS}
            />
          </div>
        </div>

        <Button type="submit" disabled={reschedule.isPending}>
          {reschedule.isPending && pendingSlotId === null ? 'Moving…' : 'Reschedule'}
        </Button>
      </form>

      {/* The named requirement of this screen: a conflict is a set of choices, not a
          message. Both codes carry the openings the backend already looked up, so the
          recovery is one click rather than a re-guess. Rendered from the frozen state
          (not `reschedule.error` directly) so picking an alternative doesn't unmount
          this panel out from under its own in-flight retry. */}
      {frozenConflict ? (
        <SuggestedAlternatives
          alternatives={frozenConflict.alternatives}
          message={frozenConflict.message}
          pendingSlotId={pendingSlotId}
          disabled={reschedule.isPending && pendingSlotId === null}
          onSelect={(alternative) => {
            submit(alternative.slot_time, alternative.availability_slot_id)
          }}
        />
      ) : reschedule.isError ? (
        <p
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {rescheduleErrorMessage(reschedule.error)}
        </p>
      ) : null}

      {reschedule.isSuccess && !reschedule.isPending ? (
        <p
          role="status"
          className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800"
        >
          Moved from {formatSlotTimeLong(reschedule.data.previous_slot_time)} to{' '}
          {formatSlotTimeLong(reschedule.data.slot_time)}.
        </p>
      ) : null}
    </section>
  )
}

/**
 * `null` for anything that is not one of the two conflicts that carry alternatives — an
 * empty array is a meaningful answer of its own ("nothing nearby either"), which
 * `<SuggestedAlternatives>` words, so it must not collapse into "no conflict".
 */
function readAlternatives(error: unknown): AlternativeSlot[] | null {
  if (!isApiError(error)) return null
  if (error.code !== 'slot_unavailable' && error.code !== 'booking_slot_conflict') return null

  const raw = error.details.suggested_alternatives
  if (!Array.isArray(raw)) return []
  return raw.filter(isAlternativeSlot)
}

/** Why the requested time was refused — `slot_unavailable` and `booking_slot_conflict`
 * are distinct conditions the backend words differently, and the difference matters
 * ("not in the availability at all" versus "already booked"). */
function conflictMessage(error: unknown): string {
  return isApiError(error) ? error.message : 'That time is not available.'
}

function isAlternativeSlot(value: unknown): value is AlternativeSlot {
  if (typeof value !== 'object' || value === null) return false
  const slot = value as Record<string, unknown>
  return typeof slot.availability_slot_id === 'string' && typeof slot.slot_time === 'string'
}

/**
 * Every rejection here is a domain error the backend already words for a person
 * (`slot_time_in_past`, `slot_unchanged`, `booking_not_reschedulable`,
 * `property_not_bookable`), so its message is passed through. The `404` is reworded for
 * the same reason as in the cancel path: it means "not found *or* not yours".
 */
function rescheduleErrorMessage(error: unknown): string {
  if (!isApiError(error)) return 'Could not reschedule this booking. Please try again.'
  if (error.status === 404) return 'This booking is no longer available to you.'
  if (error.isValidationError) return error.fieldErrors.map((field) => field.message).join(' ')
  return error.message
}
