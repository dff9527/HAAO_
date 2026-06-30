from __future__ import annotations

import copy
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator.api import (
    RequirementServiceGateway,
    get_attachment_storage,
    get_chat_repository,
    get_chat_service,
    get_settings_repository,
)
from orchestrator.attachments import AttachmentStorage
from orchestrator.chat_flow import ReasonerTurn, WorkItem
from orchestrator.cloud_models import (
    CLOUD_MODELS_SETTINGS_KEY,
    add_cloud_model,
    decrypt_cloud_model_key,
    list_cloud_models,
)
from orchestrator.cloud_usage import CloudUsage
from orchestrator.db.sqlite import (
    ChatRepository,
    RequirementRepository,
    SettingsRepository,
    TicketRepository,
    connect,
)
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.execution_resolver import resolve_execution_client
from orchestrator.main import app
from orchestrator.models.requirement import Requirement
from orchestrator.models.ticket import Ticket
from orchestrator.state_machine import TicketStateService


class _StaticReasoner:
    is_local = False

    def __init__(self) -> None:
        self.calls = []

    def respond(self, *, summary, recent, user_text):
        self.calls.append({"summary": summary, "recent": recent, "user_text": user_text})
        return ReasonerTurn(
            reply="Filed.",
            work_items=[WorkItem(title="Use attachment", prompt="Read the file.")],
        )


class _Gateway:
    def __init__(self) -> None:
        self.calls = []

    def file_backlog_proposal(self, *, project_id, title, prompt, attachment_ids=None):
        self.calls.append(
            {
                "project_id": project_id,
                "title": title,
                "prompt": prompt,
                "attachment_ids": attachment_ids or [],
            }
        )
        return "R-001"


class _LocalClient:
    def __init__(self) -> None:
        self.called = False

    def chat_completion(self, **kwargs):
        self.called = True
        return "local"


class _CloudClient:
    last_usage = CloudUsage(input_tokens=10, output_tokens=5)

    def chat_completion(self, **kwargs):
        return "updated content"

    def close(self):
        return None


def test_chat_attachment_upload_and_passthrough(tmp_path) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    chat_repository = ChatRepository(connection)
    gateway = _Gateway()

    from orchestrator.chat_flow import ChatService

    reasoner = _StaticReasoner()
    service = ChatService(chat_repository, reasoner, gateway)

    app.dependency_overrides[get_chat_repository] = lambda: chat_repository
    app.dependency_overrides[get_chat_service] = lambda: service
    app.dependency_overrides[get_attachment_storage] = lambda: AttachmentStorage(tmp_path / "attachments")
    try:
        client = TestClient(app)
        upload = client.post(
            "/api/chat/attachments",
            data={"project_id": "P-1"},
            files={"file": ("notes.txt", b"hello attachment", "text/plain")},
        )
        assert upload.status_code == 200
        attachment = upload.json()
        assert attachment["kind"] == "file"
        assert attachment["filename"] == "notes.txt"

        response = client.post(
            "/api/chat/messages",
            json={
                "project_id": "P-1",
                "text": "Please file this.",
                "attachment_ids": [attachment["id"]],
            },
        )
        assert response.status_code == 200
        assert response.json()["messages"][0]["attachment_ids"] == [attachment["id"]]
        assert "hello attachment" in reasoner.calls[0]["user_text"]
        assert gateway.calls[0]["attachment_ids"] == [attachment["id"]]
    finally:
        app.dependency_overrides.clear()


def test_requirement_gateway_converts_attachment_ids_to_requirement_attachments(tmp_path) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    chat_repository = ChatRepository(connection)
    ticket_repository = TicketRepository(connection)
    requirement_repository = RequirementRepository(connection)
    settings_repository = SettingsRepository(connection)
    upload = AttachmentStorage(tmp_path / "attachments").store(
        project_id="P-1",
        filename="notes.txt",
        mime="text/plain",
        content=b"hello",
    )
    attachment = chat_repository.create_attachment(project_id="P-1", upload=upload)

    class FakeRequirementService:
        project_id = "P-1"

        def next_requirement_id(self):
            return "R-001"

        def decompose_preview(self, requirement):
            self.requirement = requirement
            return SimpleNamespace(requirement=requirement, proposed_tickets=[])

    fake_service = FakeRequirementService()
    gateway = RequirementServiceGateway(
        fake_service,
        ticket_repository=ticket_repository,
        requirement_repository=requirement_repository,
        tech_lead=SimpleNamespace(),
        project_repository=SimpleNamespace(),
        settings_repository=settings_repository,
        chat_repository=chat_repository,
    )

    requirement_id = gateway.file_backlog_proposal(
        project_id="P-1",
        title="Use file",
        prompt="Please read it.",
        attachment_ids=[attachment.id],
    )

    assert requirement_id == "R-001"
    assert fake_service.requirement.attachments[0].type == "file"
    assert fake_service.requirement.attachments[0].value == attachment.stored_path


