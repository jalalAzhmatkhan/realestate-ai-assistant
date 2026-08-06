import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import { propertiesKey, propertyDetailQueryOptions } from './queries'
import type { PropertyDetail, PropertyWriteRequest } from './types'

export function useCreateProperty() {
  const queryClient = useQueryClient()

  return useMutation<PropertyDetail, Error, PropertyWriteRequest>({
    mutationFn: (payload) => apiClient.post<PropertyDetail>('/properties', payload),
    onSuccess: (property) => {
      settle(queryClient, property)
    },
  })
}

export function useUpdateProperty(propertyId: string) {
  const queryClient = useQueryClient()

  return useMutation<PropertyDetail, Error, PropertyWriteRequest>({
    mutationFn: (payload) => apiClient.put<PropertyDetail>(`/properties/${propertyId}`, payload),
    onSuccess: (property) => {
      settle(queryClient, property)
    },
  })
}

/** Soft-deactivation: the backend returns the listing to `draft` and never deletes a row. */
export function useDeactivateProperty(propertyId: string) {
  const queryClient = useQueryClient()

  return useMutation<PropertyDetail, Error, void>({
    mutationFn: () => apiClient.delete<PropertyDetail>(`/properties/${propertyId}`),
    onSuccess: (property) => {
      settle(queryClient, property)
    },
  })
}

/**
 * Every write answers with the full `PropertyDetail`, so the detail cache is seeded from
 * the response rather than refetched. The lists are only invalidated: a status or price
 * change can move a row across pages or out of the current filter entirely, which no
 * client-side patch can work out.
 */
function settle(queryClient: QueryClient, property: PropertyDetail): void {
  queryClient.setQueryData(propertyDetailQueryOptions(property.id).queryKey, property)
  void queryClient.invalidateQueries({ queryKey: [...propertiesKey, 'list'] })
}
