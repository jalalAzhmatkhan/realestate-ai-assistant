import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import type { Page } from '@/lib/api/pagination'
import { agentOptionsQueryOptions } from '@/lib/bookings/filterOptions'
import { usersKey } from './queries'
import type { User, UserCreateRequest, UserUpdateRequest } from './types'

export function useCreateUser() {
  const queryClient = useQueryClient()

  return useMutation<User, Error, UserCreateRequest>({
    mutationFn: (payload) => apiClient.post<User>('/users', payload),
    // Not patched into the cached pages: where a new row belongs depends on the active
    // sort and filters, which only the backend can answer.
    onSuccess: () => {
      settle(queryClient)
    },
  })
}

/**
 * Partial by contract: an omitted field is left untouched, so a role change sends only
 * `{role}` and cannot blank out the name it never rendered. There is no `DELETE` route —
 * disabling is `{status: 'disabled'}`, because deleting a user would orphan their
 * bookings (`app/api/users.py` module docstring).
 */
export function useUpdateUser(userId: string) {
  const queryClient = useQueryClient()

  return useMutation<User, Error, UserUpdateRequest>({
    mutationFn: (payload) => apiClient.patch<User>(`/users/${userId}`, payload),
    onSuccess: (user) => {
      replaceRow(queryClient, user)
      settle(queryClient)
    },
  })
}

/**
 * The updated record straight into the pages already on screen. Without this the row
 * keeps its old role or badge for the length of the refetch below — the list holds its
 * previous data while revalidating — so a confirmed change reads as one that did not
 * take. The refetch still has the final say on where the row belongs.
 */
function replaceRow(queryClient: QueryClient, user: User): void {
  queryClient.setQueriesData<Page<User>>({ queryKey: [...usersKey, 'list'] }, (page) =>
    page
      ? { ...page, results: page.results.map((row) => (row.id === user.id ? user : row)) }
      : page,
  )
}

/**
 * The lists are then invalidated rather than left patched: a role or status change can
 * drop a row out of the active filter or move it across a page boundary under
 * `sort=role`, which no client-side patch can work out.
 *
 * The bookings screen's agent picker goes with them — it is a `role=agent` slice of this
 * same collection held for five minutes, so promoting someone to `agent` would otherwise
 * leave them missing from a filter that is supposed to list every agent.
 */
function settle(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: [...usersKey, 'list'] })
  void queryClient.invalidateQueries({ queryKey: agentOptionsQueryOptions().queryKey })
}
