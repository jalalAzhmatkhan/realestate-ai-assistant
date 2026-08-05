"""MVP :class:`~app.notifications.port.NotificationPort` binding: one structured log line.

No email, no SMS, no queue — the point is that the *call sites* are already written
against the port, so the production swap is a binding change rather than a code change
in the booking tools and endpoints.
"""

import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any

from app.notifications.port import (
    BookingCancelledEvent,
    BookingCreatedEvent,
    BookingRescheduledEvent,
    EscalationCreatedEvent,
)

logger = logging.getLogger(__name__)


def _serialize(event: Any) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in asdict(event).items()
    }


class LogNotifier:
    """Writes each event as a structured log line, keyed by ``notification_event``.

    Every method swallows its own failures: a notification is a side-effect published
    *after* the caller committed, so letting it raise would turn a delivered booking
    into an apparent failure.
    """

    def _publish(self, event_name: str, event: Any) -> None:
        try:
            logger.info(
                "notification_published",
                extra={"notification_event": event_name, **_serialize(event)},
            )
        except Exception:
            logger.exception(
                "notification_publish_failed", extra={"notification_event": event_name}
            )

    def publish_booking_created(self, event: BookingCreatedEvent) -> None:
        self._publish("booking_created", event)

    def publish_booking_rescheduled(self, event: BookingRescheduledEvent) -> None:
        self._publish("booking_rescheduled", event)

    def publish_booking_cancelled(self, event: BookingCancelledEvent) -> None:
        self._publish("booking_cancelled", event)

    def publish_escalation_created(self, event: EscalationCreatedEvent) -> None:
        self._publish("escalation_created", event)
