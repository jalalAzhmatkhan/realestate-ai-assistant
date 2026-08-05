import { clearCsrfToken } from './csrf'

export const LOGIN_PATH = '/login'

let redirectToLogin: (() => void) | null = null

/**
 * Lets the router own the redirect (client-side navigation, no full reload) instead of
 * `window.location`. Registered once at the app root; until then the fallback applies.
 */
export function setUnauthorizedRedirect(redirect: (() => void) | null): void {
  redirectToLogin = redirect
}

/**
 * The single 401 path for the whole app: drop the in-memory CSRF token and go to
 * `/login`. Deliberately no retry, no token refresh — the backend issues one
 * 60-minute token with no refresh endpoint, so a retry could only ever repeat the
 * same 401.
 *
 * The already-on-`/login` guard is load-bearing, not defensive: a wrong password is
 * itself a 401 (`invalid_credentials`), and without this the login screen would
 * redirect to itself instead of showing the error. Callers that submit credentials
 * should *also* pass `redirectOnUnauthorized: false`, so the behavior does not depend
 * on which route happens to be mounted.
 */
export function handleUnauthorized(): void {
  clearCsrfToken()

  if (window.location.pathname === LOGIN_PATH) return

  if (redirectToLogin) {
    redirectToLogin()
    return
  }
  window.location.assign(LOGIN_PATH)
}
