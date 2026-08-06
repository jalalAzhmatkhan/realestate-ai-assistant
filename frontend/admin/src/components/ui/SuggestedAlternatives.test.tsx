import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import SuggestedAlternatives, { type AlternativeSlot } from './SuggestedAlternatives'

/**
 * `Z`, not `+07:00`: `UtcDateTime` (backend `app/models/types.py`) normalizes every
 * datetime to UTC on the way out, so a 10:00 Jakarta slot arrives as `03:00Z`. These
 * fixtures are the shape the wire actually carries.
 */
const alternatives: AlternativeSlot[] = [
  { availability_slot_id: 'avail-001', slot_time: '2026-08-08T03:00:00Z' },
  { availability_slot_id: 'avail-002', slot_time: '2026-08-08T07:30:00Z' },
]

describe('SuggestedAlternatives', () => {
  it("renders the backend's conflict message rather than a generic one", () => {
    render(
      <SuggestedAlternatives
        alternatives={alternatives}
        onSelect={vi.fn()}
        message="Saturday 10:00 was just booked."
      />,
    )

    expect(screen.getByText('Saturday 10:00 was just booked.')).toBeVisible()
  })

  it('converts the UTC instant into the viewer’s own clock', () => {
    render(<SuggestedAlternatives alternatives={alternatives} onSelect={vi.fn()} />)

    // `03:00Z` is a 10:00 viewing in Jakarta (the pinned test timezone). Rendering the
    // literal wall clock out of the string would offer "03:00" for a 10:00 slot — a
    // seven-hour booking error, not a formatting quirk.
    expect(screen.getByRole('button', { name: 'Sat 8 Aug, 10:00' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Sat 8 Aug, 14:30' })).toBeVisible()
  })

  it('hands the whole slot back on select, so the caller has the id to submit', () => {
    const onSelect = vi.fn()
    render(<SuggestedAlternatives alternatives={alternatives} onSelect={onSelect} />)

    fireEvent.click(screen.getByRole('button', { name: 'Sat 8 Aug, 14:30' }))

    expect(onSelect).toHaveBeenCalledWith(alternatives[1])
  })

  it('says so plainly when the backend found no alternatives', () => {
    render(<SuggestedAlternatives alternatives={[]} onSelect={vi.fn()} />)

    expect(screen.getByText(/No nearby times are open either/)).toBeVisible()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('locks every option while one is being submitted', () => {
    const onSelect = vi.fn()
    render(
      <SuggestedAlternatives
        alternatives={alternatives}
        onSelect={onSelect}
        pendingSlotId="avail-001"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /14:30/ }))

    expect(onSelect).not.toHaveBeenCalled()
  })

  it('falls back to the raw value rather than rendering "Invalid Date"', () => {
    render(
      <SuggestedAlternatives
        alternatives={[{ availability_slot_id: 'x', slot_time: 'not-a-timestamp' }]}
        onSelect={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'not-a-timestamp' })).toBeVisible()
  })
})
