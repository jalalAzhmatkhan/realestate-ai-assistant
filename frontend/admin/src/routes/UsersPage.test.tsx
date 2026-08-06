import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { json, mockApi, requestedUrls } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'
import { buildUserPage, buildUserRecord } from '@/test/userFixtures'

const PATH = '/api/v1/users'
const LIST = `GET ${PATH}`
const CREATE = `POST ${PATH}`

/** `renderApp`'s admin, so a row with this id is the caller's own. */
const SELF_ID = 'u-admin'

function listOf(...users: ReturnType<typeof buildUserRecord>[]) {
  return () => json(200, buildUserPage(users))
}

function renderUsers(entry = '/users') {
  return renderApp({ user: buildUser('admin'), initialEntries: [entry] })
}

function lastListQuery(fetchMock: ReturnType<typeof mockApi>): URLSearchParams {
  const calls = requestedUrls(fetchMock).filter((url) => url.pathname === PATH)
  return calls[calls.length - 1]?.searchParams ?? new URLSearchParams()
}

/** Body of the first request matching a method and path, parsed. */
function bodyOf(fetchMock: ReturnType<typeof mockApi>, method: string, pathname: string): unknown {
  const call = fetchMock.mock.calls.find(
    ([input, init]) => new URL(input).pathname === pathname && (init?.method ?? 'GET') === method,
  )
  const raw = call?.[1]?.body
  return typeof raw === 'string' ? (JSON.parse(raw) as unknown) : undefined
}

function rowFor(email: string): HTMLElement {
  const row = screen.getByText(email).closest('tr')
  if (!row) throw new Error(`no row for ${email}`)
  return row
}

describe('UsersPage list', () => {
  it('renders the users the backend returned', async () => {
    mockApi({
      [LIST]: listOf(
        buildUserRecord(),
        buildUserRecord({
          id: 'u-2',
          name: 'Siti Rahayu',
          email: 'siti@evdekimi.test',
          role: 'agent',
          status: 'disabled',
        }),
      ),
    })
    renderUsers()

    expect(await screen.findByText('Budi Santoso')).toBeVisible()
    const siti = rowFor('siti@evdekimi.test')
    // Status is text as well as color (UI/UX §6).
    expect(within(siti).getByText('Disabled')).toBeVisible()
    expect(within(siti).getByRole('combobox', { name: 'Role for Siti Rahayu' })).toHaveValue('agent')
    expect(within(siti).getByText('15 Jan 2026')).toBeVisible()
  })

  it('shows a loading state before the first page arrives', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    )
    renderUsers()

    expect(screen.getByTestId('loading-state')).toBeVisible()
  })

  it('offers a retry when the list fails to load', async () => {
    const fetchMock = mockApi({
      [LIST]: () => json(500, { detail: { code: 'internal_error', message: 'Boom.' } }),
    })
    renderUsers()

    expect(await screen.findByTestId('error-state')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
    })
  })

  it('offers to add one when the list is genuinely empty', async () => {
    mockApi({ [LIST]: listOf() })
    renderUsers()

    const empty = await screen.findByTestId('empty-state')
    expect(within(empty).getByText('No users yet')).toBeVisible()
    expect(within(empty).getByRole('button', { name: 'Add a user' })).toBeVisible()
  })

  it('offers to clear the filters when they are what emptied the list', async () => {
    mockApi({ [LIST]: listOf() })
    renderUsers('/users?role=admin')

    const empty = await screen.findByTestId('empty-state')
    expect(within(empty).getByText('No users match your filters')).toBeVisible()
    expect(within(empty).getByRole('button', { name: 'Clear filters' })).toBeVisible()
  })

  it('sends the search box and repeats the role param rather than joining it', async () => {
    const fetchMock = mockApi({ [LIST]: listOf(buildUserRecord()) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.type(screen.getByLabelText('Search'), 'budi')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('q')).toBe('budi')
    })

    await userEvent.click(screen.getByRole('checkbox', { name: 'Admin' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Agent' }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).getAll('role')).toEqual(['admin', 'agent'])
    })
    // The typed search survives ticking a checkbox rather than being discarded.
    expect(lastListQuery(fetchMock).get('q')).toBe('budi')
  })

  it('returns to page 1 when the filters change', async () => {
    const fetchMock = mockApi({ [LIST]: listOf(buildUserRecord()) })
    renderUsers('/users?page=3')
    await screen.findByText('Budi Santoso')

    await userEvent.click(screen.getByRole('checkbox', { name: 'Disabled' }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('page')).toBe('1')
    })
  })

  it('asks the backend to sort rather than reordering the page it has', async () => {
    const fetchMock = mockApi({ [LIST]: listOf(buildUserRecord()) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.click(screen.getByRole('button', { name: /Email/ }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('sort')).toBe('email')
    })
  })

  it('pages through the result set', async () => {
    const fetchMock = mockApi({
      [LIST]: () => json(200, buildUserPage([buildUserRecord()], { total: 40, total_pages: 2 })),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.click(screen.getByRole('button', { name: 'Next →' }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('page')).toBe('2')
    })
  })
})

