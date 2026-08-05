"""app/notifications/ — the NotificationPort seam and its MVP LogNotifier binding.

Two things matter here beyond "it logs something":

1. `EscalationCreatedEvent`'s field set is frozen by
   `Documentation/audits/2026-08-05-escalation-assignment-contract.md` Decision 5.
   `reason`/`conversation_summary` are excluded on privacy grounds, so their absence
   is asserted structurally — not merely by nobody happening to pass them.
2. A notification is published *after* the caller committed, so a failure inside the
   notifier must never surface as a failed booking. That is asserted by breaking the
   logger, not by inspecting the try/except.
"""

import dataclasses
import inspect
import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.notifications import (
    BookingCancelledEvent,
    BookingCreatedEvent,
    BookingRescheduledEvent,
    EscalationCreatedEvent,
    LogNotifier,
    NotificationPort,
)
from app.notifications import log_notifier as log_notifier_module

LOGGER_NAME = "app.notifications.log_notifier"

SLOT_TIME = datetime(2026, 8, 8, 3, 0, tzinfo=timezone.utc)
PREVIOUS_SLOT_TIME = SLOT_TIME - timedelta(days=1)
STAMP = datetime(2026, 8, 5, 5, 0, tzinfo=timezone.utc)

# The exact payload frozen by the escalation assignment contract, Decision 5.
ESCALATION_CONTRACT_FIELDS = (
    "escalation_id",
    "conversation_id",
    "escalated_by_id",
    "escalated_by_role",
    "category",
    "urgency",
    "property_id",
    "assigned_agent_id",
    "status",
    "created_at",
)

PORT_METHODS = (
    "publish_booking_created",
    "publish_booking_rescheduled",
    "publish_booking_cancelled",
    "publish_escalation_created",
)


def _booking_created() -> BookingCreatedEvent:
    return BookingCreatedEvent(
        booking_id="booking-001",
        property_id="prop-001",
        client_id="u-client-1",
        agent_id="u-agent-1",
        slot_time=SLOT_TIME,
        status="confirmed",
        created_at=STAMP,
    )


def _booking_rescheduled(**overrides) -> BookingRescheduledEvent:
    return BookingRescheduledEvent(
        booking_id="booking-001",
        property_id="prop-001",
        client_id="u-client-1",
        agent_id="u-agent-1",
        slot_time=SLOT_TIME,
        previous_slot_time=PREVIOUS_SLOT_TIME,
        rescheduled_count=1,
        status="confirmed",
        updated_at=STAMP,
        **overrides,
    )


def _booking_cancelled() -> BookingCancelledEvent:
    return BookingCancelledEvent(
        booking_id="booking-001",
        property_id="prop-001",
        client_id="u-client-1",
        agent_id="u-agent-1",
        slot_time=SLOT_TIME,
        status="cancelled",
        cancelled_at=STAMP,
    )


def _escalation_created(**overrides) -> EscalationCreatedEvent:
    values = {
        "escalation_id": "esc-001",
        "conversation_id": "conv-001",
        "escalated_by_id": "u-client-1",
        "escalated_by_role": "client",
        "category": "complaint",
        "urgency": "high",
        "property_id": "prop-001",
        "assigned_agent_id": "u-agent-1",
        "status": "queued",
        "created_at": STAMP,
    }
    values.update(overrides)
    return EscalationCreatedEvent(**values)


ALL_EVENTS = {
    "booking_created": (_booking_created, "publish_booking_created"),
    "booking_rescheduled": (_booking_rescheduled, "publish_booking_rescheduled"),
    "booking_cancelled": (_booking_cancelled, "publish_booking_cancelled"),
    "escalation_created": (_escalation_created, "publish_escalation_created"),
}


@pytest.fixture
def notifier():
    return LogNotifier()


@pytest.fixture
def published(caplog):
    """Records emitted by LogNotifier only, so an unrelated library's INFO line
    cannot make an assertion pass."""
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    def records():
        return [r for r in caplog.records if r.name == LOGGER_NAME]

    return records


# --- port conformance ---------------------------------------------------------


def test_the_port_declares_exactly_the_four_documented_methods():
    declared = {
        name
        for name, value in vars(NotificationPort).items()
        if not name.startswith("_") and callable(value)
    }

    assert declared == set(PORT_METHODS)


@pytest.mark.parametrize("method", PORT_METHODS)
def test_log_notifier_implements_the_port_method_with_the_same_signature(method):
    implementation = getattr(LogNotifier, method)
    protocol = getattr(NotificationPort, method)

    assert callable(implementation)
    assert inspect.signature(implementation) == inspect.signature(protocol)


@pytest.mark.parametrize("event_name", list(ALL_EVENTS))
def test_publishing_returns_none_and_does_not_raise(notifier, event_name):
    factory, method = ALL_EVENTS[event_name]

    assert getattr(notifier, method)(factory()) is None


# --- event payload shapes -----------------------------------------------------


def test_escalation_event_carries_exactly_the_contract_fields():
    """Decision 5 froze this payload; an extra or missing key is a contract break."""
    names = tuple(f.name for f in dataclasses.fields(EscalationCreatedEvent))

    assert names == ESCALATION_CONTRACT_FIELDS
    assert len(names) == 10


@pytest.mark.parametrize("excluded", ["reason", "conversation_summary"])
def test_escalation_event_rejects_the_deliberately_excluded_free_text_fields(excluded):
    """Free text the user typed must not fan out to logs or a queue."""
    with pytest.raises(TypeError):
        _escalation_created(**{excluded: "user typed this"})


