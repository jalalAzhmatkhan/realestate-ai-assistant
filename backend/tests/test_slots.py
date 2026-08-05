"""app/booking/slots.py — the single booking-conflict implementation (README §9).

All three booking write paths (`BookViewing`, `RescheduleViewing`, and
`POST /bookings/{id}/reschedule`) will call this module, so a defect here is a
data-integrity bug on every surface at once. Tests assert against the README's
failure-mode tables — the stable `code` strings and the `suggested_alternatives`
shape — not against whatever the implementation currently happens to return.

`now` is passed explicitly to every call and the fixture times are built relative
to it, so these tests do not start failing when the seeded August 2026 slots fall
into the past.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.booking import slots as slots_module
from app.booking import (
    BookingDomainError,
    BookingNotCancellableError,
    BookingNotFoundError,
    BookingNotReschedulableError,
    BookingSlotConflictError,
    PropertyNotBookableError,
    PropertyNotFoundError,
    RescheduleResult,
    SlotConflictError,
    SlotSuggestion,
    SlotTimeInPastError,
    SlotUnavailableError,
    SlotUnchangedError,
    cancel_booking,
    create_booking,
    find_alternative_slots,
    reschedule_booking,
    resolve_slot,
)
from app.db.session import build_engine, create_tables
from app.models import AvailabilitySlot, Booking, Property, User

from .conftest import make_db_settings

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)

T1 = NOW + timedelta(days=1)
T2 = NOW + timedelta(days=2)
T3 = NOW + timedelta(days=3)
T4 = NOW + timedelta(days=4)
T_TAKEN = NOW + timedelta(days=5)
T_PAST = NOW - timedelta(days=1)
T_UNLISTED = NOW + timedelta(days=9)

ACTIVE = "prop-active"
ORPHAN = "prop-orphan"
AGENT = "u-agent-1"
CLIENT = "u-client-1"
OTHER_CLIENT = "u-client-2"


def _user(user_id: str, role: str) -> User:
    return User(
        id=user_id,
        name=user_id,
        email=f"{user_id}@evdekimi.test",
        role=role,
        hashed_password="x",
    )


def _property(property_id: str, *, status: str, agent_id: str | None) -> Property:
    return Property(
        id=property_id,
        title=property_id,
        property_type="apartment",
        listing_type="rent",
        price=1_000_000,
        price_unit="per_month",
        bedrooms=2,
        bathrooms=1,
        area_sqm=60,
        address="Jl. Test 1",
        city="Jakarta",
        latitude=-6.2,
        longitude=106.8,
        amenities=[],
        agent_id=agent_id,
        status=status,
        description=property_id,
        listed_date=NOW.date(),
    )


def _slot(slot_id: str, property_id: str, slot_time: datetime, status: str = "open") -> AvailabilitySlot:
    return AvailabilitySlot(
        id=slot_id,
        agent_id=AGENT,
        property_id=property_id,
        slot_time=slot_time,
        status=status,
    )


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(make_db_settings(tmp_path, seed_on_startup=False))
    create_tables(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    """A world built from scratch rather than from seed_data, so the fixture times
    stay in the future relative to NOW and the null-`agent_id` property (which no
    seeded row provides) exists."""
    with Session(engine) as session:
        session.add_all(
            [
                _user("u-admin-1", "admin"),
                _user(AGENT, "agent"),
                _user(CLIENT, "client"),
                _user(OTHER_CLIENT, "client"),
            ]
        )
        session.flush()
        session.add_all(
            [
                _property(ACTIVE, status="active", agent_id=AGENT),
                _property("prop-draft", status="draft", agent_id=AGENT),
                _property("prop-sold", status="sold", agent_id=AGENT),
                _property("prop-under-offer", status="under_offer", agent_id=AGENT),
                _property(ORPHAN, status="active", agent_id=None),
            ]
        )
        session.flush()
        session.add_all(
            [
                _slot("slot-1", ACTIVE, T1),
                _slot("slot-2", ACTIVE, T2),
                _slot("slot-3", ACTIVE, T3),
                _slot("slot-4", ACTIVE, T4),
                _slot("slot-taken", ACTIVE, T_TAKEN, status="booked"),
                _slot("slot-past", ACTIVE, T_PAST),
                _slot("slot-draft", "prop-draft", T1),
                _slot("slot-sold", "prop-sold", T1),
                _slot("slot-under-offer", "prop-under-offer", T1),
                _slot("slot-orphan", ORPHAN, T1),
            ]
        )
        session.flush()
        session.add(
            Booking(
                id="booking-taken",
                availability_slot_id="slot-taken",
                property_id=ACTIVE,
                client_id=OTHER_CLIENT,
                agent_id=AGENT,
                slot_time=T_TAKEN,
                status="confirmed",
            )
        )
        session.commit()
        yield session


def _slot_status(db: Session, slot_id: str) -> str:
    db.expire_all()
    return db.get(AvailabilitySlot, slot_id).status


def _confirmed_bookings(db: Session, slot_id: str) -> list[Booking]:
    db.expire_all()
    return list(
        db.exec(
            select(Booking).where(
                Booking.availability_slot_id == slot_id, Booking.status == "confirmed"
            )
        ).all()
    )


def _book(db: Session, *, client_id: str = CLIENT, slot_time: datetime = T1, property_id: str = ACTIVE):
    return create_booking(
        db,
        property_id=property_id,
        client_id=client_id,
        requested_slot_time=slot_time,
        now=NOW,
    )


# --- exception taxonomy -------------------------------------------------------


@pytest.mark.parametrize(
    "error, code",
    [
        (BookingNotFoundError, "booking_not_found"),
        (BookingNotReschedulableError, "booking_not_reschedulable"),
        (BookingNotCancellableError, "booking_not_cancellable"),
        (PropertyNotFoundError, "property_not_found"),
        (PropertyNotBookableError, "property_not_bookable"),
        (SlotTimeInPastError, "slot_time_in_past"),
        (SlotUnchangedError, "slot_unchanged"),
        (SlotUnavailableError, "slot_unavailable"),
        (BookingSlotConflictError, "booking_slot_conflict"),
    ],
)
def test_error_codes_match_the_readme_failure_tables(error, code):
    """Clients branch on `code`, so a rename here silently breaks both surfaces."""
    assert error.code == code
    assert issubclass(error, BookingDomainError)


def test_conflict_errors_share_the_suggested_alternatives_carrier():
    assert issubclass(SlotUnavailableError, SlotConflictError)
    assert issubclass(BookingSlotConflictError, SlotConflictError)


def test_non_conflict_errors_carry_no_details():
    assert SlotTimeInPastError().details == {}


def test_conflict_details_render_the_contractual_alternative_shape():
    error = BookingSlotConflictError(
        (SlotSuggestion(availability_slot_id="slot-2", slot_time=T2),)
    )

    assert error.details == {
        "suggested_alternatives": [{"availability_slot_id": "slot-2", "slot_time": T2}]
    }


def test_domain_errors_expose_a_default_message_and_accept_an_override():
    assert SlotUnchangedError().message == SlotUnchangedError.default_message
    assert SlotUnchangedError("custom").message == "custom"


# --- find_alternative_slots ---------------------------------------------------


def test_alternatives_are_the_soonest_open_future_slots_capped_at_three(db):
    found = find_alternative_slots(db, agent_id=AGENT, property_id=ACTIVE, now=NOW)

    assert [s.availability_slot_id for s in found] == ["slot-1", "slot-2", "slot-3"]
    assert [s.slot_time for s in found] == [T1, T2, T3]


def test_alternatives_exclude_past_and_already_booked_slots(db):
    found = find_alternative_slots(
        db, agent_id=AGENT, property_id=ACTIVE, now=NOW, limit=99
    )

    ids = {s.availability_slot_id for s in found}
    assert "slot-past" not in ids
    assert "slot-taken" not in ids


def test_alternatives_never_cross_property_or_agent(db):
    found = find_alternative_slots(
        db, agent_id=AGENT, property_id="prop-draft", now=NOW, limit=99
    )

    assert [s.availability_slot_id for s in found] == ["slot-draft"]
    assert find_alternative_slots(db, agent_id="u-agent-2", property_id=ACTIVE, now=NOW) == ()


def test_no_open_slots_yields_no_alternatives_rather_than_an_error(db):
    for slot in db.exec(
        select(AvailabilitySlot).where(AvailabilitySlot.property_id == ACTIVE)
    ).all():
        slot.status = "booked"
        db.add(slot)
    db.commit()

    assert find_alternative_slots(db, agent_id=AGENT, property_id=ACTIVE, now=NOW) == ()


def test_alternative_suggestions_are_frozen(db):
    suggestion = find_alternative_slots(db, agent_id=AGENT, property_id=ACTIVE, now=NOW)[0]

    with pytest.raises(Exception):
        suggestion.availability_slot_id = "tampered"


# --- resolve_slot -------------------------------------------------------------


def test_resolve_slot_returns_the_open_slot_at_that_time(db):
    slot = resolve_slot(
        db, agent_id=AGENT, property_id=ACTIVE, requested_slot_time=T1, now=NOW
    )

    assert slot.id == "slot-1"


def test_resolve_slot_separates_unavailable_from_already_booked(db):
    with pytest.raises(SlotUnavailableError) as unavailable:
        resolve_slot(
            db, agent_id=AGENT, property_id=ACTIVE, requested_slot_time=T_UNLISTED, now=NOW
        )
    with pytest.raises(BookingSlotConflictError) as taken:
        resolve_slot(
            db, agent_id=AGENT, property_id=ACTIVE, requested_slot_time=T_TAKEN, now=NOW
        )

    assert unavailable.value.suggested_alternatives
    assert taken.value.suggested_alternatives


def test_resolve_slot_reads_a_naive_datetime_as_utc(db):
    slot = resolve_slot(
        db,
        agent_id=AGENT,
        property_id=ACTIVE,
        requested_slot_time=T1.replace(tzinfo=None),
        now=NOW,
    )

    assert slot.id == "slot-1"


# --- create_booking: happy path -----------------------------------------------


def test_create_booking_claims_the_slot_and_returns_a_confirmed_booking(db):
    booking = _book(db)

    assert booking.status == "confirmed"
    assert booking.availability_slot_id == "slot-1"
    assert booking.property_id == ACTIVE
    assert booking.client_id == CLIENT
    assert booking.agent_id == AGENT
    assert booking.slot_time == T1
    assert booking.rescheduled_count == 0
    assert _slot_status(db, "slot-1") == "booked"


def test_create_booking_persists_exactly_one_row(db):
    booking = _book(db)

    assert [b.id for b in _confirmed_bookings(db, "slot-1")] == [booking.id]


def test_create_booking_accepts_a_naive_datetime_as_utc(db):
    booking = _book(db, slot_time=T1.replace(tzinfo=None))

    assert booking.slot_time == T1


# --- create_booking: idempotency (ruling A5) ----------------------------------


def test_resubmitting_the_same_request_returns_the_same_booking(db):
    first = _book(db)
    second = _book(db)
    third = _book(db)

    assert second.id == first.id
    assert third.id == first.id


def test_resubmission_does_not_create_a_second_row(db):
    _book(db)
    _book(db)
    _book(db)

    assert len(_confirmed_bookings(db, "slot-1")) == 1


def test_resubmission_leaves_the_slot_booked_exactly_once(db):
    _book(db)
    _book(db)

    assert _slot_status(db, "slot-1") == "booked"


def test_idempotency_is_scoped_to_the_client_not_the_slot(db):
    """The dedupe must not hand another client's booking back to a stranger."""
    mine = _book(db, client_id=CLIENT)

    with pytest.raises(BookingSlotConflictError) as conflict:
        _book(db, client_id=OTHER_CLIENT)

    assert conflict.value.code == "booking_slot_conflict"
    assert [b.id for b in _confirmed_bookings(db, "slot-1")] == [mine.id]


