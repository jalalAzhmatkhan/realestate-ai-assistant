/**
 * Mirrors the frozen `GET /api/v1/auth/me` response (backend README,
 * "GET /api/v1/auth/me"). Field names are the wire names on purpose — a rename layer
 * would only add a place for the two to drift.
 */

export const ROLES = ['admin', 'agent', 'client'] as const

export type Role = (typeof ROLES)[number]

/** Roles this dashboard is for. `client` is a chat-surface user (frontend design doc §2). */
export const STAFF_ROLES: readonly Role[] = ['admin', 'agent']

export interface CurrentUserSession {
  auth_method: 'cookie' | 'bearer'
  expires_at: string
  /** Non-null only for cookie sessions; consumed by F3's session bootstrap, not here. */
  csrf_token: string | null
}

export interface CurrentUser {
  id: string
  name: string
  email: string
  role: Role
  status: string
  session: CurrentUserSession
}

export function isRole(value: unknown): value is Role {
  return typeof value === 'string' && (ROLES as readonly string[]).includes(value)
}

export function isStaffRole(role: Role): boolean {
  return STAFF_ROLES.includes(role)
}
