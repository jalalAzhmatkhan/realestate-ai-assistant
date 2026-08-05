"""The prose-only safety rules (QA task Q4).

Four behaviors the design treats as safety requirements are enforceable **only** as prose
— in ``SYSTEM_PROMPT``, in a tool's ``TOOL_DESCRIPTION``, and in the ``guidance`` field of
a failed tool result. Implementing any of them as code that inspects the model's next tool
call would be the pre-classification routing layer ``CLAUDE.md`` forbids as a hard
constraint, so there is nothing else in the system that catches a regression:

1. ``booking_ambiguous`` -> ask the user, never auto-select a candidate
   (``2026-08-05-reschedule-viewing-tool-contract.md`` decision #8)
2. ``booking_not_found`` -> reveal nothing about whether a particular booking exists
   (decision #5)
3. ``BookViewing`` vs. ``RescheduleViewing`` -> a contextual choice the descriptions are
   the only lever on (decision #12)
4. an escalation reply promises no response (escalation contract; one of the two defects
   the Phase 3 live smoke test found by hand)

Plus the second of those two live defects: a conflict carrying **no** alternatives must
not tell the model to present alternatives (``booking_common.py``).

A prompt-level rule cannot be proven by a mocked model — no ``FunctionModel`` can show
that a *real* model complies. What is asserted here is the other half, and the half that
regresses silently: that the scaffolding the model reads still carries the rule, and that
the payloads it reads do not invite the wrong behavior. The live confirmation that a real
model then complies is recorded in the Phase 3 implementation checkpoint.

Deliberately does not repeat the four exact strings
``test_agent.py::test_the_two_prose_only_safety_rules_are_present_in_the_prompt_and_descriptions``
already pins; this file asserts the rest of each rule's substance.
"""

import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.agent.prompt import SYSTEM_PROMPT
from app.agent.tools import (
    booking_common,
    escalate_to_human,
    reschedule_viewing,
    schedule_viewing,
)
from app.models import AvailabilitySlot, Booking, Property

from .test_agent import (  # noqa: F401  -- fixtures, consumed by pytest injection
    ACTIVE,
    AGENT,
    CLIENT,
    NOW,
    OTHER_CLIENT,
    T1,
    T2,
    T3,
    Run,
    _flatten,
    anyio_backend,
    db,
    engine,
    faq_index,
    make_deps,
    notifier,
    run_tool,
)
from app.agent.orchestrator import build_agent

# Second-person promises of a reply. Every one of these is a commitment the platform
# cannot keep: escalation writes a row, it does not page anyone. Applied to the tool's
# *output payloads* only — the prompt and the tool description legitimately contain these
# phrases inside prohibitions ("not 'shortly', not 'as soon as possible'").
FORBIDDEN_PROMISES = (
    "as soon as possible",
    "get back to you",
    "will contact you",
    "will call you",
    "will email you",
    "will be in touch",
    "will reach out",
    "someone will",
    "shortly",
    "expect a reply",
    "expect a response",
    "we will respond",
    "they will respond",
    "within 24",
    "business day",
)


# ------------------------------------------------------- 1. never auto-select on ambiguity


def _naive_retry_model(requested_slot_time) -> FunctionModel:
    """A model that does exactly the wrong thing: sees ``candidates`` and retries with
    ``candidates[0]``.

    This is the failure mode decision #8 exists to prevent, scripted so it is a fixture
    rather than a hypothetical.
    """

    def respond(messages, info) -> ModelResponse:
        for message in reversed(messages):
            for part in message.parts:
                if not isinstance(part, ToolReturnPart):
                    continue
                content = part.content
                payload = json.loads(content) if isinstance(content, str) else {}
                candidates = payload.get("error", {}).get("candidates")
                if candidates:
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "RescheduleViewing",
                                {
                                    "requested_slot_time": requested_slot_time.isoformat(),
                                    "booking_id": candidates[0]["booking_id"],
                                },
                            )
                        ]
                    )
                return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "RescheduleViewing",
                    {"requested_slot_time": requested_slot_time.isoformat()},
                )
            ]
        )

    return FunctionModel(respond)


