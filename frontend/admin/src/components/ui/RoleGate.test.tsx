import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Role } from '@/lib/auth/types'
import RoleGate from './RoleGate'
import { buildUser, createTestQueryClient, renderWithQueryClient } from '@/test/renderApp'

describe('RoleGate', () => {
  it('renders children for an allowed role', () => {
    renderWithQueryClient(
      <RoleGate allow={['admin']}>
        <p>secret</p>
      </RoleGate>,
      createTestQueryClient(buildUser('admin')),
    )

    expect(screen.getByText('secret')).toBeVisible()
  })

  it('renders the fallback, not the children, for a disallowed role', () => {
    renderWithQueryClient(
      <RoleGate allow={['admin']} fallback={<p>nope</p>}>
        <p>secret</p>
      </RoleGate>,
      createTestQueryClient(buildUser('agent')),
    )

    expect(screen.queryByText('secret')).not.toBeInTheDocument()
    expect(screen.getByText('nope')).toBeVisible()
  })

  it('renders the fallback for a role this build does not know', () => {
    // `allow.includes(role)` is an allowlist, so an unrecognized role is denied by
    // construction. Pinned because the obvious "simplification" to a denylist would
    // silently admit it. The cast mirrors production: `/auth/me` is an unchecked cast,
    // so the compile-time `Role` is no guarantee about what actually arrives.
    renderWithQueryClient(
      <RoleGate allow={['admin']} fallback={<p>nope</p>}>
        <p>secret</p>
      </RoleGate>,
      createTestQueryClient(buildUser('admin', { role: 'root' as Role })),
    )

    expect(screen.queryByText('secret')).not.toBeInTheDocument()
    expect(screen.getByText('nope')).toBeVisible()
  })

  it('renders nothing while identity is unresolved, so a guard cannot bounce a valid user early', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    renderWithQueryClient(
      <RoleGate allow={['admin']} fallback={<p>nope</p>}>
        <p>secret</p>
      </RoleGate>,
    )

    expect(screen.queryByText('secret')).not.toBeInTheDocument()
    expect(screen.queryByText('nope')).not.toBeInTheDocument()
  })
})
