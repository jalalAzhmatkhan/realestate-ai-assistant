"""Agent construction and the run loop.

One ``pydantic_ai.Agent``, built against whatever ``app/agent/providers.py`` returns, with
**all five tools registered up front**. On every turn the model decides — through native
function calling, from the tool schemas and the system prompt — whether to call a tool,
which one, with what arguments, and in what order.

There is no keyword matching, no regex, no intent classifier, and no if/else over the
user's message anywhere in this module or in ``app/api/chat.py``. That is the hard
constraint in ``CLAUDE.md``, and it is what makes "actually, make it Monday instead"
resolve to ``RescheduleViewing`` rather than ``BookViewing`` — a distinction that is
contextual, not lexical, and that a router would get wrong.

The one thing this module *does* decide is the exposed tool names: the assessment mandates
``SearchProperty``, ``BookViewing``, and ``EscalateToHuman`` literally, so each tool is
registered under its required name while its Python function keeps snake_case (README
Design Decisions §4).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.messages import (
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model

from app.agent.deps import AgentDeps
from app.agent.prompt import SYSTEM_PROMPT
from app.agent.tools import (
    escalate_to_human,
    reschedule_viewing,
    schedule_viewing,
    search_faq,
    search_property,
)

logger = logging.getLogger(__name__)

# Bounds a pathological run (a model that keeps re-calling a failing tool) without
# constraining legitimate multi-step work: search -> book -> escalate is three.
MAX_TOOL_CALLS_PER_TURN = 8


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool invocation, for the chat response's ``tool_calls[]`` and for the
    persisted transcript. This is what makes autonomous tool selection *visible* rather
    than something a reader has to take on faith."""

    tool: str
    arguments: dict[str, Any]
    result_summary: str


def _caller_context(ctx: RunContext[AgentDeps]) -> str:
    """Per-run context appended to the static prompt.

    Two things the model cannot know statically: what "Saturday" means, and who it is
    talking to. Neither grants anything — every tool re-authorizes against
    ``ctx.deps.user`` regardless of what the model believes about the caller.
    """
    user = ctx.deps.user
    now = datetime.now(timezone.utc)
    return (
        f"Current time: {now.isoformat()} (UTC). Listings and viewing slots are quoted "
        f"in local Indonesian time, UTC+07:00, unless a user says otherwise — resolve "
        f"relative days like 'Saturday' or 'tomorrow' against that.\n"
        f"You are speaking with {user.name} (role: {user.role}). Address them by name "
        f"when it reads naturally. Their role affects what the server will let them do; "
        f"it is not something for you to enforce or explain."
    )


def build_agent(model: Model) -> Agent[AgentDeps, str]:
    """Construct the agent with all five tools registered.

    Called once per application instance. Takes an already-built ``Model`` rather than
    ``Settings`` so a test can hand in ``FunctionModel``/``TestModel`` and exercise the
    real tool-calling loop with no API key and no network.
    """
    agent = Agent[AgentDeps, str](
        model,
        deps_type=AgentDeps,
        output_type=str,
        instructions=SYSTEM_PROMPT,
        name="realestate-assistant",
        tools=[
            # Exposed names are the assessment's mandated literals where mandated;
            # `search_faq` has no mandated name and stays snake_case.
            Tool(
                search_faq.search_faq,
                name=search_faq.TOOL_NAME,
                description=search_faq.TOOL_DESCRIPTION,
            ),
            Tool(
                search_property.search_property,
                name=search_property.TOOL_NAME,
                description=search_property.TOOL_DESCRIPTION,
            ),
            Tool(
                schedule_viewing.schedule_viewing,
                name=schedule_viewing.TOOL_NAME,
                description=schedule_viewing.TOOL_DESCRIPTION,
            ),
            Tool(
                reschedule_viewing.reschedule_viewing,
                name=reschedule_viewing.TOOL_NAME,
                description=reschedule_viewing.TOOL_DESCRIPTION,
            ),
            Tool(
                escalate_to_human.escalate_to_human,
                name=escalate_to_human.TOOL_NAME,
                description=escalate_to_human.TOOL_DESCRIPTION,
            ),
        ],
    )
    agent.instructions(_caller_context)
    return agent


def _summarize(part: ToolReturnPart) -> str:
    """A short, human-readable rendering of a tool result for ``tool_calls[]``.

    Deliberately shallow: this is for a trace/inspector view, so it must never carry more
    than the tool already returned to the model.
    """
    content = part.content
    if isinstance(content, str):
        return content if len(content) <= 300 else f"{content[:297]}..."
    results = getattr(content, "results", None)
    if results is not None:
        return f"{len(results)} result(s)"
    for attribute in ("booking_id", "escalation_id"):
        value = getattr(content, attribute, None)
        if value:
            return f"{attribute}={value}"
    return type(content).__name__


def extract_tool_calls(messages: list[ModelMessage]) -> list[ToolCallRecord]:
    """Pair the model's tool calls with their results, in call order.

    Reads the run's own message history rather than instrumenting the tools, so a call
    the model made and a result it saw cannot drift apart — including failed calls, which
    are exactly the ones worth seeing in a trace.
    """
    calls: dict[str, ToolCallPart] = {}
    order: list[str] = []
    summaries: dict[str, str] = {}

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = part
                order.append(part.tool_call_id)
            elif isinstance(part, ToolReturnPart):
                summaries[part.tool_call_id] = _summarize(part)
            elif isinstance(part, RetryPromptPart):
                # Argument-validation failures never reach a tool body, so they have no
                # ToolReturnPart — without this they would show as "no result recorded"
                # and a schema mismatch would be invisible in the trace.
                summaries[part.tool_call_id] = "rejected: invalid arguments"

    records = []
    for call_id in order:
        part = calls[call_id]
        arguments = part.args_as_dict() if part.args is not None else {}
        records.append(
            ToolCallRecord(
                tool=part.tool_name,
                arguments=arguments,
                result_summary=summaries.get(call_id, "no result recorded"),
            )
        )
    return records
