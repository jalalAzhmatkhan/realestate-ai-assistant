"""``/api/v1/bookings`` — list, cancel, reschedule.

Two things are being asserted here, and only one of them is ordinary REST behaviour:

1. The scoping posture. An out-of-scope booking is ``404`` on *every* verb, never
   ``403``, so booking ids cannot be enumerated by probing for a permission error.
2. **Cross-surface equivalence.** The router delegates to ``app/booking/slots.py``, the
   same module ``RescheduleViewing`` calls. "Delegates" is a claim about behaviour, so
   the last section drives a REST reschedule and a chat reschedule into conflicts of the
   same shape and diffs the two answers, rather than reading the imports and trusting
   them.

Fixture data is built with times relative to ``now`` rather than reused from
``seed_data/viewings.json``: the seeded slots carry fixed August 2026 dates, and a
reschedule test that silently starts asserting ``slot_time_in_past`` once those dates
elapse is worse than no test.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

from app.agent.deps import AgentDeps
from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import AvailabilitySlot, Booking, Property, User

from .conftest import make_db_settings, make_settings
from .test_agent import RecordingNotifier, run_tool
from .test_properties_api import bearer, code_of, cookie_login

BOOKINGS = "/api/v1/bookings"

ADMIN_EMAIL = "rina.admin@evdekimi.test"
AGENT1_EMAIL = "siti.agent@evdekimi.test"
AGENT2_EMAIL = "budi.agent@evdekimi.test"
CLIENT1_EMAIL = "andi.client@evdekimi.test"
CLIENT2_EMAIL = "maria.client@evdekimi.test"

ADMIN, AGENT1, AGENT2 = "u-admin-1", "u-agent-1", "u-agent-2"
CLIENT1, CLIENT2, CLIENT3 = "u-client-1", "u-client-2", "u-client-3"

# Both listings are `active` and belong to u-agent-1, and neither appears in
# seed_data/agent_availability.json — so the two surfaces below face structurally
# identical slot layouts built entirely here, with no seeded slot leaking into either
# one's `suggested_alternatives` and breaking the parity comparison.
REST_PROPERTY = "prop-006"
CHAT_PROPERTY = "prop-012"

NOW = datetime.now(timezone.utc)
T_HELD = NOW + timedelta(days=1)
T_FREE = NOW + timedelta(days=2)
T_SPARE = NOW + timedelta(days=3)
T_TAKEN = NOW + timedelta(days=4)
T_NO_SLOT = NOW + timedelta(days=10)


@pytest.fixture
def settings(tmp_path):
    settings = make_db_settings(tmp_path)
    create_tables(build_engine(settings))
    return settings


@pytest.fixture
def notifier():
    return RecordingNotifier()


@pytest.fixture
def client(settings, notifier):
    with TestClient(create_app(settings, notifier=notifier)) as test_client:
        _install_fixture_bookings(test_client.app.state.engine)
        yield test_client


def _slot_set(prefix: str, property_id: str) -> list[AvailabilitySlot]:
    """One held slot, two open ones to be offered as alternatives, and one taken by
    somebody else — the minimum layout that produces every conflict code."""
    return [
        AvailabilitySlot(
            id=f"{prefix}-held", agent_id=AGENT1, property_id=property_id,
            slot_time=T_HELD, status="booked",
        ),
        AvailabilitySlot(
            id=f"{prefix}-free", agent_id=AGENT1, property_id=property_id,
            slot_time=T_FREE, status="open",
        ),
        AvailabilitySlot(
            id=f"{prefix}-spare", agent_id=AGENT1, property_id=property_id,
            slot_time=T_SPARE, status="open",
        ),
        AvailabilitySlot(
            id=f"{prefix}-taken", agent_id=AGENT1, property_id=property_id,
            slot_time=T_TAKEN, status="booked",
        ),
    ]


def _install_fixture_bookings(engine) -> None:
    with Session(engine) as session:
        session.add_all(_slot_set("rest", REST_PROPERTY) + _slot_set("chat", CHAT_PROPERTY))
        session.flush()
        session.add_all(
            [
                Booking(
                    id="qa-rest-booking", availability_slot_id="rest-held",
                    property_id=REST_PROPERTY, client_id=CLIENT1, agent_id=AGENT1,
                    slot_time=T_HELD, status="confirmed",
                ),
                Booking(
                    id="qa-rest-blocker", availability_slot_id="rest-taken",
                    property_id=REST_PROPERTY, client_id=CLIENT3, agent_id=AGENT1,
                    slot_time=T_TAKEN, status="confirmed",
                ),
                Booking(
                    id="qa-chat-booking", availability_slot_id="chat-held",
                    property_id=CHAT_PROPERTY, client_id=CLIENT1, agent_id=AGENT1,
                    slot_time=T_HELD, status="confirmed",
                ),
                Booking(
                    id="qa-chat-blocker", availability_slot_id="chat-taken",
                    property_id=CHAT_PROPERTY, client_id=CLIENT3, agent_id=AGENT1,
                    slot_time=T_TAKEN, status="confirmed",
                ),
            ]
        )
        session.commit()


def ids_of(response) -> set[str]:
    return {item["booking_id"] for item in response.json()["results"]}


def reschedule(client, booking_id, when, headers, **body):
    return client.post(
        f"{BOOKINGS}/{booking_id}/reschedule",
        json={"requested_slot_time": when.isoformat(), **body},
        headers=headers,
    )


# ---------------------------------------------------------------------- list scope


def test_a_client_sees_only_their_own_bookings(client):
    response = client.get(BOOKINGS, headers=bearer(client, CLIENT1_EMAIL))

    assert response.status_code == 200, response.text
    for item in response.json()["results"]:
        assert item["client_id"] == CLIENT1
    assert "qa-rest-blocker" not in ids_of(response)


def test_an_agent_sees_only_bookings_they_conduct(client):
    response = client.get(BOOKINGS, headers=bearer(client, AGENT2_EMAIL))

    for item in response.json()["results"]:
        assert item["agent_id"] == AGENT2
    assert "qa-rest-booking" not in ids_of(response)


def test_an_admin_sees_every_booking(client):
    response = client.get(BOOKINGS, headers=bearer(client, ADMIN_EMAIL))

    assert {"qa-rest-booking", "qa-rest-blocker", "booking-002"} <= ids_of(response)


def test_a_client_filtering_by_another_client_id_gets_an_empty_page(client):
    """Filters intersect the scope; they never substitute for it."""
    response = client.get(
        BOOKINGS, params={"client_id": CLIENT2}, headers=bearer(client, CLIENT1_EMAIL)
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_an_agent_filtering_by_another_agent_id_gets_an_empty_page(client):
    response = client.get(
        BOOKINGS, params={"agent_id": AGENT2}, headers=bearer(client, AGENT1_EMAIL)
    )

    assert response.json()["total"] == 0


def test_status_and_property_filters_narrow_within_scope(client):
    response = client.get(
        BOOKINGS,
        params=[("status", "confirmed"), ("property_id", REST_PROPERTY)],
        headers=bearer(client, CLIENT1_EMAIL),
    )

    assert ids_of(response) == {"qa-rest-booking"}


def test_the_date_range_is_inclusive_at_both_ends(client):
    response = client.get(
        BOOKINGS,
        params={"date_from": T_HELD.isoformat(), "date_to": T_HELD.isoformat()},
        headers=bearer(client, CLIENT1_EMAIL),
    )

    assert ids_of(response) == {"qa-rest-booking", "qa-chat-booking"}


def test_an_inverted_date_range_is_rejected(client):
    response = client.get(
        BOOKINGS,
        params={"date_from": T_TAKEN.isoformat(), "date_to": T_HELD.isoformat()},
        headers=bearer(client, CLIENT1_EMAIL),
    )

    assert response.status_code == 422
    assert code_of(response) == "invalid_date_range"


def test_listing_bookings_requires_authentication(client):
    assert client.get(BOOKINGS).status_code == 401


# ---------------------------------------------------------------------- pagination


def test_page_size_above_the_ceiling_is_rejected(client, settings):
    response = client.get(
        BOOKINGS,
        params={"page_size": settings.max_page_size + 1},
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 422
    assert code_of(response) == "page_size_too_large"


def test_an_unknown_sort_field_is_rejected(client):
    response = client.get(
        BOOKINGS, params={"sort": "property_id"}, headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 422
    assert code_of(response) == "invalid_sort_field"


def test_a_page_past_the_end_is_empty_with_the_real_total(client):
    headers = bearer(client, ADMIN_EMAIL)
    total = client.get(BOOKINGS, headers=headers).json()["total"]

    response = client.get(BOOKINGS, params={"page": 50, "page_size": 2}, headers=headers)

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["total"] == total


def test_sorting_by_slot_time_honours_the_direction(client):
    headers = bearer(client, ADMIN_EMAIL)
    ascending = [
        item["slot_time"]
        for item in client.get(BOOKINGS, params={"sort": "slot_time"}, headers=headers).json()["results"]
    ]

    assert ascending == sorted(ascending)


# ------------------------------------------------------ single-item read: GET /{id}


@pytest.mark.parametrize("email", [CLIENT1_EMAIL, AGENT1_EMAIL, ADMIN_EMAIL])
def test_every_party_to_a_booking_can_read_it_by_id(client, email):
    """``qa-rest-booking`` is client-1's, conducted by agent-1 — the same scope that lets
    each of them cancel it lets each of them read it."""
    response = client.get(f"{BOOKINGS}/qa-rest-booking", headers=bearer(client, email))

    assert response.status_code == 200, response.text
    assert response.json()["booking_id"] == "qa-rest-booking"


@pytest.mark.parametrize(
    ("email", "booking_id"),
    [
        (CLIENT1_EMAIL, "qa-rest-blocker"),
        (CLIENT1_EMAIL, "booking-does-not-exist"),
        (CLIENT2_EMAIL, "qa-rest-booking"),
        (AGENT2_EMAIL, "qa-rest-booking"),
        (AGENT2_EMAIL, "booking-does-not-exist"),
        (ADMIN_EMAIL, "booking-does-not-exist"),
    ],
)
def test_an_out_of_scope_or_nonexistent_booking_reads_as_one_404(
    client, email, booking_id
):
    """Same posture as cancel and reschedule: never ``403``, so the read path does not
    become the enumeration oracle the write path closed."""
    response = client.get(f"{BOOKINGS}/{booking_id}", headers=bearer(client, email))

    assert response.status_code == 404
    assert code_of(response) == "booking_not_found"


def test_the_single_read_returns_the_same_shape_as_a_list_row(client):
    """Bookings have no summary/detail split — the list item type is already the whole
    record, so a detail fetch adds no fields."""
    headers = bearer(client, CLIENT1_EMAIL)
    row = next(
        item
        for item in client.get(BOOKINGS, headers=headers).json()["results"]
        if item["booking_id"] == "qa-rest-booking"
    )

    detail = client.get(f"{BOOKINGS}/qa-rest-booking", headers=headers).json()

    assert detail == row


def test_reading_one_booking_requires_authentication(client):
    assert client.get(f"{BOOKINGS}/qa-rest-booking").status_code == 401


# --------------------------------------------------- denormalized names on the response


NAME_FIELDS = ("property_title", "client_name", "agent_name")


def expected_names(engine, booking_id: str) -> dict[str, str]:
    """The names read straight from the referenced rows, so the assertions track the
    seed/fixture data instead of restating it."""
    with Session(engine) as session:
        booking = session.get(Booking, booking_id)
        return {
            "property_title": session.get(Property, booking.property_id).title,
            "client_name": session.get(User, booking.client_id).name,
            "agent_name": session.get(User, booking.agent_id).name,
        }


def names_in(payload: dict) -> dict[str, str]:
    return {field: payload[field] for field in NAME_FIELDS}


@pytest.mark.parametrize("email", [CLIENT1_EMAIL, AGENT1_EMAIL, ADMIN_EMAIL])
def test_a_booking_read_carries_the_property_and_party_names(client, email):
    """A ``client``/``agent`` cannot call ``GET /users/{id}`` to resolve these ids, so the
    booking payload is their only source for the names their screens render."""
    response = client.get(f"{BOOKINGS}/qa-rest-booking", headers=bearer(client, email))

    assert response.status_code == 200, response.text
    assert names_in(response.json()) == expected_names(
        client.app.state.engine, "qa-rest-booking"
    )


def test_every_listed_row_carries_the_names_of_its_own_booking(client):
    """Resolved in one batch for the whole page — this is what catches a row/name
    mismatch, which a single-row assertion cannot see."""
    response = client.get(
        BOOKINGS, params={"page_size": 50}, headers=bearer(client, ADMIN_EMAIL)
    )
    results = response.json()["results"]

    assert len(results) > 1
    for item in results:
        assert names_in(item) == expected_names(
            client.app.state.engine, item["booking_id"]
        )


def test_cancel_answers_with_the_names_too(client):
    expected = expected_names(client.app.state.engine, "qa-rest-booking")

    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/cancel", headers=bearer(client, CLIENT1_EMAIL)
    )

    assert response.status_code == 200, response.text
    assert names_in(response.json()) == expected


def test_reschedule_answers_with_the_names_too(client):
    """``BookingRescheduleResponse`` extends ``BookingResponse``, so the move keeps every
    field the list row has, ``previous_slot_time`` aside."""
    expected = expected_names(client.app.state.engine, "qa-rest-booking")

    response = reschedule(
        client, "qa-rest-booking", T_FREE, bearer(client, CLIENT1_EMAIL)
    )

    assert response.status_code == 200, response.text
    assert names_in(response.json()) == expected


def admin_page(client) -> dict[str, dict]:
    response = client.get(
        BOOKINGS, params={"page_size": 50}, headers=bearer(client, ADMIN_EMAIL)
    )
    return {item["booking_id"]: item for item in response.json()["results"]}


def test_two_rows_on_one_property_share_its_title_without_leaking_it_to_a_third(client):
    """A property has many viewings, so one page routinely holds several rows pointing at
    the same listing — the case a per-row assertion passes on by accident."""
    with Session(client.app.state.engine) as session:
        title = session.get(Property, REST_PROPERTY).title
    page = admin_page(client)

    assert page["qa-rest-booking"]["property_title"] == title
    assert page["qa-rest-blocker"]["property_title"] == title
    assert page["booking-002"]["property_id"] != REST_PROPERTY
    assert page["booking-002"]["property_title"] != title


def test_a_user_who_is_a_client_on_one_row_and_an_agent_on_another_is_not_crossed(
    client,
):
    """The client and agent ids are resolved through a single union query, so a user
    occupying both roles across a page must land in the right column on each row. No
    seeded booking produces this, hence the fixture row."""
    with Session(client.app.state.engine) as session:
        session.add(
            AvailabilitySlot(
                id="qa-dual-slot", agent_id=AGENT1, property_id=REST_PROPERTY,
                slot_time=NOW + timedelta(days=6), status="booked",
            )
        )
        session.flush()
        session.add(
            Booking(
                id="qa-dual-booking", availability_slot_id="qa-dual-slot",
                property_id=REST_PROPERTY, client_id=AGENT2, agent_id=AGENT1,
                slot_time=NOW + timedelta(days=6), status="confirmed",
            )
        )
        agent1_name = session.get(User, AGENT1).name
        agent2_name = session.get(User, AGENT2).name
        session.commit()

    page = admin_page(client)

    # u-agent-2 is this row's *client*, and seeded booking-002's *agent*
    assert page["qa-dual-booking"]["client_name"] == agent2_name
    assert page["qa-dual-booking"]["agent_name"] == agent1_name
    assert page["booking-002"]["agent_name"] == agent2_name
    assert page["booking-002"]["client_name"] != agent2_name


BOOKING_READS = {
    "list": lambda c, h: next(
        item
        for item in c.get(BOOKINGS, headers=h).json()["results"]
        if item["booking_id"] == "qa-rest-booking"
    ),
    "get": lambda c, h: c.get(f"{BOOKINGS}/qa-rest-booking", headers=h).json(),
    "cancel": lambda c, h: c.post(f"{BOOKINGS}/qa-rest-booking/cancel", headers=h).json(),
    "reschedule": lambda c, h: reschedule(c, "qa-rest-booking", T_FREE, h).json(),
}


@pytest.mark.parametrize("read", BOOKING_READS.values(), ids=list(BOOKING_READS))
def test_the_names_are_read_at_query_time_not_copied_onto_the_booking(client, read):
    """Renaming the referenced rows *after* the booking exists must change every
    endpoint's answer — the moment these become stored columns instead, this fails."""
    with Session(client.app.state.engine) as session:
        session.get(Property, REST_PROPERTY).title = "Renamed Listing"
        session.get(User, CLIENT1).name = "Renamed Client"
        session.get(User, AGENT1).name = "Renamed Agent"
        session.commit()

    payload = read(client, bearer(client, CLIENT1_EMAIL))

    assert names_in(payload) == {
        "property_title": "Renamed Listing",
        "client_name": "Renamed Client",
        "agent_name": "Renamed Agent",
    }
    # a reschedule moves the slot, never the parties whose names these are
    assert payload["property_id"] == REST_PROPERTY
    assert payload["client_id"] == CLIENT1
    assert payload["agent_id"] == AGENT1


