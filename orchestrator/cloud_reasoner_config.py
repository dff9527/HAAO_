from __future__ import annotations

import httpx

from clients.cloud_reasoner import BaseCloudReasoner
from clients.factory import OPENAI_COMPAT_BASE_URLS, make_cloud_reasoner, split_provider
from orchestrator.cloud_models import decrypt_cloud_model_key, get_cloud_model, list_cloud_models
from orchestrator.config import Settings
from orchestrator.db.sqlite import SettingsRepository

# Cloud providers the UI offers, with a friendly label. "anthropic" is the
# default; the others are OpenAI-compatible (see clients/factory.py).
CLOUD_PROVIDERS = {
    "anthropic": "Claude (Anthropic)",
    "openai": "OpenAI",
    "google": "Gemini (Google)",
    "openrouter": "OpenRouter",
    "together": "Together",
    "fireworks": "Fireworks",
}


def selected_cloud_reasoner_id(
    settings: Settings,
    settings_repository: SettingsRepository,
) -> str:
    """Provider-qualified id of the configured cloud reasoner.

    Falls back to the legacy single ``claude_model`` setting (treated as the
    Anthropic provider) when no explicit selection has been stored yet.
    """
    stored = settings_repository.get_cloud_reasoner("")
    if stored:
        return stored
    legacy_model = settings_repository.get_claude_model(settings.claude_model)
    return f"anthropic:{legacy_model}"


def default_anthropic_model_id(
    settings: Settings,
    settings_repository: SettingsRepository,
) -> str:
    legacy_model = settings_repository.get_claude_model(settings.claude_model)
    return f"anthropic:{legacy_model}"


def api_key_for_provider(provider: str, settings: Settings) -> str:
    key = {
        "anthropic": settings.claude_api_key,
        "openai": settings.openai_api_key,
        "google": settings.gemini_api_key,
        "gemini": settings.gemini_api_key,
    }.get(provider.lower(), "")
    return key or ""


def provider_options(settings: Settings) -> list[dict]:
    """Provider list for the UI, flagging which ones have an API key configured."""
    options: list[dict] = []
    for provider, label in CLOUD_PROVIDERS.items():
        options.append(
            {
                "id": provider,
                "label": label,
                "key_configured": bool(api_key_for_provider(provider, settings)),
            }
        )
    return options


def cloud_model_inventory(
    settings: Settings,
    settings_repository: SettingsRepository,
) -> list[dict]:
    default_id = default_anthropic_model_id(settings, settings_repository)
    default_entry = {
        "id": default_id,
        "label": "Claude (Anthropic) · default",
        "provider": "anthropic",
        "model_id": default_id.partition(":")[2],
        "key_configured": bool(api_key_for_provider("anthropic", settings)),
        "deletable": False,
    }
    registry_entries = [
        {**model.to_public_dict(), "deletable": True}
        for model in list_cloud_models(settings_repository)
        if model.id != default_id
    ]
    return [default_entry, *registry_entries]


def build_cloud_reasoner(
    settings: Settings,
    settings_repository: SettingsRepository,
    *,
    timeout_sec: float = 120.0,
    http_client: httpx.Client | None = None,
) -> BaseCloudReasoner:
    model_id = selected_cloud_reasoner_id(settings, settings_repository)
    provider, _ = split_provider(model_id)
    return make_cloud_reasoner(
        model_id,
        api_key=api_key_for_model(model_id, provider, settings, settings_repository),
        timeout_sec=timeout_sec,
        http_client=http_client,
    )


def api_key_for_model(
    model_id: str,
    provider: str,
    settings: Settings,
    settings_repository: SettingsRepository,
) -> str:
    registered = get_cloud_model(settings_repository, model_id)
    if registered is not None:
        return decrypt_cloud_model_key(registered)
    return api_key_for_provider(provider, settings)


def validate_cloud_reasoner_id(model_id: str) -> str:
    """Return the cleaned id if its provider is supported, else raise ValueError."""
    cleaned = model_id.strip()
    if not cleaned:
        raise ValueError("Cloud reasoner id cannot be empty")
    provider, model = split_provider(cleaned)
    if provider not in CLOUD_PROVIDERS and provider not in OPENAI_COMPAT_BASE_URLS:
        raise ValueError(
            f"Unknown cloud provider '{provider}'. Supported: " + ", ".join(sorted(CLOUD_PROVIDERS))
        )
    if provider != "anthropic" and not model:
        raise ValueError("A model id is required, e.g. 'openai:gpt-4o'")
    return cleaned
