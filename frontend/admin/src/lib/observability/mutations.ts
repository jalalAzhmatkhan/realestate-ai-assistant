import { useMutation, useQueryClient } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import { observabilityKey } from './queries'
import type { EvalRunDetail, EvalRunRequest } from './types'

/**
 * `POST /observability/retrieval/eval-runs` — synchronous (typically well under a few
 * seconds per the task spec), so the caller drives a button's loading state off
 * `isPending` rather than polling for completion. `{}` is a valid body that runs every
 * tier at k=1,3,5 (`EvalRunRequest` docstring).
 *
 * A failed run (`502 eval_run_failed`) still persists a row with `status: "failed"` —
 * the backend's error `detail.run_id` names it — so the run-history list is invalidated
 * even on error, not only on success, or the failed run would be invisible until the
 * next unrelated refetch.
 */
export function useTriggerEvalRun() {
  const queryClient = useQueryClient()

  return useMutation<EvalRunDetail, Error, EvalRunRequest>({
    mutationFn: (payload) =>
      apiClient.post<EvalRunDetail>('/observability/retrieval/eval-runs', payload),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: [...observabilityKey, 'eval-runs'] })
    },
  })
}
