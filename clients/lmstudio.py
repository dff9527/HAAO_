from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

SUPPORTED_MODELS = frozenset(
    {
        "qwen3-coder-next",
        "gemma-4-26b-a4b",
        "qwen3.6-35b-a3b",
    }
)

DEFAULT_TIMEOUT_SEC = 120.0


class LMStudioError(RuntimeError):
    """Raised when LM Studio cannot complete a chat request."""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def to_api(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class LMStudioClient:
    """OpenAI-compatible client for a local LM Studio server."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self._http_client = http_client
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def __enter__(self) -> LMStudioClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": model,
            "messages": [
                message.to_api() if isinstance(message, ChatMessage) else message
                for message in messages
            ],
            "temperature": temperature,
        }

        try:
            response = self._client().post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LMStudioError(
                f"LM Studio request timed out after {self.timeout_sec}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise LMStudioError(
                f"Could not connect to LM Studio at {self.base_url}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LMStudioError(
                f"LM Studio returned HTTP {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioError(f"LM Studio request failed: {exc}") from exc

        return _extract_content(response.json())

    def list_models(self) -> list[str]:
        try:
            response = self._client().get(
                f"{self.base_url}/models",
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LMStudioError(
                f"OpenAI-compatible model list request timed out after {self.timeout_sec}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise LMStudioError(
                f"Could not connect to OpenAI-compatible endpoint at {self.base_url}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LMStudioError(
                f"OpenAI-compatible endpoint returned HTTP {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioError(f"OpenAI-compatible model list request failed: {exc}") from exc

        return _extract_model_ids(response.json())

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self.timeout_sec)
        return self._http_client

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}


def _extract_content(data: dict[str, Any]) -> str:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LMStudioError("LM Studio response missing chat completion content") from exc


def _extract_model_ids(data: dict[str, Any]) -> list[str]:
    raw_models = data.get("data")
    if not isinstance(raw_models, list):
        raise LMStudioError("OpenAI-compatible model list response missing data[]")
    model_ids: list[str] = []
    for raw_model in raw_models:
        if isinstance(raw_model, dict) and isinstance(raw_model.get("id"), str):
            model_id = raw_model["id"].strip()
            if model_id:
                model_ids.append(model_id)
    return sorted(set(model_ids))
