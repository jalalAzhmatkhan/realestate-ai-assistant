import type { ReactNode } from 'react'

import type { Role } from '@/lib/auth/types'
import { useCurrentUser } from '@/lib/auth/useCurrentUser'

export interface RoleGateProps {
  allow: readonly Role[]
  children: ReactNode
  /**
   * Rendered instead of `children` when the role is not allowed. Omitted for nav items
   * (the item simply does not exist); a `<Navigate>` for a guarded route.
   */
  fallback?: ReactNode
}

/**
 * A **UX convenience, never a security boundary** — CLAUDE.md and frontend design doc
 * §6 both say so, and it is worth repeating at the definition site: this only decides
 * what to paint. Anyone can edit their own JS state or type a URL. The reason it is
 * still safe is that every screen calls the real RBAC-scoped endpoint and renders only
 * what came back — the SPA never fetches unscoped data and filters it here.
 *
 * While identity is still loading it renders nothing, so a guarded route cannot bounce
 * a legitimate admin away before their role has arrived. In practice `<AppLayout>`
 * already blocks its `<Outlet>` until then, so gates below it see a settled user.
 */
export default function RoleGate({ allow, children, fallback = null }: RoleGateProps) {
  const { data: user } = useCurrentUser()

  if (!user) return null
  return allow.includes(user.role) ? <>{children}</> : <>{fallback}</>
}
