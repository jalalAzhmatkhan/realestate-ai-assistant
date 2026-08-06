import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { json, mockApi, requestBody, requestedUrls } from '@/test/mockApi'
import { buildDetail } from '@/test/propertyFixtures'
import { buildUser, renderApp } from '@/test/renderApp'

const CREATE = 'POST /api/v1/properties'

async function fillRequiredFields() {
  await userEvent.type(screen.getByLabelText('Title'), 'Studio near MRT')
  await userEvent.type(screen.getByLabelText('Address'), 'Jl. Sudirman 1')
  await userEvent.type(screen.getByLabelText('City'), 'Jakarta')
  await userEvent.type(screen.getByLabelText('Price'), '2500000')
  await userEvent.type(screen.getByLabelText('Bedrooms'), '1')
  await userEvent.type(screen.getByLabelText('Bathrooms'), '1')
  await userEvent.type(screen.getByLabelText('Area (m²)'), '32')
  await userEvent.type(screen.getByLabelText('Latitude'), '-6.2088')
  await userEvent.type(screen.getByLabelText('Longitude'), '106.8456')
}

describe('PropertyCreatePage', () => {
  it('never fetches a listing with the id "new"', async () => {
    const fetchMock = mockApi({ [CREATE]: () => json(201, buildDetail()) })
    renderApp({ user: buildUser('agent'), initialEntries: ['/properties/new'] })

    expect(await screen.findByRole('heading', { name: 'New property' })).toBeVisible()
    // The create screen is its own route, not the detail screen holding a magic id.
    expect(requestedUrls(fetchMock)).toEqual([])
  })

  it('starts as a draft so a create cannot publish by omission', async () => {
    mockApi({ [CREATE]: () => json(201, buildDetail()) })
    renderApp({ user: buildUser('agent'), initialEntries: ['/properties/new'] })

    expect(await screen.findByLabelText('Status')).toHaveValue('draft')
    expect(screen.getByLabelText('Currency')).toHaveValue('IDR')
  })

  it('posts the full record and opens the listing it created', async () => {
    const fetchMock = mockApi({
      [CREATE]: () => json(201, buildDetail({ id: 'prop-9' })),
      'GET /api/v1/properties/prop-9': () => json(200, buildDetail({ id: 'prop-9' })),
    })
    const { router } = renderApp({ user: buildUser('agent'), initialEntries: ['/properties/new'] })

    await screen.findByRole('heading', { name: 'New property' })
    await fillRequiredFields()
    await userEvent.type(screen.getByLabelText('Amenities'), 'Pool\nGym')
    await userEvent.click(screen.getByRole('button', { name: 'Create property' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/properties/prop-9')
    })

    const body = requestBody(fetchMock)
    expect(body).toMatchObject({
      title: 'Studio near MRT',
      city: 'Jakarta',
      price: 2_500_000,
      latitude: -6.2088,
      longitude: 106.8456,
      status: 'draft',
      currency: 'IDR',
      // Split on newlines rather than commas, so an amenity may contain a comma.
      amenities: ['Pool', 'Gym'],
    })
    // Omitted, not sent blank: the backend stamps today, and an `agent` never chooses
    // their own listing's owner.
    expect(body).not.toHaveProperty('listed_date')
    expect(body).not.toHaveProperty('agent_id')
  })

  it('keeps the user on the form and maps a 422 to its field', async () => {
    mockApi({
      [CREATE]: () =>
        json(422, {
          detail: [
            { loc: ['body', 'city'], msg: 'String should have at least 1 character', type: 'too_short' },
          ],
        }),
    })
    const { router } = renderApp({ user: buildUser('agent'), initialEntries: ['/properties/new'] })

    await screen.findByRole('heading', { name: 'New property' })
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Create property' }))

    expect(await screen.findByText('String should have at least 1 character')).toBeVisible()
    expect(screen.getByLabelText('City')).toHaveAttribute('aria-invalid', 'true')
    expect(router.state.location.pathname).toBe('/properties/new')
    // The typed values survive a rejected save; retyping the whole form would be the
    // real cost of a validation error.
    expect(screen.getByLabelText('Title')).toHaveValue('Studio near MRT')
  })

  it('reports an unreachable backend rather than failing silently', async () => {
    mockApi({
      [CREATE]: () => {
        throw new TypeError('Failed to fetch')
      },
    })
    renderApp({ user: buildUser('agent'), initialEntries: ['/properties/new'] })

    await screen.findByRole('heading', { name: 'New property' })
    await fillRequiredFields()
    await userEvent.click(screen.getByRole('button', { name: 'Create property' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not reach the server/)
  })
})
