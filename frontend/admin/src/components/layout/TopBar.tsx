import { Link } from 'react-router'

import Button from '@/components/ui/Button'
import { isApiError } from '@/lib/api/errors'
import type { CurrentUser } from '@/lib/auth/types'
import { useLogout } from '@/lib/auth/useLogout'

export interface TopBarProps {
  user: CurrentUser
}

export default function TopBar({ user }: TopBarProps) {
  const logout = useLogout()

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="flex items-center justify-between gap-4 px-4 py-3">
        <Link
          to="/"
          className="text-sm font-semibold text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        >
          Real Estate Admin
        </Link>

        <div className="flex items-center gap-3">
          <span className="hidden text-right text-sm leading-tight sm:block">
            <span className="block font-medium text-slate-900">{user.name}</span>
            <span className="block text-xs text-slate-500">{user.email}</span>
          </span>
          <Button
            variant="secondary"
            onClick={() => {
              logout.mutate()
            }}
            disabled={logout.isPending}
          >
            {logout.isPending ? 'Logging out…' : 'Log out'}
          </Button>
        </div>
      </div>

      {/* A failed logout has to say so rather than fail silently: the session is still
          live on the server, and the user's next click is the retry. */}
      {logout.isError ? (
        <p
          role="alert"
          className="border-t border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800"
        >
          {isApiError(logout.error)
            ? logout.error.message
            : 'Could not log you out. Please try again.'}
        </p>
      ) : null}
    </header>
  )
}
