import { vi } from 'vitest'

export type ApiHandler = (request: { url: URL; init: RequestInit }) => Response

export function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Routes stubs by `"<METHOD> <pathname>"`, so a test asserting on a query string does it
 * explicitly against the recorded calls rather than by keying handlers on a full URL
 * that would then have to be kept byte-identical to the request.
 *
 * An unstubbed request rejects loudly: a screen quietly fetching something the test did
 * not anticipate is exactly the kind of thing these tests exist to catch.
 */
export function mockApi(handlers: Record<string, ApiHandler>) {
  const fetchMock = vi.fn((input: string, init: RequestInit = {}) => {
    const url = new URL(input)
    const method = init.method ?? 'GET'
    const handler = handlers[`${method} ${url.pathname}`]
    if (!handler) {
      return Promise.reject(new Error(`unstubbed request: ${method} ${url.pathname}`))
    }
    return Promise.resolve(handler({ url, init }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The URLs a stubbed run actually requested, in order. */
export function requestedUrls(fetchMock: ReturnType<typeof mockApi>): URL[] {
  return fetchMock.mock.calls.map(([input]) => new URL(input))
}

export function requestBody(fetchMock: ReturnType<typeof mockApi>, index = 0): unknown {
  const init = fetchMock.mock.calls[index]?.[1]
  return typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
}
