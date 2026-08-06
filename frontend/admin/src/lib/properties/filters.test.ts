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

describe('property list filters', () => {
  it('reads repeatable params into arrays', () => {
    const filters = parseFilters(
      new URLSearchParams('q=kemang&city=Jakarta&status=active&status=draft&property_type=villa'),
    )

    expect(filters.q).toBe('kemang')
    expect(filters.city).toBe('Jakarta')
    expect(filters.status).toEqual(['active', 'draft'])
    expect(filters.property_type).toEqual(['villa'])
  })

  it('drops values outside the backend enums instead of forwarding them as a 422', () => {
    const filters = parseFilters(new URLSearchParams('status=pending&property_type=castle'))

    expect(filters.status).toEqual([])
    expect(filters.property_type).toEqual([])
  })

  it('falls back to the default sort for a field outside the whitelist', () => {
    expect(parseFilters(new URLSearchParams('sort=agent_id')).sort).toBe(DEFAULT_SORT)
    expect(parseFilters(new URLSearchParams('sort=-price')).sort).toBe('-price')
  })

  it('ignores a page that is not a positive integer', () => {
    expect(parseFilters(new URLSearchParams('page=0')).page).toBe(1)
    expect(parseFilters(new URLSearchParams('page=abc')).page).toBe(1)
    expect(parseFilters(new URLSearchParams('page=3')).page).toBe(3)
  })

  it('round-trips through the URL without writing defaults', () => {
    expect(toSearchParams(emptyFilters()).toString()).toBe('')

    const filters = parseFilters(new URLSearchParams('q=villa&status=active&status=sold&page=2'))
    expect(parseFilters(toSearchParams(filters))).toEqual(filters)
  })

  it('omits blank text filters from the request rather than sending them empty', () => {
    const query = toQueryParams(emptyFilters())

    expect(query.q).toBeUndefined()
    expect(query.city).toBeUndefined()
    expect(query.page).toBe(1)
  })

  it('counts only the filters a "clear filters" action would reset', () => {
    expect(hasActiveFilters(emptyFilters())).toBe(false)
    // Paging and sorting narrow nothing, so they must not trigger the "no match" empty
    // state that offers to clear filters the user never set.
    expect(hasActiveFilters({ ...emptyFilters(), page: 4, sort: 'price' })).toBe(false)
    expect(hasActiveFilters({ ...emptyFilters(), city: 'Bali' })).toBe(true)
    expect(hasActiveFilters({ ...emptyFilters(), status: ['draft'] })).toBe(true)
  })

  it('toggles a column between ascending and descending', () => {
    expect(toggleSort(DEFAULT_SORT, 'price')).toBe('price')
    expect(toggleSort('price', 'price')).toBe('-price')
    expect(toggleSort('-price', 'price')).toBe('price')
  })
})
