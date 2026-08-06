"""``faq_embeddings``: the pgvector-backed FAQ retrieval index.

Schema per ``Documentation/audits/2026-08-06-pgvector-migration-contract.md`` decision
1(c). Postgres-only — SQLite cannot run ``CREATE EXTENSION vector`` or store a ``vector``
column, so ``app/db/session.py::create_tables`` and ``alembic/env.py``'s
``include_object`` filter both exclude this table on SQLite. A real deployment always
targets Postgres (``pgvector/pgvector:pg16`` in ``docker-compose.yml``); SQLite is
dev/test-only for the rest of the schema.

The ``embedding`` column is deliberately un-dimensioned (``vector``, not ``vector(n)``)
and carries **no** HNSW/IVFFlat index — both are correctness decisions, not size ones.
An approximate index would make Recall@K (Phase 7) measure the index's own recall
convolved with embedding quality; a fixed ``vector(n)`` would break the day
``EMBEDDING_PROVIDER``/the configured model changes to a different output width.
``dimensions`` records the width actually measured at index time instead.

``app/rag/reindex.py`` is the only writer of this table; ``app/rag/index.py::FaqIndex``
is the only production reader.
"""

from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class FaqEmbedding(SQLModel, table=True):
    __tablename__ = "faq_embeddings"
    __table_args__ = (
        # What makes reindexing an upsert rather than a delete-then-insert: a second
        # write for the same (faq_id, embedding_model) updates the existing row in
        # place, so a concurrent reader never sees the table momentarily without it.
        UniqueConstraint("faq_id", "embedding_model", name="uq_faq_embeddings_faq_model"),
    )

    id: str = Field(default_factory=lambda: f"fembed-{uuid4().hex[:12]}", primary_key=True)
    faq_id: str = Field(sa_column=Column(String, nullable=False))
    # Discriminator: the same faq_id can carry one row per embedding_model, so switching
    # EMBEDDING_PROVIDER/EMBEDDING_MODEL never collides with (or silently mixes into) an
    # index built under a different one.
    embedding_model: str = Field(sa_column=Column(String, nullable=False, index=True))
    # Measured from the actual embed() output at index time — never hardcoded per model,
    # so a provider silently changing a model's output width is detectable rather than
    # assumed.
    dimensions: int = Field(sa_column=Column(Integer, nullable=False))
    # sha256 of `document`. Reindexing's idempotency key: unchanged content_hash (and
    # unchanged dimensions) for an existing (faq_id, embedding_model) row means the row
    # is skipped rather than re-embedded.
    content_hash: str = Field(sa_column=Column(String, nullable=False))
    document: str = Field(sa_column=Column(Text, nullable=False))
    question: str = Field(sa_column=Column(Text, nullable=False))
    answer: str = Field(sa_column=Column(Text, nullable=False))
    category: str = Field(sa_column=Column(String, nullable=False))
    embedding: list[float] = Field(sa_column=Column(Vector(), nullable=False))
    indexed_at: datetime = Field(sa_column=Column(UtcDateTime, nullable=False))