@pytest.mark.parametrize("column", ["property_id", "client_id", "agent_id"])
def test_a_booking_pointing_at_a_missing_row_fails_loudly(client, settings, column, caplog):
    """The name maps are indexed directly, with no ``.get()`` fallback, because all three
    ids are non-null foreign keys. Should that invariant ever break — a bad migration, a
    manual ``DELETE`` — the deliberate outcome is a logged ``500``, never a blank or
    wrong name silently rendered as fact. Written through a raw connection because the
    app engine enforces ``PRAGMA foreign_keys=ON`` (``app/db/session.py``)."""
    headers = bearer(client, ADMIN_EMAIL)
    connection = sqlite3.connect(settings.database_url.removeprefix("sqlite:///"))
    connection.execute(
        f"UPDATE bookings SET {column} = 'gone' WHERE id = 'qa-rest-booking'"
    )
    connection.commit()
    connection.close()

    with caplog.at_level(logging.ERROR):
        one = client.get(f"{BOOKINGS}/qa-rest-booking", headers=headers)
        listed = client.get(BOOKINGS, params={"page_size": 50}, headers=headers)

    assert one.status_code == 500 and listed.status_code == 500
    assert one.json()["detail"]["code"] == "internal_error"
    # the envelope stays opaque; the diagnosis goes to the log
    assert "gone" not in one.text
    assert any("KeyError" in record.exc_text for record in caplog.records if record.exc_text)


