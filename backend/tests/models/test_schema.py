"""Schema creation and referential-integrity behavior of the SQLModel entities."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.db.session import build_engine, create_tables
from app.models import (
    AvailabilitySlot,
    Booking,
    Conversation,
    Escalation,
    Message,
    Property,
    User,
)

from ..conftest import make_db_settings

EXPECTED_TABLES = {
    "users",
    "properties",
    "availability_slots",
    "bookings",
    "conversations",
    "messages",
    "escalations",
    "retrieval_logs",
    "retrieval_eval_runs",
    "retrieval_eval_cases",
    "faithfulness_checks",
    "faithfulness_claims",
}

JAKARTA = timezone(timedelta(hours=7))


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(make_db_settings(tmp_path))
    create_tables(engine)
    yield engine
    engine.dispose()


def _user(user_id: str, role: str = "agent") -> User:
    return User(
        id=user_id,
        name=f"User {user_id}",
        email=f"{user_id}@evdekimi.test",
        role=role,
        hashed_password="$2b$12$notarealhashbutastringisfine",
    )


def _property(property_id: str, agent_id: str | None) -> Property:
    return Property(
        id=property_id,
        title="Test listing",
        property_type="apartment",
        listing_type="rent",
        price=1000.0,
        price_unit="per_month",
        bedrooms=2,
        bathrooms=1,
        area_sqm=60.0,
        address="Jl. Test 1",
        city="Jakarta",
        latitude=-6.2,
        longitude=106.8,
        amenities=["pool"],
        agent_id=agent_id,
        status="active",
        description="A test listing.",
        listed_date=date(2026, 7, 1),
    )


@pytest.fixture
def graph(engine):
    """A minimal valid agent -> property -> slot -> client object graph."""
    with Session(engine) as session:
        session.add(_user("u-agent-x", "agent"))
        session.add(_user("u-client-x", "client"))
        session.flush()
        session.add(_property("prop-x", "u-agent-x"))
        session.flush()
        session.add(
            AvailabilitySlot(
                id="avail-x",
                agent_id="u-agent-x",
                property_id="prop-x",
                slot_time=datetime(2026, 8, 8, 10, 0, tzinfo=JAKARTA),
                status="open",
            )
        )
        session.commit()
    return engine


# --- schema creation ----------------------------------------------------------


def test_create_all_creates_every_declared_table(engine):
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_email_is_uniquely_indexed(engine):
    """Two accounts sharing an address would make login ambiguous."""
    with Session(engine) as session:
        session.add(_user("u-1"))
        session.commit()
    with Session(engine) as session:
        duplicate = _user("u-2")
        duplicate.email = "u-1@evdekimi.test"
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()


# --- foreign keys are actually enforced on SQLite -----------------------------


def test_valid_booking_insert_succeeds(graph):
    """Control for the FK tests below: without this, a rejection could mean anything."""
    with Session(graph) as session:
        session.add(
            Booking(
                id="booking-ok",
                availability_slot_id="avail-x",
                property_id="prop-x",
                client_id="u-client-x",
                agent_id="u-agent-x",
                slot_time=datetime(2026, 8, 8, 10, 0, tzinfo=JAKARTA),
            )
        )
        session.commit()

        assert session.get(Booking, "booking-ok") is not None


@pytest.mark.parametrize(
    "field",
    ["client_id", "agent_id", "property_id", "availability_slot_id"],
)
def test_booking_with_a_dangling_reference_is_rejected(graph, field):
    """SQLite ignores foreign keys unless PRAGMA foreign_keys=ON is set per
    connection; this fails if build_engine's listener ever stops firing."""
    values = {
        "id": "booking-bad",
        "availability_slot_id": "avail-x",
        "property_id": "prop-x",
        "client_id": "u-client-x",
        "agent_id": "u-agent-x",
        "slot_time": datetime(2026, 8, 8, 10, 0, tzinfo=JAKARTA),
    }
    values[field] = "does-not-exist"

    with Session(graph) as session:
        session.add(Booking(**values))
        with pytest.raises(IntegrityError):
            session.commit()


