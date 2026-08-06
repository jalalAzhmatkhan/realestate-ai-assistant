import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { isStaffRole } from '@/lib/auth/types'
import type { CurrentUser, Role } from '@/lib/auth/types'
import { buildUser, renderApp } from '@/test/renderApp'

/**
 * The backend could send a role this build has never heard of — a future role, or a
 * typo. `CurrentUser.role` is a compile-time claim only: `/auth/me` is deserialized
 * with an unchecked cast, so the cast here reproduces what actually reaches the render.
 */
function buildUserWithRole(role: string): CurrentUser {
  return buildUser('admin', { role: role as Role })
}

describe('AppLayout', () => {
  it('shows the admin every nav item', async () => {
    renderApp({ user: buildUser('admin') })

    await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.getByRole('link', { name: 'Properties' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Bookings' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Users' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Chat Inspector' })).toBeVisible()
  })

  it('does not render the Users or Chat Inspector nav items for an agent at all', async () => {
    renderApp({ user: buildUser('agent') })

    await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.getByRole('link', { name: 'Properties' })).toBeVisible()
    // `queryByRole`, not a visibility assertion: the requirement is that the link is
    // absent from the DOM, not merely hidden (frontend-engineer.md §2).
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Chat Inspector' })).not.toBeInTheDocument()
  })

  it('always shows which role the app is rendering as', async () => {
    renderApp({ user: buildUser('agent') })

    expect(await screen.findByText('agent')).toBeVisible()
  })

  it('admits a client to the shell, showing only the Chat nav item', async () => {
    // Chatting with the agent is this product's actual purpose, and this app is the only
    // real chat surface that exists — a `client` gets the normal shell, not the wrong-app
    // screen, but the nav only ever shows them one item (`components/layout/navItems.ts`).
    renderApp({ user: buildUser('client') })

    await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.getByRole('link', { name: 'Chat' })).toBeVisible()
    expect(screen.queryByRole('link', { name: 'Properties' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Bookings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Chat Inspector' })).not.toBeInTheDocument()
    expect(screen.queryByText(/cannot access this app/)).not.toBeInTheDocument()
  })

  it('still offers a way to end the session from the wrong-app screen', async () => {
    // This screen renders before <TopBar>, so without its own logout affordance an
    // account stuck here (an unrecognized role, or a disabled account) would have a
    // live session and no visible way to end it short of typing /login.
    renderApp({ user: buildUserWithRole('superadmin') })

    expect(await screen.findByRole('button', { name: 'Log out' })).toBeVisible()
  })

  it('holds a loading state while identity is in flight', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))
    renderApp()

    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('offers a retry when identity cannot be loaded, without leaking the raw failure', async () => {
    const fetchMock = vi.fn(() => Promise.reject(new Error('socket hang up at server.ts:42')))
    vi.stubGlobal('fetch', fetchMock)
    renderApp()

    expect(await screen.findByTestId('error-state')).toBeInTheDocument()
    expect(screen.queryByText(/socket hang up/)).not.toBeInTheDocument()

    const before = fetchMock.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(before)
    })
  })

  it('asks the real /auth/me for identity, with the session cookie attached', async () => {
    // Every other test in this file seeds identity through the cache, which is what keeps
    // them about rendering rather than transport. That leaves the request itself unpinned,
    // so a typo'd path or a dropped `credentials` would not fail anything. This pins it.
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined))
    vi.stubGlobal('fetch', fetchMock)

    renderApp()

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://localhost:8000/api/v1/auth/me')
    expect(init.method).toBe('GET')
    expect(init.credentials).toBe('include')
  })
})

/**
 * The gate is an allowlist (`STAFF_ROLES.includes`), not a denylist (`role !== 'client'`),
 * and the difference only shows up on input neither form was written for. Rewritten as a
 * denylist every assertion below flips to rendering the staff shell, so these are the pin
 * against that refactor — not redundant coverage of the `client` case above.
 */
describe('AppLayout access gate fails safe', () => {
  it('treats a role this build does not know as non-staff', () => {
    expect(isStaffRole('superadmin' as Role)).toBe(false)
    expect(isStaffRole('' as Role)).toBe(false)
    // Roles are matched exactly; a case variant is not an admin.
    expect(isStaffRole('ADMIN' as Role)).toBe(false)
    expect(isStaffRole(undefined as unknown as Role)).toBe(false)
  })

  it('denies an unknown role instead of rendering the staff shell', async () => {
    renderApp({ user: buildUserWithRole('superadmin') })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('denies a payload with no role at all', async () => {
    const user = { ...buildUser('admin') } as Record<string, unknown>
    delete user.role
    renderApp({ user: user as unknown as CurrentUser })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('denies a disabled admin, whose role alone would otherwise pass', async () => {
    // Defense in depth, not the primary control: `/auth/me` already answers `401
    // session_revoked` for a user disabled mid-session (backend README), so reaching this
    // render means that upstream check did not fire.
    renderApp({ user: buildUser('admin', { status: 'disabled' }) })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
  })

  it('denies a disabled agent', async () => {
    renderApp({ user: buildUser('agent', { status: 'disabled' }) })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('denies an unrecognized status rather than treating anything non-disabled as active', async () => {
    // `status !== 'active'` is also an allowlist. `'suspended'` is not a value the backend
    // documents today, which is the point: a new one must opt in, not be admitted by default.
    renderApp({ user: buildUser('admin', { status: 'suspended' }) })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
  })

  it('denies a payload with no status at all', async () => {
    const user = { ...buildUser('admin') } as Record<string, unknown>
    delete user.status
    renderApp({ user: user as unknown as CurrentUser })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
  })

  it('denies a disabled client, whose role alone would otherwise now pass', async () => {
    // The `status` check is unconditional — admitting `client` past the role half of the
    // gate must not also skip the status half.
    renderApp({ user: buildUser('client', { status: 'disabled' }) })

    expect(await screen.findByText(/cannot access this app/)).toBeVisible()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })
})
