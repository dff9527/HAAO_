from __future__ import annotations

from orchestrator.db.sqlite import SettingsRepository
from orchestrator.local_models import cached_local_models, default_local_models
from orchestrator.role_routing import DEFAULT_ROLE_ROUTING, role_routing_store

# Legacy aliases for the cloud Tech Lead, kept for backward compatibility with
# stored routing/tickets created before provider-qualified ids existed.
CLOUD_EXECUTION_MODELS = {
    "claude-tech-lead",
    "Claude · Tech Lead",
    "Claude PO",
}

# Provider-qualified cloud ids use a "provider:model" prefix (e.g. "openai:gpt-4o").
# Keep this set in sync with clients/factory.py provider keys.
CLOUD_PROVIDER_PREFIXES = {
    "anthropic",
    "openai",
    "google",
    "gemini",
    "openrouter",
    "together",
    "fireworks",
}


def is_cloud_reasoner_model(model_id: str | None) -> bool:
    """True if ``model_id`` names a cloud reasoner (any provider), not a local model.

    Recognises both the legacy aliases and provider-qualified ids such as
    ``openai:gpt-4o`` / ``google:gemini-2.0-flash``. Local LM Studio ids use a
    ``vendor/model`` form (slash, no provider prefix) and are not matched here.
    """
    if not isinstance(model_id, str) or not model_id.strip():
        return False
    if model_id in CLOUD_EXECUTION_MODELS:
        return True
    provider, sep, _ = model_id.partition(":")
    return bool(sep) and provider.strip().lower() in CLOUD_PROVIDER_PREFIXES


def known_local_models(repository: SettingsRepository | None = None) -> set[str]:
    models = cached_local_models(repository)
    return models or default_local_models()


def _bare_model(model: str) -> str:
    """Model id without its vendor prefix (``qwen/qwen3-coder-next`` -> ``qwen3-coder-next``)."""
    if not isinstance(model, str):
        return ""
    return model.rsplit("/", 1)[-1] if "/" in model else model


def resolve_local_model(
    candidate: str | None,
    repository: SettingsRepository | None = None,
) -> str | None:
    """Return the known local model id matching ``candidate``, ignoring vendor
    prefixes, or ``None`` if it isn't a known local model.

    Model ids reach us in two forms: the bare id used in role routing/defaults
    (``qwen3-coder-next``) and the vendor-prefixed id LM Studio reports
    (``qwen/qwen3-coder-next``). Matching must treat them as the same model, and
    we return the form that is actually loaded so execution targets a real id.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    if is_cloud_reasoner_model(candidate):
        return None
    models = known_local_models(repository)
    if candidate in models:
        return candidate
    target = _bare_model(candidate)
    for model in models:
        if is_cloud_reasoner_model(model):
            continue
        if _bare_model(model) == target:
            return model
    return None


def is_local_execution_model(
    candidate: str | None,
    repository: SettingsRepository | None = None,
) -> bool:
    return resolve_local_model(candidate, repository) is not None


def local_execution_model(
    candidate: str | None,
    repository: SettingsRepository | None = None,
) -> str:
    resolved = resolve_local_model(candidate, repository)
    if resolved is not None:
        return resolved

    for routed in local_model_fallback_chain(repository):
        if not is_cloud_reasoner_model(routed):
            return routed

    fallback = resolve_local_model(DEFAULT_ROLE_ROUTING["dev_team"], repository)
    if fallback is not None:
        return fallback

    return sorted(known_local_models(repository))[0]


def local_model_fallback_chain(repository: SettingsRepository | None = None) -> list[str]:
    models = known_local_models(repository)
    routed = role_routing_store.get().get("dev_team") or DEFAULT_ROLE_ROUTING["dev_team"]
    candidates = routed if isinstance(routed, list) else [routed]
    chain: list[str] = []
    for candidate in candidates:
        resolved = resolve_local_model(candidate, repository)
        if resolved is not None and resolved not in chain:
            chain.append(resolved)
    fallback = resolve_local_model(DEFAULT_ROLE_ROUTING["dev_team"], repository)
    if fallback is not None and fallback not in chain:
        chain.append(fallback)
    return chain or sorted(models)


def next_local_fallback_model(
    current_model: str,
    repository: SettingsRepository | None = None,
) -> str | None:
    chain = local_model_fallback_chain(repository)
    resolved_current = resolve_local_model(current_model, repository) or current_model
    if resolved_current not in chain:
        return chain[0] if chain else None
    index = chain.index(resolved_current)
    if index + 1 >= len(chain):
        return None
    return chain[index + 1]


def enforce_local_execution_model(
    ticket_dict: dict,
    repository: SettingsRepository | None = None,
) -> bool:
    execution = ticket_dict.setdefault("execution", {})
    original = execution.get("assigned_model")
    sanitized = local_execution_model(
        original if isinstance(original, str) else None,
        repository=repository,
    )
    execution["assigned_model"] = sanitized
    return original != sanitized
