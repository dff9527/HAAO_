import json

import httpx
import pytest

from clients.claude_po import ClaudeTechLeadClient
from clients.cloud_reasoner import AuditResult
from clients.factory import make_cloud_reasoner, split_provider
from clients.openai_compat import OpenAICompatError, OpenAICompatReasoner
from orchestrator.models.ticket import Ticket


def _openai_response(text: str, *, prompt_tokens: int = 0, completion_tokens: int = 0) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def test_openai_compat_decompose_parses_and_validates(fresh_ticket_dict) -> None:
    ticket_payload = fresh_ticket_dict.copy()
    ticket_payload["id"] = "T-501"
    ticket_payload["status"] = "backlog"
    ticket_payload.pop("result", None)

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        body = json.loads(request.content)
        captured["model"] = body["model"]
        return httpx.Response(200, json=_openai_response(json.dumps([ticket_payload])))

    client = OpenAICompatReasoner(
        "test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    tickets = client.decompose("Add bcrypt hashing", "auth module exists")

    assert len(tickets) == 1
    assert tickets[0]["id"] == "T-501"
    Ticket.from_dict(tickets[0])
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    assert captured["model"] == "gpt-4o"


def test_openai_compat_audit_returns_verdict_and_maps_usage(fresh_ticket_dict) -> None:
    ticket = Ticket.from_dict(fresh_ticket_dict)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_openai_response(
                json.dumps({"verdict": "approved", "feedback": "looks good"}),
                prompt_tokens=120,
                completion_tokens=8,
            ),
        )

    client = OpenAICompatReasoner(
        "test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.audit(ticket, "diff content")

    assert result == AuditResult(verdict="approved", feedback="looks good")
    assert client.last_usage.input_tokens == 120
    assert client.last_usage.output_tokens == 8


def test_openai_compat_missing_api_key_raises() -> None:
    client = OpenAICompatReasoner("", base_url="https://api.openai.com/v1", model="gpt-4o")
    with pytest.raises(OpenAICompatError, match="API key"):
        client.decompose("requirement", "context")


def test_split_provider() -> None:
    assert split_provider("openai:gpt-4o") == ("openai", "gpt-4o")
    assert split_provider("google:gemini-2.0-flash") == ("google", "gemini-2.0-flash")
    # Bare ids default to anthropic for backward compatibility.
    assert split_provider("claude-sonnet-4-6") == ("anthropic", "claude-sonnet-4-6")


def test_factory_dispatches_by_provider() -> None:
    anthropic = make_cloud_reasoner("anthropic:claude-sonnet-4-6", api_key="k")
    assert isinstance(anthropic, ClaudeTechLeadClient)
    assert anthropic.model == "claude-sonnet-4-6"

    openai = make_cloud_reasoner("openai:gpt-4o", api_key="k")
    assert isinstance(openai, OpenAICompatReasoner)
    assert openai.base_url == "https://api.openai.com/v1"
    assert openai.model == "gpt-4o"

    gemini = make_cloud_reasoner("google:gemini-2.0-flash", api_key="k")
    assert isinstance(gemini, OpenAICompatReasoner)
    assert gemini.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"


def test_factory_role_alias_and_bare_id_fall_back_to_claude_default() -> None:
    # The role alias isn't a real model id -> default Anthropic model.
    alias = make_cloud_reasoner("claude-tech-lead", api_key="k")
    assert isinstance(alias, ClaudeTechLeadClient)
    assert alias.model == "claude-sonnet-4-6"


def test_factory_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown cloud provider"):
        make_cloud_reasoner("acme:some-model", api_key="k")


def test_factory_explicit_base_url_overrides() -> None:
    client = make_cloud_reasoner(
        "myprovider:my-model", api_key="k", base_url="https://example.com/v1"
    )
    assert isinstance(client, OpenAICompatReasoner)
    assert client.base_url == "https://example.com/v1"
    assert client.model == "my-model"


def test_is_cloud_reasoner_model_recognizes_providers() -> None:
    from orchestrator.model_policy import is_cloud_reasoner_model

    # Legacy aliases
    assert is_cloud_reasoner_model("claude-tech-lead") is True
    assert is_cloud_reasoner_model("Claude · Tech Lead") is True
    # Provider-qualified cloud ids
    assert is_cloud_reasoner_model("openai:gpt-4o") is True
    assert is_cloud_reasoner_model("google:gemini-2.0-flash") is True
    assert is_cloud_reasoner_model("anthropic:claude-sonnet-4-6") is True
    # Local ids (bare or vendor/model) are not cloud
    assert is_cloud_reasoner_model("qwen3-coder-next") is False
    assert is_cloud_reasoner_model("qwen/qwen3-coder-next") is False
    assert is_cloud_reasoner_model("") is False
    assert is_cloud_reasoner_model(None) is False


def test_cloud_ids_are_not_treated_as_local() -> None:
    from orchestrator import model_policy as mp

    # LM Studio reports vendor-prefixed local ids; cloud ids must never resolve
    # to a local executor.
    mp_known = mp.known_local_models
    mp.known_local_models = lambda repo=None: {"qwen/qwen3-coder-next", "google/gemma-4-26b-a4b"}
    try:
        assert mp.is_local_execution_model("openai:gpt-4o") is False
        assert mp.resolve_local_model("google:gemini-2.0-flash") is None
        # A local id still resolves (and "google/..." is local, not the cloud "google:" provider)
        assert mp.resolve_local_model("qwen3-coder-next") == "qwen/qwen3-coder-next"
    finally:
        mp.known_local_models = mp_known
