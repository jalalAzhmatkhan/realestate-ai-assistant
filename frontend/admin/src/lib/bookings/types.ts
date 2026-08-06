/**
 * Mirrors `app/schemas/booking.py` and the `BookingStatus` union in
 * `app/models/booking.py`. Wire names throughout, for the same reason as
 * `lib/properties/types.ts`.
 */

export const BOOKING_STATUSES = ['confirmed', 'cancelled', 'completed'] as const

export type BookingStatus = (typeof BOOKING_STATUSES)[number]

export const BOOKING_STATUS_LABELS: Record<BookingStatus, string> = {
  confirmed: 'Confirmed',
  cancelled: 'Cancelled',
  completed: 'Completed',
}

/**
 * The item type of `GET /bookings` **and** the response of `GET /bookings/{id}`,
 * `POST /bookings/{id}/cancel` and every write — bookings have no summary/detail split
 * (backend README, "Bookings have no such split"). So unlike a property, a detail
 * screen may legitimately render from a cached list row.
 *
 * `property_title`/`client_name`/`agent_name` are read-time denormalizations added for
 * these screens: `GET /users/{id}` is admin-only, so an `agent` has no other way to
 * resolve the ids.
 */
export interface Booking {
  booking_id: string
  property_id: string
  client_id: string
  agent_id: string
  /** ISO-8601 UTC instant — see `lib/datetime.ts` before formatting it. */
  slot_time: string
  availability_slot_id: string
  status: BookingStatus
  rescheduled_count: number
  updated_at: string
  property_title: string
  client_name: string
  agent_name: string
}

/** `POST /bookings/{id}/reschedule` — a `Booking` plus where it moved from. */
export interface BookingReschedule extends Booking {
  previous_slot_time: string
}

export interface RescheduleRequest {
  /** ISO-8601 instant. Must match an open slot in the agent's availability exactly. */
  requested_slot_time: string
  reason?: string
}

export const MAX_RESCHEDULE_REASON_LENGTH = 500

/**
 * `409 slot_unavailable` and `409 booking_slot_conflict` both carry this key. It is the
 * one error payload these screens read structurally rather than just displaying — see
 * `RescheduleForm`.
 */
export const CONFLICT_CODES_WITH_ALTERNATIVES = ['slot_unavailable', 'booking_slot_conflict'] as const
