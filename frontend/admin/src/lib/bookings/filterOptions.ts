import { queryOptions } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import type { Page } from '@/lib/api/pagination'
import type { PropertySummary } from '@/lib/properties/types'

/**
 * Option lists for the two id-valued booking filters. Both are `id -> name` lookups the
 * bookings list itself cannot supply: a page of bookings only names the properties and
 * agents that happen to appear on it, which is not the set worth filtering by.
 *
 * Both are capped at the backend's `max_page_size` (100, `app/core/config.py`). That is
 * a real ceiling, not a paginated picker — past a hundred listings the filter needs a
 * type-ahead against `GET /properties?q=`, which is a screen of its own rather than
 * something to half-build here.
 */
const OPTIONS_PAGE_SIZE = 100

/** Fresh enough for a filter dropdown; a listing added mid-session is not worth a refetch per keystroke. */
const OPTIONS_STALE_TIME = 5 * 60_000

export function propertyOptionsQueryOptions() {
  return queryOptions({
    queryKey: ['bookings', 'filter-options', 'properties'] as const,
    queryFn: ({ signal }) =>
      apiClient.get<Page<PropertySummary>>('/properties', {
        query: { page_size: OPTIONS_PAGE_SIZE, sort: 'title' },
        signal,
      }),
    staleTime: OPTIONS_STALE_TIME,
  })
}

/**
 * The subset of `UserResponse` this filter reads. F6 owns the full user type; mirroring
 * two fields here is cheaper than importing a screen that does not exist yet.
 */
export interface AgentOption {
  id: string
  name: string
}

/**
 * Admin-only, because `GET /users` is (`app/api/users.py`, `AdminUser`). Callers gate
 * this with `enabled`, so an `agent` never fires a request that would answer `403` —
 * their bookings are already scoped to themselves, which is what makes the filter
 * meaningless for them rather than merely forbidden.
 */
export function agentOptionsQueryOptions() {
  return queryOptions({
    queryKey: ['bookings', 'filter-options', 'agents'] as const,
    queryFn: ({ signal }) =>
      apiClient.get<Page<AgentOption>>('/users', {
        query: { role: 'agent', page_size: OPTIONS_PAGE_SIZE, sort: 'name' },
        signal,
      }),
    staleTime: OPTIONS_STALE_TIME,
  })
}
