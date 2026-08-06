"""``app/rag/`` — the embedding factory and the in-process FAQ index.

The load-bearing claim here is the one the README states twice and the Phase 3 checkpoint
re-asserts (Design Decisions §6, decision #4): **`EMBEDDING_PROVIDER` is independent of
`LLM_PROVIDER`**. It is not a convenience. Anthropic has no embeddings API, so if the two
were coupled — even accidentally, by one builder reading the wrong key — an
``LLM_PROVIDER=anthropic`` deployment would have no working FAQ path at all, and the
failure would appear as "search_faq is broken" rather than as a config coupling.

The second claim, from the ``search_faq`` contract: nothing clearing ``RAG_MIN_SCORE``
returns an **empty result**, not an error. Those are different outcomes and the model must
be able to tell them apart — "we have no confirmed answer" leads to an escalation, while
an error leads to an apology and, historically, to the model answering from memory anyway.
"""

import json

import pytest

from app.core.config import Settings
from app.rag.embeddings import (
    _EMBEDDING_BUILDERS,
    EmbeddingConfigurationError,
    build_embedding_model,
)
from app.rag.index import FaqEntry, FaqIndex, load_faq_entries

from .conftest import SEED_DATA_DIR, make_settings
from .test_agent import (  # noqa: F401  -- fixtures, consumed by pytest injection
    CLIENT,
    StubEmbeddingModel,
    anyio_backend,
    db,
    engine,
    faq_index,
    make_deps,
    notifier,
    run_tool,
)

SEEDED_FAQ_ENTRIES = 14


# ------------------------------------------- EMBEDDING_PROVIDER is not LLM_PROVIDER


def test_the_embedding_builder_table_matches_the_settings_literal():
    literal_values = set(Settings.model_fields["embedding_provider"].annotation.__args__)

    assert set(_EMBEDDING_BUILDERS) == literal_values
    # `anthropic` must not appear: it has no embeddings API, and offering it would be a
    # configuration that cannot work.
    assert "anthropic" not in _EMBEDDING_BUILDERS


@pytest.mark.parametrize("llm_provider", ["openai", "anthropic", "gemini"])
def test_embedding_provider_defaults_to_local_whatever_the_llm_provider_is(llm_provider):
    """`local` is the default specifically so the FAQ path works with no API key. If the
    default ever tracked `LLM_PROVIDER`, an anthropic deployment would default to a
    provider that does not exist."""
    settings = make_settings(llm_provider=llm_provider)

    assert settings.embedding_provider == "local"
    assert settings.embedding_model == settings.local_embedding_model


def test_the_embedding_model_name_follows_embedding_provider_never_llm_provider():
    """`openai_model` (chat) and `openai_embedding_model` are different fields; reading
    the former here would send a chat model name to an embeddings endpoint."""
    settings = make_settings(
        llm_provider="anthropic",
        anthropic_api_key="anthropic-key",
        embedding_provider="openai",
        openai_api_key="openai-key",
        openai_model="gpt-4o-mini",
        openai_embedding_model="text-embedding-3-small",
    )

    assert settings.embedding_model == "text-embedding-3-small"


def test_a_hosted_embedding_provider_needs_its_own_key_not_the_llm_providers():
    """The precise coupling bug this guards: an anthropic-configured deployment with a
    valid ANTHROPIC_API_KEY and `EMBEDDING_PROVIDER=openai` must still fail on the
    *OpenAI* key, naming it."""
    settings = make_settings(
        llm_provider="anthropic",
        anthropic_api_key="a-real-anthropic-key",
        embedding_provider="openai",
        openai_api_key="",
    )

    with pytest.raises(EmbeddingConfigurationError) as excinfo:
        build_embedding_model(settings)

    assert "OPENAI_API_KEY" in str(excinfo.value)
    # The remediation, since `local` is the escape hatch that needs no credential at all.
    assert "EMBEDDING_PROVIDER=local" in str(excinfo.value)


def test_gemini_embeddings_read_the_gemini_key():
    settings = make_settings(
        llm_provider="openai",
        openai_api_key="an-openai-key",
        embedding_provider="gemini",
        gemini_api_key="",
    )

    with pytest.raises(EmbeddingConfigurationError) as excinfo:
        build_embedding_model(settings)

    assert "GEMINI_API_KEY" in str(excinfo.value)


def test_anthropic_llm_plus_local_embeddings_needs_no_api_key_at_all():
    """Re-verifies the Phase 3 checkpoint's decision #4 claim directly rather than taking
    it on report. Every API key is empty; the model still builds.

    Slow (~6s) because it really loads sentence-transformers — substituting a stub would
    verify the dispatch and skip the part that could actually be broken. Skipped rather
    than failed when the Hugging Face cache is cold and unreachable, since that is an
    environment condition, not a defect.
    """
    settings = make_settings(
        llm_provider="anthropic",
        anthropic_api_key="",
        openai_api_key="",
        gemini_api_key="",
        embedding_provider="local",
    )

    try:
        model = build_embedding_model(settings)
    except OSError as exc:  # cold cache with no network
        pytest.skip(f"sentence-transformers weights unavailable offline: {exc}")

    assert model.model_name == settings.local_embedding_model


