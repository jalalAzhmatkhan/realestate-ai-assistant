from datetime import datetime

from pydantic import BaseModel, Field

from app.models import (
    FaithfulnessCheck,
    FaithfulnessClaim,
    FaithfulnessStatus,
    RetrievalEvalCase,
    RetrievalEvalRun,
    RetrievalEvalRunStatus,
    RetrievalLog,
)

ANSWER_PREVIEW_LENGTH = 160


class RetrievalLogResponse(BaseModel):
    """Item type of ``GET /observability/retrieval-logs``. Carries ``results[]`` inline
    (decision 6(1)) — there is no ``GET /retrieval-logs/{id}``."""

    id: str
    conversation_id: str
    message_id: str
    user_message_id: str | None
    user_id: str
    query_text: str
    requested_top_k: int
    effective_top_k: int
    min_score: float
    results: list[dict]
    result_count: int
    top_score: float | None
    embedding_model: str
    latency_ms: int
    created_at: datetime

    @classmethod
    def from_log(cls, log: RetrievalLog) -> "RetrievalLogResponse":
        return cls(
            id=log.id,
            conversation_id=log.conversation_id,
            message_id=log.message_id,
            user_message_id=log.user_message_id,
            user_id=log.user_id,
            query_text=log.query_text,
            requested_top_k=log.requested_top_k,
            effective_top_k=log.effective_top_k,
            min_score=log.min_score,
            results=log.results,
            result_count=log.result_count,
            top_score=log.top_score,
            embedding_model=log.embedding_model,
            latency_ms=log.latency_ms,
            created_at=log.created_at,
        )


class EvalRunRequest(BaseModel):
    """Body of ``POST /observability/retrieval/eval-runs``. Both fields optional —
    an empty body runs every tier at k=1,3,5 (decision 6(2))."""

    tiers: list[str] | None = None
    k_values: list[int] | None = None


class EvalCaseResponse(BaseModel):
    id: str
    case_id: str
    tier: str
    query_text: str
    expected_faq_ids: list[str]
    results: list[dict]
    first_relevant_rank: int | None
    reciprocal_rank: float
    recall_at_k: dict
    abstained: bool

    @classmethod
    def from_case(cls, case: RetrievalEvalCase) -> "EvalCaseResponse":
        return cls(
            id=case.id,
            case_id=case.case_id,
            tier=case.tier,
            query_text=case.query_text,
            expected_faq_ids=case.expected_faq_ids,
            results=case.results,
            first_relevant_rank=case.first_relevant_rank,
            reciprocal_rank=case.reciprocal_rank,
            recall_at_k=case.recall_at_k,
            abstained=case.abstained,
        )


class EvalRunSummary(BaseModel):
    """Item type of ``GET /observability/retrieval/eval-runs`` — every run column
    except the per-case rows."""

    id: str
    triggered_by_id: str
    tiers: list[str]
    k_values: list[int]
    eval_set_version: str
    case_count: int
    graded_case_count: int
    negative_case_count: int
    recall_at_k: dict | None
    mrr: float | None
    abstention_rate: float | None
    identity_recall_at_1: float | None
    min_score: float
    embedding_model: str
    index_row_count: int
    index_indexed_at: datetime | None
    status: RetrievalEvalRunStatus
    error_code: str | None
    duration_ms: int
    created_at: datetime

    @classmethod
    def from_run(cls, run: RetrievalEvalRun) -> "EvalRunSummary":
        return cls(
            id=run.id,
            triggered_by_id=run.triggered_by_id,
            tiers=run.tiers,
            k_values=run.k_values,
            eval_set_version=run.eval_set_version,
            case_count=run.case_count,
            graded_case_count=run.graded_case_count,
            negative_case_count=run.negative_case_count,
            recall_at_k=run.recall_at_k,
            mrr=run.mrr,
            abstention_rate=run.abstention_rate,
            identity_recall_at_1=run.identity_recall_at_1,
            min_score=run.min_score,
            embedding_model=run.embedding_model,
            index_row_count=run.index_row_count,
            index_indexed_at=run.index_indexed_at,
            status=run.status,
            error_code=run.error_code,
            duration_ms=run.duration_ms,
            created_at=run.created_at,
        )


class EvalRunDetail(EvalRunSummary):
    cases: list[EvalCaseResponse]

    @classmethod
    def from_run_and_cases(
        cls, run: RetrievalEvalRun, cases: list[RetrievalEvalCase]
    ) -> "EvalRunDetail":
        return cls(
            **EvalRunSummary.from_run(run).model_dump(),
            cases=[EvalCaseResponse.from_case(case) for case in cases],
        )


class FaithfulnessCheckSummary(BaseModel):
    """Item type of ``GET /observability/faithfulness``. ``answer_preview`` is resolved
    at read time from the referenced ``Message`` — never stored (decision 6(5))."""

    id: str
    message_id: str
    conversation_id: str
    user_id: str
    status: FaithfulnessStatus
    score: float | None
    supported_count: int | None
    unsupported_count: int | None
    not_applicable_count: int | None
    claim_count: int | None
    context_faq_ids: list[str]
    judge_model: str
    error_code: str | None
    duration_ms: int | None
    created_at: datetime
    answer_preview: str

    @classmethod
    def from_check(cls, check: FaithfulnessCheck, *, answer_preview: str) -> "FaithfulnessCheckSummary":
        return cls(
            id=check.id,
            message_id=check.message_id,
            conversation_id=check.conversation_id,
            user_id=check.user_id,
            status=check.status,
            score=check.score,
            supported_count=check.supported_count,
            unsupported_count=check.unsupported_count,
            not_applicable_count=check.not_applicable_count,
            claim_count=check.claim_count,
            context_faq_ids=check.context_faq_ids,
            judge_model=check.judge_model,
            error_code=check.error_code,
            duration_ms=check.duration_ms,
            created_at=check.created_at,
            answer_preview=answer_preview,
        )


class FaithfulnessClaimResponse(BaseModel):
    id: str
    claim_index: int
    claim_text: str
    verdict: str
    supporting_faq_ids: list[str]
    rationale: str | None

    @classmethod
    def from_claim(cls, claim: FaithfulnessClaim) -> "FaithfulnessClaimResponse":
        return cls(
            id=claim.id,
            claim_index=claim.claim_index,
            claim_text=claim.claim_text,
            verdict=claim.verdict,
            supporting_faq_ids=claim.supporting_faq_ids,
            rationale=claim.rationale,
        )


class FaithfulnessContextEntry(BaseModel):
    """One ``context_faq_ids`` entry resolved from ``faq_embeddings`` — not
    ``faq.json`` — so the admin reads the text that was actually retrieved even if the
    corpus file has since been edited (decision 6(6))."""

    faq_id: str
    question: str
    category: str
    answer: str


class FaithfulnessCheckDetail(FaithfulnessCheckSummary):
    claims: list[FaithfulnessClaimResponse]
    context: list[FaithfulnessContextEntry]

    @classmethod
    def from_check_claims_and_context(
        cls,
        check: FaithfulnessCheck,
        claims: list[FaithfulnessClaim],
        context: list[FaithfulnessContextEntry],
        *,
        answer_preview: str,
    ) -> "FaithfulnessCheckDetail":
        return cls(
            **FaithfulnessCheckSummary.from_check(check, answer_preview=answer_preview).model_dump(),
            claims=[FaithfulnessClaimResponse.from_claim(claim) for claim in claims],
            context=context,
        )
