import { useState } from 'react'
import { Link } from 'react-router'

import Button from '@/components/ui/Button'
import { isApiError } from '@/lib/api/errors'
import { useTriggerEvalRun } from '@/lib/observability/mutations'
import {
  DEFAULT_EVAL_K_VALUES,
  EVAL_TIER_LABELS,
  EVAL_TIERS,
  type EvalRunDetail,
  type EvalRunRequest,
  type EvalTier,
} from '@/lib/observability/types'
import RunResultCards from './RunResultCards'

export interface EvalRunTriggerPanelProps {
  /** Fired after a run lands (success or a persisted `failed` row), so the history table below can be told to focus it. */
  onRunCompleted?: (run: EvalRunDetail) => void
}

/**
 * The "Run evaluation" action from the task spec: a real synchronous `POST` — typically
 * well under a few seconds — so the button carries its own loading state rather than
 * this screen polling for completion. An empty `{}` body (every tier, k=1,3,5) is the
 * default; the tier/k controls below are optional tuning, not required to click Run.
 */
export default function EvalRunTriggerPanel({ onRunCompleted }: EvalRunTriggerPanelProps) {
  const [tiers, setTiers] = useState<EvalTier[]>([...EVAL_TIERS])
  const [kValuesText, setKValuesText] = useState(DEFAULT_EVAL_K_VALUES.join(', '))
  const trigger = useTriggerEvalRun()

  function toggleTier(tier: EvalTier, checked: boolean) {
    setTiers((prev) => (checked ? [...prev, tier] : prev.filter((value) => value !== tier)))
  }

  function run() {
    const kValues = parseKValues(kValuesText)
    const payload: EvalRunRequest = {}
    // An empty/full selection is sent as "unset" rather than an explicit array: the
    // backend treats a genuinely empty `tiers`/`k_values` as "use the default", and this
    // screen has no affordance for "run against zero tiers" to preserve.
    if (tiers.length > 0 && tiers.length < EVAL_TIERS.length) payload.tiers = tiers
    if (kValues.length > 0) payload.k_values = kValues

    trigger.mutate(payload, {
      onSuccess: (run) => {
        onRunCompleted?.(run)
      },
    })
  }

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Run evaluation</h2>
          <p className="text-sm text-slate-500">
            Scores the FAQ retriever against the hand-authored eval set.
          </p>
        </div>
        <Button onClick={run} disabled={trigger.isPending}>
          {trigger.isPending ? 'Running…' : 'Run evaluation'}
        </Button>
      </div>

      <details className="text-sm text-slate-700">
        <summary className="cursor-pointer font-medium text-slate-700">Options</summary>
        <div className="mt-3 space-y-3">
          <fieldset>
            <legend className="text-sm font-medium text-slate-700">Tiers</legend>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {EVAL_TIERS.map((tier) => (
                <label key={tier} className="flex items-center gap-1.5 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={tiers.includes(tier)}
                    onChange={(event) => {
                      toggleTier(tier, event.target.checked)
                    }}
                    className="size-4 rounded border-slate-300 text-blue-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
                  />
                  {EVAL_TIER_LABELS[tier]}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="space-y-1">
            <label htmlFor="eval-k-values" className="block text-sm font-medium text-slate-700">
              k values
            </label>
            <input
              id="eval-k-values"
              type="text"
              value={kValuesText}
              onChange={(event) => {
                setKValuesText(event.target.value)
              }}
              placeholder="1, 3, 5"
              className="w-full max-w-xs rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-blue-600"
            />
            <p className="text-xs text-slate-500">Comma-separated. Each must be between 1 and 10.</p>
          </div>
        </div>
      </details>

      {trigger.isError ? <TriggerError error={trigger.error} /> : null}
      {trigger.isSuccess ? <RunResultCards run={trigger.data} /> : null}
    </div>
  )
}

/** Comma-separated free text -> a list of positive integers; anything else is dropped
 * rather than sent, so a stray character does not turn into a 422 the user cannot map
 * back to what they typed. */
function parseKValues(text: string): number[] {
  return text
    .split(',')
    .map((part) => Number(part.trim()))
    .filter((value) => Number.isInteger(value) && value > 0)
}

function TriggerError({ error }: { error: unknown }) {
  if (isApiError(error) && error.code === 'faq_index_unavailable') {
    return (
      <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
        {error.message} Run the FAQ reindex, then try again.
      </p>
    )
  }

  // `502 eval_run_failed` still persists the row it failed on (`detail.run_id`), so a
  // link straight to it beats sending the admin to hunt for it in the history table.
  const runId = isApiError(error) && typeof error.details.run_id === 'string' ? error.details.run_id : undefined

  const message = isApiError(error) ? error.message : 'Could not run the evaluation. Please try again.'
  return (
    <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      {message}
      {runId ? (
        <>
          {' '}
          <Link to={`/observability/eval-runs/${runId}`} className="font-medium underline">
            View the failed run
          </Link>
        </>
      ) : null}
    </p>
  )
}
