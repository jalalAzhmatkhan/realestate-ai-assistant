import { afterEach, describe, expect, it, vi } from 'vitest'

import { getCsrfToken, setCsrfToken } from './csrf'
import {
  LOGIN_PATH,
  handleUnauthorized,
  setQueryCacheReset,
  setUnauthorizedRedirect,
} from './session'

function atPath(path: string) {
  window.history.pushState({}, '', path)
}

afterEach(() => {
  setCsrfToken(null)
  setUnauthorizedRedirect(null)
  setQueryCacheReset(null)
  atPath('/')
})

describe('handleUnauthorized', () => {
  it('tears the whole session down before redirecting', () => {
    atPath('/properties')
    setCsrfToken('token-from-a-dead-session')
    const redirect = vi.fn()
    const resetCache = vi.fn()
    setUnauthorizedRedirect(redirect)
    setQueryCacheReset(resetCache)

    handleUnauthorized()

    expect(getCsrfToken()).toBeNull()
    // The redirect is a client-side navigation, so nothing else drops this user's
    // cached rows before the next sign-in in the same tab.
    expect(resetCache).toHaveBeenCalledTimes(1)
    expect(redirect).toHaveBeenCalledTimes(1)
  })

  it('falls back to a full navigation when no router is registered', () => {
    atPath('/properties')
    const resetCache = vi.fn()
    setQueryCacheReset(resetCache)
    const assign = vi.fn()
    vi.spyOn(window.location, 'assign').mockImplementation(assign)

    handleUnauthorized()

    expect(assign).toHaveBeenCalledWith(LOGIN_PATH)
    expect(resetCache).toHaveBeenCalledTimes(1)
  })

  it('leaves a just-signed-in session intact when the 401 arrives on /login', () => {
    // A request that was already in flight when the session died can land *after* a
    // successful sign-in on this screen. Tearing down here would discard a CSRF token
    // and a cache that were both legitimately populated a moment ago, and the user
    // would look signed in while every write failed `csrf_token_missing`.
    atPath(LOGIN_PATH)
    setCsrfToken('token-from-the-login-that-just-succeeded')
    const redirect = vi.fn()
    const resetCache = vi.fn()
    setUnauthorizedRedirect(redirect)
    setQueryCacheReset(resetCache)

    handleUnauthorized()

    expect(getCsrfToken()).toBe('token-from-the-login-that-just-succeeded')
    expect(resetCache).not.toHaveBeenCalled()
    expect(redirect).not.toHaveBeenCalled()
  })

  it('does not require a cache reset to have been registered', () => {
    atPath('/bookings')
    setCsrfToken('token')
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)

    expect(() => {
      handleUnauthorized()
    }).not.toThrow()
    expect(getCsrfToken()).toBeNull()
    expect(redirect).toHaveBeenCalledTimes(1)
  })
})
