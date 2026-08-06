import { useMutation, useQueryClient } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import { setCsrfToken } from '@/lib/api/csrf'
import { currentUserQueryOptions } from './currentUser'
import type { CurrentUser, LoginResponse } from './types'

export interface Credentials {
  email: string
  password: string
}

/**
 * Signs in and leaves the app with a settled identity, so the caller only has to
 * navigate.
 *
 * Three things happen in a fixed order, and the order is the whole design:
 *
 *  1. `client_type: 'browser'` — never omitted. The backend defaults to `'api'`, which
 *     returns a Bearer token and *no* cookie, so an omitted field would sign the user
 *     in to a session this SPA has no way to present on the next request.
 *  2. The cache is emptied. Login is a session boundary: whatever a previous user in
 *     this tab left behind must not be readable by the next one.
 *  3. Identity is fetched from `/auth/me` rather than seeded from the login response.
 *
 * On (3): the login response carries a `user` that is deliberately narrower than
 * `CurrentUser` — `UserPublic` has no `status` and no `session`. Seeding the
 * `['auth','me']` cache from it would mean inventing both: asserting `status: 'active'`
 * and synthesizing an `expires_at` from `expires_in`. That is fabricating contract
 * fields the endpoint never sent, and it would also leave two independently-derived
 * CSRF tokens in play with no guarantee they agree. One extra request buys a cache
 * entry that is byte-for-byte what `/auth/me` would answer, and it routes the CSRF
 * token through the single re-seed point in `currentUser.ts` that a page reload uses
 * too — one code path, verified on every sign-in rather than only after a reload.
 *
 * `setCsrfToken` still runs on the login response first: `fetchQuery` below is a GET
 * and needs no token, but leaving the window between login and `/auth/me` tokenless
 * would make any concurrent write fail for no reason.
 */
export function useLogin() {
  const queryClient = useQueryClient()

  return useMutation<CurrentUser, Error, Credentials>({
    mutationFn: async (credentials) => {
      const session = await apiClient.post<LoginResponse>(
        '/auth/login',
        { ...credentials, client_type: 'browser' },
        // A wrong password is a 401 and a normal outcome here. Without this it would
        // take the app-wide dead-session path and redirect `/login` to itself. The
        // already-on-`/login` guard in session.ts catches that too, but this does not
        // depend on which route happens to be mounted.
        { redirectOnUnauthorized: false },
      )

      queryClient.clear()
      setCsrfToken(session.csrf_token)

      return queryClient.fetchQuery(currentUserQueryOptions())
    },
  })
}
