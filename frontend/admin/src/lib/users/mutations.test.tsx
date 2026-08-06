import { useQuery } from '@tanstack/react-query'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { agentOptionsQueryOptions } from '@/lib/bookings/filterOptions'
import { json, mockApi, requestedUrls } from '@/test/mockApi'
import { createTestQueryClient, renderWithQueryClient } from '@/test/renderApp'
import { buildUserPage, buildUserRecord } from '@/test/userFixtures'
import { useUpdateUser } from './mutations'

const USERS = '/api/v1/users'

/**
 * Both the bookings agent picker and a user write live in one tree here, because the
 * dependency between them is a query key — the one thing a per-screen test cannot see.
 * A key that no longer matches would leave the picker holding a five-minute-stale list
 * with no visible symptom on either screen.
 */
function Harness({ userId }: { userId: string }) {
  const agents = useQuery(agentOptionsQueryOptions())

  return (
    <div>
      <span data-testid="agents">{agents.data?.results.map((agent) => agent.name).join(',')}</span>
      <WriteButton userId={userId} label="promote" payload={{ role: 'agent' }} />
    </div>
  )
}

/** The write on its own, for the cases that must not mount the picker. */
function WriteButton({
  userId,
  label,
  payload,
}: {
  userId: string
  label: string
  payload: { role?: 'agent'; status?: 'disabled' }
}) {
  const update = useUpdateUser(userId)
  return (
    <button
      type="button"
      onClick={() => {
        update.mutate(payload)
      }}
    >
      {label}
    </button>
  )
}

/** The agent picker's own request, told apart from the users list by its `role=agent`. */
function agentOptionRequests(fetchMock: ReturnType<typeof mockApi>): URL[] {
  return requestedUrls(fetchMock).filter(
    (url) => url.pathname === USERS && url.searchParams.getAll('role').includes('agent'),
  )
}

describe('user write cache invalidation', () => {
  it('refetches the bookings agent picker when a user is promoted to agent', async () => {
    let agents = [{ id: 'u-9', name: 'Siti Rahayu' }]
    const fetchMock = mockApi({
      [`GET ${USERS}`]: () => json(200, buildUserPage(agents.map((a) => buildUserRecord(a)))),
      [`PATCH ${USERS}/u-1`]: () => {
        agents = [...agents, { id: 'u-1', name: 'Budi Santoso' }]
        return json(200, buildUserRecord({ role: 'agent' }))
      },
    })

    renderWithQueryClient(<Harness userId="u-1" />)
    await waitFor(() => {
      expect(screen.getByTestId('agents')).toHaveTextContent('Siti Rahayu')
    })
    expect(agentOptionRequests(fetchMock)).toHaveLength(1)

    await userEvent.click(screen.getByRole('button', { name: 'promote' }))

    // The picker refetches despite a five-minute staleTime, and lands on the newly
    // promoted agent. A key that did not match would leave it on "Siti Rahayu" alone.
    await waitFor(() => {
      expect(screen.getByTestId('agents')).toHaveTextContent('Siti Rahayu,Budi Santoso')
    })
    expect(agentOptionRequests(fetchMock).length).toBeGreaterThan(1)
  })

  /**
   * The same guarantee without an observer mounted, so the assertion is on the key
   * itself rather than on a refetch a mounted picker happens to trigger. This is the
   * case that actually ships: the admin is on `/users`, the bookings screen is not
   * rendered, and its cached options must still be marked stale for the next visit.
   */
  it('invalidates the cached agent options even with no picker on screen', async () => {
    const client = createTestQueryClient()
    const key = agentOptionsQueryOptions().queryKey
    client.setQueryData(key, buildUserPage([buildUserRecord({ id: 'u-9', role: 'agent' })]))

    mockApi({
      [`GET ${USERS}`]: () => json(200, buildUserPage([])),
      [`PATCH ${USERS}/u-1`]: () => json(200, buildUserRecord({ role: 'agent' })),
    })

    renderWithQueryClient(
      <WriteButton userId="u-1" label="promote" payload={{ role: 'agent' }} />,
      client,
    )
    await userEvent.click(screen.getByRole('button', { name: 'promote' }))

    await waitFor(() => {
      expect(client.getQueryState(key)?.isInvalidated).toBe(true)
    })
  })

  it('invalidates the picker on a status change too, not only on a role change', async () => {
    const client = createTestQueryClient()
    const key = agentOptionsQueryOptions().queryKey
    client.setQueryData(key, buildUserPage([buildUserRecord({ id: 'u-1', role: 'agent' })]))

    mockApi({
      [`GET ${USERS}`]: () => json(200, buildUserPage([])),
      [`PATCH ${USERS}/u-1`]: () => json(200, buildUserRecord({ status: 'disabled' })),
    })

    renderWithQueryClient(
      <WriteButton userId="u-1" label="disable" payload={{ status: 'disabled' }} />,
      client,
    )
    await userEvent.click(screen.getByRole('button', { name: 'disable' }))

    // A disabled agent is still listed by `GET /users?role=agent`, but the picker is a
    // slice of the same collection and must not diverge from it either way.
    await waitFor(() => {
      expect(client.getQueryState(key)?.isInvalidated).toBe(true)
    })
  })
})
