from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import JSON, Column, ForeignKey, String, Text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime, utcnow

MessageRole = Literal["user", "assistant"]


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(default_factory=lambda: f"conv-{uuid4().hex[:12]}", primary_key=True)
    user_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False, index=True)
    )
    # The agent's working memory: the run's full Pydantic AI message history, serialized
    # by ModelMessagesTypeAdapter. Distinct in purpose from the `messages` rows, which are
    # the human-readable transcript — this one carries the tool-call and tool-result parts
    # a later turn needs (e.g. the booking ids from a `booking_ambiguous` candidate list,
    # which the assistant's question text does not contain).
    #
    # In the database rather than in-process so any instance can serve any conversation
    # (CLAUDE.md's stateless-services principle). Known MVP limitation: it grows without
    # bound — the upgrade path is Redis with a window/summarization policy, which is where
    # session state is designed to move anyway.
    history_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False, index=True)
    )


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(default_factory=lambda: f"msg-{uuid4().hex[:12]}", primary_key=True)
    conversation_id: str = Field(
        sa_column=Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    )
    role: MessageRole = Field(sa_column=Column(String, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    # Which tools the model chose on this turn, and what came back — the record Phase 1's
    # QA pass flagged as missing. Populated on `assistant` rows only; null on user rows
    # and on assistant turns that needed no tool. Makes autonomous tool selection
    # auditable after the fact rather than only visible in the live response.
    tool_calls: list[dict] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False, index=True)
    )
