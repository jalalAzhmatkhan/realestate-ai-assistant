"""``EMBEDDING_PROVIDER`` -> Pydantic AI ``EmbeddingModel``.

**Deliberately independent of ``LLM_PROVIDER``** (README Design Decisions §6): Anthropic
has no embeddings API, so ``LLM_PROVIDER=anthropic`` must still be able to answer FAQs.
This factory keys off ``settings.embedding_provider`` exclusively — the same rule
``Settings.embedding_model`` already follows when picking which ``*_EMBEDDING_MODEL``
applies.

``local`` is the default so the FAQ path works with **no API key at all**. It runs
``sentence-transformers`` in-process; note that the model weights are fetched from
Hugging Face on *first* use and cached under ``~/.cache/huggingface`` — genuinely
offline from the second run onward, and offline from the first if the cache is warm or
``HF_HUB_OFFLINE=1`` is set against a pre-populated cache.

Same factory-function shape and rationale as ``app/agent/providers.py``: constructing a
provider client at import time would make importing this module require credentials.
Each builder imports its backend lazily for the same reason — ``sentence-transformers``
pulls in torch, which a deployment using ``EMBEDDING_PROVIDER=openai`` should never have
to load.
"""

from collections.abc import Callable

from pydantic_ai.embeddings import EmbeddingModel

from app.core.config import Settings


class EmbeddingConfigurationError(RuntimeError):
    """The configured ``EMBEDDING_PROVIDER`` is missing a credential or a dependency."""


def _require_key(key: str, provider: str, env_var: str) -> str:
    if not key.strip():
        raise EmbeddingConfigurationError(
            f"EMBEDDING_PROVIDER={provider} requires {env_var} to be set. "
            f"Use EMBEDDING_PROVIDER=local for a zero-key local model instead."
        )
    return key


def _build_openai(settings: Settings) -> EmbeddingModel:
    from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
    from pydantic_ai.providers.openai import OpenAIProvider

    return OpenAIEmbeddingModel(
        settings.embedding_model,
        provider=OpenAIProvider(
            api_key=_require_key(settings.openai_api_key, "openai", "OPENAI_API_KEY")
        ),
    )


def _build_gemini(settings: Settings) -> EmbeddingModel:
    from pydantic_ai.embeddings.google import GoogleEmbeddingModel
    from pydantic_ai.providers.google import GoogleProvider

    return GoogleEmbeddingModel(
        settings.embedding_model,
        provider=GoogleProvider(
            api_key=_require_key(settings.gemini_api_key, "gemini", "GEMINI_API_KEY")
        ),
    )


def _build_local(settings: Settings) -> EmbeddingModel:
    try:
        from pydantic_ai.embeddings.sentence_transformers import (
            SentenceTransformerEmbeddingModel,
        )
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise EmbeddingConfigurationError(
            "EMBEDDING_PROVIDER=local requires the `sentence-transformers` package. "
            "Run `uv sync`, or set EMBEDDING_PROVIDER=openai/gemini instead."
        ) from exc

    return SentenceTransformerEmbeddingModel(settings.embedding_model)


_EMBEDDING_BUILDERS: dict[str, Callable[[Settings], EmbeddingModel]] = {
    "openai": _build_openai,
    "gemini": _build_gemini,
    "local": _build_local,
}


def build_embedding_model(settings: Settings) -> EmbeddingModel:
    """Construct the embedding model for the configured ``EMBEDDING_PROVIDER``."""
    return _EMBEDDING_BUILDERS[settings.embedding_provider](settings)
