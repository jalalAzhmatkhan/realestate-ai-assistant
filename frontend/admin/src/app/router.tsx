import { createBrowserRouter, Navigate, type RouteObject } from 'react-router'

import AppLayout from '@/components/layout/AppLayout'
import RoleGate from '@/components/ui/RoleGate'
import BookingsPage from '@/routes/BookingsPage'
import HomePage from '@/routes/HomePage'
import LoginPage from '@/routes/LoginPage'
import NotFoundPage from '@/routes/NotFoundPage'
import PropertiesPage from '@/routes/PropertiesPage'
import UsersPage from '@/routes/UsersPage'
import { LOGIN_PATH } from '@/lib/api/session'
import type { NotAuthorizedState } from '@/lib/auth/routeState'

const USERS_PATH = '/users'

/**
 * `/login` sits outside the shell; everything else renders inside it. The `<RoleGate>`
 * around `/users` mirrors the hidden nav item so a typed URL does not leave an `agent`
 * on a screen the nav says they cannot reach — but neither is the security boundary.
 * `GET /api/v1/users` answers `403 forbidden` for a non-admin regardless of what this
 * file does (backend README, `GET /api/v1/users`).
 */
export const routes: RouteObject[] = [
  { path: LOGIN_PATH, element: <LoginPage /> },
  {
    element: <AppLayout />,
    children: [
      { path: '/', element: <HomePage /> },
      { path: '/properties', element: <PropertiesPage /> },
      { path: '/bookings', element: <BookingsPage /> },
      {
        path: USERS_PATH,
        element: (
          <RoleGate
            allow={['admin']}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: USERS_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <UsersPage />
          </RoleGate>
        ),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
