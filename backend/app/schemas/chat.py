from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

MAX_MESSAGE_LENGTH = 4000


class ChatMessageRequest(BaseModel):
    # Null starts a new conversation. An id belonging to someone else is treated as
    # not-found rather than forbidden, same posture as bookings — no id enumeration.
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


class ToolCallRecord(BaseModel):
    """One tool the model chose to call this turn, with what it passed and what it got.

    Returned to the client so autonomous tool selection is *observable* — the whole point
    of the design is that the model picked these, and a response that only carried
    ``reply`` would make that indistinguishable from a hardcoded flow.
    """

    tool: str
    arguments: dict[str, Any]
    result_summary: str


class ChatMessageResponse(BaseModel):
    conversation_id: str
    reply: str
    tool_calls: list[ToolCallRecord]
    created_at: datetime
