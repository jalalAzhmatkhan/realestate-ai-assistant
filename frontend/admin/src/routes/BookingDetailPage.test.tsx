import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { buildBooking } from '@/test/bookingFixtures'
import { json, mockApi, requestBody, type ApiHandler } from '@/test/mockApi'
import { buildUser, renderApp } from '@/test/renderApp'

const DETAIL = 'GET /api/v1/bookings/booking-1'
const CANCEL = 'POST /api/v1/bookings/booking-1/cancel'
const RESCHEDULE = 'POST /api/v1/bookings/booking-1/reschedule'

function renderDetail(handlers: Record<string, ApiHandler>, role: 'admin' | 'agent' = 'agent') {
  const fetchMock = mockApi(handlers)
  const rendered = renderApp({ user: buildUser(role), initialEntries: ['/bookings/booking-1'] })
  return { fetchMock, ...rendered }
}

function rescheduleBody(fetchMock: ReturnType<typeof mockApi>, occurrence: number): unknown {
  const indexes = fetchMock.mock.calls
    .map((call, index) => ({ call, index }))
    .filter(({ call }) => String(call[0]).endsWith('/reschedule'))
    .map(({ index }) => index)
  return requestBody(fetchMock, indexes[occurrence] ?? -1)
}

/** The `409` payload both conflict codes carry. */
function conflict(code: 'slot_unavailable' | 'booking_slot_conflict', message: string) {
  return json(409, {
    detail: {
      code,
      message,
      suggested_alternatives: [
        { availability_slot_id: 'avail-002', slot_time: '2026-08-08T07:00:00Z' },
        { availability_slot_id: 'avail-003', slot_time: '2026-08-09T03:00:00Z' },
      ],
    },
  })
}

