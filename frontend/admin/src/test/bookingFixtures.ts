import type { Page } from '@/lib/api/pagination'
import type { Booking } from '@/lib/bookings/types'

/**
 * `slot_time` is `Z`, not `+07:00`: the backend normalizes every datetime to UTC on the
 * way out (`app/models/types.py`). `03:00Z` is 10:00 in the pinned test timezone
 * (Asia/Jakarta, see `vite.config.ts`), which is what the screens must render.
 */
export function buildBooking(overrides: Partial<Booking> = {}): Booking {
  return {
    booking_id: 'booking-1',
    property_id: 'prop-1',
    client_id: 'u-client',
    agent_id: 'u-agent',
    slot_time: '2026-08-08T03:00:00Z',
    availability_slot_id: 'avail-001',
    status: 'confirmed',
    rescheduled_count: 0,
    updated_at: '2026-08-05T02:00:00Z',
    property_title: '2BR Apartment, Kemang',
    client_name: 'Budi Santoso',
    agent_name: 'Siti Rahayu',
    ...overrides,
  }
}

export function buildBookingPage(
  results: Booking[],
  overrides: Partial<Page<Booking>> = {},
): Page<Booking> {
  return {
    results,
    page: 1,
    page_size: 20,
    total: results.length,
    total_pages: 1,
    ...overrides,
  }
}
