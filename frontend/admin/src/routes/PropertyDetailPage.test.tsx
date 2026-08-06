import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { json, mockApi, requestBody, requestedUrls } from '@/test/mockApi'
import { buildDetail, buildPage, buildSummary } from '@/test/propertyFixtures'
import { buildUser, renderApp } from '@/test/renderApp'

const DETAIL = 'GET /api/v1/properties/prop-1'
const PUT = 'PUT /api/v1/properties/prop-1'
const DELETE = 'DELETE /api/v1/properties/prop-1'

/** `u-agent` owns the fixture listing; `buildUser('agent')` is exactly that id. */
function renderDetail(role: 'admin' | 'agent' = 'agent') {
  return renderApp({ user: buildUser(role), initialEntries: ['/properties/prop-1'] })
}

describe('PropertyDetailPage', () => {
  it('fetches the full record even when the list row is already cached', async () => {
    const fetchMock = mockApi({
      'GET /api/v1/properties': () => json(200, buildPage([buildSummary()])),
      [DETAIL]: () => json(200, buildDetail()),
    })
    const { user } = { user: buildUser('agent') }
    renderApp({ user, initialEntries: ['/properties'] })

    await userEvent.click(await screen.findByRole('link', { name: '2BR Apartment, Kemang' }))

    // The list row has no description, amenities or coordinates. Hydrating the edit form
    // from it would blank all three on the next save.
    await waitFor(() => {
      expect(requestedUrls(fetchMock).some((url) => url.pathname === '/api/v1/properties/prop-1')).toBe(
        true,
      )
    })
    expect(await screen.findByLabelText('Description')).toHaveValue(
      'Quiet corner unit with a garden view.',
    )
    expect(screen.getByLabelText('Amenities')).toHaveValue('Pool\nGym')
    expect(screen.getByLabelText('Latitude')).toHaveValue(-6.2607)
  })

  it('renders another agent’s listing read-only', async () => {
    mockApi({ [DETAIL]: () => json(200, buildDetail({ agent_id: 'u-someone-else' })) })
    renderDetail('agent')

    expect(await screen.findByText('Quiet corner unit with a garden view.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Deactivate' })).not.toBeInTheDocument()
  })

  it('lets an admin edit a listing they do not own', async () => {
    mockApi({ [DETAIL]: () => json(200, buildDetail({ agent_id: 'u-someone-else' })) })
    renderDetail('admin')

    expect(await screen.findByRole('button', { name: 'Save' })).toBeVisible()
  })

  it('submits every field on save, not a diff', async () => {
    const fetchMock = mockApi({
      [DETAIL]: () => json(200, buildDetail()),
      [PUT]: () => json(200, buildDetail({ title: 'Renamed' })),
    })
    renderDetail()

    const title = await screen.findByLabelText('Title')
    await userEvent.clear(title)
    await userEvent.type(title, 'Renamed')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Changes saved.')
    })

    const putIndex = fetchMock.mock.calls.findIndex(([, init]) => init?.method === 'PUT')
    expect(requestBody(fetchMock, putIndex)).toEqual({
      title: 'Renamed',
      property_type: 'apartment',
      listing_type: 'rent',
      price: 3_200_000,
      currency: 'IDR',
      price_unit: 'per_month',
      bedrooms: 2,
      bathrooms: 1,
      area_sqm: 68,
      address: 'Jl. Kemang Raya 12',
      city: 'Jakarta',
      latitude: -6.2607,
      longitude: 106.8135,
      amenities: ['Pool', 'Gym'],
      description: 'Quiet corner unit with a garden view.',
      status: 'active',
      // Resent rather than omitted: the backend defaults an absent `listed_date` to
      // today, which on a full replacement would silently re-date the listing.
      listed_date: '2026-01-15',
    })
    // `agent_id` is never sent — an agent's ownership is the backend's to decide.
    expect(requestBody(fetchMock, putIndex)).not.toHaveProperty('agent_id')
  })

  it('puts a 422 under the field the backend rejected', async () => {
    mockApi({
      [DETAIL]: () => json(200, buildDetail()),
      [PUT]: () =>
        json(422, {
          detail: [
            {
              loc: ['body', 'title'],
              msg: 'String should have at most 200 characters',
              type: 'string_too_long',
            },
            { loc: ['body', 'amenities', 0], msg: 'Input should be a valid string', type: 'string_type' },
          ],
        }),
    })
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: 'Save' }))

    expect(await screen.findByText('String should have at most 200 characters')).toBeVisible()
    expect(screen.getByLabelText('Title')).toHaveAttribute('aria-invalid', 'true')
    // An indexed path lands on the control that produced it, not in a stray list.
    expect(screen.getByLabelText('Amenities')).toHaveAttribute('aria-invalid', 'true')
  })

  it('surfaces a rejected field that has no input on this form', async () => {
    mockApi({
      [DETAIL]: () => json(200, buildDetail()),
      [PUT]: () =>
        json(422, {
          detail: [{ loc: ['body', 'agent_id'], msg: 'Unknown agent.', type: 'value_error' }],
        }),
    })
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('agent_id: Unknown agent.')
  })

  it('confirms before deactivating, and reflects the returned status', async () => {
    const fetchMock = mockApi({
      [DETAIL]: () => json(200, buildDetail()),
      [DELETE]: () => json(200, buildDetail({ status: 'draft' })),
    })
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: 'Deactivate' }))
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: 'Yes, deactivate' }))

    await waitFor(() => {
      expect(screen.getByTestId('status-badge')).toHaveTextContent('Draft')
    })
    // Nothing left to deactivate, and the form must now hold `draft` — otherwise the
    // next Save would submit the old status and undo this.
    expect(screen.queryByRole('button', { name: 'Deactivate' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Status')).toHaveValue('draft')
  })

  it('backs out of the confirmation without deactivating', async () => {
    const fetchMock = mockApi({ [DETAIL]: () => json(200, buildDetail()) })
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: 'Deactivate' }))
    await userEvent.click(screen.getByRole('button', { name: 'No, keep it' }))

    expect(screen.getByRole('button', { name: 'Deactivate' })).toBeVisible()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  })

  it('renders an out-of-scope listing as not found rather than as a raw 404', async () => {
    mockApi({
      [DETAIL]: () =>
        json(404, { detail: { code: 'property_not_found', message: 'Property not found.' } }),
    })
    renderDetail()

    expect(await screen.findByText('Not found or not accessible')).toBeVisible()
    // A 404 is settled; re-fetching it only invites the user to keep pressing.
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '← Back to properties' })).toBeVisible()
  })
})
