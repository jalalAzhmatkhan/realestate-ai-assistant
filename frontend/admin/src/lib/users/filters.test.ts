import { describe, expect, it } from 'vitest'

import {
  DEFAULT_SORT,
  emptyFilters,
  hasActiveFilters,
  parseFilters,
  toQueryParams,
  toSearchParams,
  toggleSort,
} from './filters'

describe('user list filters', () => {
  it('reads repeatable params into arrays', () => {
    const filters = parseFilters(
      new URLSearchParams('q=budi&role=admin&role=agent&status=disabled'),
    )

    expect(filters.q).toBe('budi')
    expect(filters.role).toEqual(['admin', 'agent'])
    expect(filters.status).toEqual(['disabled'])
  })

  it('drops values outside the backend enums instead of forwarding them as a 422', () => {
    const filters = parseFilters(new URLSearchParams('role=superadmin&status=pending'))

    expect(filters.role).toEqual([])
    expect(filters.status).toEqual([])
  })

  it('falls back to the default sort for a field outside the whitelist', () => {
    // `status` is a column on this table but not in USER_SORT_COLUMNS.
    expect(parseFilters(new URLSearchParams('sort=status')).sort).toBe(DEFAULT_SORT)
    expect(parseFilters(new URLSearchParams('sort=-created_at')).sort).toBe('-created_at')
  })

  it('ignores a page that is not a positive integer', () => {
    expect(parseFilters(new URLSearchParams('page=0')).page).toBe(1)
    expect(parseFilters(new URLSearchParams('page=abc')).page).toBe(1)
    expect(parseFilters(new URLSearchParams('page=3')).page).toBe(3)
  })

  it('round-trips through the URL without writing defaults', () => {
    expect(toSearchParams(emptyFilters()).toString()).toBe('')

    const filters = parseFilters(new URLSearchParams('q=budi&role=client&page=2&sort=email'))
    expect(parseFilters(toSearchParams(filters))).toEqual(filters)
  })

  it('omits a blank search from the request rather than sending it empty', () => {
    const query = toQueryParams(emptyFilters())

    expect(query.q).toBeUndefined()
    expect(query.page).toBe(1)
    expect(query.sort).toBe(DEFAULT_SORT)
  })

  it('counts only the filters a "clear filters" action would reset', () => {
    expect(hasActiveFilters(emptyFilters())).toBe(false)
    expect(hasActiveFilters({ ...emptyFilters(), page: 4, sort: 'email' })).toBe(false)
    expect(hasActiveFilters({ ...emptyFilters(), q: 'budi' })).toBe(true)
    expect(hasActiveFilters({ ...emptyFilters(), role: ['admin'] })).toBe(true)
  })

  it('toggles a column between ascending and descending', () => {
    expect(toggleSort(DEFAULT_SORT, 'email')).toBe('email')
    expect(toggleSort('email', 'email')).toBe('-email')
    expect(toggleSort('-email', 'email')).toBe('email')
  })
})
