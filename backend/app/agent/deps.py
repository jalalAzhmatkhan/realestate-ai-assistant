"""What every tool receives as ``ctx.deps``.

Assembled by ``app/api/chat.py`` **before** the agent runs and passed into the Pydantic
AI run as ``deps=``. Two properties matter:

1. **``user`` is the authenticated caller, resolved from the JWT** — not anything the
   model produced. Every tool re-authorizes against it (README Design Decisions §5).
   Nothing in this object is ever populated from LLM output.
2. **Everything is injected, nothing is global.** The DB session, RAG index, and
   notification port arrive as parameters, which is what makes each tool module
   extractable into its own service later without touching its call sites
   (README Design Decisions §1).
"""

from dataclasses import dataclass

from sqlmodel import Session

from app.core.config import Settings
from app.models import User
from app.notifications.port import NotificationPort
from app.rag.index import FaqIndex


@dataclass(frozen=True)
class AgentDeps:
    user: User
    """The authenticated caller. The sole source of identity and role for RBAC
    re-authorization inside every tool."""

    db: Session
    """Request-scoped session. Tools that write commit through app/booking/slots.py."""

    conversation_id: str
    """The conversation this run belongs to. ``escalate_to_human`` ties its record to
    this, so a user cannot escalate a session that is not theirs."""

    rag: FaqIndex
    settings: Settings
    notifier: NotificationPort
