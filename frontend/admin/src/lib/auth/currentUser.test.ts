import { QueryClient } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getCsrfToken, setCsrfToken } from '@/lib/api/csrf'
import { currentUserQueryOptions } from './currentUser'
import type { CurrentUser } from './types'

function meResponse(csrfToken: string | null, authMethod: 'cookie' | 'bearer' = 'cookie') {
  return new Response(
    JSON.stringify({
      id: 'u-admin',
      name: 'Ana Putri',
      email: 'ana@evdekimi.test',
      role: 'admin',
      status: 'active',
      session: {
        auth_method: authMethod,
        expires_at: '2026-08-06T11:00:00+07:00',
        csrf_token: csrfToken,
      },
    } satisfies CurrentUser),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
}

afterEach(() => {
  setCsrfToken(null)
})

describe('session bootstrap', () => {
  it('recovers the CSRF token a page reload wiped', async () => {
    // The state after F5: the httpOnly cookie survived, the in-memory token did not.
    // Without this re-seed the app renders as signed in and every write 403s with
    // `csrf_token_missing`.
    expect(getCsrfToken()).toBeNull()
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(meResponse('csrf-rederived-from-the-cookie'))),
    )

    const user = await new QueryClient().fetchQuery(currentUserQueryOptions())

    expect(user.role).toBe('admin')
    expect(getCsrfToken()).toBe('csrf-rederived-from-the-cookie')
  })

  it('re-seeds on every /auth/me, not only the first', async () => {
    // The token is rotated per session, so a later `/auth/me` — a manual retry off the
    // error state, say — has to win over whatever is already in memory.
    setCsrfToken('stale-token')
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(meResponse('current-token'))),
    )

    await new QueryClient().fetchQuery(currentUserQueryOptions())

    expect(getCsrfToken()).toBe('current-token')
  })
})
