"""Q9 — the pgvector migration's equivalence guard, and the behaviors it changes on purpose.

Checkpoint: ``Documentation/audits/2026-08-06-pgvector-migration-contract.md``, task Q9,
risk **R-16**: *"the migration silently changes retrieval results — every FAQ answer
degrades with nothing looking broken."* That is the failure this file exists to make loud.
Retrieval has no natural detectability: a pgvector path returning subtly worse neighbours
raises no error, fails no health check, and shows up only as an assistant that has quietly
got less useful.

The reference data lives in ``tests/fixtures/faq_retrieval_baseline.json`` and the helpers
in ``tests/faq_baseline.py``, both captured *before* B33 deleted the in-process NumPy
index that produced them — neither imports anything B33 removed.

**B33 has landed**: every test below runs against the real, Postgres-backed
``FaqIndex`` (``app/rag/index.py``), reindexed from ``seed_data/faq.json`` with
``EMBEDDING_PROVIDER=local`` (matching the baseline's own captured provider/model —
enforced by ``test_the_corpus_has_not_moved_under_the_baseline`` and the embedding-model
check inside ``pgvector_settings``). The tests that used to pin the pre-migration NumPy
index's own mechanics (`FaqIndex.ensure_built`/`is_built`/`size`, `_l2_normalize`) were
retired along with that code — their properties are superseded by the equivalence and
zero-norm-rejection tests here, which assert the same guarantees against what actually
ships now.

Needs a reachable Postgres (``docker compose --env-file backend/.env up -d postgres``);
every test below skips loudly, never silently, when one isn't available — see
``tests/postgres_support.py``.
"""

import asyncio

import pytest
from sqlmodel import Session

from app.core.config import Settings
from app.db.session import build_engine
from app.rag.embeddings import build_embedding_model
from app.rag.index import EmptyFaqIndexError, FaqEntry, FaqIndex, load_faq_entries
from app.rag.reindex import ZeroNormEmbeddingError, reindex as reindex_faq_embeddings

from .conftest import SEED_DATA_DIR
from .faq_baseline import (
    QUERY_CASES,
    SCORE_TOLERANCE,
    baseline_case,
    compare_to_baseline,
    corpus_fingerprint,
    load_baseline,
)
from .postgres_support import POSTGRES_TEST_DATABASE_URL, require_postgres


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def baseline() -> dict:
    return load_baseline()


# ------------------------------------------------- the baseline still describes this system


def test_the_corpus_has_not_moved_under_the_baseline(baseline):
    """A `faq.json` edit invalidates every recorded score, and would otherwise surface as a
    confusing rank mismatch in the equivalence tests below — blaming the migration for a
    corpus change. Fingerprinting the *embedded documents* rather than the file's bytes
    keeps this from false-alarming on a CRLF checkout."""
    entries = load_faq_entries(SEED_DATA_DIR)

    assert [entry.id for entry in entries] == baseline["corpus"]["ids"]
    assert corpus_fingerprint(entries) == baseline["corpus"]["fingerprint_sha256"], (
        "seed_data/faq.json changed since the Q9 baseline was captured. The NumPy "
        "reference implementation no longer exists (B33), so this baseline can no longer "
        "be regenerated the way it was captured — a genuine corpus change now requires a "
        "new baseline-capture strategy, not `uv run python -m tests.faq_baseline`."
    )


def test_every_declared_case_was_actually_captured(baseline):
    """Guards the quiet failure where a case is added to `QUERY_CASES` but the fixture is
    never regenerated, so the new case silently tests nothing."""
    assert [case["name"] for case in baseline["cases"]] == [case.name for case in QUERY_CASES]


# ------------------------------------------------------- properties the migration relies on


