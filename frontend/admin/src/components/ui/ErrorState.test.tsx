import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ErrorState from './ErrorState'
import { ApiError } from '@/lib/api/errors'

describe('ErrorState', () => {
  it("shows the backend's own message for an ApiError", () => {
    render(
      <ErrorState
        error={new ApiError({ status: 409, code: 'slot_unavailable', message: 'That slot is taken.' })}
      />,
    )

    expect(screen.getByText('That slot is taken.')).toBeVisible()
  })

  it('degrades an unknown throw to a fixed sentence instead of leaking internals', () => {
    render(<ErrorState error={new TypeError("Cannot read properties of undefined (reading 'id')")} />)

    expect(screen.queryByText(/Cannot read properties/)).not.toBeInTheDocument()
    expect(screen.getByText(/Something went wrong on our side/)).toBeVisible()
  })

  it('renders no retry affordance when the caller cannot retry', () => {
    render(<ErrorState />)

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('calls onRetry', () => {
    const onRetry = vi.fn()
    render(<ErrorState onRetry={onRetry} />)

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(onRetry).toHaveBeenCalledOnce()
  })
})
