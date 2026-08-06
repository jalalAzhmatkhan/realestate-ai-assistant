import { afterEach, describe, expect, it, vi } from 'vitest'

/**
 * Everything else stubs the injection points. This file imports the real entrypoint, so
 * it is the only place that proves the production `queryClient` is what actually gets
 * cleared on a 401 — an injection registered wrongly in main.tsx would be invisible to
 * every other test in the suite.
 */
afterEach(() => {
  vi.resetModules()
})

describe('main.tsx wiring', () => {
  it('registers the real queryClient as the 401 cache reset', async () => {
    document.body.innerHTML = '<div id="root"></div>'
    window.history.pushState({}, '', '/properties')
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(null, { status: 500 }))),
    )

    const { queryClient } = await import('@/app/queryClient')
    const { clearCsrfToken, getCsrfToken, setCsrfToken } = await import('@/lib/api/csrf')
    const { handleUnauthorized } = await import('@/lib/api/session')
    const { router } = await import('@/app/router')

    queryClient.setQueryData(['properties', 'list'], [{ id: 'p-1' }])
    setCsrfToken('token-from-a-dead-session')

    await import('@/main')

    handleUnauthorized()

    expect(getCsrfToken()).toBeNull()
    expect(queryClient.getQueryData(['properties', 'list'])).toBeUndefined()
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
    await vi.waitFor(() => {
      expect(router.state.location.pathname).toBe('/login')
    })

    clearCsrfToken()
  })
})
