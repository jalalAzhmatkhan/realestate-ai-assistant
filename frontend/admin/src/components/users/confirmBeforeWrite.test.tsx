import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { json, mockApi } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'
import { buildUserPage, buildUserRecord } from '@/test/userFixtures'

const PATH = '/api/v1/users'

/**
 * Every write these two controls can make, counted rather than inspected.
 *
 * Asserting on a request *body* passes just as well when no request was made at all, so
 * a confirm dialog that fired its `PATCH` on open and a second one on confirm would read
 * as correct. These count.
 */
function writesTo(fetchMock: ReturnType<typeof mockApi>, pathname: string): RequestInit[] {
  return fetchMock.mock.calls
    .filter(([input, init]) => new URL(input).pathname === pathname && init?.method === 'PATCH')
    .map(([, init]) => init as RequestInit)
}

function renderUsers(record = buildUserRecord()) {
  const fetchMock = mockApi({
    [`GET ${PATH}`]: () => json(200, buildUserPage([record])),
    [`PATCH ${PATH}/${record.id}`]: () => json(200, { ...record, role: 'agent' }),
  })
  renderApp({ user: buildUser('admin'), initialEntries: ['/users'] })
  return fetchMock
}

describe('role change confirm-before-write', () => {
  it('sends nothing while the confirmation is merely open', async () => {
    const fetchMock = renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Role for Budi Santoso' }),
      'agent',
    )
    expect(await screen.findByRole('dialog')).toBeVisible()

    expect(writesTo(fetchMock, `${PATH}/u-1`)).toHaveLength(0)
  })

  it('sends nothing when the confirmation is dismissed, and once when it is confirmed', async () => {
    const fetchMock = renderUsers()
    await screen.findByText('Budi Santoso')
    const select = screen.getByRole('combobox', { name: 'Role for Budi Santoso' })

    await userEvent.selectOptions(select, 'agent')
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Keep client' }),
    )
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(writesTo(fetchMock, `${PATH}/u-1`)).toHaveLength(0)
    // The control shows the role the backend still holds, not the abandoned pick.
    expect(select).toHaveValue('client')

    // The same control still works afterwards: a dismissal must not wedge it.
    await userEvent.selectOptions(select, 'agent')
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Yes, change it' }),
    )
    await waitFor(() => {
      expect(writesTo(fetchMock, `${PATH}/u-1`)).toHaveLength(1)
    })
  })

  it('dismisses by Escape without writing', async () => {
    const fetchMock = renderUsers()
    await screen.findByText('Budi Santoso')
    const select = screen.getByRole('combobox', { name: 'Role for Budi Santoso' })

    await userEvent.selectOptions(select, 'admin')
    // The event a browser fires on Escape over a `<dialog>`; happy-dom does not
    // synthesize it from a keypress, as `Modal.test.tsx` already works around.
    fireEvent(await screen.findByRole('dialog'), new Event('cancel', { cancelable: true }))

    await waitFor(() => {
      expect(select).toHaveValue('client')
    })
    expect(writesTo(fetchMock, `${PATH}/u-1`)).toHaveLength(0)
  })
})

describe('enable/disable confirm-before-write', () => {
  it('sends nothing when a disable is dismissed', async () => {
    const fetchMock = renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.click(screen.getByRole('button', { name: 'Disable' }))
    const dialog = await screen.findByRole('dialog')
    expect(writesTo(fetchMock, `${PATH}/u-1`)).toHaveLength(0)

    await userEvent.click(within(dialog).getByRole('button', { name: 'Keep as is' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    expect(writesTo(fetchMock, `${PATH}/u-1`)).toHaveLength(0)
    // The row still reads as it did: a dismissed disable changed nothing on screen either.
    expect(screen.getByRole('button', { name: 'Disable' })).toBeVisible()
    // Scoped to the badge: "Active" is also a status filter checkbox on this screen.
    expect(screen.getByTestId('status-badge')).toHaveTextContent('Active')
  })

  it('sends nothing when an enable is dismissed', async () => {
    const fetchMock = renderUsers(buildUserRecord({ status: 'disabled' }))
    await screen.findByText('Budi Santoso')

    await userEvent.click(screen.getByRole('button', { name: 'Enable' }))
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Keep as is' }),
    )

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(writesTo(fetchMock, `${PATH}/u-1`)).toHaveLength(0)
  })

  it('writes exactly once per confirmation, not once per click on it', async () => {
    const fetchMock = mockApi({
      [`GET ${PATH}`]: () => json(200, buildUserPage([buildUserRecord()])),
      // Held open, so the second click lands while the first write is still in flight.
      [`PATCH ${PATH}/u-1`]: () => json(200, buildUserRecord({ status: 'disabled' })),
    })
    renderApp({ user: buildUser('admin'), initialEntries: ['/users'] })
    await screen.findByText('Budi Santoso')

    await userEvent.click(screen.getByRole('button', { name: 'Disable' }))
    const confirm = within(await screen.findByRole('dialog')).getByRole('button', {
      name: 'Yes, disable',
    })
    await userEvent.dblClick(confirm)

    await waitFor(() => {
      expect(writesTo(fetchMock, `${PATH}/u-1`).length).toBeGreaterThan(0)
    })
    expect(writesTo(fetchMock, `${PATH}/u-1`)).toHaveLength(1)
  })
})

/**
 * The client-side disable is a UX nicety; `409 self_lockout_forbidden` is the real
 * boundary (`backend/app/api/users.py::_assert_not_self_lockout`). What is asserted here
 * is only that the nicety is a genuine `disabled` attribute rather than a CSS-only
 * styling that still dispatches a click.
 */
describe('self-row controls are inert, not merely styled as inert', () => {
  it('ignores clicks and keyboard activation on the caller own row', async () => {
    const fetchMock = mockApi({
      [`GET ${PATH}`]: () =>
        json(
          200,
          buildUserPage([
            buildUserRecord({ id: 'u-admin', name: 'Test admin', email: 'me@evdekimi.test', role: 'admin' }),
          ]),
        ),
    })
    renderApp({ user: buildUser('admin'), initialEntries: ['/users'] })
    await screen.findByText('me@evdekimi.test')

    const disable = screen.getByRole('button', { name: 'Disable' })
    const select = screen.getByRole('combobox', { name: 'Role for Test admin' })
    expect(disable).toBeDisabled()
    expect(select).toBeDisabled()

    await userEvent.click(disable)
    // Focus cannot even reach a disabled control, so keyboard activation has no target.
    disable.focus()
    await userEvent.keyboard('{Enter}')
    await userEvent.keyboard(' ')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(writesTo(fetchMock, `${PATH}/u-admin`)).toHaveLength(0)

    // Both controls point at the one note that explains why.
    const noteId = disable.getAttribute('aria-describedby')
    expect(noteId).toBeTruthy()
    expect(select).toHaveAttribute('aria-describedby', noteId)
    expect(document.getElementById(noteId ?? '')).toHaveTextContent(/cannot change your own role/)
  })
})
