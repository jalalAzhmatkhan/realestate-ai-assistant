import { render, screen } from '@testing-library/react'
import { RouterProvider } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import { router } from './router'
import { LOGIN_PATH } from '@/lib/api/session'
import type { Role } from '@/lib/auth/types'
import { buildUser, createTestQueryClient, renderApp } from '@/test/renderApp'
import { QueryClientProvider } from '@tanstack/react-query'

describe('router', () => {
  // Guards the redirect half of the 401 path: lib/api/client.test.ts proves the
  // handler fires, this proves the handler main.tsx registers actually lands on /login.
  it('navigates to /login through the same call the 401 handler makes', async () => {
    // Never resolves: /auth/me stays in flight so the shell holds its loading state
    // instead of racing the assertion with a failed request.
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    expect(await screen.findByTestId('loading-state')).toBeInTheDocument()

    await router.navigate(LOGIN_PATH, { replace: true })

    expect(await screen.findByText('Sign-in form lands in F3.')).toBeVisible()
    expect(window.location.pathname).toBe(LOGIN_PATH)
  })

  it('renders /login outside the shell, so a dead session is not a prerequisite for signing in', () => {
    renderApp({ initialEntries: [LOGIN_PATH] })

    expect(screen.getByText('Sign-in form lands in F3.')).toBeVisible()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('renders /users for an admin', async () => {
    renderApp({ user: buildUser('admin'), initialEntries: ['/users'] })

    expect(await screen.findByRole('heading', { name: 'Users' })).toBeVisible()
  })

  it('bounces an agent off /users to the dashboard with a reason', async () => {
    const { router: memoryRouter } = renderApp({
      user: buildUser('agent'),
      initialEntries: ['/users'],
    })

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent('You do not have access to /users.')
    expect(memoryRouter.state.location.pathname).toBe('/')
  })

  it('shows no denial notice on a normal visit to the dashboard', async () => {
    renderApp({ user: buildUser('agent') })

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

/**
 * Typing a URL is the cheapest way to try to reach a screen the nav does not offer, so
 * every guarded path is exercised here by *entering on it* rather than by navigating to
 * it from inside the shell.
 */
describe('router guards against typed URLs', () => {
  it('shows a client the wrong-app screen at /users, not the users screen', async () => {
    renderApp({ user: buildUser('client'), initialEntries: ['/users'] })

    expect(await screen.findByText(/This dashboard is for staff/)).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Users' })).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('shows an unknown role the wrong-app screen at /users', async () => {
    renderApp({
      user: buildUser('admin', { role: 'superadmin' as Role }),
      initialEntries: ['/users'],
    })

    expect(await screen.findByText(/This dashboard is for staff/)).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Users' })).not.toBeInTheDocument()
  })

  it('keeps an unmatched deep URL inside the shell, where the role still applies', async () => {
    // The catch-all lives among `<AppLayout>`'s children rather than beside it, so a
    // 404 is a gated screen too. Registered as a sibling it would render for anyone.
    renderApp({ user: buildUser('agent'), initialEntries: ['/totally/unknown/deep/path'] })

    await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
  })

  it('blocks a client at an unmatched deep URL', async () => {
    renderApp({ user: buildUser('client'), initialEntries: ['/totally/unknown/deep/path'] })

    expect(await screen.findByText(/This dashboard is for staff/)).toBeVisible()
  })
})

/**
 * The rest of the suite seeds identity into the cache, so the guard always reads a
 * settled role. These two let `/auth/me` genuinely resolve mid-render, which is the only
 * way to catch a guard that evaluates against an undefined user and paints the wrong
 * answer for a frame.
 */
describe('router guards while identity is in flight', () => {
  function deferredFetch() {
    let resolve: (response: Response) => void = () => undefined
    const pending = new Promise<Response>((r) => {
      resolve = r
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => pending),
    )
    return (user: ReturnType<typeof buildUser>) => {
      resolve(
        new Response(JSON.stringify(user), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
  }

  it('never flashes the denial notice to an admin landing on /users', async () => {
    const resolveAs = deferredFetch()
    renderApp({ initialEntries: ['/users'] })

    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Dashboard' })).not.toBeInTheDocument()

    resolveAs(buildUser('admin'))

    expect(await screen.findByRole('heading', { name: 'Users' })).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('never flashes the users screen to an agent landing on /users', async () => {
    const resolveAs = deferredFetch()
    renderApp({ initialEntries: ['/users'] })

    expect(screen.queryByRole('heading', { name: 'Users' })).not.toBeInTheDocument()

    resolveAs(buildUser('agent'))

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Users' })).not.toBeInTheDocument()
  })
})