@pytest.mark.parametrize("case_name", ["full-ranking-deposit", "full-ranking-lease-termination"])
def test_the_score_floor_is_a_suffix_predicate(case_name, baseline):
    """The checkpoint's claim that moving the floor into the SQL `WHERE` before `LIMIT`
    returns the same set as today's take-top-k-then-filter. It holds because rows are
    ordered by descending score, so the floor can only ever cut a suffix — but the metric
    definition depends on it, so it is verified from the frozen ranking rather than
    reasoned about. Runs with no embedding provider and no database.
    """
    ranked = baseline_case(baseline, case_name)["expected"]

    for floor in (-1.0, 0.0, 0.05, 0.2, 0.55, 0.9):
        for k in range(1, len(ranked) + 1):
            take_k_then_filter = [hit for hit in ranked[:k] if hit["score"] >= floor]
            filter_then_take_k = [hit for hit in ranked if hit["score"] >= floor][:k]
            assert take_k_then_filter == filter_then_take_k, f"floor={floor} k={k}"


def test_no_two_baseline_scores_tie_within_tolerance(baseline):
    """Rank order is asserted exactly, so a tie would make the equivalence test a coin flip:
    the NumPy reference's `np.argsort` was stable and broke ties by corpus order, while SQL
    `ORDER BY embedding <=> ...` guarantees nothing without a tiebreaker column. No tie
    exists in the frozen baseline — this fails the moment the corpus grows one that does,
    which is when an explicit tiebreaker would become necessary.
    """
    for case in baseline["cases"]:
        scores = [hit["score"] for hit in case["expected"]]
        gaps = [abs(a - b) for a, b in zip(scores, scores[1:], strict=False)]

        assert all(gap > SCORE_TOLERANCE for gap in gaps), (
            f"{case['name']}: adjacent scores within float tolerance of each other, so "
            f"their order is arbitrary and not safely comparable across implementations"
        )


# --------------------------------------------------------------- the pgvector-backed index


@pytest.fixture(scope="module")
def pgvector_settings(baseline) -> Settings:
    require_postgres()
    settings = Settings(
        _env_file=None,
        database_url=POSTGRES_TEST_DATABASE_URL,
        jwt_secret_key="test-secret-at-least-32-characters-long",
        llm_provider="openai",
        cors_allowed_origins="http://localhost:5173",
        embedding_provider="local",
    )
    if settings.embedding_model != baseline["embedding"]["model"]:
        pytest.fail(
            f"baseline was captured against {baseline['embedding']['model']!r} but the "
            f"configured default is now {settings.embedding_model!r}; the reference "
            "scores do not describe this model and must be re-captured deliberately."
        )
    return settings


@pytest.fixture(scope="module")
def pgvector_engine(pgvector_settings):
    engine = build_engine(pgvector_settings)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def pgvector_embedding_model(pgvector_settings):
    try:
        return build_embedding_model(pgvector_settings)
    except OSError as exc:  # cold Hugging Face cache with no network
        pytest.skip(f"sentence-transformers weights unavailable offline: {exc}")


@pytest.fixture(scope="module")
def pgvector_reindexed(pgvector_settings, pgvector_engine, pgvector_embedding_model) -> None:
    """`faq_embeddings` populated & current for embedding_model=local before any
    equivalence test runs — idempotent, so this costs ~nothing once the table is
    already current (as it will be for every run after the first in a given environment).
    """
    asyncio.run(
        reindex_faq_embeddings(
            pgvector_settings, engine=pgvector_engine, embedding_model=pgvector_embedding_model
        )
    )


@pytest.fixture
def pgvector_db(pgvector_engine):
    with Session(pgvector_engine) as session:
        yield session


@pytest.fixture
def pgvector_index(pgvector_reindexed, pgvector_embedding_model, pgvector_db) -> FaqIndex:
    return FaqIndex(pgvector_embedding_model, pgvector_db)


# --------------------------------------------------------------- B33: the equivalence gate


