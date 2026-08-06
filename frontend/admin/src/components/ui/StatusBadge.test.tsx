import { render, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BOOKING_STATUS_LABELS } from '@/lib/bookings/types'
import { PROPERTY_STATUS_LABELS } from '@/lib/properties/types'
import { USER_STATUS_LABELS } from '@/lib/users/types'
import StatusBadge, { type BadgeStatus } from './StatusBadge'

/**
 * F6 widened this component's union to cover `UserStatus`, which merges three label maps
 * into one object — a merge in which `active` appears twice. Pinned here because the
 * collision is silent: a later rename of the user label to "Enabled" would repaint every
 * property badge with it, and no property or booking test would notice.
 */
function renderBadge(status: BadgeStatus) {
  // Scoped to its own container: several of these tests render every status in turn.
  const { container } = render(<StatusBadge status={status} />)
  return within(container).getByTestId('status-badge')
}

describe('StatusBadge label mapping', () => {
  it.each([
    ['active', 'Active'],
    ['draft', 'Draft'],
    ['under_offer', 'Under offer'],
    ['sold', 'Sold'],
    ['confirmed', 'Confirmed'],
    ['cancelled', 'Cancelled'],
    ['completed', 'Completed'],
    ['disabled', 'Disabled'],
  ] as const)('renders %s as "%s"', (status, label) => {
    expect(renderBadge(status)).toHaveTextContent(label)
  })

  it('agrees with every source label map, so the merge cannot silently reassign one', () => {
    for (const [status, label] of Object.entries({
      ...PROPERTY_STATUS_LABELS,
      ...BOOKING_STATUS_LABELS,
      ...USER_STATUS_LABELS,
    })) {
      expect(renderBadge(status as BadgeStatus)).toHaveTextContent(label)
    }
    // The one key two maps share. Both must mean the same thing for one map to serve all
    // three, which is the assumption the merge rests on.
    expect(PROPERTY_STATUS_LABELS.active).toBe(USER_STATUS_LABELS.active)
  })
})

describe('StatusBadge colour mapping', () => {
  /** The class the badge carries, reduced to its Tailwind colour family. */
  function familyOf(status: BadgeStatus): string {
    const className = renderBadge(status).className
    const background = className.split(' ').find((token) => token.startsWith('bg-'))
    return background?.replace(/^bg-|-\d+$/g, '') ?? ''
  }

  /**
   * UI/UX §4's fixed mapping for the statuses that predate F6. These are asserted so a
   * later union widening cannot repaint an existing badge — greens must stay green and
   * the terminal states must stay muted red.
   */
  it.each([
    ['active', 'green'],
    ['confirmed', 'green'],
    ['draft', 'slate'],
    ['under_offer', 'amber'],
    ['sold', 'red'],
    ['cancelled', 'red'],
    ['completed', 'blue'],
  ] as const)('keeps %s in the %s family', (status, family) => {
    expect(familyOf(status)).toBe(family)
  })

  it('paints a disabled account grey rather than red, since it is dormant not failed', () => {
    expect(familyOf('disabled')).toBe('slate')
    expect(familyOf('disabled')).toBe(familyOf('draft'))
  })

  it('always renders the label as text, never colour alone (UI/UX §6)', () => {
    expect(renderBadge('disabled').textContent?.trim()).toBe('Disabled')
  })
})