describe('UsersPage role change', () => {
  it('confirms the change, naming both ends of it, before sending anything', async () => {
    // Stateful, so the refetch the mutation triggers answers with the changed role the
    // way a real backend would.
    let role: 'client' | 'agent' = 'client'
    const fetchMock = mockApi({
      [LIST]: () => json(200, buildUserPage([buildUserRecord({ role })])),
      'PATCH /api/v1/users/u-1': () => {
        role = 'agent'
        return json(200, buildUserRecord({ role }))
      },
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Role for Budi Santoso' }),
      'agent',
    )

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Budi Santoso/)).toBeVisible()
    expect(within(dialog).getByText('Client')).toBeVisible()
    expect(within(dialog).getByText('Agent')).toBeVisible()
    // Nothing has been sent yet — the select is staged, not applied.
    expect(bodyOf(fetchMock, 'PATCH', '/api/v1/users/u-1')).toBeUndefined()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, change it' }))

    await waitFor(() => {
      expect(bodyOf(fetchMock, 'PATCH', '/api/v1/users/u-1')).toEqual({ role: 'agent' })
    })
    // The row settles on the confirmed role and the dialog goes away — a change that
    // reverted to "Client" on screen would read as one that did not take.
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('combobox', { name: 'Role for Budi Santoso' })).toHaveValue('agent')
  })

  it('puts the select back when the confirmation is dismissed', async () => {
    const fetchMock = mockApi({ [LIST]: listOf(buildUserRecord()) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const select = screen.getByRole('combobox', { name: 'Role for Budi Santoso' })
    await userEvent.selectOptions(select, 'admin')
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: /^Keep/ }),
    )

    // A cancelled change must not leave the table claiming something that never happened.
    await waitFor(() => {
      expect(select).toHaveValue('client')
    })
    expect(bodyOf(fetchMock, 'PATCH', '/api/v1/users/u-1')).toBeUndefined()
  })

  it('renders the backend self-lockout message as sent', async () => {
    mockApi({
      [LIST]: listOf(buildUserRecord({ id: 'u-9', name: 'Other Admin', role: 'admin' })),
      'PATCH /api/v1/users/u-9': () =>
        json(409, {
          detail: {
            code: 'self_lockout_forbidden',
            message: 'You cannot disable your own account or remove your own admin role.',
          },
        }),
    })
    renderUsers()
    await screen.findByText('Other Admin')

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Role for Other Admin' }),
      'agent',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Yes, change it' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You cannot disable your own account or remove your own admin role.',
    )
  })
})

