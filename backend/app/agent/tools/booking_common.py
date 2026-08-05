"""Shared plumbing for the two booking tools.

``app/booking/slots.py`` is pure domain logic: it raises ``BookingDomainError``s carrying
a stable ``code`` and knows nothing about HTTP or the LLM. This module is the chat
surface's rendering of those codes — the REST surface has its own, and both start from
the same exception, which is what keeps the two descriptions of a conflict identical.

The ``guidance`` strings are the README's "orchestrator recovery" column, delivered to
the model as prose in the failed tool result. They tell the model what to *do* next; no
code anywhere checks whether it complied, because inspecting the model's next tool call
to enforce a decision is exactly the pre-classification layer ``CLAUDE.md`` forbids.
"""

from app.agent.tools.errors import ToolError
from app.booking.slots import BookingDomainError, SlotConflictError

_RECOVERY_GUIDANCE: dict[str, str] = {
    "booking_not_found": (
        "Tell the user you could not find a matching upcoming viewing to move, and offer "
        "to search for a property or book a new viewing. Do not confirm or deny that a "
        "booking with any particular reference exists — you genuinely cannot tell the "
        "difference between 'no such booking' and 'not this user's booking', and "
        "speculating either way would leak information."
    ),
    "booking_ambiguous": (
        "STOP and ask the user which viewing they mean, describing the candidates by "
        "property and time. Never choose one yourself, and never re-call this tool with "
        "a booking_id the user has not confirmed — picking wrong silently moves someone "
        "else's appointment."
    ),
    "booking_not_reschedulable": (
        "Explain that this viewing can no longer be changed, and offer to book a new one."
    ),
    "slot_time_in_past": "Ask the user for a time in the future.",
    "slot_unchanged": (
        "Confirm the viewing is already at that time. Nothing was changed — do not "
        "describe this as an error or retry it."
    ),
    "slot_unavailable": (
        "That time is not offered for this listing. Present the suggested_alternatives "
        "as concrete options and ask the user to pick one. Do not retry this tool with "
        "the same time."
    ),
    "booking_slot_conflict": (
        "That time was already taken. Present the suggested_alternatives as concrete "
        "options and ask the user to pick one. Do not retry this tool with the same time."
    ),
    "property_not_found": (
        "Say you could not find that listing, and offer to search for properties instead."
    ),
    "property_not_bookable": (
        "Explain that this listing is not currently open for viewings, and offer to find "
        "similar available properties or to connect the user with a human agent."
    ),
}


# A conflict with nothing to offer needs its own instruction. Observed live: told to
# "present the suggested_alternatives" when the list was absent, the model re-called the
# tool five times with the same arguments looking for them. The fix belongs in the prose
# the model reads, not in code that inspects its next call.
_NO_ALTERNATIVES_GUIDANCE = (
    "This listing has no other open viewing times to offer right now. Say so plainly, "
    "and offer either to look at other listings or to connect the user with a human "
    "agent. Do NOT call this tool again for this listing — there is nothing else to find, "
    "and retrying will return the same answer."
)


def as_tool_error(exc: BookingDomainError) -> ToolError:
    """Render a booking-domain failure as a structured tool result for the model.

    Codes and extras pass through unchanged: ``suggested_alternatives`` reaches the model
    in the same ``{availability_slot_id, slot_time}`` shape the REST endpoint returns, so
    all three booking surfaces describe a conflict identically.
    """
    details = dict(exc.details)
    guidance = _RECOVERY_GUIDANCE.get(exc.code)

    if isinstance(exc, SlotConflictError) and not details.get("suggested_alternatives"):
        # An empty list reads as "there are no other times" when it may only mean "none
        # were found to suggest", so the key is dropped and the guidance says what the
        # model should actually do instead.
        details.pop("suggested_alternatives", None)
        guidance = _NO_ALTERNATIVES_GUIDANCE

    return ToolError(exc.message, code=exc.code, guidance=guidance, **details)