def test_the_names_cost_two_queries_for_the_whole_page(client):
    """The guard on the N+1 the batch exists to avoid: three lookups per row would pass
    every assertion above while quietly scaling with page size."""
    headers = bearer(client, ADMIN_EMAIL)  # log in outside the recording window
    statements: list[str] = []

    @event.listens_for(client.app.state.engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    client.get(BOOKINGS, params={"page_size": 50}, headers=headers)
    event.remove(client.app.state.engine, "before_cursor_execute", record)

    properties_read = [s for s in statements if "FROM properties" in s]
    users_read = [s for s in statements if "FROM users" in s]

    assert len(properties_read) == 1, properties_read
    # one for the authenticated caller, one for the page's whole client|agent union
    assert len(users_read) == 2, users_read


# ------------------------------------------------------- out-of-scope resolution: 404


@pytest.mark.parametrize("action", ["cancel", "reschedule"])
@pytest.mark.parametrize("booking_id", ["qa-rest-blocker", "booking-does-not-exist"])
def test_another_users_booking_and_a_nonexistent_one_are_one_answer(
    client, action, booking_id
):
    """``404 booking_not_found`` for both, never ``403`` — a permission error would
    confirm the id exists."""
    headers = bearer(client, CLIENT1_EMAIL)
    body = {"requested_slot_time": T_FREE.isoformat()} if action == "reschedule" else None

    response = client.post(f"{BOOKINGS}/{booking_id}/{action}", json=body, headers=headers)

    assert response.status_code == 404
    assert code_of(response) == "booking_not_found"


def test_an_out_of_scope_cancel_does_not_touch_the_booking(client):
    client.post(f"{BOOKINGS}/qa-rest-blocker/cancel", headers=bearer(client, CLIENT1_EMAIL))

    with Session(client.app.state.engine) as session:
        assert session.get(Booking, "qa-rest-blocker").status == "confirmed"


def test_an_agent_may_act_on_a_booking_they_conduct(client):
    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/cancel", headers=bearer(client, AGENT1_EMAIL)
    )

    assert response.status_code == 200, response.text


