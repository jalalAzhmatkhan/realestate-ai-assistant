import { Link } from 'react-router'

import StatusBadge from '@/components/ui/StatusBadge'
import { formatSlotTime } from '@/lib/datetime'
import type { EvalRunSummary } from '@/lib/observability/types'
import MetricValue from './MetricValue'

const CELL = 'px-3 py-2 text-sm text-slate-700'
const PERCENT = (value: number) => `${(value * 100).toFixed(1)}%`

/**
 * Run history. Recall@K and MRR are the headline numbers per the task spec — bolded and
 * larger than the surrounding cells — with `abstention_rate`/`identity_recall_at_1`
 * folded into the same cell as small supporting text rather than their own columns, so
 * the table does not compete with the "Run evaluation" panel's cards above it for which
 * numbers matter most.
 *
 * Every row links to `GET /observability/retrieval/eval-runs/{id}` for the per-case
 * breakdown — the one drill-in this tab has.
 */
export default function EvalRunTable({ runs }: { runs: EvalRunSummary[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[52rem] border-collapse">
        <thead className="border-b border-slate-200 bg-slate-50">
          <tr>
            {['Run', 'Status', 'Tiers', 'Recall@K', 'MRR', 'Cases', 'Duration'].map((label) => (
              <th
                key={label}
                scope="col"
                className="px-3 py-2 text-left text-xs font-semibold tracking-wide text-slate-600 uppercase"
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>

        <tbody className="divide-y divide-slate-100">
          {runs.map((run) => {
            const recallEntries = Object.entries(run.recall_at_k ?? {}).sort(
              ([a], [b]) => Number(a) - Number(b),
            )

            return (
              <tr key={run.id} className="hover:bg-slate-50">
                <td className={`${CELL} whitespace-nowrap`}>
                  <Link
                    to={`/observability/eval-runs/${run.id}`}
                    className="font-medium text-blue-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                  >
                    {formatSlotTime(run.created_at)}
                  </Link>
                </td>
                <td className={CELL}>
                  <StatusBadge status={run.status} />
                </td>
                <td className={CELL}>{run.tiers.join(', ')}</td>
                <td className={CELL}>
                  {recallEntries.length > 0 ? (
                    <div className="flex flex-wrap gap-x-2 text-base font-semibold text-slate-900">
                      {recallEntries.map(([k, value]) => (
                        <span key={k}>
                          @{k}: {PERCENT(value)}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="text-base font-semibold text-slate-900">—</span>
                  )}
                </td>
                <td className={CELL}>
                  <span className="text-base font-semibold text-slate-900">
                    <MetricValue value={run.mrr} format={(v) => v.toFixed(3)} />
                  </span>
                  <p className="text-xs text-slate-500">
                    Abstention <MetricValue value={run.abstention_rate} format={PERCENT} /> · Identity
                    Recall@1 <MetricValue value={run.identity_recall_at_1} format={PERCENT} />
                  </p>
                </td>
                <td className={CELL}>
                  {run.graded_case_count}/{run.case_count}
                </td>
                <td className={`${CELL} whitespace-nowrap`}>{run.duration_ms} ms</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
