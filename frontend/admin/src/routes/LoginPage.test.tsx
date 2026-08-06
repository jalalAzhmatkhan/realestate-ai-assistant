import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getCsrfToken, setCsrfToken } from '@/lib/api/csrf'
import { setQueryCacheReset, setUnauthorizedRedirect } from '@/lib/api/session'
import { buildUser, renderApp } from '@/test/renderApp'

const EMAIL = 'admin@evdekimi.test'
const PASSWORD = 'correct horse'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Routed by path rather than by call order, because signing in is two requests and a
 * test that asserted on ordering would break the moment one of them is retried.
 * Handlers build a fresh `Response` per call — a body can only be read once.
 */
function mockBackend(handlers: Record<string, () => Response>) {
  const fetchMock = vi.fn((input: string) => {
    const { pathname } = new URL(input)
    const handler = handlers[pathname]
    if (!handler) return Promise.reject(new Error(`unstubbed request: ${pathname}`))
    return Promise.resolve(handler())
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function loginOk(role: 'admin' | 'agent' | 'client' = 'admin') {
  return json(200, {
    access_token: null,
    token_type: null,
    expires_in: 3600,
    csrf_token: 'csrf-from-login',
    user: { id: `u-${role}`, name: `Test ${role}`, email: EMAIL, role },
  })
}

async function submitCredentials() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Email'), EMAIL)
  await user.type(screen.getByLabelText('Password'), PASSWORD)
  await user.click(screen.getByRole('button', { name: 'Log in' }))
}

afterEach(() => {
  setCsrfToken(null)
  setUnauthorizedRedirect(null)
  setQueryCacheReset(null)
})

describe('LoginPage', () => {
  it('signs in as a browser client and lands on the dashboard', async () => {
    const fetchMock = mockBackend({
      '/api/v1/auth/login': () => loginOk(),
      '/api/v1/auth/me': () => json(200, buildUser('admin', { email: EMAIL })),
    })
    const { router } = renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/')

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://localhost:8000/api/v1/auth/login')
    expect(init.credentials).toBe('include')
    // The single most load-bearing field on this screen: the backend defaults to
    // `api`, which issues a Bearer token and sets no cookie at all.
    expect(JSON.parse(init.body as string)).toEqual({
      email: EMAIL,
      password: PASSWORD,
      client_type: 'browser',
    })
  })

  it("holds the CSRF token that /auth/me derives, not the login response's copy", async () => {
    mockBackend({
      '/api/v1/auth/login': () => loginOk(),
      '/api/v1/auth/me': () =>
        json(
          200,
          buildUser('admin', {
            email: EMAIL,
            session: {
              auth_method: 'cookie',
              expires_at: '2026-08-06T11:00:00+07:00',
              csrf_token: 'csrf-from-me',
            },
          }),
        ),
    })
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()
    await screen.findByRole('navigation', { name: 'Main' })

    expect(getCsrfToken()).toBe('csrf-from-me')
  })

  it('drops a previous session’s cached data before the new one starts', async () => {
    mockBackend({
      '/api/v1/auth/login': () => loginOk('agent'),
      '/api/v1/auth/me': () => json(200, buildUser('agent', { email: EMAIL })),
    })
    // The previous user in this tab was an admin whose identity is still cached. Nothing
    // reloads the page across a sign-in, so without the cache reset the agent would be
    // shown the admin's shell.
    const { router } = renderApp({ user: buildUser('admin'), initialEntries: ['/login'] })

    await submitCredentials()

    await screen.findByRole('navigation', { name: 'Main' })
    expect(router.state.location.pathname).toBe('/')
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
  })

  it('reports a rejected sign-in without hinting which field was wrong', async () => {
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)
    mockBackend({
      '/api/v1/auth/login': () =>
        json(401, { detail: { code: 'invalid_credentials', message: 'Invalid email or password.' } }),
    })
    const { router } = renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.')
    // A 401 here is an answer, not a dead session: the screen must not take the
    // app-wide redirect path and reload itself out from under the message.
    expect(redirect).not.toHaveBeenCalled()
    expect(router.state.location.pathname).toBe('/login')
    expect(screen.getByRole('button', { name: 'Log in' })).toBeEnabled()
  })

  it("shows the backend's own wording for a disabled account", async () => {
    mockBackend({
      '/api/v1/auth/login': () =>
        json(403, {
          detail: { code: 'account_disabled', message: 'This account has been disabled.' },
        }),
    })
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    expect(await screen.findByRole('alert')).toHaveTextContent('This account has been disabled.')
  })

  it('puts a 422 next to the field the backend rejected', async () => {
    mockBackend({
      '/api/v1/auth/login': () =>
        json(422, {
          detail: [
            { loc: ['body', 'email'], msg: 'String should have at least 1 character', type: 'too_short' },
          ],
        }),
    })
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    expect(await screen.findByText('String should have at least 1 character')).toBeVisible()
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
  })

  it('reports an unreachable backend instead of failing silently', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not reach the server/)
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument()
  })

  it('blocks a second submit while the first is in flight', async () => {
    mockBackend({ '/api/v1/auth/login': () => loginOk() })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    )
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Signing in…' })).toBeDisabled()
    })
  })

  it('redirects a client the same way as any other role — AppLayout decides what renders next, not the form', async () => {
    mockBackend({
      '/api/v1/auth/login': () => loginOk('client'),
      '/api/v1/auth/me': () => json(200, buildUser('client', { email: EMAIL })),
    })
    renderApp({ initialEntries: ['/login'] })

    await submitCredentials()

    // The form always redirects to `/` regardless of role; AppLayout is what decides a
    // `client` gets the shell (with only Chat in nav) rather than the staff screens —
    // see `lib/auth/sessionBoundary.test.tsx` for that decision's own coverage.
    await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.getByRole('link', { name: 'Chat' })).toBeVisible()
  })
})