def test_a_conflict_offers_up_to_three_alternatives_in_the_contract_shape(db):
    _book(db, client_id=CLIENT)

    with pytest.raises(BookingSlotConflictError) as conflict:
        _book(db, client_id=OTHER_CLIENT)

    alternatives = conflict.value.suggested_alternatives
    assert 0 < len(alternatives) <= 3
    assert all(isinstance(a, SlotSuggestion) for a in alternatives)
    assert [a.availability_slot_id for a in alternatives] == ["slot-2", "slot-3", "slot-4"]
    assert conflict.value.details["suggested_alternatives"][0].keys() == {
        "availability_slot_id",
        "slot_time",
    }


def test_a_conflict_never_suggests_the_slot_it_just_refused(db):
    _book(db, client_id=CLIENT)

    with pytest.raises(BookingSlotConflictError) as conflict:
        _book(db, client_id=OTHER_CLIENT)

    assert "slot-1" not in {a.availability_slot_id for a in conflict.value.suggested_alternatives}


def test_a_conflict_writes_nothing(db):
    _book(db, client_id=CLIENT)
    before = len(list(db.exec(select(Booking)).all()))

    with pytest.raises(BookingSlotConflictError):
        _book(db, client_id=OTHER_CLIENT)

    db.expire_all()
    assert len(list(db.exec(select(Booking)).all())) == before


