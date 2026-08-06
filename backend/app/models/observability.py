"""RAG observability: retrieval logs, retrieval evaluation runs, and answer faithfulness.

Schema for the admin-only quality instrumentation specified in
``Documentation/audits/2026-08-06-rag-observability-and-faithfulness.md`` decision 5.
Deliberately plain relational tables (columns + JSON) with no database-specific
types, so they behave identically on SQLite and Postgres.
"""

from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import JSON, Column, ForeignKey, String, Text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime, utcnow

RetrievalEvalRunStatus = Literal["completed", "failed"]
FaithfulnessStatus = Literal["scored", "no_context", "no_checkable_claims", "failed"]
FaithfulnessVerdict = Literal["supported", "unsupported", "not_applicable"]

# The judge's rationale is a screening aid, not a record: writers truncate to this
# length rather than the column enforcing it, so a verbose judgment is trimmed
# instead of failing the whole check.
FAITHFULNESS_RATIONALE_MAX_LENGTH = 500


class RetrievalLog(SQLModel, table=True):
    __tablename__ = "retrieval_logs"

    id: str = Field(default_factory=lambda: f"rlog-{uuid4().hex[:12]}", primary_key=True)
    conversation_id: str = Field(
        sa_column=Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    )
    # The assistant message this retrieval fed. `search_faq` runs before that row
    # exists, so the record is accumulated in-run and written with the turn.
    message_id: str = Field(
        sa_column=Column(String, ForeignKey("messages.id"), nullable=False, index=True)
    )
    user_message_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("messages.id"), nullable=True),
    )
    user_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False, index=True)
    )
    # What the *agent* searched for, not what the user typed: the model reformulates,
    # and a bad reformulation is one of the two ways retrieval fails.
    query_text: str = Field(sa_column=Column(Text, nullable=False))
    requested_top_k: int
    effective_top_k: int
    min_score: float
    # [{rank, faq_id, question, category, score}], rank 1-based, in rank order.
    results: list[dict] = Field(sa_column=Column(JSON, nullable=False))
    # Denormalized so filtering and sorting need no JSON query.
    result_count: int
    top_score: float | None = None
    embedding_model: str
    latency_ms: int
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False, index=True)
    )


class RetrievalEvalRun(SQLModel, table=True):
    __tablename__ = "retrieval_eval_runs"

    id: str = Field(default_factory=lambda: f"evalrun-{uuid4().hex[:12]}", primary_key=True)
    triggered_by_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False)
    )
    tiers: list[str] = Field(sa_column=Column(JSON, nullable=False))
    k_values: list[int] = Field(sa_column=Column(JSON, nullable=False))
    eval_set_version: str
    case_count: int
    # Graded (expected ids non-empty) and negative cases have different denominators
    # and are reported separately so neither can be mistaken for the other.
    graded_case_count: int
    negative_case_count: int
    recall_at_k: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    mrr: float | None = None
    abstention_rate: float | None = None
    # The identity tier is a canary, reported apart from the headline number.
    identity_recall_at_1: float | None = None
    min_score: float
    embedding_model: str
    # The index state that produced these numbers. Without it a metric drop caused
    # by a forgotten reindex is indistinguishable from a worse embedding model.
    # `index_indexed_at` is the newest indexed_at among the rows the run searched;
    # null when the run searched none.
    index_row_count: int
    index_indexed_at: datetime | None = Field(
        default=None, sa_column=Column(UtcDateTime, nullable=True)
    )
    status: RetrievalEvalRunStatus = Field(
        sa_column=Column(String, nullable=False, index=True)
    )
    error_code: str | None = None
    duration_ms: int
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False, index=True)
    )


class RetrievalEvalCase(SQLModel, table=True):
    __tablename__ = "retrieval_eval_cases"

    id: str = Field(default_factory=lambda: f"evalcase-{uuid4().hex[:12]}", primary_key=True)
    run_id: str = Field(
        sa_column=Column(
            String, ForeignKey("retrieval_eval_runs.id"), nullable=False, index=True
        )
    )
    # The stable id from seed_data/faq_eval_set.json, not a row id.
    case_id: str
    tier: str
    query_text: str = Field(sa_column=Column(Text, nullable=False))
    expected_faq_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    results: list[dict] = Field(sa_column=Column(JSON, nullable=False))
    # null means no expected id was retrieved at all.
    first_relevant_rank: int | None = None
    reciprocal_rank: float
    recall_at_k: dict = Field(sa_column=Column(JSON, nullable=False))
    # Meaningful on `negative` cases only.
    abstained: bool


class FaithfulnessCheck(SQLModel, table=True):
    __tablename__ = "faithfulness_checks"

    id: str = Field(default_factory=lambda: f"fcheck-{uuid4().hex[:12]}", primary_key=True)
    # Unique at the database level, not by a pre-check: this is what makes at most
    # one check per assistant message a guarantee rather than a race.
    message_id: str = Field(
        sa_column=Column(
            String, ForeignKey("messages.id"), nullable=False, unique=True, index=True
        )
    )
    conversation_id: str = Field(
        sa_column=Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    )
    user_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False, index=True)
    )
    status: FaithfulnessStatus = Field(
        sa_column=Column(String, nullable=False, index=True)
    )
    # Non-null if and only if status == "scored". 0.0 is a real score (every
    # checkable claim unsupported); null means not computed.
    score: float | None = None
    supported_count: int | None = None
    unsupported_count: int | None = None
    not_applicable_count: int | None = None
    claim_count: int | None = None
    # The deduplicated union of entries retrieved across the turn; [] for no_context.
    context_faq_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    judge_model: str
    error_code: str | None = None
    duration_ms: int | None = None
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False, index=True)
    )


class FaithfulnessClaim(SQLModel, table=True):
    __tablename__ = "faithfulness_claims"

    id: str = Field(default_factory=lambda: f"fclaim-{uuid4().hex[:12]}", primary_key=True)
    check_id: str = Field(
        sa_column=Column(
            String, ForeignKey("faithfulness_checks.id"), nullable=False, index=True
        )
    )
    claim_index: int
    claim_text: str = Field(sa_column=Column(Text, nullable=False))
    verdict: FaithfulnessVerdict = Field(sa_column=Column(String, nullable=False))
    # The entries the judge cited; [] for unsupported and not_applicable.
    supporting_faq_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    rationale: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