# -------------------------------------------------------------------------- cancel


def test_cancelling_releases_the_slot_and_marks_the_booking(client):
    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/cancel", headers=bearer(client, CLIENT1_EMAIL)
    )

    assert response.json()["status"] == "cancelled"
    with Session(client.app.state.engine) as session:
        assert session.get(AvailabilitySlot, "rest-held").status == "open"


def test_cancel_is_idempotent_and_notifies_exactly_once(client, notifier):
    """A double-click must not error — and must not fire a second cancellation
    notification at the user either."""
    headers = bearer(client, CLIENT1_EMAIL)

    first = client.post(f"{BOOKINGS}/qa-rest-booking/cancel", headers=headers)
    second = client.post(f"{BOOKINGS}/qa-rest-booking/cancel", headers=headers)

    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "cancelled"
    cancellations = [
        event for event in notifier.events if type(event).__name__ == "BookingCancelledEvent"
    ]
    assert len(cancellations) == 1


def test_cancelling_a_completed_booking_is_rejected(client):
    with Session(client.app.state.engine) as session:
        booking = session.get(Booking, "qa-rest-booking")
        booking.status = "completed"
        session.add(booking)
        session.commit()

    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/cancel", headers=bearer(client, CLIENT1_EMAIL)
    )

    assert response.status_code == 409
    assert code_of(response) == "booking_not_cancellable"


