"""``POST /api/v1/chat/messages`` — the entrypoint into the agent.

**This module contains no routing logic of any kind.** It resolves the authenticated
caller, loads or creates the conversation, runs the agent, persists the turn, and returns
the reply. It never inspects the message's content to decide anything — which tool runs
(if any) is decided by the model, through native function calling, from the tool schemas
and the system prompt. That is ``CLAUDE.md``'s hard constraint, and it is the reason this
file is as short as it is.

Identity is resolved **before** the agent runs and passed in as ``deps``, so every tool
re-authorizes against the real caller rather than anything the model produced (README
Design Decisions §5).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic_ai import Agent, UsageLimitExceeded
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits
from sqlmodel import Session, col, select

from app.agent.deps import AgentDeps
from app.agent.orchestrator import MAX_TOOL_CALLS_PER_TURN, build_agent, extract_tool_calls
from app.agent.providers import build_llm_model
from app.api.deps import CurrentUser, DbSession, NotifierDep, SettingsDep
from app.core.config import Settings
from app.core.exceptions import DomainError
from app.models import Conversation, Message, RetrievalLog, User
from app.observability.faithfulness import collect_context, run_faithfulness_check
from app.rag.embeddings import build_embedding_model
from app.rag.index import FaqIndex, build_faq_index
from app.schemas.chat import ChatMessageRequest, ChatMessageResponse, ToolCallRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# The agent is unavailable rather than broken when it fails: the user needs a next step,
# not a stack trace, and a 503 tells the SPA to offer a retry rather than a re-login.
AGENT_UNAVAILABLE_MESSAGE = (
    "The assistant is temporarily unavailable. Please try again in a moment."
)


class AgentUnavailableError(DomainError):
    code = "agent_unavailable"
    status_code = 503

    def __init__(self, message: str = AGENT_UNAVAILABLE_MESSAGE, **extra: object) -> None:
        super().__init__(message, **extra)


class ConversationNotFoundError(DomainError):
    """404, never 403 — a conversation belonging to someone else must be
    indistinguishable from one that does not exist, same posture as bookings."""

    code = "conversation_not_found"
    status_code = 404

    def __init__(
        self, message: str = "That conversation could not be found.", **extra: object
    ) -> None:
        super().__init__(message, **extra)


def get_agent(request: Request) -> Agent[AgentDeps, str]:
    """The application's single agent, built on first use and cached on ``app.state``.

    Lazy rather than built in ``create_app`` so the app (and every test that never chats)
    boots without an LLM API key. Cached rather than rebuilt per request because
    constructing it re-parses five tool schemas for no benefit. A test injects its own via
    ``create_app(settings, agent=...)`` and never reaches this path.
    """
    agent = request.app.state.agent
    if agent is None:
        agent = build_agent(build_llm_model(request.app.state.settings))
        request.app.state.agent = agent
    return agent


def get_judge_model(request: Request) -> Model:
    """The faithfulness judge's model — the same provider plumbing the agent uses.

    Built lazily and cached exactly like the agent, and for the same reason: a deployment
    with no LLM key must still boot and serve the REST surface. A test injects its own via
    ``create_app(settings, judge_model=...)``.
    """
    model = request.app.state.judge_model
    if model is None:
        model = build_llm_model(request.app.state.settings)
        request.app.state.judge_model = model
    return model


def get_faq_index(request: Request) -> FaqIndex:
    """One index per application instance; embeddings are computed on first search."""
    index = request.app.state.faq_index
    if index is None:
        settings: Settings = request.app.state.settings
        index = build_faq_index(build_embedding_model(settings), settings.seed_data_dir)
        request.app.state.faq_index = index
    return index


def _resolve_conversation(
    db: Session, user: User, conversation_id: str | None
) -> Conversation:
    if conversation_id is None:
        conversation = Conversation(user_id=user.id)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation

    conversation = db.exec(
        select(Conversation).where(
            col(Conversation.id) == conversation_id,
            # Scoped by owner, so an id belonging to another user resolves to "not
            # found" — a user cannot resume, read, or escalate someone else's session.
            col(Conversation.user_id) == user.id,
        )
    ).first()
    if conversation is None:
        raise ConversationNotFoundError()
    return conversation


def _load_history(conversation: Conversation) -> list[ModelMessage]:
    if not conversation.history_json:
        return []
    try:
        return ModelMessagesTypeAdapter.validate_json(conversation.history_json)
    except Exception:
        # A history the current Pydantic AI version cannot parse (e.g. after an upgrade)
        # must not brick the conversation. Dropping context degrades the reply; raising
        # would make the conversation permanently unusable.
        logger.warning(
            "chat_history_unreadable",
            extra={"conversation_id": conversation.id},
        )
        return []


def _stage_retrieval_logs(
    db: Session,
    deps: AgentDeps,
    user: User,
    conversation: Conversation,
    user_message: Message,
    assistant_message: Message,
) -> None:
    """Turn this run's accumulated ``search_faq`` calls into rows on the turn's session.

    Staged, not committed: they land in the same transaction as the messages they
    reference. A record that cannot be turned into a row is dropped and logged —
    observability must never be able to fail a chat turn.
    """
    for record in deps.retrieval_sink:
        try:
            db.add(
                RetrievalLog(
                    conversation_id=conversation.id,
                    message_id=assistant_message.id,
                    user_message_id=user_message.id,
                    user_id=user.id,
                    query_text=record.query_text,
                    requested_top_k=record.requested_top_k,
                    effective_top_k=record.effective_top_k,
                    min_score=record.min_score,
                    results=record.results,
                    result_count=record.result_count,
                    top_score=record.top_score,
                    embedding_model=record.embedding_model,
                    latency_ms=record.latency_ms,
                )
            )
        except Exception:
            logger.exception(
                "retrieval_log_row_dropped",
                extra={"conversation_id": conversation.id, "user_id": user.id},
            )


def _schedule_faithfulness_check(
    background_tasks: BackgroundTasks,
    request: Request,
    deps: AgentDeps,
    settings: Settings,
    user: User,
    conversation: Conversation,
    assistant_message: Message,
    reply_text: str,
) -> None:
    """Queue the post-response grounding check for a turn that consulted the FAQ.

    Scheduled, never awaited: the check costs two model round trips the user would
    otherwise wait on for a number they never see. Scheduling itself is wrapped because
    building the judge model can fail (no API key, misconfigured provider), and an
    observability failure must never cost the user their answer.
    """
    try:
        background_tasks.add_task(
            run_faithfulness_check,
            request.app.state.engine,
            settings,
            get_judge_model(request),
            message_id=assistant_message.id,
            conversation_id=conversation.id,
            user_id=user.id,
            reply_text=reply_text,
            context=collect_context(deps.rag, deps.retrieval_sink),
        )
    except Exception:
        logger.exception(
            "faithfulness_check_not_scheduled",
            extra={"conversation_id": conversation.id, "user_id": user.id},
        )


@router.post("/messages", response_model=ChatMessageResponse)
async def post_message(
    payload: ChatMessageRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
    notifier: NotifierDep,
) -> ChatMessageResponse:
    """Send a message to the agent and get its reply plus the tools it chose to use."""
    conversation = _resolve_conversation(db, user, payload.conversation_id)

    deps = AgentDeps(
        user=user,
        db=db,
        conversation_id=conversation.id,
        rag=get_faq_index(request),
        settings=settings,
        notifier=notifier,
    )

    try:
        result = await get_agent(request).run(
            payload.message,
            deps=deps,
            message_history=_load_history(conversation),
            usage_limits=UsageLimits(tool_calls_limit=MAX_TOOL_CALLS_PER_TURN),
        )
    except UsageLimitExceeded:
        # A model looping on a failing tool. Logged as a real signal — it usually means a
        # tool description or its error guidance is misleading, which is fixed in prose.
        logger.warning(
            "chat_tool_call_limit_exceeded",
            extra={"conversation_id": conversation.id, "user_id": user.id},
        )
        raise AgentUnavailableError() from None
    except AgentRunError:
        logger.exception(
            "chat_agent_run_failed",
            extra={"conversation_id": conversation.id, "user_id": user.id},
        )
        raise AgentUnavailableError() from None

    tool_calls = [
        ToolCallRecord(
            tool=record.tool,
            arguments=record.arguments,
            result_summary=record.result_summary,
        )
        for record in extract_tool_calls(result.new_messages())
    ]

    created_at = datetime.now(timezone.utc)
    user_message = Message(
        conversation_id=conversation.id, role="user", content=payload.message
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=result.output,
        tool_calls=[call.model_dump() for call in tool_calls] or None,
        created_at=created_at,
    )
    db.add(user_message)
    db.add(assistant_message)

    if settings.retrieval_log_enabled:
        _stage_retrieval_logs(
            db, deps, user, conversation, user_message, assistant_message
        )

    conversation.history_json = ModelMessagesTypeAdapter.dump_json(
        result.all_messages()
    ).decode()
    db.add(conversation)
    db.commit()

    # After the commit, so the assistant message the check references already exists by
    # the time the background task opens its own session. A turn that never called
    # `search_faq` gets no row at all — faithfulness to retrieved context is undefined
    # when nothing was retrieved.
    if settings.faithfulness_check_enabled and deps.retrieval_sink:
        _schedule_faithfulness_check(
            background_tasks,
            request,
            deps,
            settings,
            user,
            conversation,
            assistant_message,
            result.output,
        )

    logger.info(
        "chat_turn_completed",
        extra={
            "conversation_id": conversation.id,
            "user_id": user.id,
            "role": user.role,
            "tool_call_count": len(tool_calls),
            "tools_used": [call.tool for call in tool_calls],
        },
    )
    return ChatMessageResponse(
        conversation_id=conversation.id,
        reply=result.output,
        tool_calls=tool_calls,
        created_at=created_at,
    )
