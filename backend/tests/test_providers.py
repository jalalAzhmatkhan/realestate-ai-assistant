"""``app/agent/providers.py`` — the LLM provider factory.

Two properties carry the README's "switching providers is a `.env` change" claim
(Design Decisions §2), and both are the kind that read as true from the source and turn
out not to be:

- a missing API key surfaces as ``ProviderConfigurationError``, not as a ``KeyError``, an
  ``AttributeError``, or a provider SDK's own exception type — the whole point of the
  class is that a deployment misconfiguration is distinguishable from a bug;
- the factory is genuinely a **factory**, not a memoized singleton. An ``lru_cache`` added
  "for efficiency" would be invisible in review and would silently pin the first
  configuration for the process lifetime, which is exactly what the deferred-model-routing
  sign-off's one code-shape constraint exists to prevent.

No network and no real credentials: constructing a ``Model`` only builds a client.
"""

import pytest

from app.agent.providers import (
    _MODEL_BUILDERS,
    ProviderConfigurationError,
    build_llm_model,
)
from app.core.config import Settings

from .conftest import make_settings

# Each provider paired with the settings field carrying its key and the env var name the
# error message must name, since that name is the entire remediation instruction.
PROVIDERS = [
    ("openai", "openai_api_key", "OPENAI_API_KEY"),
    ("anthropic", "anthropic_api_key", "ANTHROPIC_API_KEY"),
    ("gemini", "gemini_api_key", "GEMINI_API_KEY"),
]


def configured(provider: str, key_field: str, key: str = "test-key", **overrides) -> Settings:
    return make_settings(llm_provider=provider, **{key_field: key}, **overrides)


# ------------------------------------------------------------------ missing credentials


@pytest.mark.parametrize(("provider", "key_field", "env_var"), PROVIDERS)
@pytest.mark.parametrize("missing", ["", "   ", "\t\n"])
def test_a_missing_key_raises_provider_configuration_error(provider, key_field, env_var, missing):
    """Whitespace counts as missing. A key of spaces is a copy-paste artifact, and
    letting it through turns a config error into a 401 from the provider at the first
    real chat request — much further from the cause."""
    with pytest.raises(ProviderConfigurationError) as excinfo:
        build_llm_model(configured(provider, key_field, key=missing))

    message = str(excinfo.value)
    assert env_var in message
    assert f"LLM_PROVIDER={provider}" in message


@pytest.mark.parametrize(("provider", "key_field", "env_var"), PROVIDERS)
def test_the_error_is_the_named_type_not_a_bare_runtime_error(provider, key_field, env_var):
    """``ProviderConfigurationError`` subclasses ``RuntimeError``, so a test asserting
    ``RuntimeError`` would pass even if the specific class were deleted and the raise
    replaced with a generic one. This asserts the exact class."""
    with pytest.raises(ProviderConfigurationError) as excinfo:
        build_llm_model(configured(provider, key_field, key=""))

    assert type(excinfo.value) is ProviderConfigurationError


@pytest.mark.parametrize(("provider", "key_field", "env_var"), PROVIDERS)
def test_only_the_selected_providers_key_is_required(provider, key_field, env_var):
    """The other two providers' keys stay empty. Requiring all three would make the
    ".env change" story false — you would need credentials for providers you never use."""
    model = build_llm_model(configured(provider, key_field))

    assert model is not None


def test_no_key_is_required_merely_to_import_the_module():
    """The factory-not-import-time-singleton constraint, stated as its consequence: the
    module is already imported by the time any assertion above runs, and none of them
    needed a credential to get here."""
    assert set(_MODEL_BUILDERS) == {"openai", "anthropic", "gemini"}


def test_the_builder_table_matches_the_settings_literal_exactly():
    """A provider half-added — a new ``Literal`` value with no builder — would fail at
    the first chat request with a ``KeyError`` rather than at config load."""
    literal_values = set(Settings.model_fields["llm_provider"].annotation.__args__)

    assert set(_MODEL_BUILDERS) == literal_values


# ------------------------------------------------------------------- factory, not cache


def test_two_calls_return_distinct_objects():
    settings = configured("openai", "openai_api_key")

    assert build_llm_model(settings) is not build_llm_model(settings)


def test_two_settings_produce_independently_configured_models():
    """The concrete failure a cache would cause: the second deployment's model name
    silently being the first's."""
    cheap = build_llm_model(
        configured("openai", "openai_api_key", key="key-cheap", openai_model="gpt-4o-mini")
    )
    strong = build_llm_model(
        configured("openai", "openai_api_key", key="key-strong", openai_model="gpt-4.1")
    )

    assert cheap.model_name == "gpt-4o-mini"
    assert strong.model_name == "gpt-4.1"
    # Not only the name: each carries its own provider client with its own credential, so
    # a future tiered get_model(tier) can point two tiers at two accounts.
    assert cheap.client.api_key == "key-cheap"
    assert strong.client.api_key == "key-strong"


def test_building_the_first_configuration_again_still_yields_the_first():
    """Order-sensitivity is how a cache actually manifests: the *third* call returning
    the second call's model. Asserting only "A then B differ" would miss it."""
    first = configured("openai", "openai_api_key", key="key-1", openai_model="gpt-4o-mini")
    second = configured("openai", "openai_api_key", key="key-2", openai_model="gpt-4.1")

    build_llm_model(first)
    build_llm_model(second)
    again = build_llm_model(first)

    assert again.model_name == "gpt-4o-mini"
    assert again.client.api_key == "key-1"


@pytest.mark.parametrize(
    ("provider", "key_field", "model_field", "expected_system"),
    [
        ("openai", "openai_api_key", "openai_model", "openai"),
        ("anthropic", "anthropic_api_key", "anthropic_model", "anthropic"),
        ("gemini", "gemini_api_key", "gemini_model", "google"),
    ],
)
def test_each_provider_builds_its_own_model_class_from_its_own_settings(
    provider, key_field, model_field, expected_system
):
    """``LLM_PROVIDER`` alone selects the backend, and the matching ``*_MODEL`` alone
    selects the model — no cross-wiring, which is the bug a dict-of-builders is meant to
    make impossible but which a copy-paste in one builder would still introduce."""
    model = build_llm_model(
        configured(provider, key_field, **{model_field: "configured-model-name"})
    )

    assert model.system == expected_system
    assert model.model_name == "configured-model-name"


def test_switching_provider_needs_no_change_beyond_settings():
    """The README's claim, asserted as behavior: the same call site, three configurations,
    three different backends."""
    systems = {
        build_llm_model(configured(provider, key_field)).system
        for provider, key_field, _ in PROVIDERS
    }

    assert systems == {"openai", "anthropic", "google"}
