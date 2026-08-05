"""``search_faq`` — RAG lookup over the knowledge base. No writes, no identity scoping."""

import logging

from pydantic import BaseModel
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.tools.errors import UpstreamToolError

logger = logging.getLogger(__name__)

TOOL_NAME = "search_faq"

# Carried into the tool schema the model sees. The system prompt is the cross-tool
# reasoning layer; this is this tool's own contract, and the model reads it every turn.
TOOL_DESCRIPTION = """\
Search the company's official FAQ knowledge base and return the matching entries.

Use this for any question about policy, process, or terms — required documents, deposits,
fees, pet rules, lease terms, cancellation and rescheduling policy, viewing hours,
maintenance responsibility, payment methods, and similar. Prefer calling it over
answering from memory whenever a question could plausibly be covered by company policy:
a policy answer that is merely plausible is worse than one you had to look up.

Ground your answer in the returned entries and nothing else. Do not invent, extrapolate,
or "fill in" a policy detail the entries do not state. An empty `results` list is a
normal, successful outcome meaning nothing in the knowledge base was close enough to be
trustworthy — when that happens, say you do not have a confirmed answer and offer to
connect the user with a human (EscalateToHuman) rather than guessing.\
"""


class FaqHitOut(BaseModel):
    id: str
    question: str
    answer: str
    category: str
    score: float


class SearchFaqOutput(BaseModel):
    results: list[FaqHitOut]


async def search_faq(
    ctx: RunContext[AgentDeps], query: str, top_k: int = 3
) -> SearchFaqOutput:
    """Search the FAQ knowledge base.

    Args:
        query: What the user wants to know, phrased as a natural-language question or
            topic (e.g. "documents required to rent", "how much is the deposit").
        top_k: How many entries to return. A hint only — the server applies its own
            ceiling.
    """
    settings = ctx.deps.settings
    # The LLM's top_k is a hint, not a ceiling: RAG_TOP_K is the server's limit, so a
    # model asking for 50 entries cannot flood its own context with weak matches.
    effective_top_k = max(1, min(top_k, settings.rag_top_k))

    try:
        hits = await ctx.deps.rag.search(
            query, top_k=effective_top_k, min_score=settings.rag_min_score
        )
    except Exception:
        # The embedding provider being unreachable is an infrastructure failure, not a
        # "no answer" — those are different outcomes and the model must be able to tell
        # them apart. The exception itself is logged, never relayed to the user.
        logger.exception(
            "search_faq_failed",
            extra={"tool": TOOL_NAME, "user_id": ctx.deps.user.id},
        )
        raise UpstreamToolError(
            "The knowledge base could not be reached, so this answer could not be verified.",
            guidance=(
                "Do not answer the policy question from memory. Apologize and offer to "
                "connect the user with a human agent."
            ),
        ) from None

    logger.info(
        "tool_call",
        extra={
            "tool": TOOL_NAME,
            "user_id": ctx.deps.user.id,
            "role": ctx.deps.user.role,
            "conversation_id": ctx.deps.conversation_id,
            "result_count": len(hits),
            "top_score": round(hits[0].score, 4) if hits else None,
        },
    )
    # Below RAG_MIN_SCORE the list is empty and that is a *success* (README's contract):
    # forcing a low-confidence answer is the failure mode this floor exists to prevent.
    return SearchFaqOutput(
        results=[
            FaqHitOut(
                id=hit.entry.id,
                question=hit.entry.question,
                answer=hit.entry.answer,
                category=hit.entry.category,
                score=round(hit.score, 4),
            )
            for hit in hits
        ]
    )