# ---------------------------------------------------------------------- reschedule


def test_a_reschedule_keeps_the_booking_id_and_reports_the_previous_time(client):
    response = reschedule(
        client, "qa-rest-booking", T_FREE, bearer(client, CLIENT1_EMAIL)
    )
    body = response.json()

    assert response.status_code == 200, response.text
    assert body["booking_id"] == "qa-rest-booking"
    assert body["rescheduled_count"] == 1
    assert datetime.fromisoformat(body["previous_slot_time"]) == T_HELD
    assert datetime.fromisoformat(body["slot_time"]) == T_FREE


def test_a_reschedule_swaps_the_slot_claims(client):
    reschedule(client, "qa-rest-booking", T_FREE, bearer(client, CLIENT1_EMAIL))

    with Session(client.app.state.engine) as session:
        assert session.get(AvailabilitySlot, "rest-held").status == "open"
        assert session.get(AvailabilitySlot, "rest-free").status == "booked"


def test_a_reschedule_publishes_the_rescheduled_event(client, notifier):
    reschedule(
        client, "qa-rest-booking", T_FREE, bearer(client, CLIENT1_EMAIL), reason="conflict"
    )

    events = [
        event for event in notifier.events if type(event).__name__ == "BookingRescheduledEvent"
    ]
    assert len(events) == 1
    assert events[0].reason == "conflict"
    assert events[0].booking_id == "qa-rest-booking"


