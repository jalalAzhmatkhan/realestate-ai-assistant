from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import Column, ForeignKey, String, Text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime, utcnow

MessageRole = Literal["user", "assistant"]


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(default_factory=lambda: f"conv-{uuid4().hex[:12]}", primary_key=True)
    user_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False, index=True)
    )
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
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False, index=True)
    )
