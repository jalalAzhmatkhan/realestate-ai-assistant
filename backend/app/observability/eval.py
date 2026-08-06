"""Retrieval evaluation: Recall@K and MRR against a curated, version-controlled set.

Contract: ``Documentation/audits/2026-08-06-rag-observability-and-faithfulness.md``
decisions 2 and 3. Two rules carry the whole design:

* **The run calls the same ``FaqRetriever`` the tool calls**, against the same
  ``faq_embeddings`` rows — never a parallel evaluation path or a re-embedded corpus.
* **The ``identity`` tier is a canary, excluded from the headline ``recall_at_k``/``mrr``**
  (reported separately as ``identity_recall_at_1``), and ``negative`` cases carry no
  recall/MRR at all — only ``abstention_rate``. Both exclusions are what "graded" means
  in decision 3's formula, not an approximation of it.
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlmodel import Session

from app.core.config import Settings
from app.models import RetrievalEvalCase, RetrievalEvalRun
from app.rag.index import FaqRetriever, load_faq_entries

logger = logging.getLogger(__name__)

EVAL_SET_FILENAME = "faq_eval_set.json"
PARAPHRASE_TIER = "paraphrase"
IDENTITY_TIER = "identity"
NEGATIVE_TIER = "negative"
VALID_TIERS = frozenset({PARAPHRASE_TIER, IDENTITY_TIER, NEGATIVE_TIER})
DEFAULT_TIERS: tuple[str, ...] = (PARAPHRASE_TIER, IDENTITY_TIER, NEGATIVE_TIER)
DEFAULT_K_VALUES: tuple[int, ...] = (1, 3, 5)
MIN_K = 1
MAX_K = 10


class EvalSetUnavailableError(RuntimeError):
    """``faq_eval_set.json`` is missing, malformed, or over ``EVAL_SET_MAX_CASES``."""


class InvalidKValueError(RuntimeError):
    """``k_values`` is empty, or some ``k`` is outside ``[MIN_K, MAX_K]``."""


class InvalidTierError(RuntimeError):
    """A requested tier is not one of ``paraphrase``/``identity``/``negative``."""


class FaqIndexUnavailableError(RuntimeError):
    """``faq_embeddings`` has zero rows for the configured ``embedding_model``.

    Running an evaluation against an unpopulated index would report Recall@1 = 0.00 and
    MRR = 0.00 — numerically correct and a catastrophically misleading way to say
    "nobody ran the reindex". A run that cannot be meaningful must refuse, mirroring
    :class:`app.rag.index.EmptyFaqIndexError`'s guard on the query path itself.
    """


class EvalRunFailedError(RuntimeError):
    """The run failed mid-flight (e.g. the embedding provider went unreachable).

    ``run_id`` names the row already persisted with ``status="failed"`` — a failed run
    that leaves no trace would be indistinguishable from a run nobody triggered.
    """

    def __init__(self, run_id: str, message: str) -> None:
        super().__init__(message)
        self.run_id = run_id


@dataclass(frozen=True)
class EvalCase:
    id: str
    tier: str
    query: str
    expected_faq_ids: list[str]


def _load_file_cases(seed_data_dir: str | Path, max_cases: int) -> tuple[str, list[EvalCase]]:
    path = Path(seed_data_dir) / EVAL_SET_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = str(raw["version"])
        cases = [
            EvalCase(
                id=str(item["id"]),
                tier=str(item["tier"]),
                query=str(item["query"]),
                expected_faq_ids=list(item.get("expected_faq_ids", [])),
            )
            for item in raw["cases"]
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvalSetUnavailableError(
            f"{EVAL_SET_FILENAME} is missing or malformed: {exc}"
        ) from exc

    if len(cases) > max_cases:
        raise EvalSetUnavailableError(
            f"{EVAL_SET_FILENAME} has {len(cases)} cases, exceeding "
            f"EVAL_SET_MAX_CASES={max_cases}."
        )
    return version, cases


def _identity_cases(seed_data_dir: str | Path) -> list[EvalCase]:
    """Generated at run time from ``faq.json``'s own ``question`` field — never
    authored, never stored in the eval-set file (decision 2)."""
    return [
        EvalCase(id=f"identity-{entry.id}", tier=IDENTITY_TIER, query=entry.question,
                  expected_faq_ids=[entry.id])
        for entry in load_faq_entries(seed_data_dir)
    ]


def _recall_at_k(expected: set[str], ranked_ids: list[str], k_values: list[int]) -> dict[str, float]:
    if not expected:
        return {}
    return {str(k): len(expected & set(ranked_ids[:k])) / len(expected) for k in k_values}


def _first_relevant_rank(expected: set[str], ranked_ids: list[str]) -> int | None:
    for rank, faq_id in enumerate(ranked_ids, start=1):
        if faq_id in expected:
            return rank
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def run_evaluation(
    db: Session,
    rag: FaqRetriever,
    settings: Settings,
    *,
    triggered_by_id: str,
    tiers: list[str] | None = None,
    k_values: list[int] | None = None,
) -> RetrievalEvalRun:
    """Run the evaluation set (or a subset of tiers), persist the run + case rows, and
    return the run. Raises before persisting anything for a request-shaped problem
    (bad tier, bad k, no eval set, empty index); persists a ``status="failed"`` run
    first for a mid-run failure (see :class:`EvalRunFailedError`)."""
    resolved_tiers = list(tiers) if tiers else list(DEFAULT_TIERS)
    for tier in resolved_tiers:
        if tier not in VALID_TIERS:
            raise InvalidTierError(
                f"Unknown tier {tier!r}. Allowed: {', '.join(sorted(VALID_TIERS))}."
            )

    resolved_k = list(k_values) if k_values else list(DEFAULT_K_VALUES)
    if not resolved_k or any(k < MIN_K or k > MAX_K for k in resolved_k):
        raise InvalidKValueError(
            f"k_values must be non-empty, each between {MIN_K} and {MAX_K}."
        )

    stats = rag.index_stats()
    if stats.row_count == 0:
        raise FaqIndexUnavailableError(
            f"faq_embeddings has no rows for embedding_model={rag.embedding_model_name!r}. "
            "Run `python -m app.rag.reindex` first."
        )

    version, file_cases = _load_file_cases(settings.seed_data_dir, settings.eval_set_max_cases)
    cases_by_tier: dict[str, list[EvalCase]] = {PARAPHRASE_TIER: [], NEGATIVE_TIER: []}
    for case in file_cases:
        cases_by_tier.setdefault(case.tier, []).append(case)
    cases_by_tier[IDENTITY_TIER] = _identity_cases(settings.seed_data_dir)

    selected: list[EvalCase] = [
        case for tier in resolved_tiers for case in cases_by_tier.get(tier, [])
    ]

    top_k = max(resolved_k)
    run_id = f"evalrun-{uuid4().hex[:12]}"
    started = time.monotonic()

    case_rows: list[RetrievalEvalCase] = []
    paraphrase_recalls: list[dict[str, float]] = []
    paraphrase_rrs: list[float] = []
    identity_recall_1: list[float] = []
    negative_total = 0
    negative_abstained = 0

    try:
        for case in selected:
            hits = await rag.search(case.query, top_k=top_k, min_score=settings.rag_min_score)
            ranked_ids = [hit.entry.id for hit in hits]
            expected = set(case.expected_faq_ids)

            recall_map = _recall_at_k(expected, ranked_ids, resolved_k)
            rank = _first_relevant_rank(expected, ranked_ids)
            reciprocal_rank = 1.0 / rank if rank else 0.0
            abstained = len(ranked_ids) == 0

            case_rows.append(
                RetrievalEvalCase(
                    id=f"evalcase-{uuid4().hex[:12]}",
                    run_id=run_id,
                    case_id=case.id,
                    tier=case.tier,
                    query_text=case.query,
                    expected_faq_ids=case.expected_faq_ids,
                    results=[
                        {
                            "rank": i,
                            "faq_id": hit.entry.id,
                            "question": hit.entry.question,
                            "category": hit.entry.category,
                            "score": hit.score,
                        }
                        for i, hit in enumerate(hits, start=1)
                    ],
                    first_relevant_rank=rank,
                    reciprocal_rank=reciprocal_rank,
                    recall_at_k=recall_map,
                    abstained=abstained,
                )
            )

            if case.tier == PARAPHRASE_TIER:
                paraphrase_recalls.append(recall_map)
                paraphrase_rrs.append(reciprocal_rank)
            elif case.tier == IDENTITY_TIER:
                identity_recall_1.append(recall_map.get("1", 0.0))
            elif case.tier == NEGATIVE_TIER:
                negative_total += 1
                if abstained:
                    negative_abstained += 1
    except Exception as exc:
        failed_run = RetrievalEvalRun(
            id=run_id,
            triggered_by_id=triggered_by_id,
            tiers=resolved_tiers,
            k_values=resolved_k,
            eval_set_version=version,
            case_count=len(selected),
            graded_case_count=0,
            negative_case_count=0,
            min_score=settings.rag_min_score,
            embedding_model=rag.embedding_model_name,
            index_row_count=stats.row_count,
            index_indexed_at=stats.indexed_at,
            status="failed",
            error_code="eval_run_failed",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        db.add(failed_run)
        db.commit()
        logger.exception("eval_run_failed", extra={"run_id": run_id})
        raise EvalRunFailedError(run_id, str(exc)) from exc

    recall_at_k = (
        {
            str(k): sum(r.get(str(k), 0.0) for r in paraphrase_recalls) / len(paraphrase_recalls)
            for k in resolved_k
        }
        if paraphrase_recalls
        else None
    )

    run = RetrievalEvalRun(
        id=run_id,
        triggered_by_id=triggered_by_id,
        tiers=resolved_tiers,
        k_values=resolved_k,
        eval_set_version=version,
        case_count=len(selected),
        graded_case_count=len(paraphrase_recalls),
        negative_case_count=negative_total,
        recall_at_k=recall_at_k,
        mrr=_mean(paraphrase_rrs),
        abstention_rate=(negative_abstained / negative_total) if negative_total else None,
        identity_recall_at_1=_mean(identity_recall_1),
        min_score=settings.rag_min_score,
        embedding_model=rag.embedding_model_name,
        index_row_count=stats.row_count,
        index_indexed_at=stats.indexed_at,
        status="completed",
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    db.add(run)
    # No relationship() anywhere in this codebase's models (app/db/seed.py), so the
    # unit-of-work does not know retrieval_eval_cases.run_id depends on this row —
    # without an explicit flush here, commit() can emit the child inserts first and
    # fail the foreign key.
    db.flush()
    for row in case_rows:
        db.add(row)
    db.commit()
    db.refresh(run)
    logger.info(
        "eval_run_completed",
        extra={
            "run_id": run.id,
            "case_count": run.case_count,
            "recall_at_k": run.recall_at_k,
            "mrr": run.mrr,
            "abstention_rate": run.abstention_rate,
        },
    )
    return run
