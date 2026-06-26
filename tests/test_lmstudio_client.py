import json

import httpx
import pytest

from clients.lmstudio import ChatMessage, LMStudioClient, LMStudioError, SUPPORTED_MODELS


def test_supported_models_constant() -> None:
    assert "qwen3-coder-next" in SUPPORTED_MODELS
    assert "gemma-4-26b-a4b" in SUPPORTED_MODELS
    assert "qwen3.6-35b-a3b" in SUPPORTED_MODELS


def test_chat_completion_returns_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3-coder-next"
        assert payload["messages"][0]["content"] == "hello"
        assert payload["max_tokens"] == 1024
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "world"}},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = LMStudioClient(
        "http://localhost:1234/v1",
        http_client=httpx.Client(transport=transport),
    )

    content = client.chat_completion(
        model="qwen3-coder-next",
        messages=[ChatMessage(role="user", content="hello")],
        temperature=0.1,
        max_tokens=1024,
    )

    assert content == "world"


def test_chat_completion_allows_endpoint_model_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "user-owned-model"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = LMStudioClient(
        "http://localhost:1234/v1",
        http_client=httpx.Client(transport=transport),
    )

    assert client.chat_completion(
        model="user-owned-model",
        messages=[ChatMessage(role="user", content="hello")],
    ) == "ok"


def test_chat_completion_retries_http_400_once() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(400, text="context overflow")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    client = LMStudioClient(
        "http://localhost:1234/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.chat_completion(
        model="qwen3-coder-next",
        messages=[ChatMessage(role="user", content="hello")],
    ) == "ok"
    assert calls == 2


def test_chat_completion_persistent_http_400_raises_after_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="context overflow")

    client = LMStudioClient(
        "http://localhost:1234/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(LMStudioError, match="HTTP 400 after 2 attempts"):
        client.chat_completion(
            model="qwen3-coder-next",
            messages=[ChatMessage(role="user", content="hello")],
        )
    assert calls == 2


def test_list_models_returns_openai_compatible_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "z-model"},
                    {"id": "a-model"},
                    {"id": "a-model"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = LMStudioClient(
        "http://localhost:1234/v1",
        http_client=httpx.Client(transport=transport),
    )

    assert client.list_models() == ["a-model", "z-model"]


def test_connection_error_is_clear() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", "http://test"))

    transport = httpx.MockTransport(handler)
    client = LMStudioClient(
        "http://localhost:1234/v1",
        http_client=httpx.Client(transport=transport),
    )

    with pytest.raises(LMStudioError, match="Could not connect to LM Studio"):
        client.chat_completion(
            model="qwen3-coder-next",
            messages=[ChatMessage(role="user", content="hello")],
        )


def test_timeout_error_is_clear() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    transport = httpx.MockTransport(handler)
    client = LMStudioClient(
        "http://localhost:1234/v1",
        http_client=httpx.Client(transport=transport),
    )

    with pytest.raises(LMStudioError, match="timed out"):
        client.chat_completion(
            model="qwen3-coder-next",
            messages=[ChatMessage(role="user", content="hello")],
        )
