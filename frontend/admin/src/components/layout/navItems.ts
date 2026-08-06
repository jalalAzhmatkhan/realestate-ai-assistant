import { ROLES, STAFF_ROLES, type Role } from '@/lib/auth/types'

export interface NavItem {
  to: string
  label: string
  /** Decorative; the label is the accessible name. */
  icon: string
  /**
   * Roles that see the item. Mirrors the route guard in `app/router.tsx` — both exist
   * so a role never sees a link that would bounce them, not because either is the
   * security boundary (see `<RoleGate>`).
   */
  allow: readonly Role[]
}

export const NAV_ITEMS: readonly NavItem[] = [
  // First in the list: chatting with the agent is this product's actual purpose, and it's
  // the one screen a `client` role can reach at all — every other item below is staff-only.
  { to: '/chat', label: 'Chat', icon: '💬', allow: ROLES },
  { to: '/properties', label: 'Properties', icon: '🏠', allow: STAFF_ROLES },
  { to: '/bookings', label: 'Bookings', icon: '📅', allow: STAFF_ROLES },
  { to: '/users', label: 'Users', icon: '👤', allow: ['admin'] },
  { to: '/chat-inspector', label: 'Chat Inspector', icon: '🔍', allow: ['admin'] },
  { to: '/observability', label: 'Observability', icon: '📊', allow: ['admin'] },
]
