"""Q9 — the pgvector migration's equivalence guard, and the behaviors it changes on purpose.

Checkpoint: ``Documentation/audits/2026-08-06-pgvector-migration-contract.md``, task Q9,
risk **R-16**: *"the migration silently changes retrieval results — every FAQ answer
degrades with nothing looking broken."* That is the failure this file exists to make loud.
Retrieval has no natural detectability: a pgvector path returning subtly worse neighbours
raises no error, fails no health check, and shows up only as an assistant that has quietly
got less useful.

The reference data lives in ``tests/fixtures/faq_retrieval_baseline.json`` and the helpers
in ``tests/faq_baseline.py``, neither of which imports anything B33 deletes. This file has
two halves:

* **Today's half** — verifies the shipped NumPy index still reproduces the baseline, and
  pins the numerical behaviors the migration inherits or deliberately reverses.
* **B33's half** — the equivalence assertions themselves, skipped until the pgvector path
  exists. They are written out rather than deferred to a ticket because the whole point of
  capturing a baseline early is that nobody has to reconstruct the intent later.
"""

import numpy as np
import pytest

from app.rag.index import build_faq_index, load_faq_entries

from .conftest import SEED_DATA_DIR
from .faq_baseline import (
    QUERY_CASES,
    SCORE_TOLERANCE,
    baseline_case,
    compare_to_baseline,
    corpus_fingerprint,
    load_baseline,
)
from .test_agent import (  # noqa: F401  -- fixtures, consumed by pytest injection
    StubEmbeddingModel,
    anyio_backend,
)

# Every skip below is gated on this one string so `pytest -rs | grep B33` lists exactly
# what the migration still owes.
B33 = "B33: activate once the pgvector retriever exists — see this file's module docstring"


@pytest.fixture(scope="module")
def baseline() -> dict:
    return load_baseline()


@pytest.fixture(scope="module")
def numpy_index(baseline):
    """The shipped NumPy index over the seeded corpus and the real configured embedding model.

    Module-scoped because building it embeds 14 documents through sentence-transformers;
    per-test would pay that repeatedly for no added coverage.

    Skipped, not failed, when the Hugging Face cache is cold and unreachable — the same
    call this suite already makes in ``test_rag.py``'s zero-key test. An environment
    condition is not a defect, but note that a skip here means **R-16 is unguarded on this
    run**, which is why the fixture-only properties below are written to run regardless.
    """
    from app.core.config import Settings
    from app.rag.embeddings import build_embedding_model

    settings = Settings(
        _env_file=None,
        jwt_secret_key="test-secret-at-least-32-characters-long",
        llm_provider="openai",
        cors_allowed_origins="http://localhost:5173",
    )
    if settings.embedding_model != baseline["embedding"]["model"]:
        pytest.fail(
            f"baseline was captured against {baseline['embedding']['model']!r} but the "
            f"configured default is now {settings.embedding_model!r}; the reference scores "
            "do not describe this model and must be re-captured deliberately"
        )

    try:
        embedding_model = build_embedding_model(settings)
    except OSError as exc:  # cold cache with no network
        pytest.skip(f"sentence-transformers weights unavailable offline: {exc}")

    return build_faq_index(embedding_model, SEED_DATA_DIR)


# ------------------------------------------------- the baseline still describes this system


def test_the_corpus_has_not_moved_under_the_baseline(baseline):
    """A `faq.json` edit invalidates every recorded score, and would otherwise surface as a
    confusing rank mismatch in the equivalence tests — blaming the migration for a corpus
    change. Fingerprinting the *embedded documents* rather than the file's bytes keeps this
    from false-alarming on a CRLF checkout."""
    entries = load_faq_entries(SEED_DATA_DIR)

    assert [entry.id for entry in entries] == baseline["corpus"]["ids"]
    assert corpus_fingerprint(entries) == baseline["corpus"]["fingerprint_sha256"], (
        "seed_data/faq.json changed since the Q9 baseline was captured. If that was "
        "intentional, re-capture with `uv run python -m tests.faq_baseline` — but only "
        "*before* B33 merges, since afterwards there is no NumPy reference left to capture."
    )


