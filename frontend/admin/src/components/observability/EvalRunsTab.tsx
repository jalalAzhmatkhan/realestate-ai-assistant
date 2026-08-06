import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'

import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import Pagination from '@/components/ui/Pagination'
import { evalRunsQueryOptions } from '@/lib/observability/queries'
import EvalRunTable from './EvalRunTable'
import EvalRunTriggerPanel from './EvalRunTriggerPanel'

const PAGE_PARAM = 'run_page'

/**
 * "Run evaluation" plus the run-history list (`GET .../eval-runs`). Kept as its own
 * `run_page` query param, distinct from the retrieval log tab's `page`, so switching
 * tabs never has one tab's pagination silently apply to the other.
 */
export default function EvalRunsTab() {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = parsePage(searchParams.get(PAGE_PARAM))

  const { data, isPending, isError, error, isPlaceholderData, refetch } = useQuery(
    evalRunsQueryOptions({ page }),
  )

  function setPage(next: number) {
    const params = new URLSearchParams(searchParams)
    if (next > 1) {
      params.set(PAGE_PARAM, String(next))
    } else {
      params.delete(PAGE_PARAM)
    }
    setSearchParams(params)
  }

  return (
    <div className="space-y-4">
      <EvalRunTriggerPanel
        onRunCompleted={() => {
          // A freshly triggered run always sorts first (`-created_at`) — jump back to
          // page 1 so it is visible without the admin having to notice it landed on a
          // page they are not looking at.
          setPage(1)
        }}
      />

      <div>
        <h2 className="text-sm font-semibold text-slate-900">Run history</h2>

        {isPending ? (
          <LoadingState label="Loading run history…" rows={4} />
        ) : isError ? (
          <ErrorState title="Could not load run history" error={error} onRetry={() => void refetch()} />
        ) : data.results.length === 0 ? (
          <EmptyState
            title="No evaluation runs yet"
            description="Click Run evaluation above to score the FAQ retriever against the eval set."
          />
        ) : (
          <>
            <EvalRunTable runs={data.results} />
            <Pagination meta={data} isPending={isPlaceholderData} onPageChange={setPage} />
          </>
        )}
      </div>
    </div>
  )
}

function parsePage(raw: string | null): number {
  const page = Number(raw)
  return Number.isInteger(page) && page > 0 ? page : 1
}
