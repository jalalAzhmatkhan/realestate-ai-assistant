import type { QueryParams } from '@/lib/api/client'
import { USER_ROLES, USER_STATUSES, type UserRole, type UserStatus } from './types'

/**
 * URL-held filter state, matching the properties and bookings lists: a filtered view is
 * linkable, and it survives a refresh or a Back out of a modal.
 */
export interface UserListFilters {
  q: string
  role: UserRole[]
  status: UserStatus[]
  page: number
  sort: string
}

/** Matches `DEFAULT_USER_SORT` in `app/api/users.py`. */
export const DEFAULT_SORT = 'name'
export const PAGE_SIZE = 20

/** The backend's `USER_SORT_COLUMNS` whitelist; anything else is a 422. */
export const SORTABLE_FIELDS = ['name', 'email', 'role', 'created_at'] as const
export type SortableField = (typeof SORTABLE_FIELDS)[number]

export function parseFilters(params: URLSearchParams): UserListFilters {
  return {
    q: params.get('q') ?? '',
    role: params.getAll('role').filter(isUserRole),
    status: params.getAll('status').filter(isUserStatus),
    page: parsePage(params.get('page')),
    sort: parseSort(params.get('sort')),
  }
}

/** Only non-defaults are written, so a pristine list keeps a clean `/users` URL. */
export function toSearchParams(filters: UserListFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  for (const value of filters.role) params.append('role', value)
  for (const value of filters.status) params.append('status', value)
  if (filters.page > 1) params.set('page', String(filters.page))
  if (filters.sort !== DEFAULT_SORT) params.set('sort', filters.sort)
  return params
}

export function toQueryParams(filters: UserListFilters): QueryParams {
  return {
    page: filters.page,
    page_size: PAGE_SIZE,
    sort: filters.sort,
    // `?q=` would be an empty free-text filter the backend still applies, so it is
    // dropped rather than sent.
    q: filters.q || undefined,
    role: filters.role,
    status: filters.status,
  }
}

/** Sort and page are excluded — neither narrows the result set. */
export function hasActiveFilters(filters: UserListFilters): boolean {
  return Boolean(filters.q || filters.role.length || filters.status.length)
}

export function emptyFilters(): UserListFilters {
  return { q: '', role: [], status: [], page: 1, sort: DEFAULT_SORT }
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

function isUserRole(value: string): value is UserRole {
  return (USER_ROLES as readonly string[]).includes(value)
}

function isUserStatus(value: string): value is UserStatus {
  return (USER_STATUSES as readonly string[]).includes(value)
}
