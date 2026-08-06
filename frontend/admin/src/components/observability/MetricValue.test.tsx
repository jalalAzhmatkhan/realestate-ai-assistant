import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import MetricValue from './MetricValue'

/**
 * Pinned on its own: the task spec is explicit that a `null` metric (a `failed` run's
 * `recall_at_k`/`mrr`/etc.) must render as "—", never as "0" or "0.00" — a null means
 * "not computed", not "zero". This is the one component every metric on the eval-runs
 * tab routes through, so the distinction only has to hold here.
 */
describe('MetricValue', () => {
  it('renders null as an em dash, never as zero', () => {
    render(<MetricValue value={null} />)
    expect(screen.getByText('—')).toBeVisible()
    expect(screen.queryByText('0')).not.toBeInTheDocument()
    expect(screen.queryByText('0.00')).not.toBeInTheDocument()
  })

  it('renders a genuine zero as zero, not as the null dash', () => {
    render(<MetricValue value={0} />)
    expect(screen.getByText('0.00')).toBeVisible()
    expect(screen.queryByText('—')).not.toBeInTheDocument()
  })

  it('applies a custom formatter to a non-null value', () => {
    render(<MetricValue value={0.789} format={(v) => `${(v * 100).toFixed(1)}%`} />)
    expect(screen.getByText('78.9%')).toBeVisible()
  })

  it('does not run the formatter at all for a null value', () => {
    let calls = 0
    render(
      <MetricValue
        value={null}
        format={(v) => {
          calls += 1
          return String(v)
        }}
      />,
    )
    expect(calls).toBe(0)
    expect(screen.getByText('—')).toBeVisible()
  })
})
