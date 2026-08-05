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
from app.models.property import (
    ListingType,
    PriceUnit,
    Property,
    PropertyStatus,
    PropertyType,
)
from app.models.user import User, UserRole, UserStatus

__all__ = [
    "AvailabilitySlot",
    "Booking",
    "BookingStatus",
    "Conversation",
    "Escalation",
    "EscalationCategory",
    "EscalationStatus",
    "EscalationUrgency",
    "ListingType",
    "Message",
    "MessageRole",
    "PriceUnit",
    "Property",
    "PropertyStatus",
    "PropertyType",
    "SlotStatus",
    "User",
    "UserRole",
    "UserStatus",
]
