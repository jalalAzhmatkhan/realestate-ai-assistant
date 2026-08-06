"""A DB-free, in-memory ``FaqRetriever`` double for tests.

Most of ``test_agent.py`` (and the files that borrow its fixtures) needs *some* ``rag``
dependency to build ``AgentDeps``, but is not testing retrieval itself — those tests, and
the app-level ``create_app(faq_index=...)`` injection point (``test_chat_api.py``,
``test_retrieval_logging.py``), want a fast, deterministic, database-free double.

This is **not** a second production retrieval implementation: ``app/rag/index.py``'s
pgvector-backed ``FaqIndex`` is the only retrieval code path that ships in ``app/``. This
class lives entirely on the test side of the ``FaqRetriever`` Protocol boundary, and its
constructor deliberately mirrors the pre-B33 ``FaqIndex(embedding_model, entries)`` shape
so none of the many existing fixture call sites needed to change — only the handful of
fixture *definitions* that used to construct the real thing now construct this instead.
"""

import asyncio

import numpy as np

from app.rag.index import FaqEntry, FaqHit


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Rows scaled to unit length, so cosine similarity is a plain dot product.

    Zero-length rows are left as zeros rather than dividing by zero — this double has no
    stake in pgvector's stricter "reject a zero-norm embedding at index time" policy
    (``app/rag/reindex.py``), which is covered against a real Postgres in
    ``tests/test_rag_baseline.py``'s activated Q9 tests instead.
    """
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


class InMemoryFaqIndex:
    """A ``FaqRetriever`` over a plain in-memory list of ``FaqEntry`` — same cosine math
    and score-floor contract the pre-migration ``FaqIndex`` had, just no longer anything
    ``app/`` ships. Embeds lazily, on first ``search``, and caches for the life of the
    instance (a fixture is typically function- or test-scoped, so this rebuilds per test,
    which is the point — no cross-test state).
    """

    def __init__(self, embedding_model, entries: list[FaqEntry]) -> None:
        self._embedding_model = embedding_model
        self._entries = entries
        self._by_id = {entry.id: entry for entry in entries}
        self._vectors: np.ndarray | None = None
        self._lock = asyncio.Lock()

    @property
    def embedding_model_name(self) -> str:
        return self._embedding_model.model_name

    def get(self, faq_id: str) -> FaqEntry | None:
        return self._by_id.get(faq_id)

    async def _ensure_built(self) -> None:
        if self._vectors is not None or not self._entries:
            return
        async with self._lock:
            if self._vectors is not None:
                return
            result = await self._embedding_model.embed(
                [entry.document for entry in self._entries], input_type="document"
            )
            self._vectors = _l2_normalize(np.asarray(result.embeddings, dtype=np.float32))

    async def search(self, query: str, *, top_k: int, min_score: float) -> list[FaqHit]:
        await self._ensure_built()
        if self._vectors is None or not query.strip():
            return []

        result = await self._embedding_model.embed(query, input_type="query")
        query_vector = _l2_normalize(np.asarray(result.embeddings[0], dtype=np.float32))
        scores = self._vectors @ query_vector

        ranked = np.argsort(-scores)[:top_k]
        return [
            FaqHit(entry=self._entries[int(i)], score=float(scores[int(i)]))
            for i in ranked
            if float(scores[int(i)]) >= min_score
        ]
