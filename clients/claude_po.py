from __future__ import annotations

from typing import Any

import httpx

from clients.cloud_reasoner import (
    AuditResult,
    BaseCloudReasoner,
    CloudReasonerError,
)
from clients.cloud_reasoner import apply_additional_instructions as _apply_additional_instructions
from orchestrator.cloud_usage import usage_from_api_payload

DEFAULT_MODEL = "claude-sonnet-4-6"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"


class ClaudeTechLeadError(CloudReasonerError):
    """Raised when the Claude Tech Lead client cannot complete a request."""


class ClaudeTechLeadClient(BaseCloudReasoner):
    """Claude (Anthropic) client for Tech Lead decomposition and technical audit."""

    error_cls = ClaudeTechLeadError

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        timeout_sec: float = 120.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(model=model, timeout_sec=timeout_sec, http_client=http_client)
        self.api_key = api_key

    def _ensure_ready(self) -> None:
        if not self.api_key:
            raise ClaudeTechLeadError("CLAUDE_API_KEY is not configured")

    def _complete(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        try:
            response = self._client().post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ClaudeTechLeadError(
                f"Claude request timed out after {self.timeout_sec}s"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ClaudeTechLeadError(
                f"Claude returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ClaudeTechLeadError(f"Claude request failed: {exc}") from exc

        body = response.json()
        self.last_usage = usage_from_api_payload(body)
        return _extract_text(body)


def list_available_claude_models(
    api_key: str,
    *,
    timeout_sec: float = 15.0,
    http_client: httpx.Client | None = None,
) -> list[str]:
    if not api_key:
        return []

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout_sec)
    try:
        response = client.get(
            ANTHROPIC_MODELS_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return []
    finally:
        if owns_client:
            client.close()

    models = payload.get("data", [])
    if not isinstance(models, list):
        return []
    ids = [item.get("id") for item in models if isinstance(item, dict)]
    return sorted(model_id for model_id in ids if isinstance(model_id, str) and model_id.strip())


def _extract_text(data: dict[str, Any]) -> str:
    try:
        blocks = data["content"]
        texts = [block["text"] for block in blocks if block.get("type") == "text"]
        if not texts:
            raise KeyError("no text blocks")
        return "\n".join(texts)
    except (KeyError, TypeError) as exc:
        raise ClaudeTechLeadError("Claude response missing text content") from exc


# Backward-compatible aliases.
ClaudePOClient = ClaudeTechLeadClient
ClaudePOError = ClaudeTechLeadError

__all__ = [
    "AuditResult",
    "ClaudeTechLeadClient",
    "ClaudeTechLeadError",
    "ClaudePOClient",
    "ClaudePOError",
    "DEFAULT_MODEL",
    "list_available_claude_models",
]
