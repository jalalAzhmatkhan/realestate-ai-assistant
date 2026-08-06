import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import { bookingDetailQueryOptions, bookingsKey } from './queries'
import type { Booking, BookingReschedule, RescheduleRequest } from './types'

/**
 * Cancel is **idempotent server-side**: an already-cancelled booking answers `200`
 * unchanged rather than an error, so a double click or a stale tab settles into the
 * same state instead of surfacing a failure the user cannot act on
 * (`app/booking/slots.py`, `cancel_booking`).
 */
export function useCancelBooking(bookingId: string) {
  const queryClient = useQueryClient()

  return useMutation<Booking, Error, void>({
    mutationFn: () => apiClient.post<Booking>(`/bookings/${bookingId}/cancel`),
    onSuccess: (booking) => {
      settle(queryClient, booking)
    },
  })
}

export function useRescheduleBooking(bookingId: string) {
  const queryClient = useQueryClient()

  return useMutation<BookingReschedule, Error, RescheduleRequest>({
    mutationFn: (payload) =>
      apiClient.post<BookingReschedule>(`/bookings/${bookingId}/reschedule`, payload),
    onSuccess: (booking) => {
      settle(queryClient, booking)
    },
  })
}

/**
 * Both writes answer with the full booking, so the detail cache is seeded from the
 * response. The lists are invalidated rather than patched: a status change can drop a
 * row out of a `status=confirmed` filter, and a new `slot_time` can move it across a
 * date range or a page boundary — neither is something a client-side patch can work out.
 *
 * `previous_slot_time` is stripped from what lands in the detail cache: the extra field
 * only means anything on the response to a move, and leaving it there would make the
 * cached record differ from what a refetch of `GET /bookings/{id}` returns.
 */
function settle(queryClient: QueryClient, booking: Booking): void {
  const { booking_id: bookingId } = booking
  queryClient.setQueryData(bookingDetailQueryOptions(bookingId).queryKey, toBooking(booking))
  void queryClient.invalidateQueries({ queryKey: [...bookingsKey, 'list'] })
}

function toBooking(booking: Booking): Booking {
  return {
    booking_id: booking.booking_id,
    property_id: booking.property_id,
    client_id: booking.client_id,
    agent_id: booking.agent_id,
    slot_time: booking.slot_time,
    availability_slot_id: booking.availability_slot_id,
    status: booking.status,
    rescheduled_count: booking.rescheduled_count,
    updated_at: booking.updated_at,
    property_title: booking.property_title,
    client_name: booking.client_name,
    agent_name: booking.agent_name,
  }
}
