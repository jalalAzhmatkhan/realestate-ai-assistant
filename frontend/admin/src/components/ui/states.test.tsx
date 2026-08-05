import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import Button from './Button'
import EmptyState from './EmptyState'
import LoadingState from './LoadingState'

describe('LoadingState', () => {
  it('announces itself to screen readers, which the skeleton bars alone cannot do', () => {
    render(<LoadingState label="Loading properties…" />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(status).toHaveTextContent('Loading properties…')
  })

  it('renders one skeleton row per expected row', () => {
    const { container } = render(<LoadingState rows={3} />)

    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(3)
  })

  it('renders a spinner instead of skeleton rows for an action', () => {
    const { container } = render(<LoadingState variant="spinner" />)

    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(0)
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(1)
  })
})

describe('EmptyState', () => {
  it('renders the message with no action when none is offered', () => {
    render(<EmptyState title="No bookings yet" />)

    expect(screen.getByText('No bookings yet')).toBeVisible()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('renders the primary action the screen supplies', () => {
    const onClick = vi.fn()
    render(
      <EmptyState
        title="No properties match your filters"
        description="Try widening the search."
        action={<Button onClick={onClick}>Clear filters</Button>}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))

    expect(onClick).toHaveBeenCalledOnce()
    expect(screen.getByText('Try widening the search.')).toBeVisible()
  })
})
