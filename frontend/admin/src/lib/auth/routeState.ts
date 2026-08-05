/**
 * Carries "you were bounced off X" from a route guard's redirect to the shell that
 * renders the notice. Lives in router state rather than a store so it is scoped to the
 * one history entry and cannot leak onto an unrelated later screen.
 */
export interface NotAuthorizedState {
  /** The path the user was denied, e.g. `/users`. */
  notAuthorized: string
}

export function readNotAuthorized(state: unknown): string | null {
  if (typeof state !== 'object' || state === null) return null
  const value = (state as Record<string, unknown>).notAuthorized
  return typeof value === 'string' ? value : null
}
