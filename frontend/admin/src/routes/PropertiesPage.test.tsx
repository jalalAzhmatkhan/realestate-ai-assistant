import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { json, mockApi, requestedUrls } from '@/test/mockApi'
import { buildDetail, buildPage, buildSummary } from '@/test/propertyFixtures'
import { buildUser, renderApp } from '@/test/renderApp'

const LIST = 'GET /api/v1/properties'

function listOf(...properties: ReturnType<typeof buildSummary>[]) {
  return () => json(200, buildPage(properties))
}

/** The query string of the most recent list request. */
function lastListQuery(fetchMock: ReturnType<typeof mockApi>): URLSearchParams {
  const listCalls = requestedUrls(fetchMock).filter((url) => url.pathname === '/api/v1/properties')
  return listCalls[listCalls.length - 1]?.searchParams ?? new URLSearchParams()
}

function renderList(entry = '/properties', role: 'admin' | 'agent' = 'admin') {
  return renderApp({ user: buildUser(role), initialEntries: [entry] })
}

describe('PropertiesPage', () => {
  it('renders the listings the backend returned', async () => {
    mockApi({
      [LIST]: listOf(
        buildSummary(),
        buildSummary({ id: 'prop-2', title: 'Villa Ubud', city: 'Bali', status: 'draft' }),
      ),
    })
    renderList()

    expect(await screen.findByRole('link', { name: '2BR Apartment, Kemang' })).toBeVisible()
    const row = screen.getByRole('link', { name: 'Villa Ubud' }).closest('tr')
    expect(within(row as HTMLElement).getByText('Draft')).toBeVisible()
    // Status is never conveyed by color alone (UI/UX §6).
    expect(within(row as HTMLElement).getByText('Bali')).toBeVisible()
  })

  it('shows a loading state before the first page arrives', () => {
    mockApi({ [LIST]: () => json(200, buildPage([])) })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    )
    renderList()

    expect(screen.getByTestId('loading-state')).toBeVisible()
  })

  it('offers a retry when the list fails to load', async () => {
    const fetchMock = mockApi({
      [LIST]: () => json(500, { detail: { code: 'internal_error', message: 'Boom.' } }),
    })
    renderList()

    expect(await screen.findByTestId('error-state')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
    })
  })

  it('offers to add one when nothing has been created yet', async () => {
    mockApi({ [LIST]: listOf() })
    renderList()

    const empty = await screen.findByTestId('empty-state')
    expect(within(empty).getByText('No properties yet')).toBeVisible()
    expect(within(empty).getByRole('button', { name: 'Add a property' })).toBeVisible()
  })

  it('offers to clear the filters when they are what emptied the list', async () => {
    mockApi({ [LIST]: listOf() })
    renderList('/properties?city=Bandung')

    const empty = await screen.findByTestId('empty-state')
    // "No properties yet" here would read as an empty database and hide the one control
    // that fixes it.
    expect(within(empty).getByText('No properties match your filters')).toBeVisible()
    expect(within(empty).getByRole('button', { name: 'Clear filters' })).toBeVisible()
  })

  it('sends the search box and city as backend filters', async () => {
    const fetchMock = mockApi({ [LIST]: listOf(buildSummary()) })
    renderList()
    await screen.findByRole('link', { name: '2BR Apartment, Kemang' })

    await userEvent.type(screen.getByLabelText('Search'), 'kemang')
    await userEvent.type(screen.getByLabelText('City'), 'Jakarta')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('q')).toBe('kemang')
    })
    expect(lastListQuery(fetchMock).get('city')).toBe('Jakarta')
  })

  it('repeats the status param rather than joining the values', async () => {
    const fetchMock = mockApi({ [LIST]: listOf(buildSummary()) })
    renderList()
    await screen.findByRole('link', { name: '2BR Apartment, Kemang' })

    await userEvent.click(screen.getByRole('checkbox', { name: 'Active' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Draft' }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).getAll('status')).toEqual(['active', 'draft'])
    })
  })

  it('returns to page 1 when the filters change', async () => {
    const fetchMock = mockApi({ [LIST]: listOf(buildSummary()) })
    renderList('/properties?page=3')
    await screen.findByRole('link', { name: '2BR Apartment, Kemang' })

    await userEvent.click(screen.getByRole('checkbox', { name: 'Villa' }))

    // Page 3 of a narrower result set is an empty page nobody asked for.
    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('page')).toBe('1')
    })
  })

  it('asks the backend to sort rather than reordering the page it has', async () => {
    const fetchMock = mockApi({ [LIST]: listOf(buildSummary()) })
    renderList()
    await screen.findByRole('link', { name: '2BR Apartment, Kemang' })

    await userEvent.click(screen.getByRole('button', { name: /Price/ }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('sort')).toBe('price')
    })
  })

  it('pages through the result set', async () => {
    const fetchMock = mockApi({
      [LIST]: () => json(200, buildPage([buildSummary()], { total: 40, total_pages: 2 })),
    })
    renderList()
    await screen.findByRole('link', { name: '2BR Apartment, Kemang' })

    await userEvent.click(screen.getByRole('button', { name: 'Next →' }))

    await waitFor(() => {
      expect(lastListQuery(fetchMock).get('page')).toBe('2')
    })
  })

  it('opens a listing from its row', async () => {
    mockApi({
      [LIST]: listOf(buildSummary()),
      'GET /api/v1/properties/prop-1': () => json(200, buildDetail()),
    })
    const { router } = renderList()

    await userEvent.click(await screen.findByRole('link', { name: '2BR Apartment, Kemang' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/properties/prop-1')
    })
  })

  it('offers "Add property" to an agent, who may create their own listings', async () => {
    mockApi({ [LIST]: listOf(buildSummary()) })
    renderList('/properties', 'agent')

    expect(await screen.findByRole('button', { name: '+ Add property' })).toBeVisible()
  })
})
