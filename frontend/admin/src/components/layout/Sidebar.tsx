import { NavLink } from 'react-router'

import RoleGate from '@/components/ui/RoleGate'
import type { Role } from '@/lib/auth/types'
import { NAV_ITEMS } from './navItems'

export interface SidebarProps {
  role: Role
}

export default function Sidebar({ role }: SidebarProps) {
  return (
    // Icon-only below `lg`, full labels above (UI/UX §6): a CSS-only collapse, so there
    // is no drawer state to get stuck open on a laptop-width screen.
    <aside className="flex w-16 shrink-0 flex-col justify-between border-r border-slate-200 bg-white lg:w-56">
      <nav aria-label="Main" className="p-2">
        <ul className="space-y-1">
          {NAV_ITEMS.map((item) => (
            <RoleGate key={item.to} allow={item.allow}>
              <li>
                <NavLink
                  to={item.to}
                  title={item.label}
                  className={({ isActive }) =>
                    [
                      'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium',
                      'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600',
                      isActive
                        ? 'bg-blue-50 text-blue-700'
                        : 'text-slate-700 hover:bg-slate-100',
                    ].join(' ')
                  }
                >
                  <span aria-hidden="true">{item.icon}</span>
                  <span className="hidden lg:inline">{item.label}</span>
                </NavLink>
              </li>
            </RoleGate>
          ))}
        </ul>
      </nav>

      {/* Deliberate and always visible (UI/UX §3): the same app renders differently per
          role, so staff should never have to guess which mode they are in. */}
      <p className="truncate border-t border-slate-200 px-3 py-3 text-xs text-slate-500">
        <span className="hidden lg:inline">role: </span>
        <span className="font-medium text-slate-700">{role}</span>
      </p>
    </aside>
  )
}
