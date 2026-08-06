"""``/api/v1/observability`` — admin-only RAG retrieval logs, evaluation runs, and
answer faithfulness checks.

Every endpoint here is ``403``, not ``404``, for a non-admin caller: these tables carry
the query text of every user's conversation, cross-conversation, and there is no
per-agent scoping to fall back to (decision 6's preamble in
``Documentation/audits/2026-08-06-rag-observability-and-faithfulness.md``). Unknown
*individual record* ids still answer ``404``.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import col, select

from app.api.chat import get_faq_index
from app.api.deps import DbSession, SettingsDep, enforce_csrf, require_role
from app.api.pagination import Page, PageParamsDep, paginate
from app.core.exceptions import (
    EvalRunFailedError as DomainEvalRunFailedError,
    EvalRunNotFoundError,
    EvalSetUnavailableError as DomainEvalSetUnavailableError,
    FaithfulnessCheckNotFoundError,
    FaqIndexUnavailableError as DomainFaqIndexUnavailableError,
    InvalidDateRangeError,
    InvalidKValueError as DomainInvalidKValueError,
    InvalidScoreRangeError,
    InvalidTierError as DomainInvalidTierError,
)
from app.models import (
    FaithfulnessCheck,
    FaithfulnessClaim,
    FaithfulnessStatus,
    Message,
    RetrievalEvalCase,
    RetrievalEvalRun,
    RetrievalEvalRunStatus,
    RetrievalLog,
    User,
)
from app.observability.eval import (
    EvalRunFailedError,
    EvalSetUnavailableError,
    FaqIndexUnavailableError,
    InvalidKValueError,
    InvalidTierError,
    run_evaluation,
)
from app.rag.index import FaqRetriever
from app.schemas.observability import (
    ANSWER_PREVIEW_LENGTH,
    EvalRunDetail,
    EvalRunRequest,
    EvalRunSummary,
    FaithfulnessCheckDetail,
    FaithfulnessCheckSummary,
    FaithfulnessContextEntry,
    RetrievalLogResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/observability",
    tags=["observability"],
    dependencies=[Depends(enforce_csrf)],
)

AdminUser = Annotated[User, Depends(require_role("admin"))]


class _NullsLastColumn:
    """Wraps a nullable sort column so ``paginate()``'s generic
    ``column.desc()``/``column.asc()`` calls both land on ``NULLS LAST``.

    SQLite and PostgreSQL disagree on where a bare ``ORDER BY`` puts ``NULL`` (first vs.
    last). Decision 6(5) calls out ``FaithfulnessCheck.score`` as the one nullable sort
    column that needs this — that undersold it: ``RetrievalLog.top_score`` (null on an
    empty-results retrieval) and ``RetrievalEvalRun.mrr`` (null on a failed or
    all-negative-tier run) are nullable and in their own sort whitelists too. All three
    wrap here rather than widening the shared ``app/api/pagination.py`` helper, which
    every other (non-nullable) list endpoint still uses unchanged.
    """

    def __init__(self, column) -> None:
        self._column = column

    def desc(self):
        return self._column.desc().nulls_last()

    def asc(self):
        return self._column.asc().nulls_last()


def _answer_preview(db: DbSession, message_id: str) -> str:
    message = db.get(Message, message_id)
    if message is None:
        return ""
    return message.content[:ANSWER_PREVIEW_LENGTH]


# ---------------------------------------------------------------------------
# (1) GET /observability/retrieval-logs
# ---------------------------------------------------------------------------

RETRIEVAL_LOG_SORT_COLUMNS = {
    "created_at": col(RetrievalLog.created_at),
    # Nullable (null when a query returned nothing) — same NULLS LAST reasoning as
    # FaithfulnessCheck.score below, and the same exposure: SQLite and PostgreSQL
    # disagree on default null placement.
    "top_score": _NullsLastColumn(col(RetrievalLog.top_score)),
    "result_count": col(RetrievalLog.result_count),
}


@router.get("/retrieval-logs", response_model=Page[RetrievalLogResponse])
def list_retrieval_logs(
    admin: AdminUser,
    db: DbSession,
    params: PageParamsDep,
    q: Annotated[str | None, Query(description="Free text over query_text")] = None,
    conversation_id: Annotated[str | None, Query()] = None,
    user_id: Annotated[str | None, Query()] = None,
    has_results: Annotated[bool | None, Query()] = None,
    date_from: Annotated[datetime | None, Query(description="Inclusive, over created_at")] = None,
    date_to: Annotated[datetime | None, Query(description="Inclusive, over created_at")] = None,
) -> Page[RetrievalLogResponse]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidDateRangeError()

    statement = select(RetrievalLog)
    if q:
        statement = statement.where(col(RetrievalLog.query_text).ilike(f"%{q.strip()}%"))
    if conversation_id:
        statement = statement.where(col(RetrievalLog.conversation_id) == conversation_id)
    if user_id:
        statement = statement.where(col(RetrievalLog.user_id) == user_id)
    if has_results is not None:
        statement = (
            statement.where(col(RetrievalLog.result_count) > 0)
            if has_results
            else statement.where(col(RetrievalLog.result_count) == 0)
        )
    if date_from is not None:
        statement = statement.where(col(RetrievalLog.created_at) >= date_from)
    if date_to is not None:
        statement = statement.where(col(RetrievalLog.created_at) <= date_to)

    page = paginate(
        db,
        statement,
        params,
        sort_columns=RETRIEVAL_LOG_SORT_COLUMNS,
        default_sort="-created_at",
        item_factory=RetrievalLogResponse.from_log,
    )
    logger.info("retrieval_logs_listed", extra={"user_id": admin.id, "total": page.total})
    return page


# ---------------------------------------------------------------------------
# (2)/(3)/(4) /observability/retrieval/eval-runs
# ---------------------------------------------------------------------------

EVAL_RUN_SORT_COLUMNS = {
    "created_at": col(RetrievalEvalRun.created_at),
    # Nullable (null on a failed run, or a run with no graded cases) — NULLS LAST for
    # the same reason as RetrievalLog.top_score above.
    "mrr": _NullsLastColumn(col(RetrievalEvalRun.mrr)),
}


@router.post("/retrieval/eval-runs", response_model=EvalRunDetail, status_code=201)
async def trigger_eval_run(
    request: Request, payload: EvalRunRequest, admin: AdminUser, db: DbSession, settings: SettingsDep
) -> EvalRunDetail:
    rag: FaqRetriever = get_faq_index(request, db)
    try:
        run = await run_evaluation(
            db,
            rag,
            settings,
            triggered_by_id=admin.id,
            tiers=payload.tiers,
            k_values=payload.k_values,
        )
    except EvalSetUnavailableError as exc:
        raise DomainEvalSetUnavailableError(str(exc)) from exc
    except FaqIndexUnavailableError as exc:
        raise DomainFaqIndexUnavailableError(str(exc)) from exc
    except InvalidKValueError as exc:
        raise DomainInvalidKValueError(str(exc)) from exc
    except InvalidTierError as exc:
        raise DomainInvalidTierError(str(exc)) from exc
    except EvalRunFailedError as exc:
        raise DomainEvalRunFailedError(str(exc), run_id=exc.run_id) from exc

    cases = db.exec(
        select(RetrievalEvalCase).where(col(RetrievalEvalCase.run_id) == run.id)
    ).all()
    logger.info("eval_run_triggered", extra={"user_id": admin.id, "run_id": run.id})
    return EvalRunDetail.from_run_and_cases(run, list(cases))


@router.get("/retrieval/eval-runs", response_model=Page[EvalRunSummary])
def list_eval_runs(
    admin: AdminUser,
    db: DbSession,
    params: PageParamsDep,
    status: Annotated[RetrievalEvalRunStatus | None, Query()] = None,
) -> Page[EvalRunSummary]:
    statement = select(RetrievalEvalRun)
    if status:
        statement = statement.where(col(RetrievalEvalRun.status) == status)

    page = paginate(
        db,
        statement,
        params,
        sort_columns=EVAL_RUN_SORT_COLUMNS,
        default_sort="-created_at",
        item_factory=EvalRunSummary.from_run,
    )
    logger.info("eval_runs_listed", extra={"user_id": admin.id, "total": page.total})
    return page


@router.get("/retrieval/eval-runs/{run_id}", response_model=EvalRunDetail)
def get_eval_run(run_id: str, admin: AdminUser, db: DbSession) -> EvalRunDetail:
    run = db.get(RetrievalEvalRun, run_id)
    if run is None:
        raise EvalRunNotFoundError()

    cases = db.exec(
        select(RetrievalEvalCase).where(col(RetrievalEvalCase.run_id) == run_id)
    ).all()
    logger.info("eval_run_read", extra={"user_id": admin.id, "run_id": run_id})
    return EvalRunDetail.from_run_and_cases(run, list(cases))


# ---------------------------------------------------------------------------
# (5)/(6) /observability/faithfulness
# ---------------------------------------------------------------------------

FAITHFULNESS_SORT_COLUMNS = {
    "created_at": col(FaithfulnessCheck.created_at),
    "score": _NullsLastColumn(col(FaithfulnessCheck.score)),
}


@router.get("/faithfulness", response_model=Page[FaithfulnessCheckSummary])
def list_faithfulness_checks(
    admin: AdminUser,
    db: DbSession,
    params: PageParamsDep,
    status: Annotated[list[FaithfulnessStatus] | None, Query(description="Repeatable")] = None,
    conversation_id: Annotated[str | None, Query()] = None,
    min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
    max_score: Annotated[float | None, Query(ge=0, le=1)] = None,
    date_from: Annotated[datetime | None, Query(description="Inclusive, over created_at")] = None,
    date_to: Annotated[datetime | None, Query(description="Inclusive, over created_at")] = None,
) -> Page[FaithfulnessCheckSummary]:
    if min_score is not None and max_score is not None and min_score > max_score:
        raise InvalidScoreRangeError()
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidDateRangeError()

    statement = select(FaithfulnessCheck)
    if status:
        statement = statement.where(col(FaithfulnessCheck.status).in_(status))
    if conversation_id:
        statement = statement.where(col(FaithfulnessCheck.conversation_id) == conversation_id)
    # Either bound excludes null-score rows (decision 6(5)) — a check that was never
    # scored has no score to compare against a range.
    if min_score is not None:
        statement = statement.where(col(FaithfulnessCheck.score) >= min_score)
    if max_score is not None:
        statement = statement.where(col(FaithfulnessCheck.score) <= max_score)
    if date_from is not None:
        statement = statement.where(col(FaithfulnessCheck.created_at) >= date_from)
    if date_to is not None:
        statement = statement.where(col(FaithfulnessCheck.created_at) <= date_to)

    page = paginate(
        db,
        statement,
        params,
        sort_columns=FAITHFULNESS_SORT_COLUMNS,
        default_sort="-created_at",
        item_factory=lambda check: FaithfulnessCheckSummary.from_check(
            check, answer_preview=_answer_preview(db, check.message_id)
        ),
    )
    logger.info("faithfulness_checks_listed", extra={"user_id": admin.id, "total": page.total})
    return page


@router.get("/faithfulness/{check_id}", response_model=FaithfulnessCheckDetail)
def get_faithfulness_check(
    request: Request, check_id: str, admin: AdminUser, db: DbSession
) -> FaithfulnessCheckDetail:
    check = db.get(FaithfulnessCheck, check_id)
    if check is None:
        raise FaithfulnessCheckNotFoundError()

    rag: FaqRetriever = get_faq_index(request, db)

    claims = db.exec(
        select(FaithfulnessClaim)
        .where(col(FaithfulnessClaim.check_id) == check_id)
        .order_by(col(FaithfulnessClaim.claim_index))
    ).all()

    context: list[FaithfulnessContextEntry] = []
    for faq_id in check.context_faq_ids:
        entry = rag.get(faq_id)
        if entry is not None:
            context.append(
                FaithfulnessContextEntry(
                    faq_id=entry.id,
                    question=entry.question,
                    category=entry.category,
                    answer=entry.answer,
                )
            )

    logger.info("faithfulness_check_read", extra={"user_id": admin.id, "check_id": check_id})
    return FaithfulnessCheckDetail.from_check_claims_and_context(
        check,
        list(claims),
        context,
        answer_preview=_answer_preview(db, check.message_id),
    )
