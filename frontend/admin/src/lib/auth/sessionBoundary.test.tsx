import { QueryClient } from '@tanstack/react-query'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/lib/api/client'
import { getCsrfToken, setCsrfToken } from '@/lib/api/csrf'
import { setQueryCacheReset, setUnauthorizedRedirect } from '@/lib/api/session'
import { currentUserQueryKey } from '@/lib/auth/currentUser'
import { buildUser, renderApp } from '@/test/renderApp'

const EMAIL = 'admin@evdekimi.test'
const PASSWORD = 'correct horse'
const OTHER_KEY = ['properties', 'list'] as const

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function unauthorized(): Response {
  return json(401, { detail: { code: 'session_revoked', message: 'Session expired.' } })
}

function mockBackend(handlers: Record<string, () => Response | Promise<Response>>) {
  const fetchMock = vi.fn((input: string) => {
    const { pathname } = new URL(input)
    const handler = handlers[pathname]
    if (!handler) return Promise.reject(new Error(`unstubbed request: ${pathname}`))
    return Promise.resolve(handler())
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function loginOk(role: 'admin' | 'agent' | 'client' = 'admin', csrf = 'csrf-from-login') {
  return json(200, {
    access_token: null,
    token_type: null,
    expires_in: 3600,
    csrf_token: csrf,
    user: { id: `u-${role}`, name: `Test ${role}`, email: EMAIL, role },
  })
}

async function submitCredentials() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Email'), EMAIL)
  await user.type(screen.getByLabelText('Password'), PASSWORD)
  await user.click(screen.getByRole('button', { name: 'Log in' }))
}

function atPath(path: string) {
  window.history.pushState({}, '', path)
}

afterEach(() => {
  setCsrfToken(null)
  setUnauthorizedRedirect(null)
  setQueryCacheReset(null)
  atPath('/')
})

/**
 * The unit test in session.test.ts asserts a `vi.fn()` was called. These assert the real
 * QueryClient is actually emptied, wired the way main.tsx wires it.
 */
describe('D3 probe: cache is genuinely emptied at a session boundary', () => {
  it('drops the previous user rows on the 401 path, not just calls a spy', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    setQueryCacheReset(() => {
      client.clear()
    })
    setUnauthorizedRedirect(vi.fn())
    atPath('/properties')

    client.setQueryData(currentUserQueryKey, buildUser('admin'))
    client.setQueryData(OTHER_KEY, [{ id: 'p-1', title: "admin's private listing" }])
    setCsrfToken('admin-token')

    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(unauthorized())),
    )

    await expect(apiClient.get('/properties')).rejects.toThrow()

    expect(client.getQueryData(currentUserQueryKey)).toBeUndefined()
    expect(client.getQueryData(OTHER_KEY)).toBeUndefined()
    expect(client.getQueryCache().getAll()).toHaveLength(0)
    expect(getCsrfToken()).toBeNull()
  })

  it('does not leak the admin cache into an agent session across logout + login', async () => {
    const fetchMock = mockBackend({
      '/api/v1/auth/logout': () => new Response(null, { status: 204 }),
      '/api/v1/auth/login': () => loginOk('agent'),
      '/api/v1/auth/me': () => json(200, buildUser('agent', { email: EMAIL })),
    })
    setCsrfToken('admin-token')
    const { client } = renderApp({ user: buildUser('admin') })
    client.setQueryData(OTHER_KEY, [{ id: 'p-1', title: "admin's private listing" }])

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Log out' }))
    await screen.findByRole('button', { name: 'Log in' })

    // Asserted before the next identity resolves: this is the window the bleed lived in.
    expect(client.getQueryData(OTHER_KEY)).toBeUndefined()
    expect(client.getQueryData(currentUserQueryKey)).toBeUndefined()

    await submitCredentials()
    await screen.findByRole('navigation', { name: 'Main' })

    expect(client.getQueryData(OTHER_KEY)).toBeUndefined()
    expect(client.getQueryData<{ role: string }>(currentUserQueryKey)?.role).toBe('agent')
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalled()
  })

  it('empties the cache when a 401 kills the session mid-logout', async () => {
    mockBackend({ '/api/v1/auth/logout': () => unauthorized() })
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)
    setCsrfToken('stale-token')
    atPath('/')

    const { client } = renderApp({ user: buildUser('admin') })
    // main.tsx's registration, reproduced against this test's client.
    setQueryCacheReset(() => {
      client.clear()
    })
    client.setQueryData(OTHER_KEY, [{ id: 'p-1' }])

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Log out' }))

    await waitFor(() => {
      expect(redirect).toHaveBeenCalledTimes(1)
    })
    expect(getCsrfToken()).toBeNull()
    expect(client.getQueryData(OTHER_KEY)).toBeUndefined()
  })
})

describe('D2 probe: a stale 401 landing on /login', () => {
  it('leaves the freshly-issued CSRF token and cache intact', async () => {
    // window.location is what handleUnauthorized reads; the memory router does not move it.
    atPath('/login')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    setQueryCacheReset(() => {
      client.clear()
    })
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)

    // State a fresh sign-in just left behind.
    setCsrfToken('csrf-from-the-login-that-just-succeeded')
    client.setQueryData(currentUserQueryKey, buildUser('admin'))

    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(unauthorized())),
    )

    // The stale request from the dead session finally lands.
    await expect(apiClient.get('/bookings')).rejects.toThrow()

    expect(getCsrfToken()).toBe('csrf-from-the-login-that-just-succeeded')
    expect(client.getQueryData(currentUserQueryKey)).toBeDefined()
    expect(redirect).not.toHaveBeenCalled()
  })

  it('still tears down when the same stale 401 lands anywhere else', async () => {
    atPath('/properties')
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)
    setCsrfToken('token')
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(unauthorized())),
    )

    await expect(apiClient.get('/bookings')).rejects.toThrow()

    expect(getCsrfToken()).toBeNull()
    expect(redirect).toHaveBeenCalledTimes(1)
  })

  it('does not exempt a path that merely starts with /login', async () => {
    atPath('/login-help')
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)
    setCsrfToken('token')
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(unauthorized())),
    )

    await expect(apiClient.get('/bookings')).rejects.toThrow()

    expect(redirect).toHaveBeenCalledTimes(1)
  })
})

