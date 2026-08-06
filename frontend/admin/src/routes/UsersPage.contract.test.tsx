import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { json, mockApi, requestedUrls } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'
import { buildUserPage, buildUserRecord } from '@/test/userFixtures'

const PATH = '/api/v1/users'
const LIST = `GET ${PATH}`
const CREATE = `POST ${PATH}`

function renderUsers(entry = '/users') {
  return renderApp({ user: buildUser('admin'), initialEntries: [entry] })
}

function lastListQuery(fetchMock: ReturnType<typeof mockApi>): URLSearchParams {
  const calls = requestedUrls(fetchMock).filter((url) => url.pathname === PATH)
  return calls[calls.length - 1]?.searchParams ?? new URLSearchParams()
}

async function openCreateForm() {
  await userEvent.click(screen.getByRole('button', { name: '+ Add user' }))
  return screen.findByRole('dialog')
}

async function fillAndSubmit(dialog: HTMLElement, email = 'ana@evdekimi.test') {
  await userEvent.type(within(dialog).getByLabelText('Name'), 'Ana Putri')
  await userEvent.type(within(dialog).getByLabelText('Email'), email)
  await userEvent.type(within(dialog).getByLabelText('Temporary password'), 'hunter2hunter2')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Create user' }))
}

/**
 * `email_already_exists` is a `409` carrying `detail.code`; a length or format rejection
 * is a `422` carrying FastAPI's field list. The create form routes both onto the email
 * input through two different branches, so each is checked not to have eaten the other.
 */
