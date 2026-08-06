import { useSearchParams } from 'react-router'

import Tabs, { type TabItem } from '@/components/ui/Tabs'
import EvalRunsTab from '@/components/observability/EvalRunsTab'
import RetrievalLogTab from '@/components/observability/RetrievalLogTab'

const TAB_PARAM = 'tab'
const RETRIEVAL_LOG_TAB = 'retrieval-log'
const EVAL_RUNS_TAB = 'eval-runs'

const TABS: readonly TabItem[] = [
  { id: RETRIEVAL_LOG_TAB, label: 'Retrieval log' },
  { id: EVAL_RUNS_TAB, label: 'Eval runs' },
]

/**
 * Admin-only RAG observability. The router's `<RoleGate>` keeps everyone else off this
 * path — a UX convenience mirroring the hidden nav item, never the security boundary:
 * every endpoint under `/observability` answers `403 forbidden` (not `404`) to a
 * non-admin regardless of what this file renders (`app/api/observability.py`).
 *
 * Two tabs only: Retrieval log and Eval runs. A Faithfulness tab is explicitly deferred
 * — the backend pipeline and its endpoints already exist and are merged, but nothing
 * here references them, by design, not because they were missed.
 */
export default function ObservabilityPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const active = parseTab(searchParams.get(TAB_PARAM))

  function setTab(next: string) {
    const params = new URLSearchParams(searchParams)
    if (next === RETRIEVAL_LOG_TAB) {
      params.delete(TAB_PARAM)
    } else {
      params.set(TAB_PARAM, next)
    }
    setSearchParams(params)
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">RAG Observability</h1>
        <p className="text-sm text-slate-500">
          What the FAQ retriever returned, and how well it scores against the eval set.
        </p>
      </div>

      <Tabs items={TABS} active={active} onChange={setTab} label="Observability sections" />

      {active === EVAL_RUNS_TAB ? <EvalRunsTab /> : <RetrievalLogTab />}
    </div>
  )
}

function parseTab(raw: string | null): string {
  return raw === EVAL_RUNS_TAB ? EVAL_RUNS_TAB : RETRIEVAL_LOG_TAB
}
