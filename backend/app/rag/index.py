"""pgvector-backed FAQ retrieval over ``faq_embeddings``.

README Design Decisions §6 / ``Documentation/audits/2026-08-06-pgvector-migration-contract.md``
decision 1: FAQ retrieval moved off the in-process NumPy flat index (B14) onto the
Postgres ``faq_embeddings`` table (``app/models/faq_embedding.py``, B32) and pgvector's
``<=>`` cosine-distance operator. Two consequences worth stating up front:

* This module holds **no corpus and no vectors**. ``FaqIndex`` here is a thin, cheap,
  per-request object — an ``EmbeddingModel`` reference plus the caller's own DB session —
  not the expensive, process-lifetime object it used to be. "Built" is a database
  property now (rows exist in ``faq_embeddings`` or they don't), not a process property,
  so the old ``ensure_built()`` / ``is_built`` / ``size`` have no replacement here.
* An **empty index for the configured embedding model is an error, not an empty
  result**. See :class:`EmptyFaqIndexError`'s docstring for why this is the one case
  :meth:`FaqIndex.search` raises for — every other "nothing matched" case still returns
  ``[]``, unchanged from before.

``app/rag/reindex.py`` is the only writer of ``faq_embeddings``; this module is the only
production reader. There is exactly one retrieval code path — no NumPy fallback.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic_ai.embeddings import EmbeddingModel
from sqlalchemy import text
from sqlmodel import Session

logger = logging.getLogger(__name__)

FAQ_FILENAME = "faq.json"
FAQ_EMBEDDINGS_TABLE = "faq_embeddings"


@dataclass(frozen=True)
class FaqEntry:
    id: str
    question: str
    answer: str
    category: str
    tags: tuple[str, ...] = ()

    @property
    def document(self) -> str:
        """What actually gets embedded.

        Question *and* answer, plus tags: a user asking "can I bring my cat?" matches
        the pet-policy answer's wording far better than its question's, and the tags
        carry vocabulary ("kyc", "deposit") that appears in neither.
        """
        parts = [self.question, self.answer]
        if self.tags:
            parts.append(" ".join(self.tags))
        return "\n".join(parts)


@dataclass(frozen=True)
class FaqHit:
    entry: FaqEntry
    score: float


def load_faq_entries(seed_data_dir: str | Path) -> list[FaqEntry]:
    path = Path(seed_data_dir) / FAQ_FILENAME
    if not path.exists():
        # A missing corpus file is a legitimate state for the *reindex command* to
        # shrug off (nothing to index), so loading it must not crash. It is not the
        # same thing as an empty `faq_embeddings` table at query time — that case is
        # `EmptyFaqIndexError`, raised by `FaqIndex.search`, not here.
        logger.warning("faq_corpus_missing", extra={"path": str(path)})
        return []

    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        FaqEntry(
            id=item["id"],
            question=item["question"],
            answer=item["answer"],
            category=item["category"],
            tags=tuple(item.get("tags", ())),
        )
        for item in raw
    ]


class EmptyFaqIndexError(RuntimeError):
    """``faq_embeddings`` has zero rows for the configured embedding model.

    Deliberately **not** the same outcome as "no result cleared RAG_MIN_SCORE" (which
    returns ``[]`` — a successful "nothing matched confidently"). An unpopulated index is
    an infrastructure gap: the knowledge base was never reindexed, or was reindexed under
    a different ``EMBEDDING_PROVIDER``/model than the one currently configured. Returning
    ``[]`` for that would be indistinguishable from a genuine no-match — every FAQ
    question would quietly get "I don't have a confirmed answer" with nothing looking
    broken, which is exactly the failure this class exists to make loud instead.

    ``app/agent/tools/search_faq.py``'s existing ``except Exception`` already maps this to
    the same ``UpstreamToolError`` it uses for an unreachable embedding provider, so no
    caller-side change was needed to wire this in.
    """


class FaqRetriever(Protocol):
    """What ``search_faq`` (and anything else) needs from a FAQ index.

    The seam that lets tests substitute a double for the real, Postgres-backed
    ``FaqIndex`` without touching the tool or ``AgentDeps``. See ``tests/rag_doubles.py``
    for the in-memory double ``create_app(faq_index=...)`` and the ``AgentDeps.rag``
    fixtures inject in its place.
    """

    @property
    def embedding_model_name(self) -> str: ...

    async def search(self, query: str, *, top_k: int, min_score: float) -> list[FaqHit]: ...

    def get(self, faq_id: str) -> FaqEntry | None: ...


def _to_pgvector_literal(values) -> str:
    """``[0.1,-0.2,...]`` — pgvector's text input format for a ``vector`` value.

    Passed as a plain string bind parameter, cast to ``vector`` inside the SQL itself
    (``CAST(:query_vector AS vector)``) rather than relying on ``pgvector.psycopg``'s
    connection-level adapter registration — one fewer global, driver-level side effect to
    reason about, at the cost of nothing: the two are equivalent on the wire.
    """
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


class FaqIndex:
    """The production ``FaqRetriever``: pgvector-backed, cheap, per-request.

    Holds just an ``EmbeddingModel`` reference and the caller's own DB session — nothing
    else. Construct one per request (see ``app/api/chat.py::get_faq_index``); it does no
    caching and has no state worth reusing across requests.
    """

    def __init__(self, embedding_model: EmbeddingModel, db: Session) -> None:
        self._embedding_model = embedding_model
        self._db = db

    @property
    def embedding_model_name(self) -> str:
        return self._embedding_model.model_name

    def get(self, faq_id: str) -> FaqEntry | None:
        """Entry text by id, for callers holding only what a search returned.

        Retrieval records identify a hit but do not carry its answer body; the
        faithfulness check needs that body as the context it judges against. Scoped to
        the current ``embedding_model`` like ``search`` — a row indexed under a
        different provider/model isn't a valid answer for this index's caller.
        """
        row = self._db.execute(
            text(
                f"""
                SELECT faq_id, question, answer, category
                FROM {FAQ_EMBEDDINGS_TABLE}
                WHERE faq_id = :faq_id AND embedding_model = :embedding_model
                LIMIT 1
                """
            ).bindparams(faq_id=faq_id, embedding_model=self.embedding_model_name)
        ).first()
        if row is None:
            return None
        return FaqEntry(id=row.faq_id, question=row.question, answer=row.answer, category=row.category)

    async def search(self, query: str, *, top_k: int, min_score: float) -> list[FaqHit]:
        """Top-``top_k`` entries scoring at least ``min_score``.

        Returns an empty list when nothing clears the floor — a *successful* "nothing
        matched confidently" (README's ``search_faq`` contract, unchanged by this
        rewrite). Raises :class:`EmptyFaqIndexError` when ``faq_embeddings`` has no rows
        at all for the configured embedding model — see that class's docstring for why
        those two cases must stay distinguishable.

        The floor is applied in the SQL ``WHERE`` clause, before ``LIMIT`` — provably
        identical to the old take-top-k-then-filter because rows are ordered by
        descending score, so the floor can only ever cut a suffix (verified against the
        frozen Q9 baseline in ``tests/test_rag_baseline.py``, not just reasoned about).
        """
        if not query.strip():
            return []

        embedding_model = self.embedding_model_name
        result = await self._embedding_model.embed(query, input_type="query")
        query_vector = _to_pgvector_literal(result.embeddings[0])

        exists = self._db.execute(
            text(
                f"SELECT 1 FROM {FAQ_EMBEDDINGS_TABLE} WHERE embedding_model = :embedding_model LIMIT 1"
            ).bindparams(embedding_model=embedding_model)
        ).first()
        if exists is None:
            raise EmptyFaqIndexError(
                f"faq_embeddings has no rows for embedding_model={embedding_model!r}. "
                "Run `python -m app.rag.reindex`, or wait for RAG_INDEX_ON_STARTUP on the "
                "next boot, to populate it."
            )

        rows = self._db.execute(
            text(
                f"""
                SELECT faq_id, question, answer, category,
                       1 - (embedding <=> CAST(:query_vector AS vector)) AS score
                FROM {FAQ_EMBEDDINGS_TABLE}
                WHERE embedding_model = :embedding_model
                  AND 1 - (embedding <=> CAST(:query_vector AS vector)) >= :min_score
                ORDER BY embedding <=> CAST(:query_vector AS vector)
                LIMIT :top_k
                """
            ).bindparams(
                query_vector=query_vector,
                embedding_model=embedding_model,
                min_score=min_score,
                top_k=top_k,
            )
        ).all()

        return [
            FaqHit(
                entry=FaqEntry(
                    id=row.faq_id,
                    question=row.question,
                    answer=row.answer,
                    category=row.category,
                ),
                score=float(row.score),
            )
            for row in rows
        ]
