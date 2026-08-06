import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { buildBooking, buildBookingPage } from '@/test/bookingFixtures'
import { json, mockApi, requestedUrls, type ApiHandler } from '@/test/mockApi'
import { buildPage, buildSummary } from '@/test/propertyFixtures'
import { buildUser, renderApp } from '@/test/renderApp'

const LIST = 'GET /api/v1/bookings'
const PROPERTY_OPTIONS = 'GET /api/v1/properties'
const AGENT_OPTIONS = 'GET /api/v1/users'

/** The filter bar fetches its own option lists, so every list test has to stub them. */
function stubs(overrides: Record<string, ApiHandler> = {}) {
  return mockApi({
    [PROPERTY_OPTIONS]: () => json(200, buildPage([buildSummary()])),
    [AGENT_OPTIONS]: () =>
      json(200, { results: [{ id: 'u-agent', name: 'Siti Rahayu' }], page: 1, page_size: 100, total: 1, total_pages: 1 }),
    [LIST]: () => json(200, buildBookingPage([buildBooking()])),
    ...overrides,
  })
}

function bookingsQuery(fetchMock: ReturnType<typeof mockApi>): URLSearchParams | undefined {
  return requestedUrls(fetchMock).find((url) => url.pathname === '/api/v1/bookings')?.searchParams
}

