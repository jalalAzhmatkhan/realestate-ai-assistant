import { Link } from 'react-router'

import Button from '@/components/ui/Button'
import type { CurrentUser } from '@/lib/auth/types'

export interface TopBarProps {
  user: CurrentUser
}

export default function TopBar({ user }: TopBarProps) {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-4 py-3">
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
        {/* Present-but-disabled rather than hidden, on the same reasoning the UI/UX doc
            applies to Reschedule (§5.5): the affordance is correct and the blocker is a
            tracked sibling task (F3 owns session bootstrap and POST /auth/logout), not a
            permission the user lacks. Hiding it would imply this role cannot log out. */}
        <Button variant="secondary" disabled title="Available once sign-in lands (F3)">
          Log out
        </Button>
      </div>
    </header>
  )
}