describe('create form 422-vs-409 routing', () => {
  it('routes a 422 on the email field itself, not only on other fields', async () => {
    mockApi({
      [LIST]: () => json(200, buildUserPage([buildUserRecord()])),
      [CREATE]: () =>
        json(422, {
          detail: [
            {
              loc: ['body', 'email'],
              msg: 'String should have at least 3 characters',
              type: 'too_short',
            },
          ],
        }),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const dialog = await openCreateForm()
    await fillAndSubmit(dialog, 'a@b.c')

    // The 409-specific branch sits in front of this field's error lookup; a branch that
    // returned early would leave a genuine 422 on the same field invisible.
    expect(
      await within(dialog).findByText('String should have at least 3 characters'),
    ).toBeVisible()
    expect(within(dialog).getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
  })

  it('shows a 422 on a non-email field against that field and not against email', async () => {
    mockApi({
      [LIST]: () => json(200, buildUserPage([buildUserRecord()])),
      [CREATE]: () =>
        json(422, {
          detail: [
            {
              loc: ['body', 'name'],
              msg: 'String should have at most 200 characters',
              type: 'too_long',
            },
          ],
        }),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const dialog = await openCreateForm()
    await fillAndSubmit(dialog)

    expect(await within(dialog).findByText('String should have at most 200 characters')).toBeVisible()
    expect(within(dialog).getByLabelText('Name')).toHaveAttribute('aria-invalid', 'true')
    expect(within(dialog).getByLabelText('Email')).not.toHaveAttribute('aria-invalid')
  })

  it('keeps a 422 in the banner-suppression path distinct from the 409 one', async () => {
    mockApi({
      [LIST]: () => json(200, buildUserPage([buildUserRecord()])),
      [CREATE]: () =>
        json(422, {
          detail: [{ loc: ['body', 'role'], msg: 'Input should be a valid role', type: 'enum' }],
        }),
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const dialog = await openCreateForm()
    await fillAndSubmit(dialog)

    // A 422 keeps its summary banner (the field list can name a field this form does not
    // render); only the 409 suppresses it, because that one is always about `email`.
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Some fields need attention.')
    expect(within(dialog).getByText('Input should be a valid role')).toBeVisible()
  })

  it('recovers to a clean form after a 409, so a corrected email can be resubmitted', async () => {
    let attempt = 0
    mockApi({
      [LIST]: () => json(200, buildUserPage([buildUserRecord()])),
      [CREATE]: () => {
        attempt += 1
        return attempt === 1
          ? json(409, {
              detail: { code: 'email_already_exists', message: 'That email address is already registered.' },
            })
          : json(201, buildUserRecord({ id: 'u-new', name: 'Ana Putri' }))
      },
    })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const dialog = await openCreateForm()
    await fillAndSubmit(dialog, 'budi@evdekimi.test')
    expect(await within(dialog).findByText('That email address is already registered.')).toBeVisible()

    const email = within(dialog).getByLabelText('Email')
    await userEvent.clear(email)
    await userEvent.type(email, 'ana@evdekimi.test')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create user' }))

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })
})

describe('user list filters and sort against the backend contract', () => {
  it('repeats the status param rather than joining it, as it does for role', async () => {
    const fetchMock = mockApi({ [LIST]: () => json(200, buildUserPage([buildUserRecord()])) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const filters = screen.getByRole('form', { name: 'User filters' })
    await userEvent.click(within(filters).getByRole('checkbox', { name: 'Active' }))
    await waitFor(() => {
      expect(lastListQuery(fetchMock).getAll('status')).toEqual(['active'])
    })

    await userEvent.click(within(filters).getByRole('checkbox', { name: 'Disabled' }))
    await waitFor(() => {
      // OR within one filter, one repeated key — `app/api/users.py` reads it with `in_`.
      expect(lastListQuery(fetchMock).getAll('status')).toEqual(['active', 'disabled'])
    })
    expect(lastListQuery(fetchMock).toString()).not.toContain('active%2Cdisabled')
  })

  it('combines two different filters as AND while each stays repeatable', async () => {
    const fetchMock = mockApi({ [LIST]: () => json(200, buildUserPage([buildUserRecord()])) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const filters = screen.getByRole('form', { name: 'User filters' })
    await userEvent.click(within(filters).getByRole('checkbox', { name: 'Agent' }))
    await userEvent.click(within(filters).getByRole('checkbox', { name: 'Admin' }))
    await userEvent.click(within(filters).getByRole('checkbox', { name: 'Disabled' }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).getAll('role')).toEqual(['agent', 'admin'])
    })
    expect(lastListQuery(fetchMock).getAll('status')).toEqual(['disabled'])
  })

  it('carries a typed search across a checkbox tick instead of discarding it', async () => {
    const fetchMock = mockApi({ [LIST]: () => json(200, buildUserPage([buildUserRecord()])) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const filters = screen.getByRole('form', { name: 'User filters' })
    await userEvent.type(within(filters).getByLabelText('Search'), 'budi')
    await userEvent.click(within(filters).getByRole('checkbox', { name: 'Client' }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('q')).toBe('budi')
    })
    expect(lastListQuery(fetchMock).getAll('role')).toEqual(['client'])
  })

  it('offers sorting only on the backend whitelist, and not on status', async () => {
    mockApi({ [LIST]: () => json(200, buildUserPage([buildUserRecord()])) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    const headers = screen.getAllByRole('columnheader')
    const sortable = headers
      .filter((header) => within(header).queryByRole('button'))
      .map((header) => header.textContent?.replace(/[^A-Za-z ]/g, '').trim())

    // `USER_SORT_COLUMNS` in `app/api/users.py` is exactly these four; a sort by `status`
    // would come back 422, so the column must not offer one.
    expect(sortable).toEqual(['Name', 'Email', 'Role', 'Created'])
    const status = headers.find((header) => header.textContent?.trim() === 'Status')
    expect(status).toBeDefined()
    expect(within(status as HTMLElement).queryByRole('button')).toBeNull()
    expect(status).not.toHaveAttribute('aria-sort')
  })

  it('never asks the backend for a sort outside the whitelist, even from a hand-edited URL', async () => {
    const fetchMock = mockApi({ [LIST]: () => json(200, buildUserPage([buildUserRecord()])) })
    renderUsers('/users?sort=-status')
    await screen.findByText('Budi Santoso')

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('sort')).toBe('name')
    })
  })

  it('toggles a whitelisted column between ascending and descending', async () => {
    const fetchMock = mockApi({ [LIST]: () => json(200, buildUserPage([buildUserRecord()])) })
    renderUsers()
    await screen.findByText('Budi Santoso')

    await userEvent.click(screen.getByRole('button', { name: /Email/ }))
    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('sort')).toBe('email')
    })

    await userEvent.click(screen.getByRole('button', { name: /Email/ }))
    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('sort')).toBe('-email')
    })
    // The header is uppercased by CSS, so its text is still "Email".
    expect(
      screen.getAllByRole('columnheader').find((header) => header.textContent?.includes('Email')),
    ).toHaveAttribute('aria-sort', 'descending')
  })

  it('sends the fixed page size and resets to page 1 when a filter changes', async () => {
    const fetchMock = mockApi({
      [LIST]: () => json(200, buildUserPage([buildUserRecord()], { page: 2, total_pages: 3, total: 41 })),
    })
    renderUsers('/users?page=2')
    await screen.findByText('Budi Santoso')

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('page')).toBe('2')
    })
    expect(lastListQuery(fetchMock).get('page_size')).toBe('20')

    await userEvent.click(
      within(screen.getByRole('form', { name: 'User filters' })).getByRole('checkbox', {
        name: 'Agent',
      }),
    )
    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('page')).toBe('1')
    })
  })
})
