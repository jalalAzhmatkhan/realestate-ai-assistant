import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'

import MetricValue from '@/components/observability/MetricValue'
import RunResultCards from '@/components/observability/RunResultCards'
import ErrorState from '@/components/ui/ErrorState'
import LoadingState from '@/components/ui/LoadingState'
import StatusBadge from '@/components/ui/StatusBadge'
import { isApiError } from '@/lib/api/errors'
import { formatSlotTime } from '@/lib/datetime'
import { evalRunDetailQueryOptions } from '@/lib/observability/queries'
import type { EvalCase } from '@/lib/observability/types'

/**
 * `GET /observability/retrieval/eval-runs/{id}` — the per-case breakdown behind a run
 * history row. Unknown id -> `404 eval_run_not_found`.
 */
export default function EvalRunDetailPage() {
  const { runId = '' } = useParams()

  const { data: run, isPending, isError, error, refetch } = useQuery(evalRunDetailQueryOptions(runId))

  if (isPending) {
    return (
      <div className="space-y-4">
        <BackLink />
        <LoadingState label="Loading run…" rows={6} />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="space-y-4">
        <BackLink />
        {isNotFound(error) ? (
          <ErrorState title="Run not found" error={error} />
        ) : (
          <ErrorState title="Could not load this run" error={error} onRetry={() => void refetch()} />
        )}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <BackLink />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Eval run</h1>
          <p className="text-sm text-slate-500">
            {formatSlotTime(run.created_at)} · {run.tiers.join(', ')} · eval set {run.eval_set_version}
          </p>
        </div>
        <StatusBadge status={run.status} />
      </div>

      <RunResultCards run={run} />

      <CaseTable cases={run.cases} />
    </div>
  )
}

function CaseTable({ cases }: { cases: EvalCase[] }) {
  if (cases.length === 0) {
    return <p className="text-sm text-slate-500">This run recorded no cases.</p>
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[52rem] border-collapse">
        <thead className="border-b border-slate-200 bg-slate-50">
          <tr>
            {['Case', 'Tier', 'Query', 'First relevant rank', 'Reciprocal rank', 'Abstained'].map(
              (label) => (
                <th
                  key={label}
                  scope="col"
                  className="px-3 py-2 text-left text-xs font-semibold tracking-wide text-slate-600 uppercase"
                >
                  {label}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {cases.map((evalCase) => (
            <tr key={evalCase.id}>
              <td className="px-3 py-2 text-sm text-slate-700">{evalCase.case_id}</td>
              <td className="px-3 py-2 text-sm text-slate-700">{evalCase.tier}</td>
              <td className="max-w-xs truncate px-3 py-2 text-sm text-slate-700" title={evalCase.query_text}>
                {evalCase.query_text}
              </td>
              <td className="px-3 py-2 text-sm text-slate-700">
                {/* `null` means nothing expected was retrieved at all — a genuine miss,
                    not rank 0. */}
                <MetricValue value={evalCase.first_relevant_rank} format={(v) => String(v)} />
              </td>
              <td className="px-3 py-2 text-sm text-slate-700">{evalCase.reciprocal_rank.toFixed(3)}</td>
              <td className="px-3 py-2 text-sm text-slate-700">{evalCase.abstained ? 'Yes' : 'No'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BackLink() {
  return (
    <Link
      to="/observability?tab=eval-runs"
      className="inline-block text-sm text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
    >
      ← Back to eval runs
    </Link>
  )
}

function isNotFound(error: unknown): boolean {
  return isApiError(error) && error.status === 404
}