def test_rescheduling_onto_the_same_time_is_rejected_without_a_write(client):
    response = reschedule(
        client, "qa-rest-booking", T_HELD, bearer(client, CLIENT1_EMAIL)
    )

    assert response.status_code == 422
    assert code_of(response) == "slot_unchanged"
    with Session(client.app.state.engine) as session:
        assert session.get(Booking, "qa-rest-booking").rescheduled_count == 0


def test_rescheduling_into_the_past_is_rejected(client):
    response = reschedule(
        client, "qa-rest-booking", NOW - timedelta(days=1), bearer(client, CLIENT1_EMAIL)
    )

    assert response.status_code == 422
    assert code_of(response) == "slot_time_in_past"


def test_rescheduling_a_cancelled_booking_is_rejected(client):
    headers = bearer(client, CLIENT1_EMAIL)
    client.post(f"{BOOKINGS}/qa-rest-booking/cancel", headers=headers)

    response = reschedule(client, "qa-rest-booking", T_FREE, headers)

    assert response.status_code == 409
    assert code_of(response) == "booking_not_reschedulable"


def test_a_reschedule_body_without_a_slot_time_is_a_field_level_422(client):
    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/reschedule",
        json={"reason": "no time given"},
        headers=bearer(client, CLIENT1_EMAIL),
    )

    assert response.status_code == 422


