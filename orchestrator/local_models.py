from __future__ import annotations

from dataclasses import dataclass

from clients.lmstudio import LMStudioClient, LMStudioError, SUPPORTED_MODELS
from orchestrator.config import Settings
from orchestrator.db.sqlite import SettingsRepository

LOCAL_MODEL_ENDPOINTS_SETTINGS_KEY = "local_model_endpoints"
LOCAL_MODEL_CACHE_SETTINGS_KEY = "local_model_cache"


@dataclass(frozen=True)
class LocalModelEndpoint:
    id: str
    label: str
    base_url: str
    api_key: str = ""

    def to_public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "base_url": self.base_url,
            "api_key": self.api_key,
        }


@dataclass(frozen=True)
class LocalModelEndpointResult:
    endpoint: LocalModelEndpoint
    ok: bool
    models: list[str]
    error: str = ""

    def to_dict(self) -> dict:
        payload = self.endpoint.to_public_dict()
        payload["ok"] = self.ok
        payload["models"] = self.models
        payload["error"] = self.error
        return payload


def get_local_model_endpoints(
    repository: SettingsRepository,
    settings: Settings,
) -> list[LocalModelEndpoint]:
    stored = repository.get_json(LOCAL_MODEL_ENDPOINTS_SETTINGS_KEY)
    if not isinstance(stored, list):
        return [default_local_model_endpoint(settings)]
    endpoints = [_endpoint_from_dict(item) for item in stored if isinstance(item, dict)]
    return endpoints or [default_local_model_endpoint(settings)]


def set_local_model_endpoints(
    repository: SettingsRepository,
    endpoints: list[LocalModelEndpoint],
) -> list[LocalModelEndpoint]:
    normalized = endpoints or []
    repository.set_json(
        LOCAL_MODEL_ENDPOINTS_SETTINGS_KEY,
        [endpoint.to_public_dict() for endpoint in normalized],
    )
    return normalized


def discover_local_models(
    endpoints: list[LocalModelEndpoint],
    *,
    timeout_sec: float = 8.0,
) -> list[LocalModelEndpointResult]:
    results: list[LocalModelEndpointResult] = []
    for endpoint in endpoints:
        client = LMStudioClient(
            endpoint.base_url,
            api_key=endpoint.api_key,
            timeout_sec=timeout_sec,
        )
        try:
            models = client.list_models()
        except LMStudioError as exc:
            results.append(
                LocalModelEndpointResult(
                    endpoint=endpoint,
                    ok=False,
                    models=[],
                    error=str(exc),
                )
            )
        else:
            results.append(
                LocalModelEndpointResult(
                    endpoint=endpoint,
                    ok=True,
                    models=models,
                )
            )
        finally:
            client.close()
    return results


def cache_local_models(
    repository: SettingsRepository,
    model_ids: list[str],
) -> list[str]:
    models = sorted({model_id.strip() for model_id in model_ids if model_id.strip()})
    repository.set_json(LOCAL_MODEL_CACHE_SETTINGS_KEY, models)
    return models


def cached_local_models(repository: SettingsRepository | None = None) -> set[str]:
    if repository is None:
        return set()
    stored = repository.get_json(LOCAL_MODEL_CACHE_SETTINGS_KEY)
    if not isinstance(stored, list):
        return set()
    return {item.strip() for item in stored if isinstance(item, str) and item.strip()}


def default_local_models() -> set[str]:
    return set(SUPPORTED_MODELS)


def default_local_model_endpoint(settings: Settings) -> LocalModelEndpoint:
    return LocalModelEndpoint(
        id="lmstudio",
        label="LM Studio",
        base_url=settings.lmstudio_base_url,
        api_key="",
    )


def _endpoint_from_dict(data: dict) -> LocalModelEndpoint:
    raw_id = data.get("id")
    raw_label = data.get("label")
    raw_base_url = data.get("base_url")
    raw_api_key = data.get("api_key")
    endpoint_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else "local"
    label = raw_label if isinstance(raw_label, str) and raw_label.strip() else endpoint_id
    base_url = (
        raw_base_url
        if isinstance(raw_base_url, str) and raw_base_url.strip()
        else "http://localhost:1234/v1"
    )
    api_key = raw_api_key if isinstance(raw_api_key, str) else ""
    return LocalModelEndpoint(
        id=endpoint_id.strip(),
        label=label.strip(),
        base_url=base_url.strip().rstrip("/"),
        api_key=api_key,
    )
