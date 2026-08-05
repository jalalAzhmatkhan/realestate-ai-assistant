"""In-process flat vector index over ``backend/seed_data/faq.json``.

README Design Decisions §6: the ~14 seed FAQ entries are embedded once, held as a small
NumPy array, and queried with cosine similarity. No FAISS/Chroma/pgvector, no extra
infra, trivial to unit-test at 14 rows.

**Known limits, accepted for MVP:** an O(n) linear scan, no persistence beyond the
process (rebuilt on startup), and one copy of the embeddings per instance. The documented
upgrade path is a Knowledge Base service over pgvector so embeddings are computed once
centrally — the seam is this class's interface, which the ``search_faq`` tool is the only
consumer of.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic_ai.embeddings import EmbeddingModel

logger = logging.getLogger(__name__)

FAQ_FILENAME = "faq.json"


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
        # An empty index is a legitimate state (the tool returns empty `results`
        # rather than an error), so a missing corpus must not crash the agent.
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


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Rows scaled to unit length, so cosine similarity is a plain dot product.

    Zero-length rows would divide by zero; they are left as zeros instead, scoring 0
    against everything rather than producing NaNs that poison the whole ranking.
    """
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


class FaqIndex:
    """Lazily-built flat vector index. One instance per application."""

    def __init__(self, embedding_model: EmbeddingModel, entries: list[FaqEntry]) -> None:
        self._embedding_model = embedding_model
        self._entries = entries
        self._vectors: np.ndarray | None = None
        # Guards the build so N concurrent first-requests don't each pay for a full
        # corpus embedding (and N times the API bill).
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def is_built(self) -> bool:
        return self._vectors is not None

    async def ensure_built(self) -> None:
        if self._vectors is not None or not self._entries:
            return
        async with self._lock:
            if self._vectors is not None:
                return
            result = await self._embedding_model.embed(
                [entry.document for entry in self._entries], input_type="document"
            )
            self._vectors = _l2_normalize(np.asarray(result.embeddings, dtype=np.float32))
            logger.info(
                "faq_index_built",
                extra={
                    "entries": len(self._entries),
                    "dimensions": int(self._vectors.shape[1]),
                    "embedding_model": self._embedding_model.model_name,
                },
            )

    async def search(self, query: str, *, top_k: int, min_score: float) -> list[FaqHit]:
        """Top-``top_k`` entries scoring at least ``min_score``.

        Returns an empty list when nothing clears the floor — the caller renders that
        as "no relevant answer", never as an error (README's ``search_faq`` contract).
        """
        await self.ensure_built()
        if self._vectors is None or not query.strip():
            return []

        result = await self._embedding_model.embed(query, input_type="query")
        query_vector = _l2_normalize(np.asarray(result.embeddings[0], dtype=np.float32))
        scores = self._vectors @ query_vector

        # argsort over 14 rows; a partial sort would be premature at this size.
        ranked = np.argsort(-scores)[:top_k]
        return [
            FaqHit(entry=self._entries[int(i)], score=float(scores[int(i)]))
            for i in ranked
            if float(scores[int(i)]) >= min_score
        ]


def build_faq_index(embedding_model: EmbeddingModel, seed_data_dir: str | Path) -> FaqIndex:
    """Construct (but do not embed) the index. Embedding happens on first search."""
    return FaqIndex(embedding_model, load_faq_entries(seed_data_dir))