# -------------------------------------- conflicts render as 409, not an unhandled 500


def test_a_taken_slot_answers_409_with_serialized_alternatives(client):
    """Regression test for the ``jsonable_encoder`` defect: ``suggested_alternatives``
    carries ``datetime`` objects, and handing the raw payload to ``JSONResponse`` made
    every real conflict render as an unhandled ``500``."""
    response = reschedule(
        client, "qa-rest-booking", T_TAKEN, bearer(client, CLIENT1_EMAIL)
    )
    detail = response.json()["detail"]

    assert response.status_code == 409, response.text
    assert detail["code"] == "booking_slot_conflict"
    assert [item["availability_slot_id"] for item in detail["suggested_alternatives"]] == [
        "rest-free",
        "rest-spare",
    ]
    for item in detail["suggested_alternatives"]:
        # Round-trips as a datetime, i.e. it was encoded rather than stringified by
        # accident somewhere upstream.
        assert datetime.fromisoformat(item["slot_time"]).tzinfo is not None


def test_a_time_with_no_slot_at_all_answers_409_slot_unavailable(client):
    response = reschedule(
        client, "qa-rest-booking", T_NO_SLOT, bearer(client, CLIENT1_EMAIL)
    )
    detail = response.json()["detail"]

    assert response.status_code == 409, response.text
    assert detail["code"] == "slot_unavailable"
    assert len(detail["suggested_alternatives"]) == 2


def test_a_conflict_leaves_the_original_booking_untouched(client):
    reschedule(client, "qa-rest-booking", T_TAKEN, bearer(client, CLIENT1_EMAIL))

    with Session(client.app.state.engine) as session:
        booking = session.get(Booking, "qa-rest-booking")
        assert booking.availability_slot_id == "rest-held"
        assert booking.rescheduled_count == 0


def test_alternatives_never_include_a_past_slot(client):
    with Session(client.app.state.engine) as session:
        session.add(
            AvailabilitySlot(
                id="rest-elapsed", agent_id=AGENT1, property_id=REST_PROPERTY,
                slot_time=NOW - timedelta(days=2), status="open",
            )
        )
        session.commit()

    response = reschedule(
        client, "qa-rest-booking", T_TAKEN, bearer(client, CLIENT1_EMAIL)
    )

    offered = {
        item["availability_slot_id"]
        for item in response.json()["detail"]["suggested_alternatives"]
    }
    assert "rest-elapsed" not in offered


# ------------------------------------------------------------- cross-surface parity


