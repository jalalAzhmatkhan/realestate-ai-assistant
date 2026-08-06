import type { QueryParams } from '@/lib/api/client'
import { PROPERTY_STATUSES, PROPERTY_TYPES, type PropertyStatus, type PropertyType } from './types'

/**
 * The filter state lives in the URL, not in component state. A filtered list is then
 * linkable and — the reason that matters daily — clicking a row into a detail screen and
 * pressing Back returns the exact same filtered page rather than a reset list.
 */
export interface PropertyListFilters {
  q: string
  city: string
  property_type: PropertyType[]
  status: PropertyStatus[]
  page: number
  sort: string
}

/** Matches `DEFAULT_PROPERTY_SORT` in `app/api/properties.py`. */
export const DEFAULT_SORT = '-listed_date'
export const PAGE_SIZE = 20

/** The backend's `PROPERTY_SORT_COLUMNS` whitelist; anything else is a 422. */
export const SORTABLE_FIELDS = ['listed_date', 'price', 'title', 'city'] as const
export type SortableField = (typeof SORTABLE_FIELDS)[number]

export function parseFilters(params: URLSearchParams): PropertyListFilters {
  return {
    q: params.get('q') ?? '',
    city: params.get('city') ?? '',
    property_type: params.getAll('property_type').filter(isPropertyType),
    status: params.getAll('status').filter(isPropertyStatus),
    page: parsePage(params.get('page')),
    sort: parseSort(params.get('sort')),
  }
}

/** Only non-defaults are written, so a pristine list keeps a clean `/properties` URL. */
export function toSearchParams(filters: PropertyListFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.city) params.set('city', filters.city)
  for (const value of filters.property_type) params.append('property_type', value)
  for (const value of filters.status) params.append('status', value)
  if (filters.page > 1) params.set('page', String(filters.page))
  if (filters.sort !== DEFAULT_SORT) params.set('sort', filters.sort)
  return params
}

export function toQueryParams(filters: PropertyListFilters): QueryParams {
  return {
    page: filters.page,
    page_size: PAGE_SIZE,
    sort: filters.sort,
    // Empty strings are dropped rather than sent: `?q=` is a filter the backend would
    // apply, and `?city=` would match no city at all.
    q: filters.q || undefined,
    city: filters.city || undefined,
    property_type: filters.property_type,
    status: filters.status,
  }
}

/**
 * Drives which of §5.2's two empty states is shown — "no match, clear filters" versus
 * "none yet, add one". Sort and page are excluded: neither narrows the result set, and
 * page 3 of an emptied list is a stale-page problem, not a filter the user can clear.
 */
export function hasActiveFilters(filters: PropertyListFilters): boolean {
  return Boolean(
    filters.q || filters.city || filters.property_type.length || filters.status.length,
  )
}

export function emptyFilters(): PropertyListFilters {
  return { q: '', city: '', property_type: [], status: [], page: 1, sort: DEFAULT_SORT }
}

/** `title` -> ascending, clicking the active ascending column -> `-title`. */
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

/**
 * A hand-edited `?sort=` outside the whitelist falls back to the default instead of
 * reaching the backend as a 422 the user cannot act on.
 */
function parseSort(raw: string | null): string {
  if (!raw) return DEFAULT_SORT
  const { field } = readSort(raw)
  return (SORTABLE_FIELDS as readonly string[]).includes(field) ? raw : DEFAULT_SORT
}

function isPropertyType(value: string): value is PropertyType {
  return (PROPERTY_TYPES as readonly string[]).includes(value)
}

function isPropertyStatus(value: string): value is PropertyStatus {
  return (PROPERTY_STATUSES as readonly string[]).includes(value)
}
