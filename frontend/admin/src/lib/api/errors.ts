/**
 * Normalizes the three error body shapes the backend can emit into one typed error,
 * so no screen ever has to shape-sniff a response itself:
 *
 *  1. `{"detail": {"code", "message", ...extra}}` — every domain error
 *  2. `{"detail": [{"loc", "msg", "type"}, ...]}` — FastAPI's native 422 field list,
 *     left untouched by the backend precisely so the SPA can map it onto form fields
 *  3. `{"detail": "..."}` — Starlette's built-in HTTPException default (e.g. a 404 for
 *     an unrouted path), which never carries a `code`
 *
 * Plus two failures that produce no backend body at all: a rejected `fetch` (offline,
 * DNS, blocked by CORS) and an unparseable body (proxy HTML error page).
 */

/** Assigned when `fetch` itself rejects — the request never reached the backend. */
export const NETWORK_ERROR_CODE = 'network_error'
/** Assigned to FastAPI's native 422, which carries a field list instead of a `code`. */
export const VALIDATION_ERROR_CODE = 'validation_error'
/** Assigned when a non-2xx response's body matches none of the known shapes. */
export const UNEXPECTED_ERROR_CODE = 'unexpected_error'

export interface FieldError {
  /**
   * Dot-joined path with FastAPI's leading location segment (`body`/`query`/`path`)
   * stripped, e.g. `loc: ["body", "email"]` -> `"email"`. Empty string when the error
   * is about the request as a whole rather than one field.
   */
  field: string
  message: string
  type: string
}

export class ApiError extends Error {
  /** HTTP status, or `0` when the request never reached the backend. */
  readonly status: number
  /** Stable, machine-readable. Branch on this, never on `message`. */
  readonly code: string
  /** Non-empty only for FastAPI's native 422. */
  readonly fieldErrors: readonly FieldError[]
  /** Error-specific extras merged into `detail`, e.g. `suggested_alternatives`. */
  readonly details: Readonly<Record<string, unknown>>

  constructor(init: {
    status: number
    code: string
    message: string
    fieldErrors?: readonly FieldError[]
    details?: Record<string, unknown>
  }) {
    super(init.message)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.fieldErrors = init.fieldErrors ?? []
    this.details = init.details ?? {}
  }

  get isNetworkError(): boolean {
    return this.status === 0
  }

  get isValidationError(): boolean {
    return this.fieldErrors.length > 0
  }

  /** First message for `field`, if the backend rejected that field. */
  fieldError(field: string): string | undefined {
    return this.fieldErrors.find((error) => error.field === field)?.message
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}

export function networkError(cause: unknown): ApiError {
  return new ApiError({
    status: 0,
    code: NETWORK_ERROR_CODE,
    message: 'Could not reach the server. Check your connection and try again.',
    details: { cause },
  })
}

export function toApiError(status: number, body: unknown): ApiError {
  const detail = isRecord(body) ? body.detail : undefined

  if (isRecord(detail) && typeof detail.code === 'string') {
    const { code, message, ...extra } = detail
    return new ApiError({
      status,
      code,
      message: typeof message === 'string' ? message : fallbackMessage(status),
      details: extra,
    })
  }

  if (Array.isArray(detail)) {
    const fieldErrors = detail.filter(isRecord).map(toFieldError)
    return new ApiError({
      status,
      code: VALIDATION_ERROR_CODE,
      message: 'Some fields need attention.',
      fieldErrors,
    })
  }

  if (typeof detail === 'string' && detail.length > 0) {
    return new ApiError({ status, code: UNEXPECTED_ERROR_CODE, message: detail })
  }

  return new ApiError({
    status,
    code: UNEXPECTED_ERROR_CODE,
    message: fallbackMessage(status),
  })
}

function toFieldError(raw: Record<string, unknown>): FieldError {
  const loc = Array.isArray(raw.loc) ? raw.loc : []
  return {
    field: loc.slice(1).filter(isFieldSegment).join('.'),
    message: typeof raw.msg === 'string' ? raw.msg : 'Invalid value.',
    type: typeof raw.type === 'string' ? raw.type : 'invalid',
  }
}

function isFieldSegment(segment: unknown): segment is string | number {
  return typeof segment === 'string' || typeof segment === 'number'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function fallbackMessage(status: number): string {
  return status >= 500
    ? 'The server ran into a problem. Please try again.'
    : `The request could not be completed (HTTP ${String(status)}).`
}