describe('UsersPage enable/disable', () => {
  it('disables an account through a confirmation, never a delete', async () => {
    const fetchMock = mockApi({
      [LIST]: listOf(buildUserRecord()),
      'PATCH /api/v1/users/u-1': () => json(200, buildUserRecord({ status: 'disabled' })),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.click(within(rowFor('budi@evdekimi.test')).getByRole('button', { name: 'Disable' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/signed out on their next request/)).toBeVisible()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, disable' }))

    await waitFor(() => {
      expect(bodyOf(fetchMock, 'PATCH', '/api/v1/users/u-1')).toEqual({ status: 'disabled' })
    })
    // There is no DELETE /users/{id} in the contract; disabling is the deactivation path.
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  })

  it('confirms re-enabling too, since a misclick there restores access', async () => {
    const fetchMock = mockApi({
      [LIST]: listOf(buildUserRecord({ status: 'disabled' })),
      'PATCH /api/v1/users/u-1': () => json(200, buildUserRecord()),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.click(within(rowFor('budi@evdekimi.test')).getByRole('button', { name: 'Enable' }))
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Yes, enable' }),
    )

    await waitFor(() => {
      expect(bodyOf(fetchMock, 'PATCH', '/api/v1/users/u-1')).toEqual({ status: 'active' })
    })
  })
})

describe('UsersPage self-lockout guards', () => {
  it("inerts the caller's own row controls and says why", async () => {
    mockApi({
      [LIST]: listOf(
        buildUserRecord({ id: SELF_ID, name: 'Test admin', email: 'me@evdekimi.test', role: 'admin' }),
        buildUserRecord(),
      ),
    })
    renderUsers()
    // Waited on by email: "Test admin" is also the signed-in name in the top bar, so
    // waiting on that would resolve before the list had arrived.
    await screen.findByText('me@evdekimi.test')

    const own = rowFor('me@evdekimi.test')
    expect(within(own).getByRole('combobox', { name: 'Role for Test admin' })).toBeDisabled()
    expect(within(own).getByRole('button', { name: 'Disable' })).toBeDisabled()
    expect(within(own).getByText(/This is your account/)).toBeVisible()

    // Absence on one row alone would read as a rendering bug, so the control stays put
    // and is explained — and every other row is untouched.
    const other = rowFor('budi@evdekimi.test')
    expect(within(other).getByRole('combobox', { name: 'Role for Budi Santoso' })).toBeEnabled()
    expect(within(other).getByRole('button', { name: 'Disable' })).toBeEnabled()
  })
})

describe('UsersPage create', () => {
  async function openCreateForm() {
    await userEvent.click(screen.getByRole('button', { name: '+ Add user' }))
    return screen.findByRole('dialog')
  }

  it('creates a user from the modal and closes it', async () => {
    const fetchMock = mockApi({
      [LIST]: listOf(buildUserRecord()),
      [CREATE]: () => json(201, buildUserRecord({ id: 'u-new', name: 'Ana Putri' })),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const dialog = await openCreateForm()
    await userEvent.type(within(dialog).getByLabelText('Name'), 'Ana Putri')
    await userEvent.type(within(dialog).getByLabelText('Email'), '  Ana@Evdekimi.test  ')
    await userEvent.type(within(dialog).getByLabelText('Temporary password'), 'correct horse ')
    await userEvent.selectOptions(within(dialog).getByRole('combobox', { name: 'Role' }), 'agent')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create user' }))

    await waitFor(() => {
      expect(bodyOf(fetchMock, 'POST', PATH)).toEqual({
        name: 'Ana Putri',
        email: 'Ana@Evdekimi.test',
        // Never trimmed: trailing whitespace is part of the password.
        password: 'correct horse ',
        role: 'agent',
        status: 'active',
      })
    })
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })

  it('masks the password field and does not offer the admin their own saved one', async () => {
    mockApi({ [LIST]: listOf(buildUserRecord()) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const password = within(await openCreateForm()).getByLabelText('Temporary password')
    expect(password).toHaveAttribute('type', 'password')
    expect(password).toHaveAttribute('autocomplete', 'new-password')
  })

  it('routes the 409 email conflict onto the email field instead of a banner', async () => {
    mockApi({
      [LIST]: listOf(buildUserRecord()),
      [CREATE]: () =>
        json(409, {
          detail: {
            code: 'email_already_exists',
            message: 'That email address is already registered.',
          },
        }),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const dialog = await openCreateForm()
    await userEvent.type(within(dialog).getByLabelText('Name'), 'Ana Putri')
    await userEvent.type(within(dialog).getByLabelText('Email'), 'budi@evdekimi.test')
    await userEvent.type(within(dialog).getByLabelText('Temporary password'), 'hunter2hunter2')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create user' }))

    const email = within(dialog).getByLabelText('Email')
    await waitFor(() => {
      expect(email).toHaveAttribute('aria-invalid', 'true')
    })
    expect(within(dialog).getByText('That email address is already registered.')).toBeVisible()
    // Said once, next to the field that has to change — not also as a banner.
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('routes a 422 onto the field the backend named', async () => {
    mockApi({
      [LIST]: listOf(buildUserRecord()),
      [CREATE]: () =>
        json(422, {
          detail: [
            { loc: ['body', 'password'], msg: 'String should have at least 8 characters', type: 'too_short' },
          ],
        }),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const dialog = await openCreateForm()
    await userEvent.type(within(dialog).getByLabelText('Name'), 'Ana Putri')
    await userEvent.type(within(dialog).getByLabelText('Email'), 'ana@evdekimi.test')
    await userEvent.type(within(dialog).getByLabelText('Temporary password'), 'shortish')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create user' }))

    expect(
      await within(dialog).findByText('String should have at least 8 characters'),
    ).toBeVisible()
  })
})