# --- create_booking: cancel then rebook (the dedupe predicate's sharp edge) ----


def test_rebooking_a_cancelled_slot_produces_a_genuinely_new_booking(db):
    original = _book(db)
    original_id = original.id
    cancel_booking(db, original)

    rebooked = _book(db)

    assert rebooked.id != original_id


def test_rebooking_after_cancellation_leaves_the_cancelled_row_cancelled(db):
    original = _book(db)
    original_id = original.id
    cancel_booking(db, original)

    _book(db)

    db.expire_all()
    assert db.get(Booking, original_id).status == "cancelled"


def test_rebooking_after_cancellation_yields_one_cancelled_and_one_confirmed_row(db):
    original = _book(db)
    cancel_booking(db, original)
    rebooked = _book(db)

    db.expire_all()
    rows = db.exec(select(Booking).where(Booking.availability_slot_id == "slot-1")).all()

    assert {b.id: b.status for b in rows} == {
        original.id: "cancelled",
        rebooked.id: "confirmed",
    }


def test_rebooking_after_cancellation_reclaims_the_slot(db):
    original = _book(db)
    cancel_booking(db, original)
    assert _slot_status(db, "slot-1") == "open"

    _book(db)

    assert _slot_status(db, "slot-1") == "booked"


def test_a_cancelled_booking_does_not_block_another_client(db):
    cancel_booking(db, _book(db, client_id=CLIENT))

    taken_over = _book(db, client_id=OTHER_CLIENT)

    assert taken_over.client_id == OTHER_CLIENT
    assert _slot_status(db, "slot-1") == "booked"


