"""SQLModel entities.

Importing this package registers every table on ``SQLModel.metadata``, which is
what ``create_all`` walks — a model that is never imported is silently absent from
the schema, so new entities belong in the list below.
"""

from app.models.availability_slot import AvailabilitySlot, SlotStatus
from app.models.booking import Booking, BookingStatus
from app.models.conversation import Conversation, Message, MessageRole
from app.models.escalation import (
    Escalation,
    EscalationCategory,
    EscalationStatus,
    EscalationUrgency,
)
from app.models.faq_embedding import FaqEmbedding
from app.models.observability import (
    FAITHFULNESS_RATIONALE_MAX_LENGTH,
    FaithfulnessCheck,
    FaithfulnessClaim,
    FaithfulnessStatus,
    FaithfulnessVerdict,
    RetrievalEvalCase,
    RetrievalEvalRun,
    RetrievalEvalRunStatus,
    RetrievalLog,
)
from app.models.property import (
    ListingType,
    PriceUnit,
    Property,
    PropertyStatus,
    PropertyType,
)
from app.models.user import User, UserRole, UserStatus

__all__ = [
    "FAITHFULNESS_RATIONALE_MAX_LENGTH",
    "AvailabilitySlot",
    "Booking",
    "BookingStatus",
    "Conversation",
    "Escalation",
    "EscalationCategory",
    "EscalationStatus",
    "EscalationUrgency",
    "FaqEmbedding",
    "FaithfulnessCheck",
    "FaithfulnessClaim",
    "FaithfulnessStatus",
    "FaithfulnessVerdict",
    "ListingType",
    "Message",
    "MessageRole",
    "PriceUnit",
    "Property",
    "PropertyStatus",
    "PropertyType",
    "RetrievalEvalCase",
    "RetrievalEvalRun",
    "RetrievalEvalRunStatus",
    "RetrievalLog",
    "SlotStatus",
    "User",
    "UserRole",
    "UserStatus",
]
