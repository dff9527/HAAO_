from __future__ import annotations

from typing import Protocol

from clients.factory import make_cloud_reasoner
from clients.lmstudio import ChatMessage
from orchestrator.cloud_models import (
    CloudModelRegistryError,
    decrypt_cloud_model_key,
    get_cloud_model,
)
from orchestrator.cloud_usage import CloudUsage
from orchestrator.db.sqlite import SettingsRepository
from orchestrator.model_policy import is_cloud_reasoner_model


class ExecutionClient(Protocol):
    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        ...


class CloudExecutionAdapter:
    """chat_completion-compatible wrapper for registered cloud models."""

    is_cloud_execution = True

    def __init__(self, settings_repository: SettingsRepository, registry_id: str) -> None:
        model = get_cloud_model(settings_repository, registry_id)
        if model is None:
            raise CloudModelRegistryError(f"Cloud model is not registered: {registry_id}")
        api_key = decrypt_cloud_model_key(model)
        self.registry_id = model.id
        self.model_id = f"{model.provider}:{model.model_id}"
        self.client = make_cloud_reasoner(self.model_id, api_key=api_key)
        self.last_usage = CloudUsage()

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        del model, temperature, max_tokens
        self.client._ensure_ready()
        result = self.client._complete(_messages_to_prompt(messages))
        usage = getattr(self.client, "last_usage", CloudUsage())
        self.last_usage = usage if isinstance(usage, CloudUsage) else CloudUsage()
        return result

    def close(self) -> None:
        self.client.close()


def resolve_execution_client(
    assigned_model: str,
    *,
    local_client: ExecutionClient,
    settings_repository: SettingsRepository | None = None,
) -> ExecutionClient:
    if not is_cloud_reasoner_model(assigned_model):
        return local_client
    if settings_repository is None:
        raise CloudModelRegistryError("Settings repository is required for cloud execution")
    return CloudExecutionAdapter(settings_repository, assigned_model)


def _messages_to_prompt(messages: list[ChatMessage | dict[str, str]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if isinstance(message, dict):
            role = message.get("role", role)
            content = message.get("content", content)
        role_text = str(role or "user").upper()
        content_text = str(content or "")
        parts.append(f"{role_text}:\n{content_text}")
    return "\n\n".join(parts)