# --- create_booking: validation failures --------------------------------------


def test_a_past_requested_time_is_rejected(db):
    with pytest.raises(SlotTimeInPastError) as error:
        _book(db, slot_time=T_PAST)

    assert error.value.code == "slot_time_in_past"


def test_the_current_instant_counts_as_past(db):
    with pytest.raises(SlotTimeInPastError):
        _book(db, slot_time=NOW)


def test_a_time_outside_the_agents_availability_is_slot_unavailable(db):
    with pytest.raises(SlotUnavailableError) as error:
        _book(db, slot_time=T_UNLISTED)

    assert error.value.code == "slot_unavailable"
    assert [a.availability_slot_id for a in error.value.suggested_alternatives] == [
        "slot-1",
        "slot-2",
        "slot-3",
    ]


def test_an_unknown_property_is_property_not_found(db):
    with pytest.raises(PropertyNotFoundError) as error:
        _book(db, property_id="prop-does-not-exist")

    assert error.value.code == "property_not_found"


@pytest.mark.parametrize(
    "property_id, slot_time",
    [("prop-draft", T1), ("prop-sold", T1), ("prop-under-offer", T1)],
)
def test_a_non_active_property_is_not_bookable(db, property_id, slot_time):
    with pytest.raises(PropertyNotBookableError) as error:
        _book(db, property_id=property_id, slot_time=slot_time)

    assert error.value.code == "property_not_bookable"


def test_a_property_with_no_agent_is_not_bookable(db):
    """`Booking.agent_id` is non-nullable and nobody could conduct the viewing."""
    with pytest.raises(PropertyNotBookableError):
        _book(db, property_id=ORPHAN)


def test_an_unbookable_property_leaves_its_slot_open(db):
    with pytest.raises(PropertyNotBookableError):
        _book(db, property_id="prop-sold")

    assert _slot_status(db, "slot-sold") == "open"


# --- create_booking: the slot claim is the concurrency guard ------------------


def test_only_one_of_two_clients_can_take_the_same_slot(db):
    first = _book(db, client_id=CLIENT)

    with pytest.raises(BookingSlotConflictError):
        _book(db, client_id=OTHER_CLIENT)

    assert _slot_status(db, "slot-1") == "booked"
    assert [b.id for b in _confirmed_bookings(db, "slot-1")] == [first.id]


