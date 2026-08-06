"""faq_embeddings: pgvector-backed FAQ retrieval index (B32)

Revision ID: 5ca614128dea
Revises: c5293d4e9eb4
Create Date: 2026-08-06 12:00:00.000000

Postgres-only. SQLite cannot run ``CREATE EXTENSION vector`` or store a ``vector``
column, so this migration is a deliberate no-op on any non-Postgres dialect — the same
condition ``alembic/env.py``'s ``include_object`` filter and
``app/db/session.py::create_tables`` use to keep the SQLite dev/test path from ever
seeing this table. See ``Documentation/audits/2026-08-06-pgvector-migration-contract.md``
decision 1(c)/(f).

No HNSW/IVFFlat index on ``embedding``, and the column itself is un-dimensioned
(``vector``, not ``vector(n)``) — both deliberate correctness decisions, not size ones
(see ``app/models/faq_embedding.py``'s module docstring).
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from pgvector.sqlalchemy import Vector

import app.models.types

# revision identifiers, used by Alembic.
revision: str = "5ca614128dea"
down_revision: Union[str, Sequence[str], None] = "c5293d4e9eb4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Upgrade schema."""
    if not _is_postgres():
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "faq_embeddings",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("faq_id", sa.String(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("indexed_at", app.models.types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("faq_id", "embedding_model", name="uq_faq_embeddings_faq_model"),
    )
    op.create_index(
        op.f("ix_faq_embeddings_embedding_model"),
        "faq_embeddings",
        ["embedding_model"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    if not _is_postgres():
        return

    op.drop_index(op.f("ix_faq_embeddings_embedding_model"), table_name="faq_embeddings")
    op.drop_table("faq_embeddings")
    # Deliberately not dropping the `vector` extension: other objects/sessions may
    # depend on it, and `CREATE EXTENSION IF NOT EXISTS` is a safe no-op on the next
    # upgrade regardless.
