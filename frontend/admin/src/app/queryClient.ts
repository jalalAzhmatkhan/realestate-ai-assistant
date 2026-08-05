import { QueryClient } from '@tanstack/react-query'

import { isApiError } from '@/lib/api/errors'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A 4xx is a settled answer — retrying a 401/403/404/422 only delays the error
      // state the screen already knows how to render, and for 401 it would fight the
      // single redirect path in lib/api/session.ts.
      retry: (failureCount, error) => {
        if (isApiError(error) && error.status >= 400 && error.status < 500) return false
        return failureCount < 2
      },
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
    mutations: {
      retry: false,
    },
  },
})
