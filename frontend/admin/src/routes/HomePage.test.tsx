import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import HomePage from './HomePage'

describe('HomePage', () => {
  it('renders', () => {
    render(<HomePage />)
    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  })
})
