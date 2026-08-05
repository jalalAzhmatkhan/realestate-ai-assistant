"""Startup seed loader: idempotency, the kill switch, and data fidelity."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, func, select

from app.core.security import verify_password
from app.db.seed import seed_if_empty
from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import AvailabilitySlot, Booking, Property, User

from .conftest import SEED_DATA_DIR, SEED_PASSWORD, make_db_settings

JAKARTA = timezone(timedelta(hours=7))


def _seed_file(name: str) -> list[dict]:
    return json.loads((Path(SEED_DATA_DIR) / name).read_text(encoding="utf-8"))


def _count(session: Session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def _counts(engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "users": _count(session, User),
            "properties": _count(session, Property),
            "slots": _count(session, AvailabilitySlot),
            "bookings": _count(session, Booking),
        }


@pytest.fixture
def engine_factory(tmp_path):
    engines = []

    def build(**overrides):
        engine = build_engine(make_db_settings(tmp_path, **overrides))
        create_tables(engine)
        engines.append(engine)
        return engine

    yield build
    for engine in engines:
        engine.dispose()


# --- loading ------------------------------------------------------------------


def test_seed_loads_every_file_in_dependency_order(tmp_path, engine_factory):
    settings = make_db_settings(tmp_path)
    engine = engine_factory()

    seed_if_empty(engine, settings)

    assert _counts(engine) == {
        "users": len(_seed_file("users.json")),
        "properties": len(_seed_file("properties.json")),
        "slots": len(_seed_file("agent_availability.json")),
        "bookings": len(_seed_file("viewings.json")),
    }


def test_seeded_passwords_verify_against_the_documented_credential(tmp_path, engine_factory):
    """A bad hash in users.json would make every login test pass for the wrong reason."""
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        users = session.exec(select(User)).all()

    assert users
    for user in users:
        assert verify_password(SEED_PASSWORD, user.hashed_password), user.email


def test_seeded_users_cover_every_role(tmp_path, engine_factory):
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        roles = {user.role for user in session.exec(select(User)).all()}

    assert roles == {"admin", "agent", "client"}


def test_seeded_properties_use_property_type(tmp_path, engine_factory):
    """`type` was renamed to `property_type` (ruling X1); a stale key would be dropped
    silently by a dict-splat load rather than raising."""
    assert all("type" not in row for row in _seed_file("properties.json"))

    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        listing = session.get(Property, "prop-001")

    assert listing.property_type


def test_seeded_slot_times_keep_their_instant(tmp_path, engine_factory):
    """viewings.json is written in +07:00; a dropped offset is a 7-hour data error."""
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        booking = session.get(Booking, "booking-001")

    assert booking.slot_time == datetime(2026, 8, 8, 10, 0, tzinfo=JAKARTA)
    assert booking.created_at.tzinfo is not None


def test_seed_fixture_gives_one_client_two_confirmed_future_bookings(
    tmp_path, engine_factory
):
    """Fixture the RescheduleViewing ambiguity path depends on."""
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        bookings = session.exec(
            select(Booking).where(
                Booking.client_id == "u-client-1", Booking.status == "confirmed"
            )
        ).all()

    assert {b.id for b in bookings} == {"booking-001", "booking-004"}


def test_seeded_amenities_survive_as_a_list(tmp_path, engine_factory):
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        listing = session.get(Property, "prop-001")

    assert isinstance(listing.amenities, list)


# --- fixture consistency ------------------------------------------------------
#
# Phase 2's booking/conflict logic will be tested against these rows, so silent
# fixture drift here would make those tests pass for the wrong reason.


def test_every_confirmed_booking_holds_a_booked_slot(tmp_path, engine_factory):
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        for booking in session.exec(select(Booking)).all():
            slot = session.get(AvailabilitySlot, booking.availability_slot_id)
            expected = "booked" if booking.status == "confirmed" else "open"
            assert slot.status == expected, booking.id


def test_no_slot_is_marked_booked_without_a_confirmed_booking(tmp_path, engine_factory):
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        held = {
            b.availability_slot_id
            for b in session.exec(select(Booking).where(Booking.status == "confirmed")).all()
        }
        booked = {
            s.id
            for s in session.exec(
                select(AvailabilitySlot).where(AvailabilitySlot.status == "booked")
            ).all()
        }

    assert booked == held


def test_bookings_agree_with_their_slot_on_time_agent_and_property(tmp_path, engine_factory):
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        for booking in session.exec(select(Booking)).all():
            slot = session.get(AvailabilitySlot, booking.availability_slot_id)
            assert (slot.slot_time, slot.agent_id, slot.property_id) == (
                booking.slot_time,
                booking.agent_id,
                booking.property_id,
            ), booking.id


def test_booking_participants_hold_the_roles_their_columns_imply(tmp_path, engine_factory):
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        for booking in session.exec(select(Booking)).all():
            assert session.get(User, booking.client_id).role == "client", booking.id
            assert session.get(User, booking.agent_id).role == "agent", booking.id


def test_listings_are_owned_by_users_holding_the_agent_role(tmp_path, engine_factory):
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        for listing in session.exec(select(Property)).all():
            if listing.agent_id is not None:
                assert session.get(User, listing.agent_id).role == "agent", listing.id


def test_seed_covers_every_property_status(tmp_path, engine_factory):
    """Non-active listings are the fixtures for `property_not_bookable`."""
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))

    with Session(engine) as session:
        statuses = {p.status for p in session.exec(select(Property)).all()}

    assert {"active", "draft", "sold", "under_offer"} <= statuses


# --- idempotency and the kill switch ------------------------------------------


def test_seed_is_idempotent(tmp_path, engine_factory):
    settings = make_db_settings(tmp_path)
    engine = engine_factory()

    seed_if_empty(engine, settings)
    first = _counts(engine)
    seed_if_empty(engine, settings)
    seed_if_empty(engine, settings)

    assert _counts(engine) == first


def test_seed_noops_when_disabled(tmp_path, engine_factory):
    engine = engine_factory(seed_on_startup=False)

    seed_if_empty(engine, make_db_settings(tmp_path, seed_on_startup=False))

    assert _counts(engine)["users"] == 0


def test_seed_noops_on_a_non_empty_database_even_when_enabled(tmp_path, engine_factory):
    """The emptiness check, not the flag, is the real guard — an operator who leaves
    SEED_ON_STARTUP=true must not get duplicate rows on every restart."""
    settings = make_db_settings(tmp_path, seed_on_startup=True)
    engine = engine_factory(seed_on_startup=True)
    with Session(engine) as session:
        session.add(
            User(
                id="u-preexisting",
                name="Pre Existing",
                email="pre@evdekimi.test",
                role="admin",
                hashed_password="x",
            )
        )
        session.commit()

    seed_if_empty(engine, settings)

    assert _counts(engine) == {"users": 1, "properties": 0, "slots": 0, "bookings": 0}


def test_disabled_seed_leaves_an_already_seeded_database_alone(tmp_path, engine_factory):
    engine = engine_factory()
    seed_if_empty(engine, make_db_settings(tmp_path))
    before = _counts(engine)

    seed_if_empty(engine, make_db_settings(tmp_path, seed_on_startup=False))

    assert _counts(engine) == before


# --- startup wiring -----------------------------------------------------------


def test_app_startup_creates_tables_and_seeds(tmp_path):
    settings = make_db_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        counts = _counts(app.state.engine)

    assert counts["users"] == len(_seed_file("users.json"))


def test_app_startup_honors_the_seed_kill_switch(tmp_path):
    settings = make_db_settings(tmp_path, seed_on_startup=False)
    app = create_app(settings)

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        counts = _counts(app.state.engine)

    assert counts["users"] == 0


def test_restarting_the_app_does_not_duplicate_rows(tmp_path):
    settings = make_db_settings(tmp_path)

    with TestClient(create_app(settings)):
        pass
    app = create_app(settings)
    with TestClient(app):
        counts = _counts(app.state.engine)

    assert counts["users"] == len(_seed_file("users.json"))
