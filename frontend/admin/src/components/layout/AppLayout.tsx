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

  if (!isStaffRole(user.role) || user.status !== 'active') {
    // Belt-and-braces with F3's login-time rejection: a `client` can hold a perfectly
    // valid session from the chat surface, so arriving here is not an error — it is the
    // wrong app (frontend design doc §2, §5). The `status` check is the same defense in
    // depth: the backend already 401s a disabled user's `/auth/me` mid-session
    // (README, `session_revoked`), so a non-active status reaching this render at all
    // means something upstream didn't catch it — fail to the same safe screen rather
    // than trust the role check alone (CLAUDE.md's "defense in depth" for RBAC).
    //
    // This screen still needs its own way out: it renders before <TopBar>, so without a
    // logout action here a client (or a disabled staff account) who successfully signs
    // in lands with a live session and no visible way to end it short of typing /login.
    return (
      <main className="flex min-h-screen items-center justify-center p-6">
        <div className="max-w-sm space-y-3 text-center">
          <h1 className="text-xl font-semibold text-slate-900">Real Estate Admin</h1>
          <p className="text-sm text-slate-500">
            This dashboard is for staff. If you are looking for properties or a viewing,
            use the chat assistant.
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