def test_message_with_a_dangling_conversation_is_rejected(engine):
    with Session(engine) as session:
        session.add(
            Message(id="msg-bad", conversation_id="conv-nope", role="user", content="hi")
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_escalation_with_a_dangling_conversation_is_rejected(engine):
    with Session(engine) as session:
        session.add(_user("u-client-e", "client"))
        session.commit()
    with Session(engine) as session:
        session.add(
            Escalation(
                id="esc-bad",
                conversation_id="conv-nope",
                escalated_by_id="u-client-e",
                reason="r",
                conversation_summary="s",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_property_agent_id_may_be_null(engine):
    """Unassigned listings are legal — escalate_to_human's E4 branch depends on it."""
    with Session(engine) as session:
        session.add(_property("prop-unowned", None))
        session.commit()

        assert session.get(Property, "prop-unowned").agent_id is None


def test_foreign_keys_are_enforced_on_a_second_connection(graph):
    """The pragma is per-connection: a pooled connection opened later must get it too."""
    with Session(graph) as session:
        session.add(
            Conversation(id="conv-1", user_id="u-client-x"),
        )
        session.commit()
    with Session(graph) as session:
        session.add(Conversation(id="conv-2", user_id="ghost"))
        with pytest.raises(IntegrityError):
            session.commit()


# --- UtcDateTime --------------------------------------------------------------


def test_aware_non_utc_datetime_round_trips_to_the_same_instant(graph):
    """A +07:00 slot must not read back as if it were 10:00 UTC (a 7-hour error)."""
    written = datetime(2026, 8, 8, 10, 0, tzinfo=JAKARTA)

    with Session(graph) as session:
        session.add(
            AvailabilitySlot(
                id="avail-tz", agent_id="u-agent-x", property_id="prop-x", slot_time=written
            )
        )
        session.commit()

    with Session(graph) as session:
        read = session.get(AvailabilitySlot, "avail-tz").slot_time

    assert read.tzinfo is not None
    assert read == written
    assert read.utcoffset() == timedelta(0)
    assert (read.hour, read.day) == (3, 8)


def test_stored_column_value_is_normalized_to_naive_utc(graph):
    """Proves normalization happens on write, not just symmetrically on both sides."""
    with Session(graph) as session:
        session.add(
            AvailabilitySlot(
                id="avail-raw",
                agent_id="u-agent-x",
                property_id="prop-x",
                slot_time=datetime(2026, 8, 8, 10, 0, tzinfo=JAKARTA),
            )
        )
        session.commit()

    with graph.connect() as connection:
        raw = connection.execute(
            text("SELECT slot_time FROM availability_slots WHERE id = 'avail-raw'")
        ).scalar_one()

    assert str(raw).startswith("2026-08-08 03:00:00")


def test_naive_datetime_is_read_back_as_utc_aware(graph):
    with Session(graph) as session:
        session.add(
            AvailabilitySlot(
                id="avail-naive",
                agent_id="u-agent-x",
                property_id="prop-x",
                slot_time=datetime(2026, 8, 8, 3, 0),
            )
        )
        session.commit()

    with Session(graph) as session:
        read = session.get(AvailabilitySlot, "avail-naive").slot_time

    assert read == datetime(2026, 8, 8, 3, 0, tzinfo=timezone.utc)


def test_two_equal_instants_in_different_offsets_compare_equal_after_a_round_trip(graph):
    utc_form = datetime(2026, 8, 8, 3, 0, tzinfo=timezone.utc)
    jakarta_form = datetime(2026, 8, 8, 10, 0, tzinfo=JAKARTA)

    with Session(graph) as session:
        session.add(
            AvailabilitySlot(
                id="avail-a", agent_id="u-agent-x", property_id="prop-x", slot_time=utc_form
            )
        )
        session.add(
            AvailabilitySlot(
                id="avail-b",
                agent_id="u-agent-x",
                property_id="prop-x",
                slot_time=jakarta_form,
            )
        )
        session.commit()

    with Session(graph) as session:
        assert (
            session.get(AvailabilitySlot, "avail-a").slot_time
            == session.get(AvailabilitySlot, "avail-b").slot_time
        )


def test_default_timestamps_are_timezone_aware(graph):
    with Session(graph) as session:
        session.add(
            Booking(
                id="booking-defaults",
                availability_slot_id="avail-x",
                property_id="prop-x",
                client_id="u-client-x",
                agent_id="u-agent-x",
                slot_time=datetime(2026, 8, 8, 10, 0, tzinfo=JAKARTA),
            )
        )
        session.commit()

    with Session(graph) as session:
        booking = session.get(Booking, "booking-defaults")

    assert booking.created_at.tzinfo is not None
    assert booking.status == "confirmed"
    assert booking.rescheduled_count == 0
