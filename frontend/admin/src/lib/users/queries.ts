import { queryOptions } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import type { Page } from '@/lib/api/pagination'
import { toQueryParams, type UserListFilters } from './filters'
import type { User } from './types'

export const usersKey = ['users'] as const

/**
 * Admin-only server-side (`app/api/users.py`, `AdminUser`). No `enabled` guard is added
 * here: `/users` is the only screen that calls this and the router already keeps a
 * non-admin off it, so a guard would be a second copy of a rule that is enforced twice
 * already — once by the router for UX, once by the backend's `403` for real.
 */
export function userListQueryOptions(filters: UserListFilters) {
  return queryOptions({
    queryKey: [...usersKey, 'list', filters] as const,
    queryFn: ({ signal }) =>
      apiClient.get<Page<User>>('/users', { query: toQueryParams(filters), signal }),
    // Keeps the current rows on screen while the next page loads, as on the other lists.
    placeholderData: (previous) => previous,
  })
}
