import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import CancelBookingButton from '@/components/bookings/CancelBookingButton'
import RescheduleForm from '@/components/bookings/RescheduleForm'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import StatusBadge from '@/components/ui/StatusBadge'
import { isApiError } from '@/lib/api/errors'
import { bookingDetailQueryOptions } from '@/lib/bookings/queries'
import type { Booking } from '@/lib/bookings/types'
import { formatSlotTimeLong } from '@/lib/datetime'

export default function BookingDetailPage() {
  const { bookingId = '' } = useParams()
  const { data: booking, isPending, isError, error, refetch } = useQuery(
    bookingDetailQueryOptions(bookingId),
  )

  if (isPending) {
    return <LoadingState label="Loading booking…" rows={5} />
  }

  if (isError) {
    return (
      <div className="space-y-4">
        <BackLink />
        {isNotFound(error) ? (
          // A 404 here is a settled answer — and deliberately the same one for an
          // unknown id and for somebody else's booking, so this must not word it as
          // "deleted" or as "forbidden". No retry: it would fetch the same 404.
          <ErrorState title="Not found or not accessible" error={error} />
        ) : (
          <ErrorState
            title="Could not load this booking"
            error={error}
            onRetry={() => {
              void refetch()
            }}
          />
        )}
      </div>
    )
  }

  return <BookingDetailView booking={booking} />
}

function BookingDetailView({ booking }: { booking: Booking }) {
  return (
    <div className="space-y-4">
      <BackLink />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-900">{booking.property_title}</h1>
        <StatusBadge status={booking.status} />
      </div>

      <dl className="grid gap-x-6 gap-y-3 rounded-lg border border-slate-200 bg-white p-4 sm:grid-cols-2">
        <Field label="Property">
          <Link
            to={`/properties/${booking.property_id}`}
            className="text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            {booking.property_title}
          </Link>
        </Field>
        <Field label="Slot">{formatSlotTimeLong(booking.slot_time)}</Field>
        <Field label="Client">{booking.client_name}</Field>
        <Field label="Agent">{booking.agent_name}</Field>
        <Field label="Rescheduled">
          {booking.rescheduled_count === 0
            ? 'Never'
            : `${String(booking.rescheduled_count)}${booking.rescheduled_count === 1 ? ' time' : ' times'}`}
        </Field>
        <Field label="Last updated">{formatSlotTimeLong(booking.updated_at)}</Field>
      </dl>

      {/* Both actions are rendered for every booking the caller can see. The scope that
          made it visible (`scoped_booking_query`) is the same one that governs cancel and
          reschedule, so there is no state where a viewer of a booking is not also
          permitted to act on it — the per-booking gate is the booking's own status, not
          the caller's role. */}
      {booking.status === 'confirmed' ? (
        <>
          <div className="flex flex-wrap justify-end gap-2">
            <CancelBookingButton booking={booking} />
          </div>
          <RescheduleForm booking={booking} />
        </>
      ) : (
        <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
          {booking.status === 'cancelled'
            ? 'This viewing was cancelled and its slot released. Booking a new one is a fresh request from the client.'
            : 'This viewing has taken place. It can no longer be moved or cancelled.'}
        </p>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-900">{children}</dd>
    </div>
  )
}

function BackLink() {
  return (
    <Link
      to="/bookings"
      className="inline-block text-sm text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
    >
      ← Back to bookings
    </Link>
  )
}

function isNotFound(error: unknown): boolean {
  return isApiError(error) && error.status === 404
}
