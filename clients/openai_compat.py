from __future__ import annotations

from typing import Any

import httpx

from clients.cloud_reasoner import BaseCloudReasoner, CloudReasonerError
from orchestrator.cloud_usage import CloudUsage


class OpenAICompatError(CloudReasonerError):
    """Raised when an OpenAI-compatible cloud reasoner cannot complete a request."""


class OpenAICompatReasoner(BaseCloudReasoner):
    """Tech Lead reasoner for any OpenAI-compatible chat-completions endpoint.

    Covers OpenAI, Gemini (via Google's OpenAI-compatible endpoint), OpenRouter,
    Together, Fireworks, etc. — anything that speaks the OpenAI ``/chat/completions``
    contract. Provider selection is just a different ``base_url`` + ``model``.
    """

    error_cls = OpenAICompatError

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        model: str,
        timeout_sec: float = 120.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(model=model, timeout_sec=timeout_sec, http_client=http_client)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _ensure_ready(self) -> None:
        if not self.api_key:
            raise OpenAICompatError("API key is not configured for this cloud provider")

    def _complete(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        try:
            response = self._client().post(url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise OpenAICompatError(
                f"Cloud request timed out after {self.timeout_sec}s"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise OpenAICompatError(
                f"Cloud provider returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenAICompatError(f"Cloud request failed: {exc}") from exc

        body = response.json()
        self.last_usage = _usage_from_openai_payload(body)
        return _extract_text(body)


def _usage_from_openai_payload(body: dict[str, Any]) -> CloudUsage:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return CloudUsage()
    return CloudUsage(
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        cost_status="estimated",
    )


def _extract_text(body: dict[str, Any]) -> str:
    try:
        message = body["choices"][0]["message"]
        content = message["content"]
        # Some providers return content as a list of typed parts.
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise KeyError("empty content")
        return content
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenAICompatError("Cloud provider response missing message content") from exc
