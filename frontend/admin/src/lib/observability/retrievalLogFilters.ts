import type { QueryParams } from '@/lib/api/client'
import { dayBoundaryToIso } from '@/lib/datetime'

/**
 * URL-held filter state for `GET /observability/retrieval-logs`, same reasoning as the
 * other three lists: a filtered view is linkable and survives a refresh.
 *
 * `has_results` is a tri-state (`''`/`'true'`/`'false'`) rather than a boolean, because
 * the backend's filter itself is tri-state — omitted means "no filter", not "false".
 * `date_from`/`date_to` are held as plain `YYYY-MM-DD`, converted to instants in
 * `toQueryParams`, exactly like the bookings list.
 */
export interface RetrievalLogFilters {
  q: string
  conversation_id: string
  user_id: string
  has_results: '' | 'true' | 'false'
  date_from: string
  date_to: string
  page: number
  sort: string
}

/** Matches the backend's default `-created_at` (`app/api/observability.py`). */
export const DEFAULT_SORT = '-created_at'
export const PAGE_SIZE = 20

/** The backend's `RETRIEVAL_LOG_SORT_COLUMNS` whitelist; anything else is a 422. */
export const SORTABLE_FIELDS = ['created_at', 'top_score', 'result_count'] as const
export type SortableField = (typeof SORTABLE_FIELDS)[number]

export function parseFilters(params: URLSearchParams): RetrievalLogFilters {
  return {
    q: params.get('q') ?? '',
    conversation_id: params.get('conversation_id') ?? '',
    user_id: params.get('user_id') ?? '',
    has_results: parseHasResults(params.get('has_results')),
    date_from: parseDate(params.get('date_from')),
    date_to: parseDate(params.get('date_to')),
    page: parsePage(params.get('page')),
    sort: parseSort(params.get('sort')),
  }
}

export function toSearchParams(filters: RetrievalLogFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.conversation_id) params.set('conversation_id', filters.conversation_id)
  if (filters.user_id) params.set('user_id', filters.user_id)
  if (filters.has_results) params.set('has_results', filters.has_results)
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.page > 1) params.set('page', String(filters.page))
  if (filters.sort !== DEFAULT_SORT) params.set('sort', filters.sort)
  return params
}

export function toQueryParams(filters: RetrievalLogFilters): QueryParams {
  return {
    page: filters.page,
    page_size: PAGE_SIZE,
    sort: filters.sort,
    q: filters.q || undefined,
    conversation_id: filters.conversation_id || undefined,
    user_id: filters.user_id || undefined,
    has_results: filters.has_results ? filters.has_results === 'true' : undefined,
    date_from: dayBoundaryToIso(filters.date_from, 'start') ?? undefined,
    date_to: dayBoundaryToIso(filters.date_to, 'end') ?? undefined,
  }
}

/** Sort and page are excluded — neither narrows the result set. */
export function hasActiveFilters(filters: RetrievalLogFilters): boolean {
  return Boolean(
    filters.q ||
      filters.conversation_id ||
      filters.user_id ||
      filters.has_results ||
      filters.date_from ||
      filters.date_to,
  )
}

export function emptyFilters(): RetrievalLogFilters {
  return {
    q: '',
    conversation_id: '',
    user_id: '',
    has_results: '',
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

function parseHasResults(raw: string | null): '' | 'true' | 'false' {
  return raw === 'true' || raw === 'false' ? raw : ''
}

/**
 * Anything that is not a calendar date is dropped rather than forwarded, exactly like the
 * bookings list's own `parseDate`.
 */
function parseDate(raw: string | null): string {
  return raw && /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : ''
}