def test_chat_attachment_rejects_executable(tmp_path) -> None:
    chat_repository = ChatRepository(connect(tmp_path / "haao.sqlite3"))
    app.dependency_overrides[get_chat_repository] = lambda: chat_repository
    app.dependency_overrides[get_attachment_storage] = lambda: AttachmentStorage(tmp_path / "attachments")
    try:
        client = TestClient(app)
        response = client.post(
            "/api/chat/attachments",
            data={"project_id": "P-1"},
            files={"file": ("tool.exe", b"MZ", "application/x-msdownload")},
        )
        assert response.status_code == 400
        assert "Executable" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_cloud_registry_encrypts_key_and_requires_secret(tmp_path, monkeypatch) -> None:
    settings = SettingsRepository(connect(tmp_path / "haao.sqlite3"))

    monkeypatch.delenv("HAAO_SECRET_KEY", raising=False)
    response = TestClient(app)
    app.dependency_overrides[get_settings_repository] = lambda: settings
    try:
        denied = response.post(
            "/api/config/cloud-models",
            json={
                "label": "GPT",
                "provider": "openai",
                "model_id": "gpt-4o",
                "api_key": "sk-secret",
            },
        )
        assert denied.status_code == 400
        assert "HAAO_SECRET_KEY" in denied.json()["detail"]
    finally:
        app.dependency_overrides.clear()

    monkeypatch.setenv("HAAO_SECRET_KEY", "test-master-secret")
    model = add_cloud_model(
        settings,
        label="GPT",
        provider="openai",
        model_id="gpt-4o",
        api_key="sk-secret",
    )
    stored = settings.get_json(CLOUD_MODELS_SETTINGS_KEY, [])
    assert stored[0]["key_ref"] != "sk-secret"
    assert "sk-secret" not in stored[0]["key_ref"]
    assert model.to_public_dict()["key_configured"] is True
    assert decrypt_cloud_model_key(list_cloud_models(settings)[0]) == "sk-secret"


def test_execution_resolver_returns_local_or_cloud(tmp_path, monkeypatch) -> None:
    settings = SettingsRepository(connect(tmp_path / "haao.sqlite3"))
    local = _LocalClient()

    assert resolve_execution_client(
        "qwen3-coder-next",
        local_client=local,
        settings_repository=settings,
    ) is local

    monkeypatch.setenv("HAAO_SECRET_KEY", "test-master-secret")
    add_cloud_model(
        settings,
        label="GPT",
        provider="openai",
        model_id="gpt-4o",
        api_key="sk-secret",
    )
    client = resolve_execution_client(
        "openai:gpt-4o",
        local_client=local,
        settings_repository=settings,
    )
    assert getattr(client, "is_cloud_execution", False) is True


def test_cloud_execution_usage_records_to_ticket_and_requirement(
    tmp_path,
    fresh_ticket_dict,
) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    tickets = TicketRepository(connection)
    requirements = RequirementRepository(connection)
    settings = SettingsRepository(connection)
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "app.py"
    target.write_text("old content", encoding="utf-8")

    requirement = requirements.create(
        Requirement(id="R-001", prompt="Change app.py", project_id="default")
    )
    ticket_payload = copy.deepcopy(fresh_ticket_dict)
    ticket_payload["status"] = "in_progress"
    ticket_payload["task"]["target_files"] = ["app.py"]
    ticket_payload["context"]["files"] = [
        {"path": "app.py", "content": "old content", "truncated": False}
    ]
    ticket_payload["metadata"] = {"project_id": "default", "requirement_id": requirement.id}
    ticket = tickets.create(Ticket.from_dict(ticket_payload))

    loop = ExecutionLoop(
        tickets,
        TicketStateService(tickets),
        _LocalClient(),
        repo_root=repo,
        settings_repository=settings,
        requirement_repository=requirements,
    )
    updated = loop._record_cloud_execution_usage(ticket, _CloudClient())

    assert updated.metadata.model_dump(mode="json")["cloud_input_tokens"] == 10
    assert requirements.get("R-001").cloud_output_tokens == 5
    assert requirements.get("R-001").cloud_cost_usd > 0
