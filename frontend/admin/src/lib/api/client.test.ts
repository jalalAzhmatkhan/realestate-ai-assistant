import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from './client'
import { setCsrfToken } from './csrf'
import { ApiError } from './errors'
import { setUnauthorizedRedirect } from './session'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  setCsrfToken(null)
  setUnauthorizedRedirect(null)
})

describe('apiClient', () => {
  it('sends the in-memory CSRF token and the session cookie on writes', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    setCsrfToken('token-from-login')

    await apiClient.post('/properties', { title: 'Unit 4B' })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('token-from-login')
    expect(init.credentials).toBe('include')
  })

  it("omits the CSRF header on GET, matching the backend's exempt methods", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { results: [] }))
    vi.stubGlobal('fetch', fetchMock)
    setCsrfToken('token-from-login')

    await apiClient.get('/properties', { query: { status: ['active', 'draft'] } })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('?status=active&status=draft')
    expect((init.headers as Headers).has('X-CSRF-Token')).toBe(false)
  })

  it('normalizes a domain error into a code callers can branch on', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          detail: {
            code: 'booking_slot_conflict',
            message: 'That slot is already booked.',
            suggested_alternatives: [{ availability_slot_id: 'avail-002' }],
          },
        }),
      ),
    )

    const error = await apiClient.post('/bookings/b-1/reschedule', {}).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 409, code: 'booking_slot_conflict' })
    expect((error as ApiError).details.suggested_alternatives).toHaveLength(1)
  })

  it("maps FastAPI's native 422 list onto form fields", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          detail: [{ loc: ['body', 'email'], msg: 'field required', type: 'missing' }],
        }),
      ),
    )

    const error = (await apiClient
      .post('/auth/login', {})
      .catch((e: unknown) => e)) as ApiError

    expect(error.isValidationError).toBe(true)
    expect(error.fieldError('email')).toBe('field required')
  })

  it('redirects to /login exactly once on 401 and does not retry', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(401, { detail: { code: 'not_authenticated', message: 'x' } }))
    vi.stubGlobal('fetch', fetchMock)
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)

    await expect(apiClient.get('/auth/me')).rejects.toBeInstanceOf(ApiError)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(redirect).toHaveBeenCalledTimes(1)
  })

  it('leaves the 401 to the caller when redirectOnUnauthorized is false', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(401, { detail: { code: 'invalid_credentials', message: 'x' } }),
        ),
    )
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)

    const error = (await apiClient
      .post('/auth/login', {}, { redirectOnUnauthorized: false })
      .catch((e: unknown) => e)) as ApiError

    expect(error.code).toBe('invalid_credentials')
    expect(redirect).not.toHaveBeenCalled()
  })
})