async def _book(db, notifier, faq_index, *, user_id, slot_time, property_id=ACTIVE) -> str:
    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {"property_id": property_id, "requested_slot_time": slot_time.isoformat()},
                )
            ],
            "booked",
        ],
        make_deps(db, notifier, faq_index, user_id=user_id),
    )
    return run.payload()["booking_id"]


@pytest.mark.anyio
async def test_nothing_in_code_stops_a_model_that_auto_selects_an_ambiguous_candidate(
    db, notifier, faq_index
):
    """The negative control, and a guard on the architecture rather than on behavior.

    A model that retries with ``candidates[0]`` succeeds, and someone else's viewing
    moves. That is *correct* for this codebase: blocking it would require inspecting the
    model's next tool call, which ``CLAUDE.md`` forbids as a hard constraint. If this test
    ever starts failing, a code-level guard was added and the hard constraint was broken —
    which is a conversation to have, not a silent improvement.

    It is also why every assertion below matters: prose is the only enforcement there is.
    """
    first = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T2)

    agent = build_agent(_naive_retry_model(T3))
    run = Run(
        await agent.run(
            "move my viewing", deps=make_deps(db, notifier, faq_index, user_id=CLIENT)
        )
    )

    assert run.error(0)["code"] == "booking_ambiguous"
    # The second call went through. The candidate ids are real and actionable.
    assert run.payload(1)["booking_id"] == first
    assert db.get(Booking, first).rescheduled_count == 1


