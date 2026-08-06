import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getCsrfToken, setCsrfToken } from '@/lib/api/csrf'
import { setUnauthorizedRedirect } from '@/lib/api/session'
import { currentUserQueryKey } from '@/lib/auth/currentUser'
import { buildUser, renderApp } from '@/test/renderApp'

function noContent(): Response {
  return new Response(null, { status: 204 })
}

async function clickLogOut() {
  await userEvent.setup().click(await screen.findByRole('button', { name: 'Log out' }))
}

afterEach(() => {
  setCsrfToken(null)
  setUnauthorizedRedirect(null)
})

describe('logging out', () => {
  it('revokes the session server-side, then clears everything this tab held', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(noContent()))
    vi.stubGlobal('fetch', fetchMock)
    setCsrfToken('live-token')
    const { router, client } = renderApp({ user: buildUser('admin') })

    await clickLogOut()

    expect(await screen.findByRole('button', { name: 'Log in' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/login')
    expect(getCsrfToken()).toBeNull()
    expect(client.getQueryData(currentUserQueryKey)).toBeUndefined()

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://localhost:8000/api/v1/auth/logout')
    expect(init.method).toBe('POST')
    // Logout is state-changing, so it carries the double-submit header like any other
    // write — the token has to still be in memory when the request goes out.
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('live-token')
    expect(init.credentials).toBe('include')
  })

  it('keeps the user signed in and says so when logout fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: { code: 'internal_error', message: 'Boom.' } }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      ),
    )
    setCsrfToken('live-token')
    const { router } = renderApp({ user: buildUser('admin') })

    await clickLogOut()

    // Looking signed out while the cookie is still live is the worse failure: the
    // screen reports the error and the next click is the retry.
    expect(await screen.findByRole('alert')).toHaveTextContent('Boom.')
    expect(router.state.location.pathname).toBe('/')
    expect(getCsrfToken()).toBe('live-token')
    expect(screen.getByRole('button', { name: 'Log out' })).toBeEnabled()
  })
})