def test_a_claim_lost_after_the_availability_check_still_conflicts(db, monkeypatch):
    """Simulates the read-then-write race: the check passed, then a concurrent
    claimer won. The conditional UPDATE must catch it, not the earlier check."""
    _book(db, client_id=CLIENT)
    stale = db.get(AvailabilitySlot, "slot-1")
    monkeypatch.setattr(
        slots_module, "_assert_claimable", lambda *args, **kwargs: stale
    )

    with pytest.raises(BookingSlotConflictError):
        create_booking(
            db,
            property_id=ACTIVE,
            client_id=OTHER_CLIENT,
            requested_slot_time=T1,
            now=NOW,
        )

    assert _slot_status(db, "slot-1") == "booked"
    assert len(_confirmed_bookings(db, "slot-1")) == 1


def test_a_failed_insert_rolls_the_slot_claim_back(db):
    """The claim and the insert are one transaction: a booking that never persisted
    must not leave the slot advertised as taken."""
    with pytest.raises(Exception):
        create_booking(
            db,
            property_id=ACTIVE,
            client_id="u-does-not-exist",
            requested_slot_time=T1,
            now=NOW,
        )

    db.rollback()
    assert _slot_status(db, "slot-1") == "open"
    assert _confirmed_bookings(db, "slot-1") == []


# --- reschedule_booking: happy path -------------------------------------------


def test_reschedule_keeps_the_booking_id(db):
    booking = _book(db)
    original_id = booking.id

    result = reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)

    assert isinstance(result, RescheduleResult)
    assert result.booking.id == original_id


def test_reschedule_moves_the_booking_row(db):
    booking = _book(db)

    reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)

    db.expire_all()
    moved = db.get(Booking, booking.id)
    assert moved.slot_time == T2
    assert moved.availability_slot_id == "slot-2"
    assert moved.status == "confirmed"


def test_reschedule_increments_rescheduled_count(db):
    booking = _book(db)

    reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)
    reschedule_booking(db, booking, requested_slot_time=T3, now=NOW)

    db.expire_all()
    assert db.get(Booking, booking.id).rescheduled_count == 2


def test_reschedule_releases_the_old_slot_and_claims_the_new_one(db):
    booking = _book(db)

    reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)

    assert _slot_status(db, "slot-1") == "open"
    assert _slot_status(db, "slot-2") == "booked"


def test_reschedule_reports_what_it_moved_from(db):
    booking = _book(db)

    result = reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)

    assert result.previous_slot_time == T1
    assert result.previous_availability_slot_id == "slot-1"


def test_a_released_slot_is_bookable_by_someone_else(db):
    booking = _book(db, client_id=CLIENT)
    reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)

    taken_over = _book(db, client_id=OTHER_CLIENT, slot_time=T1)

    assert taken_over.availability_slot_id == "slot-1"


# --- reschedule_booking: failures ---------------------------------------------


def test_rescheduling_to_the_same_time_is_slot_unchanged(db):
    booking = _book(db)

    with pytest.raises(SlotUnchangedError) as error:
        reschedule_booking(db, booking, requested_slot_time=T1, now=NOW)

    assert error.value.code == "slot_unchanged"


def test_slot_unchanged_writes_nothing(db):
    """README: 'no write occurs'. A bumped `updated_at` would be a phantom edit in
    the audit trail for a request that changed nothing."""
    booking = _book(db)
    db.expire_all()
    before = db.get(Booking, booking.id)
    snapshot = (before.updated_at, before.rescheduled_count, before.availability_slot_id)

    with pytest.raises(SlotUnchangedError):
        reschedule_booking(db, booking, requested_slot_time=T1, now=NOW)

    db.expire_all()
    after = db.get(Booking, booking.id)
    assert (after.updated_at, after.rescheduled_count, after.availability_slot_id) == snapshot
    assert _slot_status(db, "slot-1") == "booked"


def test_rescheduling_a_cancelled_booking_is_rejected(db):
    booking = _book(db)
    cancel_booking(db, booking)

    with pytest.raises(BookingNotReschedulableError) as error:
        reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)

    assert error.value.code == "booking_not_reschedulable"


def test_rescheduling_a_completed_booking_is_rejected(db):
    booking = _book(db)
    booking.status = "completed"
    db.add(booking)
    db.commit()

    with pytest.raises(BookingNotReschedulableError):
        reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)


def test_rescheduling_a_booking_whose_slot_already_passed_is_rejected(db):
    """A viewing that already happened is treated as completed."""
    booking = _book(db)

    with pytest.raises(BookingNotReschedulableError):
        reschedule_booking(
            db, booking, requested_slot_time=T3, now=T2 + timedelta(hours=1)
        )