@pytest.mark.anyio
async def test_the_ambiguity_payload_carries_no_field_that_invites_a_choice(
    db, notifier, faq_index
):
    """Exactly ``{code, message, guidance, candidates}`` — nothing ranked, defaulted, or
    recommended. A ``best_match``/``most_likely``/``default_booking_id`` field would make
    auto-selecting the *documented* reading of the payload, and no prompt wording would
    reliably outweigh it."""
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T2)

    run = await run_tool(
        [[("RescheduleViewing", {"requested_slot_time": T3.isoformat()})], "which one?"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    error = run.error()

    assert set(error) == {"code", "message", "guidance", "candidates"}
    assert error["code"] == "booking_ambiguous"
    # The message states the condition and stops there; it does not narrate a resolution.
    assert error["message"] == "Several upcoming viewings match that description."
    for candidate in error["candidates"]:
        assert set(candidate) == {"booking_id", "property_title", "slot_time"}


@pytest.mark.anyio
async def test_the_ambiguity_guidance_forbids_both_choosing_and_re_calling(
    db, notifier, faq_index
):
    """Two distinct prohibitions, because a model can honor one and break the other:
    "do not choose" without "do not re-call" leaves re-calling with a guessed id open."""
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T2)

    run = await run_tool(
        [[("RescheduleViewing", {"requested_slot_time": T3.isoformat()})], "which one?"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    guidance = _flatten(run.error()["guidance"])

    assert guidance.startswith("STOP.")
    assert "Ask the user which of these viewings they mean" in guidance
    assert "Never choose one yourself" in guidance
    assert (
        "never call this tool again with a booking_id the user has not explicitly confirmed"
        in guidance
    )


def test_the_prompt_forbids_every_flavour_of_picking_a_candidate():
    """"Do not pick one" alone leaves "but the soonest is obviously right" open, which is
    precisely the rationalization a capable model produces."""
    prompt = _flatten(SYSTEM_PROMPT)

    assert (
        "Stop and ask the user which one they mean, describing the options by property "
        "and time in plain language." in prompt
    )
    assert "Do not pick the first, the soonest, or the most likely." in prompt
    assert (
        "Do not call the tool again with an id the user has not explicitly chosen." in prompt
    )
    # The reason, not just the rule — a model that understands the stake generalizes to
    # phrasings the rule did not anticipate.
    assert (
        "Every one of those candidates is a real appointment belonging to a real person"
        in prompt
    )
    assert (
        "This holds no matter how confident you feel and no matter how obvious the right "
        "answer looks." in prompt
    )


def test_the_reschedule_description_repeats_the_ambiguity_rule_in_the_tool_schema():
    """The prompt and the tool schema reach the model through different channels; a model
    that skims one must meet the other."""
    description = _flatten(reschedule_viewing.TOOL_DESCRIPTION)

    assert "you must ASK THE USER which one they mean" in description
    assert "wait for their answer before calling this tool again" in description
    assert "do NOT re-call this tool with a candidate the user has not chosen" in description
    assert "moving the wrong one disrupts a real person's day" in description


# ------------------------------------------------- 2. booking_not_found reveals nothing


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("arguments", "label"),
    [
        ({"booking_id": "booking-belonging-to-nobody"}, "explicit id"),
        ({}, "no hints at all"),
    ],
)
async def test_the_not_found_payload_leaks_no_existence_information(
    db, notifier, faq_index, arguments, label
):
    """The payload must carry no field a model could reason from — and specifically must
    not echo the ``booking_id`` it was given, which would let the model repeat it back to
    the user as though the platform had acknowledged it."""
    run = await run_tool(
        [
            [("RescheduleViewing", {"requested_slot_time": T2.isoformat(), **arguments})],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    error = run.error()

    assert set(error) == {"code", "message", "guidance"}, label
    assert error["code"] == "booking_not_found"
    rendered = json.dumps(error).lower()
    for leak in ("booking-belonging-to-nobody", "belongs to", "another user", "forbidden",
                 "not yours", "permission", "u-client-1"):
        assert leak not in rendered, f"{label}: leaked {leak!r}"


@pytest.mark.anyio
async def test_the_not_found_guidance_forbids_speculating_either_way(
    db, notifier, faq_index
):
    run = await run_tool(
        [[("RescheduleViewing", {"requested_slot_time": T2.isoformat()})], "sorry"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    guidance = _flatten(run.error()["guidance"])

    assert "Do not confirm or deny that any particular booking exists." in guidance
    assert "offer to book one" in guidance


@pytest.mark.anyio
async def test_out_of_scope_and_nonexistent_are_identical_on_both_resolution_paths(
    db, notifier, faq_index
):
    """``test_agent.py`` pins this for an explicit ``booking_id``. The hint-filtered path
    is the other way in, and a client probing ``property_id`` values is the same oracle."""
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)

    def hint(property_id):
        return run_tool(
            [
                [
                    (
                        "RescheduleViewing",
                        {
                            "requested_slot_time": T2.isoformat(),
                            "property_id": property_id,
                        },
                    )
                ],
                "sorry",
            ],
            make_deps(db, notifier, faq_index, user_id=OTHER_CLIENT),
        )

    # ACTIVE exists and has a booking on it — just not this caller's.
    existing_but_not_theirs = (await hint(ACTIVE)).error()
    never_existed = (await hint("prop-does-not-exist")).error()

    assert existing_but_not_theirs == never_existed


def test_the_prompt_explains_why_it_cannot_tell_and_forbids_reasoning_aloud():
    prompt = _flatten(SYSTEM_PROMPT)

    assert (
        "the server answers both the same way on purpose, so that nobody can use you to "
        "find out which." in prompt
    )
    assert (
        "Do not speculate, do not reason aloud about which it might be, and do not offer "
        "to check again with a different id." in prompt
    )


def test_the_reschedule_description_repeats_the_do_not_speculate_rule():
    description = _flatten(reschedule_viewing.TOOL_DESCRIPTION)

    assert "If this returns `booking_not_found`" in description
    assert "Do not speculate about whether some particular booking exists." in description


# ------------------------------- 3. BookViewing vs. RescheduleViewing selection lever


def test_each_booking_tool_names_the_other_and_states_when_it_does_not_apply():
    """The descriptions are the *only* lever on this choice (contract decision #12). A
    description that describes only what its own tool does leaves the model to infer the
    boundary, and the boundary is the whole difficulty."""
    book = _flatten(schedule_viewing.TOOL_DESCRIPTION)
    move = _flatten(reschedule_viewing.TOOL_DESCRIPTION)

    assert book.startswith("Book a NEW property viewing appointment")
    assert (
        "Do NOT use this to change the time of a viewing the user already has." in book
    )
    assert "use RescheduleViewing for those" in book

    assert move.startswith("Move an EXISTING viewing appointment to a different time")
    assert (
        "if they want to see another property, or the same property a second time, that "
        "is BookViewing instead" in move
    )
    # Its own boundary, so the model does not reach for it to change *which* property.
    assert (
        "It cannot change which property is being viewed or which agent conducts it."
        in move
    )


def test_both_descriptions_state_the_asymmetric_cost_of_guessing_wrong():
    """Symmetric-sounding instructions get resolved by coin flip. The cost is not
    symmetric — a wrong BookViewing leaves a phantom appointment nobody cancels — so the
    model needs to know which way to err."""
    book = _flatten(schedule_viewing.TOOL_DESCRIPTION)
    move = _flatten(reschedule_viewing.TOOL_DESCRIPTION)

    assert (
        "Booking a new viewing when the user meant to move an existing one leaves them "
        "with two appointments and nobody told about the first" in book
    )
    assert "ask before calling either tool" in book
    assert (
        "Getting this wrong creates a duplicate appointment and leaves the original one "
        "standing, so when it is genuinely unclear, ask." in move
    )


def test_the_prompt_frames_the_choice_by_world_state_with_both_worked_examples():
    """Framing it lexically ("if they say 'instead'") is what a keyword router does and
    what the hard constraint forbids; the prompt has to frame it by world state."""
    prompt = _flatten(SYSTEM_PROMPT)

    assert (
        "the difference between them is about the state of the world, not about the "
        "words the user chose." in prompt
    )
    assert (
        "does an appointment already exist that this person wants to happen at a "
        "different time?" in prompt
    )
    # Both examples, because one alone reads as a rule about the word it contains.
    assert (
        '"Can we make it Monday instead?" right after you booked Saturday is a move.'
        in prompt
    )
    assert '"Can I also see the other unit on Monday?" is an addition' in prompt
    assert (
        "Both kinds of request mention a day and a viewing, so the phrasing will not "
        "settle it." in prompt
    )
    assert (
        "Booking a new viewing when the user wanted to move one leaves them with two "
        "appointments, one of which nobody knows they will not attend." in prompt
    )
    assert "ask which they mean. One short question is cheap; a phantom appointment is not." in prompt


def test_no_tool_other_than_the_two_booking_tools_mentions_moving_an_appointment():
    """Scaffolding hygiene: if `search_faq` or `EscalateToHuman` described itself in
    reschedule-adjacent terms, it would compete for the same turn."""
    for module in (escalate_to_human,):
        description = module.TOOL_DESCRIPTION.lower()
        assert "reschedul" not in description
        assert "move an existing" not in description


# ------------------------------------------- 4. an escalation reply promises no response


def _assert_promises_nothing(text: str, label: str) -> None:
    lowered = text.lower()
    for phrase in FORBIDDEN_PROMISES:
        assert phrase not in lowered, f"{label} promises a response: {phrase!r} in {text!r}"


@pytest.mark.anyio
async def test_the_escalation_result_promises_no_response_and_says_so_explicitly(
    db, notifier, faq_index
):
    """Regression test for one of the two defects the Phase 3 live smoke test found by
    hand ("they will get back to you as soon as possible"). The model echoes this message
    to the user, so a promise here becomes a promise the platform made."""
    run = await run_tool(
        [
            [
                (
                    "EscalateToHuman",
                    {"reason": "wants a human", "conversation_summary": "refund question"},
                )
            ],
            "logged",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    message = run.payload()["message"]

    _assert_promises_nothing(message, "escalation success message")
    # Not merely silent about a callback — actively instructs against implying one, since
    # "logged for a human colleague" alone reads as a promise to a helpful model.
    assert (
        "Do not tell them anyone will respond, review it, or get back to them" in message
    )
    assert "nobody was paged" in message
    assert run.payload()["escalation_id"] in message


@pytest.mark.anyio
async def test_the_assigned_reply_promises_no_response_either(db, notifier, faq_index):
    """B18b added a *second* success message, on the ``queued`` path, and the test above
    only ever reaches the unassigned one — so the existing regression does not cover it.

    "Flagged it to the agent handling that listing" is the strongest true claim available:
    assignment records who a listing belongs to, it still pages nobody.
    """
    run = await run_tool(
        [
            [
                (
                    "EscalateToHuman",
                    {
                        "reason": "wants a human about this listing",
                        "conversation_summary": "asked about the rent",
                        "property_id": ACTIVE,
                    },
                )
            ],
            "logged",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    payload = run.payload()
    message = payload["message"]

    assert payload["status"] == "queued", "fixture no longer exercises the assigned path"
    _assert_promises_nothing(message, "assigned escalation message")
    assert "nobody was paged" in message
    assert payload["escalation_id"] in message


@pytest.mark.anyio
async def test_the_rate_limited_reply_promises_no_response_either(db, notifier, faq_index):
    """The path a frustrated user reaches after asking three times — the one where an
    implied callback does the most damage."""
    deps = make_deps(db, notifier, faq_index, user_id=CLIENT)
    run = None
    for _ in range(4):
        run = await run_tool(
            [
                [("EscalateToHuman", {"reason": "again", "conversation_summary": "again"})],
                "logged",
            ],
            deps,
        )

    _assert_promises_nothing(run.payload()["message"], "rate-limited escalation message")
    assert "already with the team" in run.payload()["message"]


@pytest.mark.anyio
async def test_the_degraded_reply_promises_no_response_and_still_gives_a_next_step(
    db, notifier, faq_index, monkeypatch
):
    """Double persistence failure. The user gets a real next step (contact support) and
    still no promise — the temptation to soften a failure with "someone will follow up"
    is strongest exactly here."""

    def always_fails(*args, **kwargs):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(escalate_to_human, "_persist_escalation", always_fails)

    run = await run_tool(
        [
            [("EscalateToHuman", {"reason": "help", "conversation_summary": "help"})],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    payload = run.payload()

    assert payload["escalation_id"] is None
    _assert_promises_nothing(payload["message"], "degraded escalation message")
    assert escalate_to_human.SUPPORT_CONTACT in payload["message"]
    # Degraded, not failed: raising would leave a user who already asked for a human with
    # an apology and nothing actionable.
    assert payload["status"] == "queued_unassigned"


def test_the_prompt_and_the_tool_description_both_forbid_implying_a_reply():
    prompt = _flatten(SYSTEM_PROMPT)
    description = _flatten(escalate_to_human.TOOL_DESCRIPTION)

    assert (
        "Do not say that anyone will call, email, review it, get back to them, or "
        'respond — not "shortly", not "as soon as possible", not in any other wording.'
        in prompt
    )
    assert "Nobody is paged when you escalate; a record is created." in prompt
    assert (
        "implying one is a promise you cannot keep, and the user will wait on it." in prompt
    )
    assert (
        "Do NOT say anyone will call, email, review it, get back to them, or respond, in "
        "any wording." in description
    )
    assert "This creates a record; it does not page a human." in description


# ----------------------------------- 5. a conflict with no alternatives (live defect #1)


@pytest.fixture
def solo_slot_property(db):
    """A listing whose only viewing slot is the one about to be taken, so a conflict on
    it genuinely has nothing to offer."""
    db.add(
        Property(
            id="prop-solo",
            title="Solo listing",
            property_type="apartment",
            listing_type="rent",
            price=4_000_000,
            price_unit="per_month",
            bedrooms=1,
            bathrooms=1,
            area_sqm=40,
            address="Jl. Solo",
            city="Jakarta",
            latitude=-6.2,
            longitude=106.8,
            amenities=[],
            agent_id=AGENT,
            status="active",
            description="Only one slot",
            listed_date=NOW.date(),
        )
    )
    db.flush()
    db.add(
        AvailabilitySlot(
            id="slot-solo", agent_id=AGENT, property_id="prop-solo", slot_time=T1
        )
    )
    db.commit()
    return "prop-solo"


@pytest.mark.anyio
async def test_a_conflict_with_alternatives_asks_the_model_to_present_them(
    db, notifier, faq_index
):
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    run = await run_tool(
        [
            [("BookViewing", {"property_id": ACTIVE, "requested_slot_time": T1.isoformat()})],
            "taken",
        ],
        make_deps(db, notifier, faq_index, user_id=OTHER_CLIENT),
    )
    error = run.error()

    assert error["code"] == "booking_slot_conflict"
    assert error["suggested_alternatives"]
    assert "Present the suggested_alternatives as concrete options" in _flatten(
        error["guidance"]
    )


@pytest.mark.anyio
async def test_a_conflict_with_no_alternatives_never_asks_for_alternatives(
    db, notifier, faq_index, solo_slot_property
):
    """Regression test for the second defect the live smoke test found by hand: told to
    "present the suggested_alternatives" when the list was absent, the model re-called the
    tool five times with identical arguments hunting for them.

    The key property is not that the guidance differs but *how*: it must not name
    alternatives at all, and it must say to stop calling."""
    await _book(
        db, notifier, faq_index, user_id=CLIENT, slot_time=T1, property_id=solo_slot_property
    )
    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": solo_slot_property,
                        "requested_slot_time": T1.isoformat(),
                    },
                )
            ],
            "taken",
        ],
        make_deps(db, notifier, faq_index, user_id=OTHER_CLIENT),
    )
    error = run.error()
    guidance = _flatten(error["guidance"])

    assert error["code"] == "booking_slot_conflict"
    # The key is dropped, not set to []: an empty list still reads as a list to enumerate,
    # and a model that finds the key will look for entries in it.
    assert "suggested_alternatives" not in error
    assert "suggested_alternatives" not in guidance
    assert "Present the" not in guidance
    assert "no other open viewing times to offer" in guidance
    assert "Do NOT call this tool again for this listing" in guidance
    assert "retrying will return the same answer" in guidance


@pytest.mark.anyio
async def test_the_two_conflict_guidances_are_actually_different_strings(
    db, notifier, faq_index, solo_slot_property
):
    """The bug was one guidance string used for both cases. Asserting each in isolation
    would still pass if someone merged them back into a single "mention alternatives if
    present" sentence."""
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    await _book(
        db, notifier, faq_index, user_id=CLIENT, slot_time=T1, property_id=solo_slot_property
    )

    with_alternatives = await run_tool(
        [
            [("BookViewing", {"property_id": ACTIVE, "requested_slot_time": T1.isoformat()})],
            "taken",
        ],
        make_deps(db, notifier, faq_index, user_id=OTHER_CLIENT),
    )
    without = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": solo_slot_property,
                        "requested_slot_time": T1.isoformat(),
                    },
                )
            ],
            "taken",
        ],
        make_deps(db, notifier, faq_index, user_id=OTHER_CLIENT),
    )

    assert with_alternatives.error()["code"] == without.error()["code"]
    assert with_alternatives.error()["guidance"] != without.error()["guidance"]


@pytest.mark.anyio
async def test_the_empty_alternatives_rule_covers_reschedule_and_slot_unavailable_too(
    db, notifier, faq_index, solo_slot_property
):
    """Both booking tools render conflicts through ``booking_common``, and
    ``slot_unavailable`` is a ``SlotConflictError`` subclass — the fix has to hold for all
    four combinations, not just the one the live test happened to hit."""
    await _book(
        db, notifier, faq_index, user_id=CLIENT, slot_time=T1, property_id=solo_slot_property
    )

    run = await run_tool(
        [
            [("RescheduleViewing", {"requested_slot_time": T3.isoformat()})],
            "nothing else",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    error = run.error()

    # T3 is not in prop-solo's availability at all, and prop-solo's only slot is taken.
    assert error["code"] == "slot_unavailable"
    assert "suggested_alternatives" not in error
    assert error["guidance"] == booking_common._NO_ALTERNATIVES_GUIDANCE
