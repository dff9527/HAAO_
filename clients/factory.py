from __future__ import annotations

import httpx

from clients.claude_po import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from clients.claude_po import ClaudeTechLeadClient
from clients.cloud_reasoner import BaseCloudReasoner
from clients.openai_compat import OpenAICompatReasoner

# Known OpenAI-compatible providers -> base URL.
OPENAI_COMPAT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
}

# Bare ids that mean "the Anthropic Claude reasoner" rather than a real model id.
ANTHROPIC_ALIASES = {"anthropic", "claude", "claude-tech-lead", "claude · tech lead"}


def split_provider(model_id: str) -> tuple[str, str]:
    """Split a provider-qualified id (``openai:gpt-4o``) into ``(provider, model)``.

    Bare ids (no ``provider:`` prefix) default to the ``anthropic`` provider for
    backward compatibility with the existing single-provider configuration.
    """
    if ":" in model_id:
        provider, _, model = model_id.partition(":")
        return provider.strip().lower(), model.strip()
    return "anthropic", model_id.strip()


def make_cloud_reasoner(
    model_id: str,
    *,
    api_key: str,
    base_url: str | None = None,
    timeout_sec: float = 120.0,
    http_client: httpx.Client | None = None,
) -> BaseCloudReasoner:
    """Build the right cloud reasoner for a (provider-qualified) model id.

    Examples: ``anthropic:claude-sonnet-4-6``, ``openai:gpt-4o``,
    ``google:gemini-2.0-flash``. Bare ids and the ``claude-tech-lead`` role alias
    resolve to the Anthropic client so existing config keeps working.
    """
    provider, model = split_provider(model_id)

    if provider in ANTHROPIC_ALIASES:
        # A role alias like "claude-tech-lead" isn't a real model id -> use default.
        chosen = model if model and model.lower() not in ANTHROPIC_ALIASES else ANTHROPIC_DEFAULT_MODEL
        return ClaudeTechLeadClient(
            api_key,
            model=chosen,
            timeout_sec=timeout_sec,
            http_client=http_client,
        )

    resolved_base = base_url or OPENAI_COMPAT_BASE_URLS.get(provider)
    if resolved_base is None:
        raise ValueError(
            f"Unknown cloud provider '{provider}'. Pass an explicit base_url or use one of: "
            + ", ".join(sorted(OPENAI_COMPAT_BASE_URLS))
        )
    return OpenAICompatReasoner(
        api_key,
        base_url=resolved_base,
        model=model,
        timeout_sec=timeout_sec,
        http_client=http_client,
    )
