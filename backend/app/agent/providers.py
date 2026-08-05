"""``LLM_PROVIDER`` -> Pydantic AI ``Model``.

This is the **only** module in the codebase that knows an LLM provider exists by name
(README Design Decisions §2). The orchestrator builds one ``Agent`` against whatever
this returns; switching providers is a ``.env`` change, and adding a fourth is one
entry in ``_MODEL_BUILDERS`` plus its config fields.

**Factory function, not an import-time singleton** — the one code-shape constraint
carried over from the deferred-model-routing sign-off
(``Documentation/audits/2026-08-05-pm-signoffs-model-routing-and-escalation-assignment.md``
Decision 1). A module-level ``model = OpenAIChatModel(...)`` would (a) require an API
key merely to *import* the package, breaking every test that never talks to a model,
and (b) make a future tiered ``build_llm_model(settings, tier=...)`` a rewrite of the
orchestrator's construction path rather than one added parameter. No ``tier`` parameter
is added today: model routing is deliberately out of MVP scope, and a parameter nothing
passes is worse documentation than its absence.
"""

from collections.abc import Callable

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.config import Settings


class ProviderConfigurationError(RuntimeError):
    """``LLM_PROVIDER`` names a provider whose API key is missing.

    Deliberately raised here rather than at ``Settings`` validation time: the API key
    is only required to *talk to* a model. Validating it in ``Settings`` would stop the
    app booting — and every test constructing one — for a credential most of the
    surface never needs.
    """


def _require_key(key: str, provider: str, env_var: str) -> str:
    if not key.strip():
        raise ProviderConfigurationError(
            f"LLM_PROVIDER={provider} requires {env_var} to be set."
        )
    return key


def _build_openai(settings: Settings) -> Model:
    return OpenAIChatModel(
        settings.openai_model,
        provider=OpenAIProvider(
            api_key=_require_key(settings.openai_api_key, "openai", "OPENAI_API_KEY")
        ),
    )


def _build_anthropic(settings: Settings) -> Model:
    return AnthropicModel(
        settings.anthropic_model,
        provider=AnthropicProvider(
            api_key=_require_key(
                settings.anthropic_api_key, "anthropic", "ANTHROPIC_API_KEY"
            )
        ),
    )


def _build_gemini(settings: Settings) -> Model:
    return GoogleModel(
        settings.gemini_model,
        provider=GoogleProvider(
            api_key=_require_key(settings.gemini_api_key, "gemini", "GEMINI_API_KEY")
        ),
    )


# Keyed by the literal values Settings.llm_provider accepts, so a new provider cannot
# be half-added: the Literal and this table have to agree or mypy/the KeyError catches it.
_MODEL_BUILDERS: dict[str, Callable[[Settings], Model]] = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "gemini": _build_gemini,
}


def build_llm_model(settings: Settings) -> Model:
    """Construct the chat/tool-calling model for the configured provider.

    Called once per application instance (lazily, on the first chat request — see
    ``app/api/chat.py``), never at import.
    """
    return _MODEL_BUILDERS[settings.llm_provider](settings)
