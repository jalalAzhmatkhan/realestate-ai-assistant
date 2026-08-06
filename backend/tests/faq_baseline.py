"""Q9 — the frozen pre-migration retrieval baseline, and the machinery to compare against it.

Checkpoint: ``Documentation/audits/2026-08-06-pgvector-migration-contract.md``, task Q9,
risk R-16 ("the migration silently changes retrieval results").

**Why this module exists separately from ``test_rag.py``:** B33 rewrites
``app/rag/index.py`` onto pgvector and deletes the NumPy path. The reference behavior
disappears with it, so the expected ids/ranks/scores had to be recorded *before* that
happens. Nothing here imports ``FaqIndex`` or any other symbol B33 touches — the fixture
and these helpers survive the rewrite untouched, and the post-B33 equivalence test loads
this same data.

**How B33 uses it:** run each :data:`QUERY_CASES` entry through the new pgvector retriever
against the same corpus and embedding model, then feed the results to
:func:`compare_to_baseline`. Same ids in the same rank order; scores within
:data:`SCORE_TOLERANCE`.

Regenerate (only ever legitimate if the corpus or the embedding model is *deliberately*
changed, which invalidates the baseline's whole purpose for the migration):

    uv run python -m tests.faq_baseline
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "fixtures" / "faq_retrieval_baseline.json"

# Cosine similarity recomputed by a different implementation of the same math differs in
# the last few decimal places; pgvector stores float4, as this index does. Exact equality
# would fail for reasons that are not regressions. A *ranking* change, by contrast, is
# never within tolerance — hence the id order is compared exactly.
SCORE_TOLERANCE = 1e-6


@dataclass(frozen=True)
class BaselineCase:
    name: str
    query: str
    top_k: int
    min_score: float
    intent: str


# `min_score=-1.0` sits below `Settings.rag_min_score`'s validated range on purpose: it
# disables the floor so the *complete* 14-row ordering is pinned, which is the strongest
# equivalence signal available and the one a subtly-different distance operator breaks first.
QUERY_CASES: tuple[BaselineCase, ...] = (
    BaselineCase(
        name="single-clear-match",
        query="How much is the security deposit for a furnished apartment?",
        top_k=3,
        min_score=0.55,
        intent="Unambiguously one entry, at the production top_k/min_score defaults.",
    ),
    BaselineCase(
        name="paraphrase-matches-answer-text",
        query="can I bring my cat to the apartment?",
        top_k=3,
        min_score=0.0,
        intent=(
            "'cat' appears in the pet entry's *answer* and tags, never in its question — "
            "pins the claim that `FaqEntry.document` embeds question+answer+tags. Floor "
            "lowered to 0.0 deliberately; see `paraphrase-below-production-floor`."
        ),
    ),
    BaselineCase(
        name="paraphrase-below-production-floor",
        query="can I bring my cat to the apartment?",
        top_k=3,
        min_score=0.55,
        intent=(
            "The same query at the shipped RAG_MIN_SCORE default returns nothing: faq-004 "
            "scores ~0.49. Recorded as an observed fact, not an endorsement — this is the "
            "very example `FaqEntry.document`'s docstring cites, so it is a retrieval-"
            "tuning finding for the Phase 7 eval work, independent of the migration."
        ),
    ),
    BaselineCase(
        name="ambiguous-between-two-entries",
        query="What documents do I need?",
        top_k=3,
        min_score=0.5,
        intent=(
            "Genuinely ambiguous between the rent (faq-001) and buy (faq-002) document "
            "lists, which score ~0.62 and ~0.52. Their relative order is the fragile part "
            "and is asserted exactly; the floor is 0.5 so both are actually returned."
        ),
    ),
    BaselineCase(
        name="tag-only-vocabulary",
        query="kyc",
        top_k=3,
        min_score=-1.0,
        intent=(
            "'kyc' occurs only in faq-001's tags. Pins the tags' contribution to the "
            "embedded document — weak (~0.12) but ordered correctly."
        ),
    ),
    BaselineCase(
        name="no-match-above-floor",
        query="What is the weather forecast for Jakarta tomorrow?",
        top_k=3,
        min_score=0.55,
        intent=(
            "Off-topic. Must be an empty list — a *successful* 'nothing matched "
            "confidently', which is what the search_faq contract distinguishes from an error."
        ),
    ),
    BaselineCase(
        name="empty-query",
        query="",
        top_k=3,
        min_score=0.55,
        intent="Short-circuits before the provider is called at all.",
    ),
    BaselineCase(
        name="whitespace-only-query",
        query="   \t\n  ",
        top_k=3,
        min_score=0.55,
        intent="Same short-circuit — `.strip()`, not a truthiness check.",
    ),
    BaselineCase(
        name="top-k-truncation",
        query="rental fees and commission",
        top_k=1,
        min_score=0.0,
        intent="Many entries clear a 0.0 floor; exactly one may be returned.",
    ),
    BaselineCase(
        name="full-ranking-deposit",
        query="security deposit refund after moving out",
        top_k=14,
        min_score=-1.0,
        intent="Complete corpus ordering, floor disabled.",
    ),
    BaselineCase(
        name="full-ranking-lease-termination",
        query="I want to end my lease early",
        top_k=14,
        min_score=-1.0,
        intent="Complete corpus ordering on a second, unrelated topic.",
    ),
)


def corpus_fingerprint(entries) -> str:
    """Content hash over what actually gets embedded, not over the file's bytes.

    Byte hashing would false-alarm on a CRLF checkout; this only moves when an id or an
    embedded document really changes — which is exactly when the baseline goes stale.
    """
    payload = json.dumps(
        [[entry.id, entry.document] for entry in entries], ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def baseline_case(baseline: dict, name: str) -> dict:
    return next(case for case in baseline["cases"] if case["name"] == name)


def compare_to_baseline(
    expected: list[dict], actual: list[tuple[str, float]], *, tolerance: float = SCORE_TOLERANCE
) -> list[str]:
    """Differences between a recorded case and an implementation's results, as messages.

    ``actual`` is ``(faq_id, score)`` pairs in rank order — deliberately not ``FaqHit``, so
    a post-B33 result type with a different shape needs no change here. Empty list == match.
    """
    problems = []
    expected_ids = [hit["id"] for hit in expected]
    actual_ids = [faq_id for faq_id, _ in actual]
    if expected_ids != actual_ids:
        problems.append(f"rank order changed: expected {expected_ids}, got {actual_ids}")
        return problems

    for rank, (hit, (_, score)) in enumerate(zip(expected, actual, strict=True)):
        drift = abs(hit["score"] - score)
        if drift > tolerance:
            problems.append(
                f"rank {rank} ({hit['id']}): score {score!r} differs from baseline "
                f"{hit['score']!r} by {drift:.3e} (tolerance {tolerance:.1e})"
            )
    return problems


def _capture() -> dict:  # pragma: no cover - provenance tool, not part of the suite
    import asyncio

    from app.core.config import Settings
    from app.rag.embeddings import build_embedding_model
    from app.rag.index import build_faq_index, load_faq_entries

    from .conftest import SEED_DATA_DIR

    settings = Settings(
        _env_file=None,
        jwt_secret_key="capture-only-secret-at-least-32-characters",
        llm_provider="openai",
        cors_allowed_origins="http://localhost:5173",
    )
    embedding_model = build_embedding_model(settings)
    index = build_faq_index(embedding_model, SEED_DATA_DIR)
    entries = load_faq_entries(SEED_DATA_DIR)

    async def run(case: BaselineCase):
        hits = await index.search(case.query, top_k=case.top_k, min_score=case.min_score)
        return [{"id": hit.entry.id, "score": hit.score} for hit in hits]

    cases = [
        {
            "name": case.name,
            "query": case.query,
            "top_k": case.top_k,
            "min_score": case.min_score,
            "intent": case.intent,
            "expected": asyncio.run(run(case)),
        }
        for case in QUERY_CASES
    ]

    return {
        "schema_version": 1,
        "task": "Q9 (Documentation/audits/2026-08-06-pgvector-migration-contract.md)",
        "captured_on": "2026-08-06",
        "reference_implementation": (
            "app/rag/index.py :: FaqIndex — in-process NumPy flat index, L2-normalized "
            "dot-product cosine, as shipped by B14 and deleted by B33"
        ),
        "embedding": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
        },
        "corpus": {
            "path": "backend/seed_data/faq.json",
            "entry_count": len(entries),
            "fingerprint_sha256": corpus_fingerprint(entries),
            "ids": [entry.id for entry in entries],
        },
        "score_tolerance": SCORE_TOLERANCE,
        "cases": cases,
    }


if __name__ == "__main__":  # pragma: no cover
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(_capture(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {BASELINE_PATH}")