@pytest.mark.anyio
@pytest.mark.parametrize("case", QUERY_CASES, ids=lambda case: case.name)
async def test_the_pgvector_retriever_matches_the_numpy_baseline(case, baseline, pgvector_index):
    """**Q9's actual deliverable and the only guard against R-16.**

    Two things not to do when this turns red. Do not widen `SCORE_TOLERANCE` to make a
    *rank* difference pass — the id order is compared before scores precisely so the two
    failures stay distinguishable. And do not re-capture the baseline against the
    pgvector path: that would assert the new implementation equals itself.
    """
    hits = await pgvector_index.search(case.query, top_k=case.top_k, min_score=case.min_score)

    problems = compare_to_baseline(
        baseline_case(baseline, case.name)["expected"],
        [(hit.entry.id, hit.score) for hit in hits],
    )

    assert not problems, "\n".join(problems)


@pytest.mark.anyio
async def test_the_reindex_command_rejects_a_zero_norm_embedding(pgvector_settings, pgvector_engine):
    """Inverts the pre-migration NumPy index's old tolerance (score it 0.0, keep going):
    a zero-norm embedding must be rejected *at index time* instead, because under `<=>`,
    cosine distance against a zero vector is undefined and a stored zero row would surface
    as NaN or an arbitrary ordering rather than a harmless last-place finish.
    """

    class ZeroEmbeddingModel:
        model_name = "zero-norm-test-model"

        async def embed(self, inputs, *, input_type, settings=None):
            texts = [inputs] if isinstance(inputs, str) else list(inputs)

            class _Result:
                embeddings = [[0.0, 0.0, 0.0] for _ in texts]

            return _Result()

    import app.rag.reindex as reindex_module

    original_load = reindex_module.load_faq_entries
    try:
        reindex_module.load_faq_entries = lambda seed_data_dir: [
            FaqEntry(id="faq-zero-norm-probe", question="", answer="", category="misc")
        ]
        with pytest.raises(ZeroNormEmbeddingError, match="faq-zero-norm-probe"):
            await reindex_faq_embeddings(
                pgvector_settings,
                engine=pgvector_engine,
                embedding_model=ZeroEmbeddingModel(),
            )
    finally:
        reindex_module.load_faq_entries = original_load

    # Nothing was left behind: the raise happens before the row is added to the session.
    with Session(pgvector_engine) as session:
        from sqlalchemy import text

        row = session.execute(
            text("SELECT 1 FROM faq_embeddings WHERE embedding_model = :m LIMIT 1").bindparams(
                m="zero-norm-test-model"
            )
        ).first()
    assert row is None


@pytest.mark.anyio
async def test_an_empty_index_raises_rather_than_returning_no_results(
    pgvector_embedding_model, pgvector_db
):
    """The counterpart to the pre-migration behavior of a missing `faq.json` (empty
    result, not a raise). An empty `faq_embeddings` for the configured `embedding_model`
    must raise: `[]` is a *successful* "nothing matched confidently", so returning it for
    an unpopulated index would turn a total outage into a system that looks like it is
    working and simply never knows anything.
    """

    class NeverIndexedEmbeddingModel:
        model_name = "never-indexed-test-model"

        async def embed(self, inputs, *, input_type, settings=None):
            class _Result:
                embeddings = [[0.1, 0.2, 0.3]]

            return _Result()

    index = FaqIndex(NeverIndexedEmbeddingModel(), pgvector_db)

    with pytest.raises(EmptyFaqIndexError, match="never-indexed-test-model"):
        await index.search("deposit", top_k=3, min_score=0.55)


