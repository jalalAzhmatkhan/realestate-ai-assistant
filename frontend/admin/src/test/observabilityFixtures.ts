import type { Page } from '@/lib/api/pagination'
import type { EvalCase, EvalRunDetail, EvalRunSummary, RetrievalLogItem } from '@/lib/observability/types'

export function buildRetrievalLog(overrides: Partial<RetrievalLogItem> = {}): RetrievalLogItem {
  return {
    id: 'log-1',
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    user_message_id: 'msg-0',
    user_id: 'u-client',
    query_text: 'What is the deposit for a viewing?',
    requested_top_k: 3,
    effective_top_k: 3,
    min_score: 0.5,
    results: [
      { rank: 1, faq_id: 'faq-1', question: 'What is the booking deposit?', category: 'booking', score: 0.91 },
    ],
    result_count: 1,
    top_score: 0.91,
    embedding_model: 'text-embedding-3-small',
    latency_ms: 120,
    created_at: '2026-08-01T03:00:00Z',
    ...overrides,
  }
}

export function buildRetrievalLogPage(
  results: RetrievalLogItem[],
  overrides: Partial<Page<RetrievalLogItem>> = {},
): Page<RetrievalLogItem> {
  return {
    results,
    page: 1,
    page_size: 20,
    total: results.length,
    total_pages: 1,
    ...overrides,
  }
}

export function buildEvalRunSummary(overrides: Partial<EvalRunSummary> = {}): EvalRunSummary {
  return {
    id: 'run-1',
    triggered_by_id: 'u-admin',
    tiers: ['paraphrase', 'identity', 'negative'],
    k_values: [1, 3, 5],
    eval_set_version: 'v1',
    case_count: 30,
    graded_case_count: 20,
    negative_case_count: 10,
    recall_at_k: { '1': 0.79, '3': 0.93, '5': 0.96 },
    mrr: 0.85,
    abstention_rate: 0.9,
    identity_recall_at_1: 1,
    min_score: 0.5,
    embedding_model: 'text-embedding-3-small',
    index_row_count: 42,
    index_indexed_at: '2026-07-30T00:00:00Z',
    status: 'completed',
    error_code: null,
    duration_ms: 2400,
    created_at: '2026-08-01T03:00:00Z',
    ...overrides,
  }
}

/** A `failed` run: every metric is `null` together, never `0`. */
export function buildFailedEvalRunSummary(overrides: Partial<EvalRunSummary> = {}): EvalRunSummary {
  return buildEvalRunSummary({
    id: 'run-failed',
    recall_at_k: null,
    mrr: null,
    abstention_rate: null,
    identity_recall_at_1: null,
    status: 'failed',
    error_code: 'eval_run_failed',
    ...overrides,
  })
}

export function buildEvalRunPage(
  results: EvalRunSummary[],
  overrides: Partial<Page<EvalRunSummary>> = {},
): Page<EvalRunSummary> {
  return {
    results,
    page: 1,
    page_size: 20,
    total: results.length,
    total_pages: 1,
    ...overrides,
  }
}

export function buildEvalCase(overrides: Partial<EvalCase> = {}): EvalCase {
  return {
    id: 'case-1',
    case_id: 'q-001',
    tier: 'paraphrase',
    query_text: 'How much deposit do I need for a viewing?',
    expected_faq_ids: ['faq-1'],
    results: [
      { rank: 1, faq_id: 'faq-1', question: 'What is the booking deposit?', category: 'booking', score: 0.91 },
    ],
    first_relevant_rank: 1,
    reciprocal_rank: 1,
    recall_at_k: { '1': 1, '3': 1, '5': 1 },
    abstained: false,
    ...overrides,
  }
}

export function buildEvalRunDetail(overrides: Partial<EvalRunDetail> = {}): EvalRunDetail {
  return {
    ...buildEvalRunSummary(),
    cases: [buildEvalCase()],
    ...overrides,
  }
}