def test_every_declared_case_was_actually_captured(baseline):
    """Guards the quiet failure where a case is added to `QUERY_CASES` but the fixture is
    never regenerated, so the new case silently tests nothing."""
    assert [case["name"] for case in baseline["cases"]] == [case.name for case in QUERY_CASES]


@pytest.mark.anyio
@pytest.mark.parametrize("case", QUERY_CASES, ids=lambda case: case.name)
async def test_the_numpy_index_reproduces_its_own_baseline(case, baseline, numpy_index):
    """The capture is only a valid reference if it is reproducible. A failure here before
    B33 means the *current* system is non-deterministic — which would invalidate the whole
    approach, since there would be nothing stable for pgvector to match."""
    hits = await numpy_index.search(case.query, top_k=case.top_k, min_score=case.min_score)

    problems = compare_to_baseline(
        baseline_case(baseline, case.name)["expected"],
        [(hit.entry.id, hit.score) for hit in hits],
    )

    assert not problems, "\n".join(problems)


# ------------------------------------------------------- properties the migration relies on


@pytest.mark.parametrize("case_name", ["full-ranking-deposit", "full-ranking-lease-termination"])
def test_the_score_floor_is_a_suffix_predicate(case_name, baseline):
    """The checkpoint's claim that moving the floor into the SQL `WHERE` before `LIMIT`
    returns the same set as today's take-top-k-then-filter. It holds because rows are
    ordered by descending score, so the floor can only ever cut a suffix — but the metric
    definition depends on it, so it is verified from the frozen ranking rather than
    reasoned about. Runs with no embedding provider.
    """
    ranked = baseline_case(baseline, case_name)["expected"]

    for floor in (-1.0, 0.0, 0.05, 0.2, 0.55, 0.9):
        for k in range(1, len(ranked) + 1):
            take_k_then_filter = [hit for hit in ranked[:k] if hit["score"] >= floor]
            filter_then_take_k = [hit for hit in ranked if hit["score"] >= floor][:k]
            assert take_k_then_filter == filter_then_take_k, f"floor={floor} k={k}"


def test_no_two_baseline_scores_tie_within_tolerance(baseline):
    """Rank order is asserted exactly, so a tie would make the equivalence test a coin flip:
    `np.argsort` is stable and breaks ties by corpus order, while SQL `ORDER BY` guarantees
    nothing without a tiebreaker column. No tie exists today — this fails the moment the
    corpus grows one that does, which is when B33 would need an explicit tiebreaker.
    """
    for case in baseline["cases"]:
        scores = [hit["score"] for hit in case["expected"]]
        gaps = [abs(a - b) for a, b in zip(scores, scores[1:], strict=False)]

        assert all(gap > SCORE_TOLERANCE for gap in gaps), (
            f"{case['name']}: adjacent scores within float tolerance of each other, so "
            f"their order is arbitrary and not safely comparable across implementations"
        )


def test_a_zero_length_row_normalizes_to_zeros_not_nan():
    """Today's numerical contract, stated in `_l2_normalize`'s own docstring. NaN does not
    stay local — every comparison against it is false, so a single unembeddable row would
    silently reorder the entire result set rather than just scoring badly itself.

    B33 reverses the *policy* (reindex rejects zero-norm embeddings outright) but inherits
    the reason, so what today's code does is recorded here before it goes.
    """
    from app.rag.index import _l2_normalize

    normalized = _l2_normalize(np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 4.0]], dtype=np.float32))

    assert not np.isnan(normalized).any()
    assert np.array_equal(normalized[0], np.zeros(3, dtype=np.float32))
    np.testing.assert_allclose(normalized[1], [0.6, 0.0, 0.8], atol=1e-6)
    assert float(normalized[0] @ normalized[1]) == 0.0


