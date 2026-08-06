import { useState } from 'react'

import Button from '@/components/ui/Button'
import Modal from '@/components/ui/Modal'
import { isApiError } from '@/lib/api/errors'
import { useCancelBooking } from '@/lib/bookings/mutations'
import type { Booking } from '@/lib/bookings/types'
import { formatSlotTimeLong } from '@/lib/datetime'

export interface CancelBookingButtonProps {
  booking: Booking
  onCancelled?: () => void
  /** Compact label for a table row, where the column header already says "Actions". */
  compact?: boolean
}

/**
 * Rendered only for a `confirmed` booking. The other two states are not "cancel is
 * disabled" but "there is nothing to cancel": `cancelled` would answer `200` unchanged
 * (the endpoint is idempotent) and `completed` answers `409 booking_not_cancellable`.
 * The status badge beside this button already says which — a disabled button explains
 * less than its absence.
 *
 * This is never the authorization boundary: the same scope that made this booking
 * visible is what governs the cancel, so an out-of-scope id is a `404` server-side
 * whatever this renders.
 */
export default function CancelBookingButton({
  booking,
  onCancelled,
  compact = false,
}: CancelBookingButtonProps) {
  const [open, setOpen] = useState(false)
  const cancel = useCancelBooking(booking.booking_id)

  if (booking.status !== 'confirmed') return null

  function close() {
    setOpen(false)
    cancel.reset()
  }

  return (
    <>
      <Button
        variant="secondary"
        onClick={() => {
          setOpen(true)
        }}
      >
        {compact ? 'Cancel' : 'Cancel booking'}
      </Button>

      <Modal
        open={open}
        onClose={close}
        title="Cancel this viewing?"
        footer={
          <>
            <Button variant="ghost" disabled={cancel.isPending} onClick={close}>
              Keep booking
            </Button>
            <Button
              autoFocus
              variant="secondary"
              disabled={cancel.isPending}
              onClick={() => {
                cancel.mutate(undefined, {
                  onSuccess: () => {
                    setOpen(false)
                    onCancelled?.()
                  },
                })
              }}
            >
              {cancel.isPending ? 'Cancelling…' : 'Yes, cancel it'}
            </Button>
          </>
        }
      >
        <p>
          {booking.client_name}&rsquo;s viewing of {booking.property_title} on{' '}
          <span className="font-medium text-slate-900">
            {formatSlotTimeLong(booking.slot_time)}
          </span>
          .
        </p>
        {/* Unlike deactivating a listing, this does not park the record somewhere it can
            be brought back from: the slot returns to the open pool and may be taken by
            somebody else before anyone notices the mistake. */}
        <p className="mt-2">
          The slot is released for other clients to book, and this cannot be undone.
        </p>

        {cancel.isError ? (
          <p
            role="alert"
            className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800"
          >
            {cancelErrorMessage(cancel.error)}
          </p>
        ) : null}
      </Modal>
    </>
  )
}

/**
 * The backend's own `detail.message` is used for the domain rejections it words better
 * than this screen could (`booking_not_cancellable`). A `404` is the one case that needs
 * rewording: it means "not found *or* not yours", and repeating the raw message would
 * imply the booking was deleted.
 */
function cancelErrorMessage(error: unknown): string {
  if (!isApiError(error)) return 'Could not cancel this booking. Please try again.'
  if (error.status === 404) return 'This booking is no longer available to you.'
  return error.message
}
