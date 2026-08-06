import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'

import './index.css'
import { queryClient } from '@/app/queryClient'
import { router } from '@/app/router'
import { LOGIN_PATH, setQueryCacheReset, setUnauthorizedRedirect } from '@/lib/api/session'

// Hands the 401 path a client-side navigation instead of a full page reload. Registered
// against the data router directly rather than through a component + `useNavigate`, so
// `apiRequest` does not depend on any component being mounted to redirect correctly.
setUnauthorizedRedirect(() => {
  void router.navigate(LOGIN_PATH, { replace: true })
})

// Same injection trick, for the other half of a session teardown: because that redirect
// is a client-side navigation, nothing else would drop the previous user's cached data.
setQueryCacheReset(() => {
  queryClient.clear()
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