describe('BookingsPage', () => {
  it('renders names rather than the ids the row also carries', async () => {
    stubs()
    renderApp({ user: buildUser('admin'), initialEntries: ['/bookings'] })

    expect(await screen.findByRole('link', { name: '2BR Apartment, Kemang' })).toBeVisible()

    // Scoped to the row: the agent filter's options carry the same names.
    const row = screen.getAllByRole('row')[1]
    expect(within(row).getByText('Budi Santoso')).toBeVisible()
    expect(within(row).getByText('Siti Rahayu')).toBeVisible()
    // The three name fields exist so this screen never has to resolve an id itself.
    expect(screen.queryByText('u-client')).not.toBeInTheDocument()
    expect(screen.queryByText('prop-1')).not.toBeInTheDocument()
  })

  it('renders the UTC slot in the viewer’s own clock', async () => {
    stubs()
    renderApp({ user: buildUser('admin'), initialEntries: ['/bookings'] })

    // 03:00Z is a 10:00 viewing in Jakarta. Printing the wall clock from the string
    // would send an agent seven hours early.
    expect(await screen.findByText('Sat 8 Aug, 10:00')).toBeVisible()
  })

  it('sends the filters as query params, widening the dates to whole local days', async () => {
    const fetchMock = stubs()
    renderApp({
      user: buildUser('admin'),
      initialEntries: ['/bookings?status=confirmed&date_from=2026-08-08&date_to=2026-08-09'],
    })

    await screen.findByRole('link', { name: '2BR Apartment, Kemang' })

    const query = bookingsQuery(fetchMock)
    expect(query?.getAll('status')).toEqual(['confirmed'])
    // Local midnight and local end-of-day in Jakarta, as UTC instants: a bare date would
    // shift the inclusive range by seven hours and drop the last evening's viewings.
    expect(query?.get('date_from')).toBe('2026-08-07T17:00:00.000Z')
    expect(query?.get('date_to')).toBe('2026-08-09T16:59:59.999Z')
  })

  it('keeps the filter state in the URL so a filtered list is linkable', async () => {
    stubs()
    const { router } = renderApp({ user: buildUser('admin'), initialEntries: ['/bookings'] })

    await userEvent.click(await screen.findByLabelText('Cancelled'))

    await waitFor(() => {
      expect(router.state.location.search).toBe('?status=cancelled')
    })
  })

  it('drops a hand-edited date filter that is not a calendar date', async () => {
    const fetchMock = stubs()
    renderApp({ user: buildUser('admin'), initialEntries: ['/bookings?date_from=last-tuesday'] })

    await screen.findByRole('link', { name: '2BR Apartment, Kemang' })

    // Forwarding it would produce a 422 about a filter the user never set.
    expect(bookingsQuery(fetchMock)?.has('date_from')).toBe(false)
  })

  it('offers an admin the agent filter, and asks the admin-only endpoint for its options', async () => {
    const fetchMock = stubs()
    renderApp({ user: buildUser('admin'), initialEntries: ['/bookings'] })

    expect(await screen.findByLabelText('Agent')).toBeVisible()
    await waitFor(() => {
      expect(requestedUrls(fetchMock).some((url) => url.pathname === '/api/v1/users')).toBe(true)
    })
  })

  it('hides the agent filter and column from an agent, and never calls /users', async () => {
    const fetchMock = stubs()
    renderApp({ user: buildUser('agent'), initialEntries: ['/bookings'] })

    await screen.findByRole('link', { name: '2BR Apartment, Kemang' })

    // Their list is already scoped to themselves server-side, so the column would repeat
    // one name down the page and the filter could only ever be a no-op.
    expect(screen.queryByLabelText('Agent')).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /Agent/ })).not.toBeInTheDocument()
    expect(screen.getByText('Viewings you are conducting.')).toBeVisible()
    // `GET /users` is admin-only; firing it here would be a guaranteed 403.
    expect(requestedUrls(fetchMock).some((url) => url.pathname === '/api/v1/users')).toBe(false)
  })

  it('still filters when an option list fails to load', async () => {
    stubs({ [PROPERTY_OPTIONS]: () => json(500, {}) })
    renderApp({ user: buildUser('admin'), initialEntries: ['/bookings'] })

    expect(await screen.findByText(/Property list unavailable/)).toBeVisible()
    // The bookings themselves loaded; one failed dropdown must not take the screen down.
    expect(screen.getByRole('link', { name: '2BR Apartment, Kemang' })).toBeVisible()
    expect(screen.getByLabelText('Viewings from')).toBeEnabled()
  })

  it('tells an impossible date range apart from a failure worth retrying', async () => {
    stubs({
      [LIST]: () =>
        json(422, {
          detail: {
            code: 'invalid_date_range',
            message: 'date_from must not be later than date_to.',
          },
        }),
    })
    const { router } = renderApp({
      user: buildUser('admin'),
      initialEntries: ['/bookings?date_from=2026-08-09&date_to=2026-08-08'],
    })

    expect(await screen.findByText('That date range cannot be searched')).toBeVisible()
    // Retrying would re-send the same range the backend has already refused.
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()

    const errorState = screen.getByTestId('error-state')
    await userEvent.click(within(errorState).getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => {
      expect(router.state.location.search).toBe('')
    })
  })

  it('distinguishes an emptied filter from an empty list', async () => {
    stubs({ [LIST]: () => json(200, buildBookingPage([])) })
    const { unmount } = renderApp({
      user: buildUser('admin'),
      initialEntries: ['/bookings?status=cancelled'],
    })

    expect(await screen.findByText('No bookings match your filters')).toBeVisible()
    unmount()

    stubs({ [LIST]: () => json(200, buildBookingPage([])) })
    renderApp({ user: buildUser('admin'), initialEntries: ['/bookings'] })

    expect(await screen.findByText('No bookings yet')).toBeVisible()
    // Nothing to create from here: viewings are booked through the assistant.
    expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument()
  })

  it('cancels from a row behind a confirmation, and reflects the new status', async () => {
    let cancelled = false
    const fetchMock = stubs({
      [LIST]: () => json(200, buildBookingPage([buildBooking(cancelled ? { status: 'cancelled' } : {})])),
      'POST /api/v1/bookings/booking-1/cancel': () => {
        cancelled = true
        return json(200, buildBooking({ status: 'cancelled' }))
      },
    })
    renderApp({ user: buildUser('admin'), initialEntries: ['/bookings'] })

    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/cannot be undone/)).toBeVisible()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, cancel it' }))

    await waitFor(() => {
      expect(screen.getByTestId('status-badge')).toHaveTextContent('Cancelled')
    })
    // Nothing left to cancel, so the action is gone rather than disabled.
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
  })

  it('backs out of the row confirmation without navigating away', async () => {
    const fetchMock = stubs()
    const { router } = renderApp({ user: buildUser('admin'), initialEntries: ['/bookings'] })

    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await userEvent.click(screen.getByRole('button', { name: 'Keep booking' }))

    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    // The modal lives inside a clickable row; without the guard, dismissing it would
    // also open the booking.
    expect(router.state.location.pathname).toBe('/bookings')
  })
})