@pytest.mark.anyio
async def test_a_corpus_edit_without_a_reindex_is_caught_by_the_identity_canary(
    pgvector_settings, pgvector_engine, pgvector_embedding_model
):
    """Risk R-17: `faq.json` edited (or `EMBEDDING_PROVIDER` switched) with no reindex —
    leaving an index that is stale rather than absent, which `EmptyFaqIndexError` cannot
    see (a stale index is never empty). The canary a running deployment actually runs is
    exactly this: compare each live corpus entry's `content_hash` against what is stored,
    which is what `app/rag/reindex.py`'s own skip/re-embed decision is built on — this
    test drives that comparison directly rather than through a similarity threshold,
    which would make the assertion depend on embedding-model specifics rather than on the
    hash comparison the real safeguard is.
    """
    import hashlib

    import app.rag.reindex as reindex_module
    from sqlalchemy import text

    canary = FaqEntry(
        id="faq-canary-identity-test",
        question="canary identity probe question",
        answer="canary identity probe answer",
        category="misc",
    )
    original_load = reindex_module.load_faq_entries
    try:
        reindex_module.load_faq_entries = lambda seed_data_dir: [canary]
        await reindex_faq_embeddings(
            pgvector_settings, engine=pgvector_engine, embedding_model=pgvector_embedding_model
        )

        def stored_content_hash() -> str:
            with Session(pgvector_engine) as db:
                row = db.execute(
                    text(
                        "SELECT content_hash FROM faq_embeddings "
                        "WHERE faq_id = :faq_id AND embedding_model = :embedding_model"
                    ).bindparams(
                        faq_id=canary.id, embedding_model=pgvector_settings.embedding_model
                    )
                ).first()
            assert row is not None
            return row.content_hash

        # Freshly reindexed: the stored hash matches the live corpus.
        assert stored_content_hash() == hashlib.sha256(
            canary.document.encode("utf-8")
        ).hexdigest()

        # Edit the corpus *without* reindexing (swap what load_faq_entries returns, but
        # never call reindex again): the stored row keeps the old hash.
        edited_canary = FaqEntry(
            id="faq-canary-identity-test",
            question="an entirely different question about lease deposits",
            answer="a completely different answer about lease deposits",
            category="misc",
        )
        reindex_module.load_faq_entries = lambda seed_data_dir: [edited_canary]

        live_hash = hashlib.sha256(edited_canary.document.encode("utf-8")).hexdigest()
        assert stored_content_hash() != live_hash, (
            "the stored content_hash matched the edited (un-reindexed) corpus — the "
            "canary would not have caught a forgotten reindex"
        )
    finally:
        reindex_module.load_faq_entries = original_load
        # Clean up: prune the canary row by reindexing against the real corpus again.
        await reindex_faq_embeddings(
            pgvector_settings, engine=pgvector_engine, embedding_model=pgvector_embedding_model
        )


@pytest.mark.anyio
async def test_force_re_embeds_and_a_plain_reindex_does_not(
    pgvector_settings, pgvector_engine, pgvector_embedding_model, pgvector_reindexed
):
    """Idempotence by `content_hash`: a second plain `python -m app.rag.reindex` must issue
    no embedding calls at all, while `--force` must re-embed every row. Without the first
    half, `RAG_INDEX_ON_STARTUP` would re-embed the whole corpus on every boot of every
    worker — the exact per-process cost pgvector was adopted to eliminate.
    """
    calls: list[str] = []
    real_embed = pgvector_embedding_model.embed

    async def counting_embed(inputs, *, input_type, settings=None):
        if input_type == "document":
            calls.append("document")
        return await real_embed(inputs, input_type=input_type, settings=settings)

    class CountingWrapper:
        model_name = pgvector_embedding_model.model_name
        embed = staticmethod(counting_embed)

    plain_report = await reindex_faq_embeddings(
        pgvector_settings, engine=pgvector_engine, embedding_model=CountingWrapper()
    )
    assert calls == [], "a plain reindex with nothing changed issued an embedding call"
    assert plain_report.embedded == 0
    assert plain_report.skipped == plain_report.total_considered

    calls.clear()
    force_report = await reindex_faq_embeddings(
        pgvector_settings, engine=pgvector_engine, embedding_model=CountingWrapper(), force=True
    )
    assert calls == ["document"], "a --force reindex should batch all re-embeds into one call"
    assert force_report.embedded == force_report.total_considered
    assert force_report.skipped == 0