def _chat_reschedule(engine, when, notifier):
    """The same move, driven through ``RescheduleViewing`` with a scripted model."""
    with Session(engine) as session:
        deps = AgentDeps(
            user=session.get(User, CLIENT1),
            db=session,
            conversation_id="conv-parity",
            rag=None,
            settings=make_settings(),
            notifier=notifier,
        )
        return run_tool(
            [
                [
                    (
                        "RescheduleViewing",
                        {
                            "booking_id": "qa-chat-booking",
                            "requested_slot_time": when.isoformat(),
                        },
                    )
                ],
                "done",
            ],
            deps,
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("when", "expected_code"),
    [(T_TAKEN, "booking_slot_conflict"), (T_NO_SLOT, "slot_unavailable")],
)
async def test_rest_and_chat_describe_the_same_conflict_identically(
    client, notifier, when, expected_code
):
    """Two different bookings, structurally identical slot layouts, one shared
    ``app/booking/slots.py``. The error code, message, and alternatives shape must match
    — a fork between the surfaces is the one class of bug here that corrupts data rather
    than returning a wrong response."""
    rest = reschedule(client, "qa-rest-booking", when, bearer(client, CLIENT1_EMAIL))
    chat = (await _chat_reschedule(client.app.state.engine, when, notifier)).error()

    rest_detail = rest.json()["detail"]

    assert rest_detail["code"] == chat["code"] == expected_code
    assert rest_detail["message"] == chat["message"]

    def shape(alternatives):
        return [
            (sorted(item), datetime.fromisoformat(item["slot_time"]))
            for item in alternatives
        ]

    assert shape(rest_detail["suggested_alternatives"]) == shape(
        chat["suggested_alternatives"]
    )


@pytest.mark.anyio
async def test_rest_and_chat_reschedules_produce_the_same_event_payload(client, notifier):
    """A downstream consumer must not be able to tell which surface moved the booking."""
    reschedule(client, "qa-rest-booking", T_FREE, bearer(client, CLIENT1_EMAIL))
    await _chat_reschedule(client.app.state.engine, T_FREE, notifier)

    rest_event, chat_event = [
        event for event in notifier.events if type(event).__name__ == "BookingRescheduledEvent"
    ]
    assert type(rest_event) is type(chat_event)
    assert vars(rest_event).keys() == vars(chat_event).keys()
    assert rest_event.client_id == chat_event.client_id == CLIENT1
    assert rest_event.rescheduled_count == chat_event.rescheduled_count == 1


# ---------------------------------------------------------------------------- CSRF


@pytest.mark.parametrize("action", ["cancel", "reschedule"])
def test_a_cookie_write_without_the_csrf_header_is_rejected(client, action):
    cookie_login(client, CLIENT1_EMAIL)

    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/{action}",
        json={"requested_slot_time": T_FREE.isoformat()},
    )

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_missing"


def test_a_cookie_write_with_the_wrong_csrf_token_is_rejected(client):
    cookie_login(client, CLIENT1_EMAIL)

    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/cancel", headers={"X-CSRF-Token": "wrong"}
    )

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_invalid"


def test_a_cookie_read_needs_no_csrf_header(client):
    cookie_login(client, CLIENT1_EMAIL)

    assert client.get(BOOKINGS).status_code == 200


def test_a_cookie_write_with_the_right_csrf_token_succeeds(client):
    csrf = cookie_login(client, CLIENT1_EMAIL)

    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/cancel", headers={"X-CSRF-Token": csrf}
    )

    assert response.status_code == 200, response.text


def test_a_bearer_write_needs_no_csrf_header(client):
    response = client.post(
        f"{BOOKINGS}/qa-rest-booking/cancel", headers=bearer(client, CLIENT1_EMAIL)
    )

    assert response.status_code == 200, response.text


def test_csrf_is_checked_before_scoping_leaks_an_id(client):
    """A cookie caller without a token must not learn whether the booking exists."""
    cookie_login(client, CLIENT1_EMAIL)

    response = client.post(f"{BOOKINGS}/booking-does-not-exist/cancel")

    assert code_of(response) == "csrf_token_missing"
