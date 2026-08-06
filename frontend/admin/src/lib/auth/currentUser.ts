import { queryOptions } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import { setCsrfToken } from '@/lib/api/csrf'
import type { CurrentUser } from './types'

export const currentUserQueryKey = ['auth', 'me'] as const

/**
 * The one source of "who am I" for the whole SPA. Every RBAC-aware render reads this
 * and nothing else, so there is a single place a reviewer has to check.
 *
 * `staleTime: Infinity` because identity does not change mid-session: it changes on
 * login and on logout, both of which reset this key explicitly rather than relying on
 * a refetch interval.
 */
export function currentUserQueryOptions() {
  return queryOptions({
    queryKey: currentUserQueryKey,
    queryFn: ({ signal }) => fetchCurrentUser(signal),
    staleTime: Infinity,
  })
}

/**
 * This is the session bootstrap. A full page reload keeps the httpOnly session cookie
 * but wipes the in-memory CSRF token, so identity alone is not enough to come back
 * usable — without re-seeding the token here, the app would render as signed in and
 * every write would fail `csrf_token_missing`.
 *
 * The re-seed lives in the query function rather than in a component effect so it
 * happens as part of resolving `/auth/me`, before anything downstream renders and can
 * fire a write. An effect would run a frame too late.
 */
async function fetchCurrentUser(signal: AbortSignal): Promise<CurrentUser> {
  // A 401 here is a dead session, so the default redirect applies: apiClient sends the
  // user to /login rather than this query surfacing an error state nobody can act on.
  const user = await apiClient.get<CurrentUser>('/auth/me', { signal })

  // Mirrors the backend's answer verbatim, `null` included. `null` only ever comes back
  // for a Bearer session, which this SPA cannot create — it never sends an
  // Authorization header — so treating it as authoritative cannot strand a cookie
  // session without a token.
  setCsrfToken(user.session.csrf_token)

  return user
}
