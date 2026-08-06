import { createBrowserRouter, Navigate, type RouteObject } from 'react-router'

import AppLayout from '@/components/layout/AppLayout'
import RoleGate from '@/components/ui/RoleGate'
import BookingDetailPage from '@/routes/BookingDetailPage'
import BookingsPage from '@/routes/BookingsPage'
import ChatInspectorPage from '@/routes/ChatInspectorPage'
import ChatPage from '@/routes/ChatPage'
import EvalRunDetailPage from '@/routes/EvalRunDetailPage'
import HomePage from '@/routes/HomePage'
import LoginPage from '@/routes/LoginPage'
import NotFoundPage from '@/routes/NotFoundPage'
import ObservabilityPage from '@/routes/ObservabilityPage'
import PropertiesPage from '@/routes/PropertiesPage'
import PropertyCreatePage from '@/routes/PropertyCreatePage'
import PropertyDetailPage from '@/routes/PropertyDetailPage'
import UsersPage from '@/routes/UsersPage'
import { LOGIN_PATH } from '@/lib/api/session'
import type { NotAuthorizedState } from '@/lib/auth/routeState'
import { STAFF_ROLES } from '@/lib/auth/types'

const USERS_PATH = '/users'
const CHAT_INSPECTOR_PATH = '/chat-inspector'
const OBSERVABILITY_PATH = '/observability'
const PROPERTIES_PATH = '/properties'
const BOOKINGS_PATH = '/bookings'

/**
 * `/login` sits outside the shell; everything else renders inside it. The `<RoleGate>`s
 * mirror the hidden nav items (`components/layout/navItems.ts`) so a typed URL does not
 * leave a role on a screen the nav says they cannot reach — but neither is the security
 * boundary. Every guarded endpoint enforces the same restriction independently
 * server-side (backend README): `GET /api/v1/users` answers `403 forbidden` for a
 * non-admin regardless of what this file does, and `properties`/`bookings` RBAC-scope
 * their own results per caller. `/properties` and `/bookings` need an explicit gate here
 * (unlike before F-client-chat) now that `<AppLayout>` admits `client` into the shell —
 * they used to be staff-only by simply being unreachable by any other role.
 */
export const routes: RouteObject[] = [
  { path: LOGIN_PATH, element: <LoginPage /> },
  {
    element: <AppLayout />,
    children: [
      { path: '/', element: <HomePage /> },
      // No <RoleGate>: every authenticated role may chat, matching the backend exactly
      // (`app/api/chat.py` takes any authenticated `CurrentUser`, no `require_role`).
      { path: '/chat', element: <ChatPage /> },
      {
        path: PROPERTIES_PATH,
        element: (
          <RoleGate
            allow={STAFF_ROLES}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: PROPERTIES_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <PropertiesPage />
          </RoleGate>
        ),
      },
      // Listed before `:propertyId`, and a separate component, so creating never routes
      // through the detail screen and fetches a listing with the id "new".
      {
        path: '/properties/new',
        element: (
          <RoleGate
            allow={STAFF_ROLES}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: PROPERTIES_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <PropertyCreatePage />
          </RoleGate>
        ),
      },
      {
        path: '/properties/:propertyId',
        element: (
          <RoleGate
            allow={STAFF_ROLES}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: PROPERTIES_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <PropertyDetailPage />
          </RoleGate>
        ),
      },
      {
        path: BOOKINGS_PATH,
        element: (
          <RoleGate
            allow={STAFF_ROLES}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: BOOKINGS_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <BookingsPage />
          </RoleGate>
        ),
      },
      {
        path: '/bookings/:bookingId',
        element: (
          <RoleGate
            allow={STAFF_ROLES}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: BOOKINGS_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <BookingDetailPage />
          </RoleGate>
        ),
      },
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
      {
        path: CHAT_INSPECTOR_PATH,
        element: (
          <RoleGate
            allow={['admin']}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: CHAT_INSPECTOR_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <ChatInspectorPage />
          </RoleGate>
        ),
      },
      {
        path: OBSERVABILITY_PATH,
        element: (
          <RoleGate
            allow={['admin']}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: OBSERVABILITY_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <ObservabilityPage />
          </RoleGate>
        ),
      },
      // Nested under the same `<RoleGate>` rather than sharing one at a parent route:
      // every other guarded screen in this router (`/users`, `/chat-inspector`) gates a
      // single leaf, so a run's drill-in gets the same explicit gate instead of being the
      // one guarded-by-ancestor exception.
      {
        path: `${OBSERVABILITY_PATH}/eval-runs/:runId`,
        element: (
          <RoleGate
            allow={['admin']}
            fallback={
              <Navigate
                to="/"
                replace
                state={{ notAuthorized: OBSERVABILITY_PATH } satisfies NotAuthorizedState}
              />
            }
          >
            <EvalRunDetailPage />
          </RoleGate>
        ),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