def test_escalation_event_allows_a_null_property_and_assignee():
    event = _escalation_created(
        property_id=None, assigned_agent_id=None, status="queued_unassigned"
    )

    assert event.property_id is None
    assert event.assigned_agent_id is None


def test_the_excluded_free_text_is_still_reachable_on_the_escalation_row():
    """Decision 5 promises 'a consumer that needs them fetches by escalation_id' —
    that promise only holds if the row actually carries them."""
    from app.models import Escalation

    assert {"reason", "conversation_summary"} <= set(Escalation.model_fields)


def test_every_escalation_event_field_traces_back_to_the_row_or_the_caller():
    """`escalated_by_role` is derived from the authenticated user, not stored on the
    escalation; everything else must exist on the row a consumer would fetch."""
    from app.models import Escalation

    columns = set(Escalation.model_fields) | {"escalation_id", "escalated_by_role"}

    assert set(ESCALATION_CONTRACT_FIELDS) <= columns


def test_reschedule_event_carries_the_previous_slot_time_and_an_optional_reason():
    """`previous_slot_time` is what lets a consumer render 'moved from X to Y'
    without correlating a cancel/create pair."""
    assert _booking_rescheduled().reason is None
    assert _booking_rescheduled(reason="client asked").reason == "client asked"
    assert _booking_rescheduled().previous_slot_time == PREVIOUS_SLOT_TIME


@pytest.mark.parametrize("event_name", list(ALL_EVENTS))
def test_events_are_frozen(event_name):
    factory, _ = ALL_EVENTS[event_name]
    event = factory()

    with pytest.raises(dataclasses.FrozenInstanceError):
        event.property_id = "tampered"


@pytest.mark.parametrize("event_name", list(ALL_EVENTS))
def test_event_field_names_never_collide_with_log_record_attributes(event_name):
    """A colliding key makes `logging` raise KeyError inside `_publish`, which
    swallows it — the event would vanish with no error anywhere."""
    factory, _ = ALL_EVENTS[event_name]
    reserved = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
        "message",
        "asctime",
    }

    assert not {f.name for f in dataclasses.fields(factory())} & reserved


# --- log output ---------------------------------------------------------------


@pytest.mark.parametrize("event_name", list(ALL_EVENTS))
def test_each_publish_emits_exactly_one_distinguishable_line(
    notifier, published, event_name
):
    factory, method = ALL_EVENTS[event_name]

    getattr(notifier, method)(factory())

    records = published()
    assert len(records) == 1
    assert records[0].getMessage() == "notification_published"
    assert records[0].notification_event == event_name
    assert records[0].levelno == logging.INFO


def test_the_four_events_are_told_apart_by_notification_event(notifier, published):
    notifier.publish_booking_created(_booking_created())
    notifier.publish_booking_rescheduled(_booking_rescheduled())
    notifier.publish_booking_cancelled(_booking_cancelled())
    notifier.publish_escalation_created(_escalation_created())

    assert [r.notification_event for r in published()] == list(ALL_EVENTS)


@pytest.mark.parametrize("event_name", list(ALL_EVENTS))
def test_every_event_field_reaches_the_log_line(notifier, published, event_name):
    factory, method = ALL_EVENTS[event_name]
    event = factory()

    getattr(notifier, method)(event)

    record = published()[0]
    for field in dataclasses.fields(event):
        assert hasattr(record, field.name), field.name


def test_datetimes_are_serialized_as_iso_strings(notifier, published):
    notifier.publish_booking_rescheduled(_booking_rescheduled())

    record = published()[0]
    assert record.slot_time == SLOT_TIME.isoformat()
    assert record.previous_slot_time == PREVIOUS_SLOT_TIME.isoformat()
    assert record.updated_at == STAMP.isoformat()


def test_the_escalation_line_carries_the_assignee_so_a_consumer_need_not_requery(
    notifier, published
):
    notifier.publish_escalation_created(_escalation_created())

    record = published()[0]
    assert record.assigned_agent_id == "u-agent-1"
    assert record.status == "queued"


def test_the_escalation_line_never_leaks_free_text(notifier, published):
    notifier.publish_escalation_created(_escalation_created())

    record = published()[0]
    assert not hasattr(record, "reason")
    assert not hasattr(record, "conversation_summary")


# --- failure containment ------------------------------------------------------


@pytest.mark.parametrize("event_name", list(ALL_EVENTS))
def test_a_broken_logger_never_propagates_into_the_caller(
    notifier, monkeypatch, event_name
):
    """A committed booking must not look like a failure because a log write broke."""
    factory, method = ALL_EVENTS[event_name]

    def explode(*args, **kwargs):
        raise RuntimeError("log sink is down")

    monkeypatch.setattr(log_notifier_module.logger, "info", explode)

    assert getattr(notifier, method)(factory()) is None


def test_a_swallowed_failure_is_still_recorded_for_diagnosis(
    notifier, published, monkeypatch
):
    def explode(*args, **kwargs):
        raise RuntimeError("log sink is down")

    monkeypatch.setattr(log_notifier_module.logger, "info", explode)
    notifier.publish_booking_created(_booking_created())

    records = published()
    assert [r.getMessage() for r in records] == ["notification_publish_failed"]
    assert records[0].levelno == logging.ERROR
    assert records[0].notification_event == "booking_created"
    assert records[0].exc_info is not None
