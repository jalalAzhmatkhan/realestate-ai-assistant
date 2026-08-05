import { getCsrfToken } from './csrf'
import { networkError, toApiError } from './errors'
import { handleUnauthorized } from './session'

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'
).replace(/\/+$/, '')

const CSRF_HEADER = 'X-CSRF-Token'
// Mirrors the backend's CSRF_EXEMPT_METHODS inverted (app/api/deps.py): only
// state-changing methods carry the double-submit header.
const CSRF_REQUIRED_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue | readonly QueryValue[]>

export interface RequestOptions {
  method?: HttpMethod
  /** Serialized as JSON. Arrays repeat a param (`?status=a&status=b`), per the backend's filter contract. */
  body?: unknown
  query?: QueryParams
  headers?: Record<string, string>
  signal?: AbortSignal
  /**
   * Set `false` on requests where a 401 is a normal outcome to render inline rather
   * than a dead session — login being the only such case today.
   */
  redirectOnUnauthorized?: boolean
}

/**
 * The one place the SPA talks to the backend.
 *
 * Every request carries the httpOnly session cookie (`credentials: 'include'`), every
 * state-changing request carries the in-memory CSRF token, every failure arrives as an
 * `ApiError`, and every 401 takes the same single redirect path. Screens never call
 * `fetch` directly — that is what keeps those four guarantees from drifting per-screen.
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers = new Headers(options.headers)

  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }

  if (CSRF_REQUIRED_METHODS.has(method)) {
    const csrfToken = getCsrfToken()
    // Absent is not an error to raise here: the backend answers 403 csrf_token_missing,
    // which normalizes like any other domain error. Failing client-side instead would
    // hide a genuine session problem behind a message the backend never sent.
    if (csrfToken) headers.set(CSRF_HEADER, csrfToken)
  }

  const init: RequestInit = {
    method,
    headers,
    // Non-negotiable: the session is an httpOnly cookie on a different origin in dev.
    credentials: 'include',
  }
  if (options.body !== undefined) init.body = JSON.stringify(options.body)
  if (options.signal) init.signal = options.signal

  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), init)
  } catch (cause) {
    // An aborted request is the caller's own doing (TanStack Query cancelling a stale
    // fetch), not a failure worth surfacing as one.
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw networkError(cause)
  }

  if (response.ok) {
    return (await readBody(response)) as T
  }

  const error = toApiError(response.status, await readBody(response))

  if (response.status === 401 && (options.redirectOnUnauthorized ?? true)) {
    handleUnauthorized()
  }

  throw error
}

export const apiClient = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...options, method: 'POST', body }),
  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...options, method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...options, method: 'PATCH', body }),
  delete: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...options, method: 'DELETE' }),
}

function buildUrl(path: string, query?: QueryParams): string {
  const url = `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`
  if (!query) return url

  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    for (const item of Array.isArray(value) ? value : [value]) {
      if (item === null || item === undefined) continue
      params.append(key, String(item))
    }
  }
  const serialized = params.toString()
  return serialized ? `${url}?${serialized}` : url
}

async function readBody(response: Response): Promise<unknown> {
  // 204 is a real success shape here (POST /auth/logout), not a missing body.
  if (response.status === 204) return undefined

  const text = await response.text()
  if (!text) return undefined

  try {
    return JSON.parse(text) as unknown
  } catch {
    // A proxy's HTML error page, or a truncated response. `toApiError` turns this into
    // `unexpected_error` rather than letting a parse exception escape as something no
    // caller is typed to handle.
    return undefined
  }
}
