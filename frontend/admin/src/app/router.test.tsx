import { render, screen } from '@testing-library/react'
import { RouterProvider } from 'react-router'
import { describe, expect, it } from 'vitest'

import { router } from './router'
import { LOGIN_PATH } from '@/lib/api/session'

describe('router', () => {
  // Guards the redirect half of the 401 path: lib/api/client.test.ts proves the
  // handler fires, this proves the handler main.tsx registers actually lands on /login.
  it('navigates to /login through the same call the 401 handler makes', async () => {
    render(<RouterProvider router={router} />)
    expect(await screen.findByRole('heading', { name: 'Real Estate Admin' })).toBeVisible()

    await router.navigate(LOGIN_PATH, { replace: true })

    expect(await screen.findByText('Sign-in form lands in F3.')).toBeVisible()
    expect(window.location.pathname).toBe(LOGIN_PATH)
  })
})
