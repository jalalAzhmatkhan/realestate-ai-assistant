import type { CurrentUser } from '@/lib/auth/types'
import type { PropertyDetail } from './types'

/**
 * Cosmetic only, and deliberately so: there is no `can_edit` field on the response, and
 * the backend re-derives this independently through `find_editable_property` on every
 * `PUT`/`DELETE`. Getting this wrong renders a button that 404s — it can never grant
 * anything (CLAUDE.md, "tool calls are re-authorized, not trusted").
 *
 * Mirrors that server-side rule: an `admin` edits anything, an `agent` edits what they
 * own. An unowned listing (`agent_id: null`) is admin-only, which is why the null check
 * is explicit rather than an `===` that would pass for a user whose id was also null.
 */
export function canEditProperty(user: CurrentUser, property: PropertyDetail): boolean {
  if (user.role === 'admin') return true
  return property.agent_id !== null && property.agent_id === user.id
}
