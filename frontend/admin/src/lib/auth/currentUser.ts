import { queryOptions } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import type { CurrentUser, Role } from './types'
import { isRole } from './types'

export const currentUserQueryKey = ['auth', 'me'] as const

/**
 * The one source of "who am I" for the whole SPA. Every RBAC-aware render reads this
 * and nothing else, so there is a single place F3 has to touch and a single place a
 * reviewer has to check.
 *
 * `staleTime: Infinity` because identity does not change mid-session: it changes on
 * login and on logout, both of which invalidate this key explicitly rather than
 * relying on a refetch interval.
 */
export function currentUserQueryOptions() {
  return queryOptions({
    queryKey: currentUserQueryKey,
    queryFn: ({ signal }) => fetchCurrentUser(signal),
    staleTime: Infinity,
  })
}

function fetchCurrentUser(signal: AbortSignal): Promise<CurrentUser> {
  const override = devRoleOverride()
  if (override) return Promise.resolve(override)

  // A 401 here is a dead session, so the default redirect applies: apiClient sends the
  // user to /login rather than this query surfacing an error state nobody can act on.
  return apiClient.get<CurrentUser>('/auth/me', { signal })
}

/**
 * TEMPORARY (F2 only) — delete with F3.
 *
 * F2 builds the nav shell and its RBAC rendering before F3 lands login, so there is no
 * way to *become* an admin or an agent yet. Rather than invent a parallel auth system,
 * this returns a synthetic `CurrentUser` when `VITE_DEV_ROLE` is set, letting a
 * developer render the shell in either mode locally.
 *
 * Two properties make this safe to land ahead of real auth:
 *  - `import.meta.env.DEV` is statically `false` in a production build, so Vite drops
 *    this whole branch — a `VITE_DEV_ROLE` in a prod env file cannot do anything.
 *  - It substitutes only the *response*, never the request path. The shape below is the
 *    README's `/auth/me` shape, so F3's change is deleting this function and its one
 *    call site; no consumer of `useCurrentUser` changes at all.
 *
 * It is also the reason `session.csrf_token` is left alone here: recovering the CSRF
 * token from `/auth/me` is F3's session bootstrap, and wiring it to a synthetic value
 * would make writes fail in a way that looks like a backend bug.
 */
function devRoleOverride(): CurrentUser | null {
  if (!import.meta.env.DEV) return null

  const role: unknown = import.meta.env.VITE_DEV_ROLE
  if (!isRole(role)) return null

  return {
    id: `dev-${role}`,
    name: DEV_NAMES[role],
    email: `${role}@dev.local`,
    role,
    status: 'active',
    session: { auth_method: 'cookie', expires_at: '', csrf_token: null },
  }
}

const DEV_NAMES: Record<Role, string> = {
  admin: 'Dev Admin',
  agent: 'Dev Agent',
  client: 'Dev Client',
}
