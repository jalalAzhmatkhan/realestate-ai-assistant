"""Structured tool failures the model can read and act on.

Every tool failure the README's tables name is raised as a :class:`ToolError` carrying a
stable ``code`` plus any error-specific extras (``candidates``,
``suggested_alternatives``). Two properties this buys:

- **The model sees the failure and adapts** instead of the run crashing. ``ToolError``
  subclasses Pydantic AI's ``ToolFailed``, which returns the payload to the model as a
  failed tool result.
- **``ToolFailed``, not ``ModelRetry``, is deliberate.** ``ModelRetry`` prepends
  correction instructions and pushes the model to re-call the same tool immediately —
  precisely the wrong pressure for ``booking_ambiguous``, where retrying without asking
  the user would silently reschedule the wrong person's viewing
  (``2026-08-05-reschedule-viewing-tool-contract.md`` decision #8). What the model should
  do next is stated in prose, in the payload's ``guidance`` field and in the system
  prompt — never enforced by code inspecting the model's next call, which would be the
  pre-classification layer ``CLAUDE.md`` forbids.

Codes are the same strings ``app/core/exceptions.py`` and ``app/booking/slots.py`` use, so
the chat surface and the REST surface describe an identical condition identically.
"""

import json
from typing import Any

from pydantic_ai import ToolFailed


class ToolError(ToolFailed):
    """A terminal, structured tool failure.

    The message handed to the model is a JSON object rather than a sentence: it keeps
    ``code`` machine-stable while leaving the user-facing wording to the model, and it
    carries extras (alternatives, candidates) in a shape the model can enumerate rather
    than parse out of prose.
    """

    code: str = "tool_error"
    default_message: str = "The request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        guidance: str | None = None,
        **details: Any,
    ) -> None:
        # Instance-level override so a domain exception's own `code` can be carried
        # through verbatim (see booking_common.as_tool_error) instead of needing one
        # ToolError subclass per booking failure mode, which would be two parallel
        # taxonomies to keep in step.
        if code is not None:
            self.code = code
        self.error_message = message or self.default_message
        self.details = details
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.error_message,
            **details,
        }
        if guidance:
            payload["guidance"] = guidance
        super().__init__(json.dumps({"error": payload}, default=str))


class NotFoundToolError(ToolError):
    code = "not_found"


class ValidationToolError(ToolError):
    code = "invalid_request"


class UpstreamToolError(ToolError):
    """A dependency (database, embedding provider) failed.

    The user-facing recovery is an apology plus an offer to escalate; the underlying
    exception is logged, never relayed — a stack trace in a chat reply is both useless
    to the user and an information leak.
    """

    code = "service_unavailable"
    default_message = (
        "That service is temporarily unavailable, so this request could not be completed."
    )
