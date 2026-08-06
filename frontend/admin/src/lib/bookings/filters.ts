import type { QueryParams } from '@/lib/api/client'
import { dayBoundaryToIso } from '@/lib/datetime'
import { BOOKING_STATUSES, type BookingStatus } from './types'

/**
 * URL-held filter state, for the same reason as the properties list: a filtered view is
 * linkable, and clicking into a booking and pressing Back returns the same page rather
 * than a reset list.
 *
 * `date_from`/`date_to` are held as plain `YYYY-MM-DD` — what the date inputs carry and
 * what keeps the URL readable. The conversion to the instants the backend compares
 * against `slot_time` happens once, in `toQueryParams`.
 */
export interface BookingListFilters {
  property_id: string
  /** Admin-only in the UI; an `agent`'s list is already scoped to themselves server-side. */
  agent_id: string
  status: BookingStatus[]
  date_from: string
  date_to: string
  page: number
  sort: string
}

/** Matches `DEFAULT_BOOKING_SORT` in `app/api/bookings.py`. */
export const DEFAULT_SORT = '-slot_time'
export const PAGE_SIZE = 20

/** The backend's `BOOKING_SORT_COLUMNS` whitelist; anything else is a 422. */
export const SORTABLE_FIELDS = ['slot_time', 'created_at', 'status'] as const
export type SortableField = (typeof SORTABLE_FIELDS)[number]

export function parseFilters(params: URLSearchParams): BookingListFilters {
  return {
    property_id: params.get('property_id') ?? '',
    agent_id: params.get('agent_id') ?? '',
    status: params.getAll('status').filter(isBookingStatus),
    date_from: parseDate(params.get('date_from')),
    date_to: parseDate(params.get('date_to')),
    page: parsePage(params.get('page')),
    sort: parseSort(params.get('sort')),
  }
}

export function toSearchParams(filters: BookingListFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.property_id) params.set('property_id', filters.property_id)
  if (filters.agent_id) params.set('agent_id', filters.agent_id)
  for (const value of filters.status) params.append('status', value)
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.page > 1) params.set('page', String(filters.page))
  if (filters.sort !== DEFAULT_SORT) params.set('sort', filters.sort)
  return params
}

export function toQueryParams(filters: BookingListFilters): QueryParams {
  return {
    page: filters.page,
    page_size: PAGE_SIZE,
    sort: filters.sort,
    property_id: filters.property_id || undefined,
    agent_id: filters.agent_id || undefined,
    status: filters.status,
    // Widened to the whole local day at each end: the range is inclusive over a UTC
    // `slot_time`, so a bare date would shift the window by the viewer's offset.
    date_from: dayBoundaryToIso(filters.date_from, 'start') ?? undefined,
    date_to: dayBoundaryToIso(filters.date_to, 'end') ?? undefined,
  }
}

/** Sort and page are excluded — neither narrows the result set. */
export function hasActiveFilters(filters: BookingListFilters): boolean {
  return Boolean(
    filters.property_id ||
      filters.agent_id ||
      filters.status.length ||
      filters.date_from ||
      filters.date_to,
  )
}

export function emptyFilters(): BookingListFilters {
  return {
    property_id: '',
    agent_id: '',
    status: [],
    date_from: '',
    date_to: '',
    page: 1,
    sort: DEFAULT_SORT,
  }
}

export function toggleSort(current: string, field: SortableField): string {
  return current === field ? `-${field}` : field
}

export function readSort(sort: string): { field: string; direction: 'asc' | 'desc' } {
  return sort.startsWith('-')
    ? { field: sort.slice(1), direction: 'desc' }
    : { field: sort, direction: 'asc' }
}

function parsePage(raw: string | null): number {
  const page = Number(raw)
  return Number.isInteger(page) && page > 0 ? page : 1
}

/** A hand-edited `?sort=` outside the whitelist falls back rather than reaching a 422. */
function parseSort(raw: string | null): string {
  if (!raw) return DEFAULT_SORT
  const { field } = readSort(raw)
  return (SORTABLE_FIELDS as readonly string[]).includes(field) ? raw : DEFAULT_SORT
}

/**
 * Anything that is not a calendar date is dropped rather than forwarded. The date
 * inputs cannot produce another shape, but a hand-edited URL can, and the backend would
 * answer a 422 about a filter the user never set.
 */
function parseDate(raw: string | null): string {
  return raw && /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : ''
}

function isBookingStatus(value: string): value is BookingStatus {
  return (BOOKING_STATUSES as readonly string[]).includes(value)
}