@pytest.mark.anyio
async def test_a_missing_corpus_today_is_an_empty_result_and_a_warning_not_a_raise(
    tmp_path, caplog
):
    """**The behavior B33 deliberately inverts.** Today a missing corpus degrades quietly:
    `load_faq_entries` logs and returns `[]`, and every search answers "nothing matched
    confidently" — indistinguishable, to the model, from a genuine no-match.

    That is precisely why the checkpoint requires the pgvector path to *raise* on an empty
    index instead: an unpopulated index would otherwise make every FAQ question in the
    system answer "I don't have a confirmed answer" with nothing appearing broken. Recorded
    here so the change is verifiable rather than assumed.
    """
    with caplog.at_level("WARNING", logger="app.rag.index"):
        index = build_faq_index(StubEmbeddingModel(), tmp_path)

    assert index.size == 0
    assert "faq_corpus_missing" in caplog.text
    assert await index.search("deposit", top_k=3, min_score=0.55) == []
    assert not index.is_built


# --------------------------------------------------------------- B33: the equivalence gate


@pytest.mark.skip(reason=B33)
@pytest.mark.anyio
@pytest.mark.parametrize("case", QUERY_CASES, ids=lambda case: case.name)
async def test_the_pgvector_retriever_matches_the_numpy_baseline(case, baseline):
    """**This is Q9's actual deliverable and the only guard against R-16.**

    Wiring B33 must supply: build the pgvector-backed retriever against a Postgres database
    reindexed from `seed_data/faq.json` with `EMBEDDING_PROVIDER=local`, then::

        hits = await retriever.search(case.query, top_k=case.top_k, min_score=case.min_score)
        problems = compare_to_baseline(
            baseline_case(baseline, case.name)["expected"],
            [(hit.entry.id, hit.score) for hit in hits],
        )
        assert not problems, "\\n".join(problems)

    Two things not to do when this turns red. Do not widen `SCORE_TOLERANCE` to make a
    *rank* difference pass — the id order is compared before scores precisely so the two
    failures stay distinguishable. And do not re-capture the baseline against the pgvector
    path: that asserts the new implementation equals itself.

    Relaxing the tolerance for a genuine float-width difference (`<=>` returns cosine
    *distance*, so scores arrive as `1 - distance` and carry one more rounding step than
    the NumPy dot product) is legitimate — but record the number and the reason.
    """


@pytest.mark.skip(reason=B33)
def test_the_reindex_command_rejects_a_zero_norm_embedding():
    """B33 inverts `test_a_zero_length_row_normalizes_to_zeros_not_nan` above: a zero-norm
    embedding must be rejected *at index time* rather than stored and scored as 0.0.

    The reason it can no longer be tolerated: with `<=>`, cosine distance against a
    zero vector is undefined, and a stored zero row would surface as NaN or as an
    arbitrary ordering rather than the harmless last-place finish it gets today.
    """


@pytest.mark.skip(reason=B33)
def test_an_empty_index_raises_rather_than_returning_no_results():
    """The counterpart to
    `test_a_missing_corpus_today_is_an_empty_result_and_a_warning_not_a_raise`.

    An empty `faq_embeddings` for the configured `embedding_model` must raise. `[]` is a
    *successful* "nothing matched confidently", so returning it for an unpopulated index
    turns a total outage into a system that looks like it is working and simply never
    knows anything.
    """


@pytest.mark.skip(reason=B33)
def test_a_corpus_edit_without_a_reindex_is_caught_by_the_identity_canary():
    """Risk R-17: `faq.json` edited, or `EMBEDDING_PROVIDER` switched, with no reindex —
    leaving an index that is stale rather than absent, which the empty-index guard above
    cannot see. `test_the_corpus_has_not_moved_under_the_baseline` catches this for the
    seeded corpus; the canary is what catches it in a running deployment.
    """


@pytest.mark.skip(reason=B33)
def test_force_re_embeds_and_a_plain_reindex_does_not():
    """Idempotence by `content_hash`: a second plain `python -m app.rag.reindex` must issue
    no embedding calls at all, while `--force` must re-embed every row. Without the first
    half, `RAG_INDEX_ON_STARTUP` re-embeds the whole corpus on every boot of every worker —
    the exact per-process cost pgvector is being adopted to eliminate.
    """
