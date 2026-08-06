import { queryOptions } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import type { Page } from '@/lib/api/pagination'
import { toQueryParams, type RetrievalLogFilters } from './retrievalLogFilters'
import type { EvalRunDetail, EvalRunStatus, EvalRunSummary, RetrievalLogItem } from './types'

export const observabilityKey = ['observability'] as const

/**
 * Admin-only server-side, `403 forbidden` (not `404`) for anyone else
 * (`app/api/observability.py` module docstring). No `enabled` guard here for the same
 * reason `userListQueryOptions` skips one: the router's `<RoleGate>` is the only caller,
 * and the backend's `403` is the real boundary regardless.
 */
export function retrievalLogsQueryOptions(filters: RetrievalLogFilters) {
  return queryOptions({
    queryKey: [...observabilityKey, 'retrieval-logs', filters] as const,
    queryFn: ({ signal }) =>
      apiClient.get<Page<RetrievalLogItem>>('/observability/retrieval-logs', {
        query: toQueryParams(filters),
        signal,
      }),
    // Keeps the current rows on screen while the next page loads, as on the other lists.
    placeholderData: (previous) => previous,
  })
}

export interface EvalRunListFilters {
  status?: EvalRunStatus
  page: number
}

export const EVAL_RUN_PAGE_SIZE = 20

export function evalRunsQueryOptions(filters: EvalRunListFilters) {
  return queryOptions({
    queryKey: [...observabilityKey, 'eval-runs', filters] as const,
    queryFn: ({ signal }) =>
      apiClient.get<Page<EvalRunSummary>>('/observability/retrieval/eval-runs', {
        query: {
          page: filters.page,
          page_size: EVAL_RUN_PAGE_SIZE,
          sort: '-created_at',
          status: filters.status,
        },
        signal,
      }),
    placeholderData: (previous) => previous,
  })
}

/** `GET /observability/retrieval/eval-runs/{id}` — the per-case drill-in. Unknown id -> 404. */
export function evalRunDetailQueryOptions(runId: string) {
  return queryOptions({
    queryKey: [...observabilityKey, 'eval-run-detail', runId] as const,
    queryFn: ({ signal }) =>
      apiClient.get<EvalRunDetail>(`/observability/retrieval/eval-runs/${runId}`, { signal }),
  })
}
