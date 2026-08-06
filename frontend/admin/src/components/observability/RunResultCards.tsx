import type { EvalRunSummary } from '@/lib/observability/types'
import MetricValue from './MetricValue'

const PERCENT = (value: number) => `${(value * 100).toFixed(1)}%`

/**
 * Recall@K and MRR are the two headline numbers the task explicitly asked to be
 * prominent — rendered as cards, one per k-value for recall plus one for MRR.
 * `abstention_rate`/`identity_recall_at_1` are secondary/canary numbers, shown smaller
 * underneath rather than as their own cards, per the same instruction.
 *
 * A `failed` run has every metric `null` together — every card renders "—" via
 * `MetricValue`, never "0%"/"0.00", so a failed run cannot be misread as a run that
 * scored zero.
 */
export default function RunResultCards({ run }: { run: EvalRunSummary }) {
  const recallEntries = Object.entries(run.recall_at_k ?? {}).sort(
    ([a], [b]) => Number(a) - Number(b),
  )
  // A failed run has `recall_at_k: null` and no k-value keys to iterate — fall back to
  // the k-values the run was asked to compute, so the "—" cards still show which k they
  // are missing rather than rendering nothing at all.
  const kValues = recallEntries.length > 0 ? recallEntries.map(([k]) => k) : run.k_values.map(String)

  return (
    <div className="space-y-2">
      {run.status === 'failed' ? (
        <p role="status" className="text-sm font-medium text-red-800">
          Run failed{run.error_code ? ` (${run.error_code})` : ''} — metrics were not computed.
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {kValues.map((k) => (
          <Card key={k} label={`Recall@${k}`} value={run.recall_at_k?.[k] ?? null} format={PERCENT} />
        ))}
        <Card label="MRR" value={run.mrr} format={(v) => v.toFixed(3)} />
      </div>

      <p className="text-xs text-slate-500">
        Abstention rate: <MetricValue value={run.abstention_rate} format={PERCENT} /> · Identity
        Recall@1: <MetricValue value={run.identity_recall_at_1} format={PERCENT} />
      </p>
    </div>
  )
}

function Card({
  label,
  value,
  format,
}: {
  label: string
  value: number | null
  format: (value: number) => string
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-center">
      <p className="text-xs font-medium tracking-wide text-slate-500 uppercase">{label}</p>
      <p className="text-lg font-semibold text-slate-900">
        <MetricValue value={value} format={format} />
      </p>
    </div>
  )
}
