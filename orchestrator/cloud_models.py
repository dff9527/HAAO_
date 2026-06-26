from __future__ import annotations

import json
from dataclasses import dataclass

from clients.factory import ANTHROPIC_ALIASES, OPENAI_COMPAT_BASE_URLS
from orchestrator.db.sqlite import SettingsRepository
from orchestrator.secrets_crypto import (
    SECRET_ENV_VAR,
    SecretEncryptionError,
    decrypt_secret as _decrypt_secret,
    encrypt_secret as _encrypt_secret,
)


CLOUD_MODELS_SETTINGS_KEY = "cloud_models"
class CloudModelRegistryError(ValueError):
    """Raised when a cloud model registry operation is invalid."""


@dataclass(frozen=True)
class CloudModel:
    id: str
    label: str
    provider: str
    model_id: str
    key_ref: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "model_id": self.model_id,
            "key_configured": bool(self.key_ref),
        }


def list_cloud_models(repository: SettingsRepository) -> list[CloudModel]:
    stored = repository.get_json(CLOUD_MODELS_SETTINGS_KEY, [])
    if not isinstance(stored, list):
        return []
    models: list[CloudModel] = []
    for item in stored:
        if not isinstance(item, dict):
            continue
        try:
            models.append(_model_from_json(item))
        except CloudModelRegistryError:
            continue
    return models


def add_cloud_model(
    repository: SettingsRepository,
    *,
    provider: str,
    model_id: str,
    api_key: str,
    label: str = "",
) -> CloudModel:
    cleaned_provider = _normalize_provider(provider)
    cleaned_model = model_id.strip()
    if not cleaned_model:
        raise CloudModelRegistryError("model_id cannot be empty")
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise CloudModelRegistryError("api_key cannot be empty")
    model = CloudModel(
        id=f"{cleaned_provider}:{cleaned_model}",
        label=label.strip() or f"{cleaned_provider}:{cleaned_model}",
        provider=cleaned_provider,
        model_id=cleaned_model,
        key_ref=encrypt_secret(cleaned_key),
    )
    models = [existing for existing in list_cloud_models(repository) if existing.id != model.id]
    models.append(model)
    repository.set_json(CLOUD_MODELS_SETTINGS_KEY, [_model_to_json(item) for item in models])
    return model


def delete_cloud_model(repository: SettingsRepository, model_id: str) -> bool:
    cleaned = model_id.strip()
    models = list_cloud_models(repository)
    kept = [model for model in models if model.id != cleaned]
    if len(kept) == len(models):
        return False
    repository.set_json(CLOUD_MODELS_SETTINGS_KEY, [_model_to_json(item) for item in kept])
    return True


def get_cloud_model(repository: SettingsRepository, model_id: str) -> CloudModel | None:
    cleaned = model_id.strip()
    for model in list_cloud_models(repository):
        if model.id == cleaned:
            return model
    return None


def decrypt_cloud_model_key(model: CloudModel) -> str:
    return decrypt_secret(model.key_ref)


def encrypt_secret(value: str) -> str:
    try:
        return _encrypt_secret(value)
    except SecretEncryptionError as exc:
        raise CloudModelRegistryError(str(exc).replace("storing encrypted secrets", "adding cloud keys")) from exc


def decrypt_secret(value: str) -> str:
    try:
        return _decrypt_secret(value)
    except SecretEncryptionError as exc:
        raise CloudModelRegistryError(str(exc)) from exc


def _normalize_provider(provider: str) -> str:
    cleaned = provider.strip().lower()
    if cleaned in ANTHROPIC_ALIASES:
        return "anthropic"
    if cleaned == "claude":
        return "anthropic"
    allowed = {"anthropic", *OPENAI_COMPAT_BASE_URLS.keys()}
    if cleaned not in allowed:
        raise CloudModelRegistryError(
            "provider must be one of: " + ", ".join(sorted(allowed))
        )
    return cleaned


def _model_from_json(item: dict) -> CloudModel:
    model_id = item.get("model_id")
    provider = item.get("provider")
    key_ref = item.get("key_ref")
    if not isinstance(model_id, str) or not isinstance(provider, str) or not isinstance(key_ref, str):
        raise CloudModelRegistryError("Invalid stored cloud model")
    cleaned_provider = _normalize_provider(provider)
    registry_id = item.get("id")
    if not isinstance(registry_id, str) or not registry_id.strip():
        registry_id = f"{cleaned_provider}:{model_id.strip()}"
    label = item.get("label")
    return CloudModel(
        id=registry_id.strip(),
        label=label.strip() if isinstance(label, str) and label.strip() else registry_id.strip(),
        provider=cleaned_provider,
        model_id=model_id.strip(),
        key_ref=key_ref,
    )


def _model_to_json(model: CloudModel) -> dict[str, str]:
    return {
        "id": model.id,
        "label": model.label,
        "provider": model.provider,
        "model_id": model.model_id,
        "key_ref": model.key_ref,
    }

