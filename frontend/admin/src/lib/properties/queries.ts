import { queryOptions } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import type { Page } from '@/lib/api/pagination'
import { toQueryParams, type PropertyListFilters } from './filters'
import type { PropertyDetail, PropertySummary } from './types'

export const propertiesKey = ['properties'] as const

export function propertyListQueryOptions(filters: PropertyListFilters) {
  return queryOptions({
    queryKey: [...propertiesKey, 'list', filters] as const,
    queryFn: ({ signal }) =>
      apiClient.get<Page<PropertySummary>>('/properties', {
        query: toQueryParams(filters),
        signal,
      }),
    // Keeps the previous page's rows on screen while the next one loads, so paging does
    // not collapse the table to a skeleton and jump the scroll position.
    placeholderData: (previous) => previous,
  })
}

/**
 * Always a fresh fetch of the full record, never the list row for the same id.
 * `PropertySummary` omits `latitude`, `longitude`, `amenities`, `description` and
 * `listed_date`, all of which `PUT` requires — hydrating the edit form from a cached
 * list row would silently blank the first four and reset the fifth to today on save.
 */
export function propertyDetailQueryOptions(propertyId: string) {
  return queryOptions({
    queryKey: [...propertiesKey, 'detail', propertyId] as const,
    queryFn: ({ signal }) => apiClient.get<PropertyDetail>(`/properties/${propertyId}`, { signal }),
  })
}