describe('BookingDetailPage', () => {
  it('shows the names, the slot in the viewer’s clock, and the reschedule count', async () => {
    renderDetail({ [DETAIL]: () => json(200, buildBooking({ rescheduled_count: 2 })) })

    expect(await screen.findByText('Budi Santoso')).toBeVisible()
    expect(screen.getByText('Siti Rahayu')).toBeVisible()
    expect(screen.getByText('2 times')).toBeVisible()
    expect(screen.getByText('Saturday, 8 August 2026 at 10:00 GMT+7')).toBeVisible()
  })

  it('renders an out-of-scope booking as not found rather than as forbidden', async () => {
    renderDetail({
      [DETAIL]: () => json(404, { detail: { code: 'booking_not_found', message: 'No matching booking was found.' } }),
    })

    expect(await screen.findByText('Not found or not accessible')).toBeVisible()
    // A 404 is settled; a retry button would only invite the user to keep pressing.
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '← Back to bookings' })).toBeVisible()
  })

  it('offers neither action on a cancelled booking, and says why', async () => {
    renderDetail({ [DETAIL]: () => json(200, buildBooking({ status: 'cancelled' })) })

    expect(await screen.findByText(/its slot released/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Cancel booking' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Reschedule booking' })).not.toBeInTheDocument()
  })

  it('cancels behind the modal and settles into the cancelled state', async () => {
    const { fetchMock } = renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [CANCEL]: () => json(200, buildBooking({ status: 'cancelled' })),
    })

    await userEvent.click(await screen.findByRole('button', { name: 'Cancel booking' }))
    // The modal names what is being cancelled, so a misclick is caught before the write.
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Saturday, 8 August 2026 at 10:00/)).toBeVisible()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)

    await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, cancel it' }))

    expect(await screen.findByText(/its slot released/)).toBeVisible()
    expect(screen.getByTestId('status-badge')).toHaveTextContent('Cancelled')
  })

  it('treats an idempotent second cancel as success, not as an error', async () => {
    // What a stale tab sends: the backend answers 200 with the already-cancelled record
    // rather than a conflict, and the screen must not invent an error state for it.
    renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [CANCEL]: () => json(200, buildBooking({ status: 'cancelled' })),
    })

    await userEvent.click(await screen.findByRole('button', { name: 'Cancel booking' }))
    await userEvent.click(screen.getByRole('button', { name: 'Yes, cancel it' }))

    await waitFor(() => {
      expect(screen.getByTestId('status-badge')).toHaveTextContent('Cancelled')
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps the modal open and explains a refused cancel', async () => {
    renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [CANCEL]: () =>
        json(409, {
          detail: {
            code: 'booking_not_cancellable',
            message: 'This booking can no longer be cancelled.',
          },
        }),
    })

    await userEvent.click(await screen.findByRole('button', { name: 'Cancel booking' }))
    await userEvent.click(screen.getByRole('button', { name: 'Yes, cancel it' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'This booking can no longer be cancelled.',
    )
  })

  it('reschedules to a chosen time and reports where it moved from', async () => {
    const { fetchMock } = renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [RESCHEDULE]: () =>
        json(200, {
          ...buildBooking({ slot_time: '2026-08-09T03:00:00Z', rescheduled_count: 1 }),
          previous_slot_time: '2026-08-08T03:00:00Z',
        }),
    })

    const input = await screen.findByLabelText('New date and time')
    // Prefilled with the current slot in local time, so the user edits a known-good value.
    expect(input).toHaveValue('2026-08-08T10:00')

    await userEvent.clear(input)
    await userEvent.type(input, '2026-08-09T10:00')
    await userEvent.type(screen.getByLabelText(/Reason/), 'Client asked to move it.')
    await userEvent.click(screen.getByRole('button', { name: 'Reschedule' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Moved from Saturday, 8 August 2026 at 10:00 GMT+7 to Sunday, 9 August 2026 at 10:00 GMT+7',
    )
    expect(rescheduleBody(fetchMock, 0)).toEqual({
      // The local wall clock the user picked, sent as the instant it denotes.
      requested_slot_time: '2026-08-09T03:00:00.000Z',
      reason: 'Client asked to move it.',
    })
    // The form re-syncs to the new slot; resubmitting it would otherwise be
    // `slot_unchanged` against a time that did change.
    expect(screen.getByLabelText('New date and time')).toHaveValue('2026-08-09T10:00')
  })

  it('omits an untouched reason rather than sending an empty string', async () => {
    const { fetchMock } = renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [RESCHEDULE]: () =>
        json(200, {
          ...buildBooking({ slot_time: '2026-08-09T03:00:00Z' }),
          previous_slot_time: '2026-08-08T03:00:00Z',
        }),
    })

    const input = await screen.findByLabelText('New date and time')
    await userEvent.clear(input)
    await userEvent.type(input, '2026-08-09T10:00')
    await userEvent.click(screen.getByRole('button', { name: 'Reschedule' }))

    await screen.findByRole('status')
    expect(rescheduleBody(fetchMock, 0)).not.toHaveProperty('reason')
  })

  it('renders a 409 conflict as clickable alternatives, and retries with the one picked', async () => {
    let attempt = 0
    const { fetchMock } = renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [RESCHEDULE]: () => {
        attempt += 1
        if (attempt === 1) return conflict('slot_unavailable', 'That time is not in the agent’s availability.')
        return json(200, {
          ...buildBooking({ slot_time: '2026-08-08T07:00:00Z', rescheduled_count: 1 }),
          previous_slot_time: '2026-08-08T03:00:00Z',
        })
      },
    })

    const input = await screen.findByLabelText('New date and time')
    await userEvent.clear(input)
    await userEvent.type(input, '2026-08-08T11:15')
    await userEvent.click(screen.getByRole('button', { name: 'Reschedule' }))

    // The conflict is a set of choices, not a message: the backend already looked these
    // openings up, so recovery is one click rather than another guess.
    const suggestions = await screen.findByTestId('suggested-alternatives')
    expect(within(suggestions).getByText('That time is not in the agent’s availability.')).toBeVisible()
    expect(within(suggestions).getByRole('button', { name: 'Sat 8 Aug, 14:00' })).toBeVisible()
    expect(within(suggestions).getByRole('button', { name: 'Sun 9 Aug, 10:00' })).toBeVisible()

    await userEvent.click(within(suggestions).getByRole('button', { name: 'Sat 8 Aug, 14:00' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Moved from')
    // Retried with the alternative's own `slot_time`, untouched — not with a time this
    // screen re-derived from the label it rendered.
    expect(rescheduleBody(fetchMock, 1)).toEqual({ requested_slot_time: '2026-08-08T07:00:00Z' })
    expect(screen.queryByTestId('suggested-alternatives')).not.toBeInTheDocument()
  })

  it('keeps the alternatives visible, with the picked one pending, while its own retry is in flight', async () => {
    // The retry an alternative click fires clears `reschedule.error` the instant it
    // starts — deriving the panel straight from that error made it unmount mid-submit,
    // taking the pending affordance and the disabled-other-options state with it. This
    // pins the panel staying mounted for the retry it was itself the cause of.
    let rescheduleCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string, init: RequestInit = {}) => {
        const { pathname } = new URL(input)
        const method = init.method ?? 'GET'
        if (method === 'GET' && pathname === '/api/v1/bookings/booking-1') {
          return Promise.resolve(json(200, buildBooking()))
        }
        if (method === 'POST' && pathname === '/api/v1/bookings/booking-1/reschedule') {
          rescheduleCalls += 1
          if (rescheduleCalls === 1) {
            return Promise.resolve(
              conflict('slot_unavailable', 'That time is not in the agent’s availability.'),
            )
          }
          // The retry itself hangs, so there is a moment to inspect it mid-flight.
          return new Promise<Response>(() => undefined)
        }
        return Promise.reject(new Error(`unstubbed request: ${method} ${pathname}`))
      }),
    )
    renderApp({ user: buildUser('agent'), initialEntries: ['/bookings/booking-1'] })

    const input = await screen.findByLabelText('New date and time')
    await userEvent.clear(input)
    await userEvent.type(input, '2026-08-08T11:15')
    await userEvent.click(screen.getByRole('button', { name: 'Reschedule' }))

    const suggestions = await screen.findByTestId('suggested-alternatives')
    const picked = within(suggestions).getByRole('button', { name: 'Sat 8 Aug, 14:00' })
    const other = within(suggestions).getByRole('button', { name: 'Sun 9 Aug, 10:00' })

    await userEvent.click(picked)

    // Still mounted, not replaced by a generic error and not gone entirely.
    await waitFor(() => {
      expect(screen.getByTestId('suggested-alternatives')).toBeInTheDocument()
    })
    expect(picked).toHaveTextContent('Sat 8 Aug, 14:00 …')
    expect(other).toBeDisabled()
  })

  it('offers the alternatives a booked-slot conflict carries too', async () => {
    renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [RESCHEDULE]: () => conflict('booking_slot_conflict', 'That slot is already booked.'),
    })

    const input = await screen.findByLabelText('New date and time')
    await userEvent.clear(input)
    await userEvent.type(input, '2026-08-08T14:00')
    await userEvent.click(screen.getByRole('button', { name: 'Reschedule' }))

    const suggestions = await screen.findByTestId('suggested-alternatives')
    expect(within(suggestions).getByText('That slot is already booked.')).toBeVisible()
  })

  it('says so plainly when a conflict has no alternatives to offer', async () => {
    renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [RESCHEDULE]: () =>
        json(409, {
          detail: {
            code: 'slot_unavailable',
            message: 'That time is not in the agent’s availability.',
            suggested_alternatives: [],
          },
        }),
    })

    const input = await screen.findByLabelText('New date and time')
    await userEvent.clear(input)
    await userEvent.type(input, '2026-08-08T14:00')
    await userEvent.click(screen.getByRole('button', { name: 'Reschedule' }))

    // An empty list is its own answer — it must not collapse into "no conflict happened".
    expect(await screen.findByText(/No nearby times are open either/)).toBeVisible()
  })

  it('shows a non-conflict rejection as a plain message, with no alternatives', async () => {
    renderDetail({
      [DETAIL]: () => json(200, buildBooking()),
      [RESCHEDULE]: () =>
        json(422, {
          detail: { code: 'slot_unchanged', message: 'The booking is already at that time.' },
        }),
    })

    await userEvent.click(await screen.findByRole('button', { name: 'Reschedule' }))

    expect(await screen.findByText('The booking is already at that time.')).toBeVisible()
    expect(screen.queryByTestId('suggested-alternatives')).not.toBeInTheDocument()
  })

  it('does not call the endpoint when no time is picked', async () => {
    const { fetchMock } = renderDetail({ [DETAIL]: () => json(200, buildBooking()) })

    await userEvent.clear(await screen.findByLabelText('New date and time'))
    await userEvent.click(screen.getByRole('button', { name: 'Reschedule' }))

    expect(await screen.findByText('Pick a date and time to move this viewing to.')).toBeVisible()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})
