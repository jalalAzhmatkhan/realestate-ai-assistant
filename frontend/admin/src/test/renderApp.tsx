import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { routes } from '@/app/router'
import { currentUserQueryKey } from '@/lib/auth/currentUser'
import type { CurrentUser, Role } from '@/lib/auth/types'

export function buildUser(role: Role, overrides: Partial<CurrentUser> = {}): CurrentUser {
  return {
    id: `u-${role}`,
    name: `Test ${role}`,
    email: `${role}@evdekimi.test`,
    role,
    status: 'active',
    session: { auth_method: 'cookie', expires_at: '2026-08-05T11:00:00+07:00', csrf_token: 'tok' },
    ...overrides,
  }
}

/**
 * Seeds identity through the cache rather than by stubbing `fetch`, so a role test
 * exercises the same `useCurrentUser` path production uses without depending on how
 * `/auth/me` happens to be fetched.
 */
export function createTestQueryClient(user?: CurrentUser): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (user) client.setQueryData(currentUserQueryKey, user)
  return client
}

export function renderWithQueryClient(ui: ReactElement, client = createTestQueryClient()) {
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

export function renderApp(options: { user?: CurrentUser; initialEntries?: string[] } = {}) {
  const client = createTestQueryClient(options.user)
  const router = createMemoryRouter(routes, { initialEntries: options.initialEntries ?? ['/'] })

  return {
    router,
    ...render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  }
}