def test_rescheduling_into_the_past_is_rejected(db):
    booking = _book(db)

    with pytest.raises(SlotTimeInPastError):
        reschedule_booking(db, booking, requested_slot_time=T_PAST, now=NOW)


def test_rescheduling_onto_someone_elses_booking_conflicts_with_alternatives(db):
    booking = _book(db)

    with pytest.raises(BookingSlotConflictError) as error:
        reschedule_booking(db, booking, requested_slot_time=T_TAKEN, now=NOW)

    assert error.value.code == "booking_slot_conflict"
    assert 0 < len(error.value.suggested_alternatives) <= 3


def test_rescheduling_to_a_time_with_no_slot_is_slot_unavailable(db):
    booking = _book(db)

    with pytest.raises(SlotUnavailableError) as error:
        reschedule_booking(db, booking, requested_slot_time=T_UNLISTED, now=NOW)

    assert error.value.code == "slot_unavailable"


def test_rescheduling_a_no_longer_active_property_is_rejected(db):
    booking = _book(db)
    listing = db.get(Property, ACTIVE)
    listing.status = "sold"
    db.add(listing)
    db.commit()

    with pytest.raises(PropertyNotBookableError) as error:
        reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)

    assert error.value.code == "property_not_bookable"


def test_a_failed_reschedule_leaves_the_original_booking_untouched(db):
    """The new slot is claimed before the old one is released, so a conflict must
    not strand the booking on a released slot."""
    booking = _book(db)

    with pytest.raises(BookingSlotConflictError):
        reschedule_booking(db, booking, requested_slot_time=T_TAKEN, now=NOW)

    db.expire_all()
    unchanged = db.get(Booking, booking.id)
    assert unchanged.availability_slot_id == "slot-1"
    assert unchanged.slot_time == T1
    assert unchanged.rescheduled_count == 0
    assert _slot_status(db, "slot-1") == "booked"
    assert _slot_status(db, "slot-taken") == "booked"


def test_a_failed_reschedule_does_not_disturb_the_other_clients_booking(db):
    booking = _book(db)

    with pytest.raises(BookingSlotConflictError):
        reschedule_booking(db, booking, requested_slot_time=T_TAKEN, now=NOW)

    db.expire_all()
    assert db.get(Booking, "booking-taken").status == "confirmed"


# --- cancel_booking -----------------------------------------------------------


def test_cancel_marks_the_booking_and_releases_the_slot(db):
    booking = _book(db)

    cancelled = cancel_booking(db, booking)

    assert cancelled.status == "cancelled"
    assert _slot_status(db, "slot-1") == "open"


def test_cancel_bumps_updated_at(db):
    booking = _book(db)
    db.expire_all()
    before = db.get(Booking, booking.id).updated_at

    cancel_booking(db, db.get(Booking, booking.id))

    db.expire_all()
    assert db.get(Booking, booking.id).updated_at >= before


def test_cancelling_twice_is_a_no_op(db):
    booking = _book(db)
    cancel_booking(db, booking)

    again = cancel_booking(db, booking)

    assert again.status == "cancelled"
    assert again.id == booking.id


def test_a_second_cancel_does_not_release_a_slot_someone_else_now_holds(db):
    """The idempotent branch must return early, not re-run the slot release —
    otherwise a stale tab would free the new holder's slot."""
    booking = _book(db, client_id=CLIENT)
    cancel_booking(db, booking)
    _book(db, client_id=OTHER_CLIENT)

    cancel_booking(db, booking)

    assert _slot_status(db, "slot-1") == "booked"


def test_cancelling_a_completed_booking_is_rejected(db):
    booking = _book(db)
    booking.status = "completed"
    db.add(booking)
    db.commit()

    with pytest.raises(BookingNotCancellableError) as error:
        cancel_booking(db, booking)

    assert error.value.code == "booking_not_cancellable"


def test_a_rejected_cancel_leaves_the_slot_booked(db):
    booking = _book(db)
    booking.status = "completed"
    db.add(booking)
    db.commit()

    with pytest.raises(BookingNotCancellableError):
        cancel_booking(db, booking)

    assert _slot_status(db, "slot-1") == "booked"


def test_cancel_then_reschedule_is_rejected_rather_than_resurrecting_the_booking(db):
    booking = _book(db)
    cancel_booking(db, booking)

    with pytest.raises(BookingNotReschedulableError):
        reschedule_booking(db, booking, requested_slot_time=T2, now=NOW)

    assert _slot_status(db, "slot-2") == "open"
