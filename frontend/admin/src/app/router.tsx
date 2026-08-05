import { createBrowserRouter } from 'react-router'

import HomePage from '@/routes/HomePage'
import LoginPage from '@/routes/LoginPage'
import NotFoundPage from '@/routes/NotFoundPage'
import { LOGIN_PATH } from '@/lib/api/session'

/**
 * Flat on purpose for now. F2's nav shell wraps the authenticated entries in a layout
 * route (and F3's guard in a parent `element`), which is an insertion around this array
 * rather than a rewrite of it.
 */
export const router = createBrowserRouter([
  { path: LOGIN_PATH, element: <LoginPage /> },
  { path: '/', element: <HomePage /> },
  { path: '*', element: <NotFoundPage /> },
])
