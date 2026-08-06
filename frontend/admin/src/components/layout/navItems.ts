import type { Role } from '@/lib/auth/types'

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

const STAFF: readonly Role[] = ['admin', 'agent']

export const NAV_ITEMS: readonly NavItem[] = [
  { to: '/properties', label: 'Properties', icon: '🏠', allow: STAFF },
  { to: '/bookings', label: 'Bookings', icon: '📅', allow: STAFF },
  { to: '/users', label: 'Users', icon: '👤', allow: ['admin'] },
  { to: '/chat-inspector', label: 'Chat Inspector', icon: '🔍', allow: ['admin'] },
]
