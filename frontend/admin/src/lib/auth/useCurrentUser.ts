import { useQuery } from '@tanstack/react-query'

import { currentUserQueryOptions } from './currentUser'

export function useCurrentUser() {
  return useQuery(currentUserQueryOptions())
}