describe('login ordering probe', () => {
  it('a write issued while login is in flight goes out without the new token', async () => {
    let releaseLogin: (() => void) | undefined
    const gate = new Promise<void>((resolve) => {
      releaseLogin = resolve
    })

    const fetchMock = vi.fn((input: string) => {
      const { pathname } = new URL(input)
      if (pathname === '/api/v1/auth/login') {
        return gate.then(() => loginOk('admin', 'csrf-from-login'))
      }
      if (pathname === '/api/v1/auth/me') {
        return Promise.resolve(json(200, buildUser('admin', { email: EMAIL })))
      }
      return Promise.resolve(new Response(null, { status: 204 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Signing in…' })).toBeDisabled()
    })

    // Concurrent write racing the login window.
    const racing = apiClient.post('/bookings', { property_id: 'p-1' })
    releaseLogin?.()
    await racing
    await screen.findByRole('navigation', { name: 'Main' })

    const racingCall = fetchMock.mock.calls.find(
      ([url]) => new URL(url).pathname === '/api/v1/bookings',
    ) as unknown as [string, RequestInit]
    expect((racingCall[1].headers as Headers).get('X-CSRF-Token')).toBeNull()

    // A write issued after the sequence settles carries the token /auth/me derived.
    expect(getCsrfToken()).toBe('tok')
  })

  it('keeps mutation state coherent even though clear() also wipes the mutation cache', async () => {
    let attempt = 0
    mockBackend({
      '/api/v1/auth/login': () => {
        attempt += 1
        return attempt === 1
          ? json(401, { detail: { code: 'invalid_credentials', message: 'Invalid email or password.' } })
          : loginOk('admin')
      },
      '/api/v1/auth/me': () => json(200, buildUser('admin', { email: EMAIL })),
    })
    const { router } = renderApp({ initialEntries: ['/login'] })

    await submitCredentials()
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.')

    await userEvent.setup().click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/')
    // The stale error banner from the first attempt must not survive the second.
    expect(screen.queryByText('Incorrect email or password.')).not.toBeInTheDocument()
  })
})

describe('logout probe', () => {
  it('clears nothing when the network drops mid-logout', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    setCsrfToken('live-token')
    const { router, client } = renderApp({ user: buildUser('admin') })
    setQueryCacheReset(() => {
      client.clear()
    })

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Log out' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not reach the server/)
    expect(router.state.location.pathname).toBe('/')
    expect(getCsrfToken()).toBe('live-token')
    expect(client.getQueryData(currentUserQueryKey)).toBeDefined()
  })

  it('sends the CSRF header before any local teardown, on every attempt', async () => {
    let attempt = 0
    const fetchMock = vi.fn(() => {
      attempt += 1
      return Promise.resolve(
        attempt === 1
          ? json(500, { detail: { code: 'internal_error', message: 'Boom.' } })
          : new Response(null, { status: 204 }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    setCsrfToken('live-token')
    const { router } = renderApp({ user: buildUser('admin') })

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Log out' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Boom.')

    // The retry must still hold a token; a teardown before the request would break it.
    await user.click(screen.getByRole('button', { name: 'Log out' }))
    await screen.findByRole('button', { name: 'Log in' })

    for (const call of fetchMock.mock.calls as unknown as [string, RequestInit][]) {
      expect((call[1].headers as Headers).get('X-CSRF-Token')).toBe('live-token')
    }
    expect(router.state.location.pathname).toBe('/login')
    expect(getCsrfToken()).toBeNull()
  })
})

describe('contract fidelity probe', () => {
  it('never surfaces the backend 401 wording, whatever it says', async () => {
    mockBackend({
      '/api/v1/auth/login': () =>
        json(401, {
          detail: { code: 'invalid_credentials', message: 'No account exists for that email.' },
        }),
    })
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Incorrect email or password.')
    expect(alert).not.toHaveTextContent(/No account exists/)
    expect(screen.queryByLabelText('Email')).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByLabelText('Password')).not.toHaveAttribute('aria-invalid')
  })

  it('a client lands past /login and is stopped only by AppLayout', async () => {
    mockBackend({
      '/api/v1/auth/login': () => loginOk('client'),
      '/api/v1/auth/me': () => json(200, buildUser('client', { email: EMAIL })),
    })
    const { router, client } = renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    expect(await screen.findByText(/This dashboard is for staff/)).toBeVisible()
    // Past /login: the route really changed, so the gate is AppLayout's, not the form's.
    expect(router.state.location.pathname).toBe('/')
    expect(client.getQueryData<{ role: string }>(currentUserQueryKey)?.role).toBe('client')
    expect(getCsrfToken()).toBe('tok')
    // The staff gate has its own logout affordance (D4 fix) rather than leaving a
    // client stuck with a live session and no visible way to end it.
    expect(screen.getByRole('button', { name: 'Log out' })).toBeVisible()
  })

  it('an inactive staff account is stopped by the same gate', async () => {
    mockBackend({
      '/api/v1/auth/login': () => loginOk('admin'),
      '/api/v1/auth/me': () => json(200, buildUser('admin', { email: EMAIL, status: 'disabled' })),
    })
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    expect(await screen.findByText(/This dashboard is for staff/)).toBeVisible()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })
})