# ------------------------------------------------------------------------- the index
#
# `FaqIndex` is now a thin, per-request, pgvector-backed object (B32/B33) — it holds no
# corpus and does no in-process "build", so the properties the old NumPy index's own
# mechanics used to earn here (lazy build, a lock against concurrent first-builds, an
# empty corpus answering cleanly, a zero-length row scoring 0.0 rather than NaN) no
# longer describe anything that ships. Their replacements:
#
# * Score-floor/ranking correctness against the real thing: the activated Q9 equivalence
#   tests in `tests/test_rag_baseline.py`, run against a real Postgres.
# * A zero-norm embedding: rejected at *index* time now, not scored at *query* time —
#   `tests/test_rag_baseline.py::test_the_reindex_command_rejects_a_zero_norm_embedding`.
# * An empty corpus: inverted on purpose — an unpopulated `faq_embeddings` for the
#   configured embedding model now *raises* (`EmptyFaqIndexError`), not searches cleanly
#   — `tests/test_rag_baseline.py::test_an_empty_index_raises_rather_than_returning_no_results`.
#
# What is still worth pinning here, with no database at all: the blank-query short
# circuit, which must never touch the embedding provider *or* the database.


@pytest.mark.anyio
async def test_a_blank_query_returns_nothing_without_calling_the_provider_or_the_database():
    class ExplodingEmbeddingModel:
        model_name = "should-not-be-used"

        async def embed(self, *args, **kwargs):
            raise AssertionError("embed() must not be called for a blank query")

    class ExplodingSession:
        def execute(self, *args, **kwargs):
            raise AssertionError("the database must not be queried for a blank query")

    index = FaqIndex(ExplodingEmbeddingModel(), ExplodingSession())

    assert await index.search("   ", top_k=3, min_score=0.1) == []


def test_a_missing_corpus_file_yields_no_entries_not_a_crash(tmp_path):
    """The FAQ corpus file going missing must degrade the FAQ answer, not take down the
    agent — `search_faq` is one of five tools and the other four are unaffected."""
    assert load_faq_entries(tmp_path) == []


def test_the_seeded_corpus_loads_with_the_fields_the_index_embeds():
    entries = load_faq_entries(SEED_DATA_DIR)

    assert len(entries) == SEEDED_FAQ_ENTRIES
    assert all(entry.id and entry.question and entry.answer and entry.category for entry in entries)


def test_the_embedded_document_carries_question_answer_and_tags():
    """Embedding the question alone is the obvious implementation and the wrong one: "can
    I bring my cat?" matches the pet policy's *answer* text, and vocabulary like "kyc"
    lives only in the tags."""
    entry = FaqEntry("f1", "Can I keep a pet?", "Pets need written consent.", "policy", ("kyc",))

    document = entry.document

    assert "Can I keep a pet?" in document
    assert "Pets need written consent." in document
    assert "kyc" in document


def test_an_entry_without_tags_still_produces_a_document():
    entry = FaqEntry("f1", "Q", "A", "policy")

    assert entry.document == "Q\nA"


# -------------------------------------------------------------- search_faq tool wiring


@pytest.mark.anyio
async def test_an_unreachable_embedding_provider_is_an_error_not_an_empty_result(
    db, notifier, faq_index, monkeypatch
):
    """The distinction the tool exists to preserve. "Nothing matched" and "the knowledge
    base is unreachable" must not look the same to the model: the first is an answer, the
    second must stop it answering from memory."""

    async def unreachable(*args, **kwargs):
        raise ConnectionError("embedding provider unreachable")

    monkeypatch.setattr(faq_index, "search", unreachable)

    run = await run_tool(
        [[("search_faq", {"query": "deposit"})], "sorry"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    error = run.error()

    assert error["code"] == "service_unavailable"
    assert "Do not answer the policy question from memory." in error["guidance"]
    assert "connect the user with a human agent" in error["guidance"]
    # The underlying exception is logged, never relayed — a stack trace in a chat reply is
    # useless to the user and an information leak.
    rendered = json.dumps(error)
    assert "ConnectionError" not in rendered
    assert "embedding provider unreachable" not in rendered


@pytest.mark.anyio
async def test_search_faq_needs_no_role_and_scopes_nothing(db, notifier, faq_index):
    """Public knowledge, per the tool RBAC table: identical results for every role. A
    difference here would mean the FAQ had acquired scoping nobody designed."""
    per_role = {}
    for user_id in ("u-admin-1", "u-agent-1", "u-client-1"):
        run = await run_tool(
            [[("search_faq", {"query": "deposit"})], "answered"],
            make_deps(db, notifier, faq_index, user_id=user_id, rag_min_score=0.5),
        )
        per_role[user_id] = run.payload()["results"]

    assert per_role["u-admin-1"] == per_role["u-agent-1"] == per_role["u-client-1"]
    assert [hit["id"] for hit in per_role["u-client-1"]] == ["faq-deposit"]
