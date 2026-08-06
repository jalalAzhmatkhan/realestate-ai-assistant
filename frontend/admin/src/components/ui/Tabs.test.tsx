import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import Tabs from './Tabs'

const ITEMS = [
  { id: 'a', label: 'Tab A' },
  { id: 'b', label: 'Tab B' },
]

describe('Tabs', () => {
  it('marks the active tab as selected and the other as not', () => {
    render(<Tabs items={ITEMS} active="a" onChange={vi.fn()} label="Test tabs" />)

    expect(screen.getByRole('tab', { name: 'Tab A' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Tab B' })).toHaveAttribute('aria-selected', 'false')
  })

  it('reports the clicked tab id without changing anything itself', async () => {
    const onChange = vi.fn()
    render(<Tabs items={ITEMS} active="a" onChange={onChange} label="Test tabs" />)

    await userEvent.click(screen.getByRole('tab', { name: 'Tab B' }))

    expect(onChange).toHaveBeenCalledWith('b')
    // Still "a": the component holds no state of its own — the caller decides.
    expect(screen.getByRole('tab', { name: 'Tab A' })).toHaveAttribute('aria-selected', 'true')
  })

  it('exposes an accessible tablist name', () => {
    render(<Tabs items={ITEMS} active="a" onChange={vi.fn()} label="Test tabs" />)
    expect(screen.getByRole('tablist', { name: 'Test tabs' })).toBeVisible()
  })
})
