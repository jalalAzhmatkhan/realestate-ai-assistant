/**
 * Mirrors `app/schemas/user.py` and the `UserRole`/`UserStatus` unions in
 * `app/models/user.py`. Wire names throughout, for the same reason as
 * `lib/properties/types.ts`.
 */

import { ROLES, type Role } from '@/lib/auth/types'

export const USER_ROLES = ROLES
export type UserRole = Role

export const USER_STATUSES = ['active', 'disabled'] as const
export type UserStatus = (typeof USER_STATUSES)[number]

export const USER_ROLE_LABELS: Record<UserRole, string> = {
  admin: 'Admin',
  agent: 'Agent',
  client: 'Client',
}

export const USER_STATUS_LABELS: Record<UserStatus, string> = {
  active: 'Active',
  disabled: 'Disabled',
}

/**
 * The item type of `GET /users` **and** the response of `GET /users/{id}`, `POST /users`
 * and `PATCH /users/{id}`. Users have no summary/detail split — a list row already is
 * the whole record — which is why this screen never refetches a row it just listed.
 *
 * `hashed_password` is absent by construction on the backend, not filtered here.
 */
export interface User {
  id: string
  name: string
  email: string
  role: UserRole
  status: UserStatus
  /** ISO-8601 UTC instant. */
  created_at: string
}

export const MIN_PASSWORD_LENGTH = 8
/** bcrypt truncates past 72 bytes, so the backend rejects rather than truncates. */
export const MAX_PASSWORD_LENGTH = 72
export const MAX_NAME_LENGTH = 200
export const MIN_EMAIL_LENGTH = 3
export const MAX_EMAIL_LENGTH = 320

export interface UserCreateRequest {
  name: string
  email: string
  password: string
  role: UserRole
  status?: UserStatus
}

/**
 * `PATCH /users/{id}`: every field optional, and an omitted field is left untouched.
 * This screen only ever sends `role` or `status` — it offers no rename, no email change
 * and no password reset, so those fields are typed but never populated here.
 */
export interface UserUpdateRequest {
  name?: string
  email?: string
  password?: string
  role?: UserRole
  status?: UserStatus
}

/** `409` from `POST /users` and from `PATCH` when an email collides. */
export const EMAIL_ALREADY_EXISTS_CODE = 'email_already_exists'
/**
 * `409` from `PATCH /users/{id}` when an admin targets their own account with a
 * disable or a demotion. The backend's message is already written for a person; screens
 * render it rather than rewording it.
 */
export const SELF_LOCKOUT_CODE = 'self_lockout_forbidden'
