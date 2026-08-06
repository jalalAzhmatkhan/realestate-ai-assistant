import { clearCsrfToken } from './csrf'

export const LOGIN_PATH = '/login'

let redirectToLogin: (() => void) | null = null
let resetQueryCache: (() => void) | null = null

/**
 * Lets the router own the redirect (client-side navigation, no full reload) instead of
 * `window.location`. Registered once at the app root; until then the fallback applies.
 */
export function setUnauthorizedRedirect(redirect: (() => void) | null): void {
  redirectToLogin = redirect
}

/**
 * Registered once at the app root with `queryClient.clear`. Injected rather than
 * imported so this module stays free of a dependency on `app/queryClient.ts`, which
 * imports `api/errors.ts` and would otherwise close a cycle through the API layer.
 */
export function setQueryCacheReset(reset: (() => void) | null): void {
  resetQueryCache = reset
}

/**
 * Everything that must not outlive a session boundary. Both halves matter: the CSRF
 * token because it is paired with a cookie that is now gone, and the query cache
 * because the 401 redirect is a client-side navigation — nothing reloads the page, so
 * without this the next user to sign in in the same tab can be served the previous
 * user's cached rows before any refetch lands.
 */
export function endSession(): void {
  clearCsrfToken()
  resetQueryCache?.()
}

/**
 * The single 401 path for the whole app: tear the session down and go to `/login`.
 * Deliberately no retry, no token refresh — the backend issues one 60-minute token
 * with no refresh endpoint, so a retry could only ever repeat the same 401.
 *
 * The already-on-`/login` guard is load-bearing, not defensive: a wrong password is
 * itself a 401 (`invalid_credentials`), and without this the login screen would
 * redirect to itself instead of showing the error. Callers that submit credentials
 * should *also* pass `redirectOnUnauthorized: false`, so the behavior does not depend
 * on which route happens to be mounted.
 *
 * The guard runs *before* `endSession()` on purpose: a stale in-flight request can
 * 401 just after a login has succeeded on this very screen, and tearing down there
 * would discard a token and a cache that were both legitimately just populated.
 */
export function handleUnauthorized(): void {
  if (window.location.pathname === LOGIN_PATH) return

  endSession()

  if (redirectToLogin) {
    redirectToLogin()
    return
  }
  window.location.assign(LOGIN_PATH)
}
