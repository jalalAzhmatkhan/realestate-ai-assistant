import { Outlet, useLocation } from 'react-router'

import Button from '@/components/ui/Button'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import { readNotAuthorized } from '@/lib/auth/routeState'
import { isStaffRole } from '@/lib/auth/types'
import { useCurrentUser } from '@/lib/auth/useCurrentUser'
import { useLogout } from '@/lib/auth/useLogout'
import Sidebar from './Sidebar'
import TopBar from './TopBar'

/**
 * Wraps every authenticated screen. It resolves identity once, here, so that everything
 * below it — nav items, route guards, per-screen `<RoleGate>`s — reads a settled role
 * and never flashes the wrong shell while `/auth/me` is in flight.
 */
export default function AppLayout() {
  const { data: user, isPending, isError, error, refetch } = useCurrentUser()
  const location = useLocation()
  const deniedPath = readNotAuthorized(location.state)

  if (isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <LoadingState variant="spinner" label="Loading your session…" />
      </div>
    )
  }

  if (isError) {
    // A 401 never reaches here — apiClient has already redirected to /login. This is a
    // 5xx or a dead network, which is retryable rather than a session problem.
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <ErrorState
          title="Could not load your session"
          error={error}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }

  // `client` is deliberately admitted to the shell, not just the top gate: this app is the
  // product's only real chat surface today (the "separate chat consumer" the frontend
  // design doc originally assumed was never built), and this product's actual purpose is
  // clients chatting with the agent. A `client` still cannot reach any staff-only screen —
  // the nav only ever shows them `/chat` (`components/layout/navItems.ts`), and every
  // staff screen (`/properties`, `/bookings`, `/users`, `/chat-inspector`,
  // `/observability`) carries its own `<RoleGate>` as the actual enforcement, same as
  // before. The `status` check stays unconditional: an inactive account of any role still
  // hits the screen below.
  if ((!isStaffRole(user.role) && user.role !== 'client') || user.status !== 'active') {
    // Belt-and-braces with F3's login-time rejection: an unrecognized role, or an active
    // session behind a now-disabled account, is not a role this build knows how to seat —
    // fail to a safe, logged-out-adjacent screen rather than trust the role check alone
    // (CLAUDE.md's "defense in depth" for RBAC). The backend already 401s a disabled
    // user's `/auth/me` mid-session (README, `session_revoked`), so a non-active status
    // reaching this render at all means something upstream didn't catch it.
    //
    // This screen still needs its own way out: it renders before <TopBar>, so without a
    // logout action here a stuck account lands with a live session and no visible way to
    // end it short of typing /login.
    return (
      <main className="flex min-h-screen items-center justify-center p-6">
        <div className="max-w-sm space-y-3 text-center">
          <h1 className="text-xl font-semibold text-slate-900">Real Estate Admin</h1>
          <p className="text-sm text-slate-500">
            Your account cannot access this app right now. If this seems wrong, contact an
            administrator.
          </p>
          <WrongAppLogout />
        </div>
      </main>
    )
  }

  return (
    <div className="flex min-h-screen flex-col bg-slate-50">
      <TopBar user={user} />
      <div className="flex flex-1">
        <Sidebar role={user.role} />
        <main className="min-w-0 flex-1 p-4 lg:p-6">
          {deniedPath ? (
            <p
              role="alert"
              className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900"
            >
              You do not have access to {deniedPath}.
            </p>
          ) : null}
          <Outlet />
        </main>
      </div>
    </div>
  )
}

function WrongAppLogout() {
  const logout = useLogout()

  return (
    <div className="space-y-1">
      <Button variant="secondary" onClick={() => logout.mutate()} disabled={logout.isPending}>
        {logout.isPending ? 'Logging out…' : 'Log out'}
      </Button>
      {logout.isError ? (
        <p role="alert" className="text-sm text-red-700">
          Could not log you out. Please try again.
        </p>
      ) : null}
    </div>
  )
}
