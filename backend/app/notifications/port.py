"""The seam between a side-effect and its delivery mechanism.

Booking and escalation side-effects are decoupled from the request path (README Design
Decisions §8): a caller commits its transaction, then publishes an event, and never
blocks on delivery. In the MVP that binding is :class:`~app.notifications.log_notifier.LogNotifier`,
which writes a structured log line; swapping it for a Kafka/RabbitMQ/SQS publisher
later touches only ``app/notifications/`` and no call site.

**"Non-blocking" here is a design property, not a threading one.** There is no queue
and no executor in the MVP: the contract is that publishing happens *after* the
caller's commit and that its failure must never fail the caller's operation. Nothing
in this package spawns a thread.

Events are frozen dataclasses rather than loose dicts so both reschedule call sites
(the ``RescheduleViewing`` tool and ``POST /bookings/{id}/reschedule``) emit a payload
a downstream consumer cannot tell apart — the field set is fixed by the type, not by
whichever call site was written first.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class BookingCreatedEvent:
    booking_id: str
    property_id: str
    client_id: str
    agent_id: str
    slot_time: datetime
    status: str
    created_at: datetime


@dataclass(frozen=True)
class BookingRescheduledEvent:
    """Emitted identically by both reschedule surfaces.

    ``booking_id`` is unchanged by a reschedule, and ``previous_slot_time`` is what
    lets a consumer render "moved from X to Y" without correlating a cancel/create
    pair. ``reason`` is carried per the ``RescheduleViewing`` contract, which states it
    is passed into the event.
    """

    booking_id: str
    property_id: str
    client_id: str
    agent_id: str
    slot_time: datetime
    previous_slot_time: datetime
    rescheduled_count: int
    status: str
    updated_at: datetime
    reason: str | None = None


@dataclass(frozen=True)
class BookingCancelledEvent:
    booking_id: str
    property_id: str
    client_id: str
    agent_id: str
    slot_time: datetime
    status: str
    cancelled_at: datetime


@dataclass(frozen=True)
class EscalationCreatedEvent:
    """Frozen by ``2026-08-05-escalation-assignment-contract.md`` Decision 5.

    Carries ``assigned_agent_id`` and ``status`` so a consumer knows who the escalation
    is for without re-querying the escalation table — the coupling this port exists to
    remove. ``reason`` and ``conversation_summary`` are deliberately **absent**: they
    are free text the user typed and a fan-out event is the wrong place for personal
    detail; a consumer that needs them fetches by ``escalation_id``.

    ``property_id`` is the *resolved* property only (Decision 6) — an unresolvable id
    is ``None`` here and on the persisted row.
    """

    escalation_id: str
    conversation_id: str
    escalated_by_id: str
    escalated_by_role: str
    category: str
    urgency: str
    property_id: str | None
    assigned_agent_id: str | None
    status: str
    created_at: datetime


class NotificationPort(Protocol):
    """Publish a side-effect event. Implementations must not raise into the caller —
    a failed notification must never roll back a committed booking."""

    def publish_booking_created(self, event: BookingCreatedEvent) -> None: ...

    def publish_booking_rescheduled(self, event: BookingRescheduledEvent) -> None: ...

    def publish_booking_cancelled(self, event: BookingCancelledEvent) -> None: ...

    def publish_escalation_created(self, event: EscalationCreatedEvent) -> None: ...
