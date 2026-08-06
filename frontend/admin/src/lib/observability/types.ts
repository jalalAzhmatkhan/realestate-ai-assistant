/**
 * Mirrors `app/schemas/observability.py` and `app/api/observability.py`. Wire names
 * throughout, for the same reason as `lib/properties/types.ts`.
 *
 * Only the retrieval-log and eval-run surfaces are typed here — F8 explicitly cut the
 * Faithfulness tab from this pass even though `/observability/faithfulness` and
 * `/observability/faithfulness/{id}` are already implemented and merged on the backend.
 * Nothing here references those endpoints; adding the tab later is additive, not a
 * rework of this file.
 */

/** One row of `RetrievalLogResponse.results` — inline, not a separate fetch. */
export interface RetrievalResultEntry {
  rank: number
  faq_id: string
  question: string
  category: string
  score: number
}

/** Item type of `GET /observability/retrieval-logs`. */
export interface RetrievalLogItem {
  id: string
  conversation_id: string
  message_id: string
  user_message_id: string | null
  user_id: string
  query_text: string
  requested_top_k: number
  effective_top_k: number
  min_score: number
  results: RetrievalResultEntry[]
  result_count: number
  /** `null` only when `result_count` is 0 — never coerced to 0 in the UI. */
  top_score: number | null
  embedding_model: string
  latency_ms: number
  created_at: string
}

/** The three tiers `run_evaluation` accepts (`app/observability/eval.py`, `VALID_TIERS`). */
export const EVAL_TIERS = ['paraphrase', 'identity', 'negative'] as const
export type EvalTier = (typeof EVAL_TIERS)[number]

export const EVAL_TIER_LABELS: Record<EvalTier, string> = {
  paraphrase: 'Paraphrase',
  identity: 'Identity',
  negative: 'Negative',
}

/** Matches the empty-body default in `EvalRunRequest` — every tier at k=1,3,5. */
export const DEFAULT_EVAL_K_VALUES = [1, 3, 5] as const

export interface EvalRunRequest {
  tiers?: EvalTier[]
  k_values?: number[]
}

export type EvalRunStatus = 'completed' | 'failed'

/**
 * `recall_at_k` keys are the k-values as strings (`{"1": 0.79, "3": 0.93, "5": 0.96}`,
 * per the backend contract) — never coerced to numbers, since the wire shape is already
 * exactly what `Object.entries` over a k-value needs.
 */
export type RecallAtK = Record<string, number>

/**
 * Item type of `GET /observability/retrieval/eval-runs` — every run column except the
 * per-case rows. `recall_at_k`/`mrr`/`abstention_rate`/`identity_recall_at_1` are all
 * `null` together on a `failed` run — rendered as "—", never as 0 (task spec, and the
 * schema docstring: a null means "not computed", not "zero").
 */
export interface EvalRunSummary {
  id: string
  triggered_by_id: string
  tiers: string[]
  k_values: number[]
  eval_set_version: string
  case_count: number
  graded_case_count: number
  negative_case_count: number
  recall_at_k: RecallAtK | null
  mrr: number | null
  abstention_rate: number | null
  identity_recall_at_1: number | null
  min_score: number
  embedding_model: string
  index_row_count: number
  index_indexed_at: string | null
  status: EvalRunStatus
  error_code: string | null
  duration_ms: number
  created_at: string
}

export interface EvalCaseResult {
  rank: number
  faq_id: string
  question: string
  category: string
  score: number
}

export interface EvalCase {
  id: string
  case_id: string
  tier: string
  query_text: string
  expected_faq_ids: string[]
  results: EvalCaseResult[]
  first_relevant_rank: number | null
  reciprocal_rank: number
  recall_at_k: RecallAtK
  abstained: boolean
}

/** `EvalRunDetail` — the summary plus the per-case breakdown, from `POST`/`GET .../{id}`. */
export interface EvalRunDetail extends EvalRunSummary {
  cases: EvalCase[]
}
