import { queryOptions } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import type { Page } from '@/lib/api/pagination'
import { toQueryParams, type BookingListFilters } from './filters'
import type { Booking } from './types'

export const bookingsKey = ['bookings'] as const

export function bookingListQueryOptions(filters: BookingListFilters) {
  return queryOptions({
    queryKey: [...bookingsKey, 'list', filters] as const,
    queryFn: ({ signal }) =>
      apiClient.get<Page<Booking>>('/bookings', { query: toQueryParams(filters), signal }),
    placeholderData: (previous) => previous,
  })
}

/**
 * A booking's list row and its detail response are the same type, so unlike the
 * property screens this fetch could in principle be skipped in favour of the cached
 * row. It is not: arriving by direct URL, by refresh, or after the row has aged out has
 * to work identically, and a detail screen that renders a stale slot time is the one
 * thing that would make an agent turn up at the wrong hour.
 */
export function bookingDetailQueryOptions(bookingId: string) {
  return queryOptions({
    queryKey: [...bookingsKey, 'detail', bookingId] as const,
    queryFn: ({ signal }) => apiClient.get<Booking>(`/bookings/${bookingId}`, { signal }),
  })
}
