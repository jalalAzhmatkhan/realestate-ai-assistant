from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import Column, ForeignKey, String, Text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime, utcnow

EscalationCategory = Literal[
    "complaint", "complex_request", "policy_exception", "technical_issue", "other"
]
EscalationUrgency = Literal["low", "medium", "high"]
EscalationStatus = Literal["queued", "queued_unassigned"]


class Escalation(SQLModel, table=True):
    __tablename__ = "escalations"

    id: str = Field(default_factory=lambda: f"esc-{uuid4().hex[:12]}", primary_key=True)
    conversation_id: str = Field(
        sa_column=Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    )
    escalated_by_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False, index=True)
    )
    reason: str = Field(sa_column=Column(Text, nullable=False))
    category: EscalationCategory = Field(
        default="other", sa_column=Column(String, nullable=False, index=True)
    )
    conversation_summary: str = Field(sa_column=Column(Text, nullable=False))
    urgency: EscalationUrgency = Field(
        default="medium", sa_column=Column(String, nullable=False, index=True)
    )
    # Only a *resolved* property id is persisted; a supplied-but-unresolvable one is
    # stored as null so "escalations for property X" queries stay correct
    # (escalation-assignment contract, Decision 6).
    property_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("properties.id"), nullable=True, index=True),
    )
    assigned_agent_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("users.id"), nullable=True, index=True),
    )
    status: EscalationStatus = Field(
        default="queued_unassigned", sa_column=Column(String, nullable=False, index=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False, index=True)
    )
