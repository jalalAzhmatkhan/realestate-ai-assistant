/**
 * The CSRF token lives in JS memory and nowhere else — never `localStorage`, never a
 * readable cookie. It is the second half of the backend's double-submit pair; the first
 * half is the `csrf` claim inside the httpOnly session cookie, which JS cannot read.
 * Persisting this half to storage would hand an XSS payload the one piece the httpOnly
 * cookie was designed to withhold.
 *
 * A full page reload therefore wipes it while the cookie survives. `GET /auth/me`
 * returns a fresh copy for exactly this reason (backend README, "GET /api/v1/auth/me"),
 * so the app-load session query is what repopulates it.
 */

let csrfToken: string | null = null

export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

export function getCsrfToken(): string | null {
  return csrfToken
}

export function clearCsrfToken(): void {
  csrfToken = null
}
