import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'

import { apiClient } from '@/lib/api/client'
import { clearCsrfToken } from '@/lib/api/csrf'
import { LOGIN_PATH } from '@/lib/api/session'

/**
 * Ends the session server-side first, then locally.
 *
 * Order matters: `POST /auth/logout` needs the CSRF header to be accepted, so clearing
 * the token first would make the backend answer `403 csrf_token_missing` and leave the
 * token live until its TTL expires. Only once the server has revoked it is the local
 * teardown safe to run.
 *
 * A failed logout deliberately does *not* clear anything and does not navigate. Looking
 * signed out while the cookie is still valid is the worse of the two failure modes —
 * the screen reports the error and the user can retry. The one exception handles
 * itself: a 401 means there is nothing left to revoke, and apiClient's dead-session
 * path already tears down and redirects.
 */
export function useLogout() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  return useMutation<void, Error, void>({
    mutationFn: () => apiClient.post<void>('/auth/logout'),
    onSuccess: () => {
      clearCsrfToken()
      // Same session-boundary reasoning as the 401 path: this navigation is client-side,
      // so nothing else drops this user's cached rows before the next sign-in.
      queryClient.clear()
      void navigate(LOGIN_PATH, { replace: true })
    },
  })
}
