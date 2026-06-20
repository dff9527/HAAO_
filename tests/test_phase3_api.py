from __future__ import annotations

import copy
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator.api import (
    get_auto_orchestrator,
    get_project_repository,
    get_repository,
    get_requirement_service,
    get_settings_repository,
)
from orchestrator.db.sqlite import DuplicateTicketError, SettingsRepository, TicketRepository, connect
from orchestrator.main import app
from orchestrator.models.ticket import Ticket
from tests.conftest import init_git_repo


class FakeRequirementService:
    def __init__(self, ticket: Ticket) -> None:
        self.ticket = ticket

    def submit(self, requirement: str, repo_context: str = ""):
        class Result:
            tickets = [self.ticket]

        assert requirement == "Build a tiny thing"
        return Result()

    def next_requirement_id(self):
        return "R-001"

    def decompose_preview(self, requirement):
        assert requirement.prompt == "Build a tiny thing"
        updated_requirement = requirement.model_copy(
            update={
                "status": "preview_ready",
                "proposed_tickets": [self.ticket.to_dict()],
            }
        )
        return SimpleNamespace(
            requirement=updated_requirement,
            proposed_tickets=[self.ticket],
        )

    def confirm(self, requirement_id: str, tickets: list[dict] | None = None):
        requirement = SimpleNamespace(
            to_dict=lambda: {
                "id": requirement_id,
                "status": "confirmed",
                "generated_ticket_ids": ["T-300"],
            }
        )
        return SimpleNamespace(requirement=requirement, tickets=[self.ticket])

    def discard(self, requirement_id: str):
        class Requirement:
            def to_dict(self):
                return {"id": requirement_id, "status": "discarded"}

        return Requirement()

    def list_requirements(self):
        return []

    def get_requirement(self, requirement_id: str):
        return None


class DuplicateTicketRequirementService(FakeRequirementService):
    def confirm(self, requirement_id: str, tickets: list[dict] | None = None):
        raise DuplicateTicketError("Ticket id already exists: T-001")


def make_api_ticket(ticket_dict: dict, status: str = "backlog") -> Ticket:
    payload = copy.deepcopy(ticket_dict)
    payload["id"] = "T-300"
    payload["status"] = status
    payload["dependencies"] = []
    payload.pop("result", None)
    return Ticket.from_dict(payload)


def test_b012_tickets_rest_api_and_manual_move(tmp_path, fresh_ticket_dict) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(make_api_ticket(fresh_ticket_dict))

    def override_repository():
        return repository

    app.dependency_overrides[get_repository] = override_repository
    try:
        client = TestClient(app)

        response = client.get("/tickets")
        assert response.status_code == 200
        assert response.json()["tickets"][0]["id"] == "T-300"

        response = client.get("/tickets/T-300")
        assert response.status_code == 200
        assert response.json()["ticket"]["status"] == "backlog"

        response = client.post("/tickets/T-300/move", json={"status": "ready"})
        assert response.status_code == 200
        assert response.json()["ticket"]["status"] == "ready"

        response = client.post("/tickets/T-300/move", json={"status": "done"})
        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_product_owner_gate_endpoints_and_role_routing(tmp_path, fresh_ticket_dict) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(make_api_ticket(fresh_ticket_dict))

    def override_repository():
        return repository

    app.dependency_overrides[get_repository] = override_repository
    try:
        client = TestClient(app)

        response = client.put(
            "/config/role-routing",
            json={"routing": {"dev_team": "gemma-4-26b-a4b"}},
        )
        assert response.status_code == 200
        assert response.json()["routing"]["dev_team"] == "gemma-4-26b-a4b"

        response = client.get("/config/role-routing")
        assert response.status_code == 200
        assert response.json()["routing"]["tech_lead"] == "claude-tech-lead"

        fresh_client = TestClient(app)
        response = fresh_client.get("/config/role-routing")
        assert response.status_code == 200
        assert response.json()["routing"]["dev_team"] == "gemma-4-26b-a4b"

        response = client.post("/tickets/T-300/approve")
        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert ticket["status"] == "ready"
        assert ticket["metadata"]["needs_approval"] is False
        # Approve preserves the ticket's own valid local model rather than
        # forcing the global dev_team routing model.
        assert ticket["execution"]["assigned_model"] == "qwen3-coder-next"

        response = client.post(
            "/tickets/T-300/assign_model",
            json={"model": "claude-tech-lead"},
        )
        assert response.status_code == 200
        assert response.json()["ticket"]["execution"]["assigned_model"] == "gemma-4-26b-a4b"

        repository.update_status("T-300", "review")
        ticket_json = repository.get("T-300").to_dict()
        ticket_json["status"] = "awaiting_acceptance"
        repository.save(Ticket.from_dict(ticket_json))

        response = client.post("/tickets/T-300/reject", json={"feedback": "Not valuable enough"})
        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert ticket["status"] == "backlog"
        assert ticket["metadata"]["product_rejection_feedback"] == "Not valuable enough"

        ticket_json = repository.get("T-300").to_dict()
        ticket_json["status"] = "awaiting_acceptance"
        repository.save(Ticket.from_dict(ticket_json))
        response = client.post("/tickets/T-300/accept")
        assert response.status_code == 200
        assert response.json()["ticket"]["status"] == "done"
    finally:
        app.dependency_overrides.clear()


def test_retry_resets_stale_result_but_preserves_rework_diff(
    tmp_path,
    fresh_ticket_dict,
) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    payload = make_api_ticket(fresh_ticket_dict, status="backlog").to_dict()
    payload["result"] = {
        "outcome": "success",
        "diff": "diff --git a/app.py b/app.py\n+fixed once\n",
        "test_output": "passed",
    }
    payload["audit"] = {
        "reviewed_by": "claude-tech-lead",
        "verdict": "rejected",
        "feedback": "Add the missing edge case.",
    }
    repository.create(Ticket.from_dict(payload))

    def override_repository():
        return repository

    app.dependency_overrides[get_repository] = override_repository
    try:
        client = TestClient(app)

        response = client.post("/tickets/T-300/retry")

        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert ticket["status"] == "ready"
        assert ticket["result"]["outcome"] == "pending"
        assert "diff" not in ticket["result"]
        assert ticket["metadata"]["previous_rejected_diff"].startswith("diff --git")
        assert ticket["metadata"]["previous_review_feedback"] == "Add the missing edge case."
        assert ticket["audit"]["verdict"] == "pending"
    finally:
        app.dependency_overrides.clear()


def test_b021_delete_ticket_endpoint_removes_ticket_and_logs(tmp_path, fresh_ticket_dict) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(make_api_ticket(fresh_ticket_dict))
    repository.append_log("T-300", "soon deleted")

    def override_repository():
        return repository

    app.dependency_overrides[get_repository] = override_repository
    try:
        client = TestClient(app)
        response = client.delete("/tickets/T-300")
        assert response.status_code == 200
        assert response.json() == {"deleted": True, "ticket_id": "T-300"}
        assert repository.get("T-300") is None
        assert repository.logs_for_ticket("T-300") == []

        response = client.delete("/tickets/T-404")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_b021_delete_running_ticket_requires_force(tmp_path, fresh_ticket_dict) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(make_api_ticket(fresh_ticket_dict, status="in_progress"))

    def override_repository():
        return repository

    app.dependency_overrides[get_repository] = override_repository
    try:
        client = TestClient(app)
        response = client.delete("/tickets/T-300")
        assert response.status_code == 409

        response = client.delete("/tickets/T-300?force=true")
        assert response.status_code == 200
        assert repository.get("T-300") is None
    finally:
        app.dependency_overrides.clear()


def test_b012_requirements_endpoint_uses_service(fresh_ticket_dict) -> None:
    ticket = make_api_ticket(fresh_ticket_dict)

    def override_requirement_service():
        return FakeRequirementService(ticket)

    app.dependency_overrides[get_requirement_service] = override_requirement_service
    try:
        client = TestClient(app)
        response = client.post("/requirements", json={"requirement": "Build a tiny thing"})
        assert response.status_code == 200
        assert response.json()["tickets"][0]["id"] == "T-300"
    finally:
        app.dependency_overrides.clear()


def test_b013_requirement_preview_confirm_and_discard_endpoints(fresh_ticket_dict) -> None:
    ticket = make_api_ticket(fresh_ticket_dict)

    def override_requirement_service():
        return FakeRequirementService(ticket)

    app.dependency_overrides[get_requirement_service] = override_requirement_service
    try:
        client = TestClient(app)
        response = client.post(
            "/requirements/decompose",
            json={
                "prompt": "Build a tiny thing",
                "scope_paths": ["orchestrator/main.py"],
                "constraints": ["Keep it small"],
                "acceptance_notes": "Useful to PO",
            },
        )
        assert response.status_code == 200
        assert response.json()["requirement"]["status"] == "preview_ready"
        assert response.json()["proposed_tickets"][0]["id"] == "T-300"

        response = client.post(
            "/requirements/R-001/confirm",
            json={"tickets": [ticket.to_dict()]},
        )
        assert response.status_code == 200
        assert response.json()["requirement"]["status"] == "confirmed"
        assert response.json()["tickets"][0]["id"] == "T-300"

        response = client.post("/requirements/R-002/discard")
        assert response.status_code == 200
        assert response.json()["requirement"]["status"] == "discarded"

        response = client.get("/requirements")
        assert response.status_code == 200
        assert response.json()["requirements"] == []

        response = client.get("/requirements/R-404")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_b025_project_conventions_endpoint_prefills_test_command(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repo / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    init_git_repo(repo)

    connection = connect(tmp_path / "haao.sqlite3")
    from orchestrator.db.sqlite import ProjectRepository

    project_repository = ProjectRepository(connection)
    project = project_repository.create(name="Repo", path=repo)

    app.dependency_overrides[get_project_repository] = lambda: project_repository
    try:
        client = TestClient(app)
        response = client.get(f"/projects/{project.id}/conventions")
        assert response.status_code == 200
        body = response.json()
        assert body["project_id"] == project.id
        assert body["test_command"] == "pytest -q"
        assert "Existing test locations" in body["conventions"]
    finally:
        app.dependency_overrides.clear()


def test_b035_create_manual_ticket_endpoint(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("x = 1\n", encoding="utf-8")
    init_git_repo(repo)

    connection = connect(tmp_path / "haao.sqlite3")
    from orchestrator.db.sqlite import ProjectRepository

    project_repository = ProjectRepository(connection)
    project = project_repository.create(name="Calc", path=repo)
    repository = TicketRepository(connection, project_id=project.id)
    settings_repository = SettingsRepository(connection)

    app.dependency_overrides[get_project_repository] = lambda: project_repository
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_settings_repository] = lambda: settings_repository
    try:
        client = TestClient(app)
        response = client.post(
            "/tickets",
            json={
                "project_id": project.id,
                "title": "Bump x",
                "type": "chore",
                "target_files": ["calc.py"],
                "task_description": "Set x to 2",
                "dod_tests": [f'{sys.executable} -c "assert True"'],
                "assigned_model": "qwen3-coder-next",
            },
        )
        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert ticket["status"] == "ready"
        assert ticket["metadata"]["needs_approval"] is False
        assert ticket["metadata"]["human_authored"] is True
        assert ticket["context"]["files"][0]["path"] == "calc.py"

        rejected = client.post(
            "/tickets",
            json={
                "project_id": project.id,
                "title": "Cloud model",
                "type": "feature",
                "target_files": ["calc.py"],
                "task_description": "Should fail",
                "assigned_model": "claude-tech-lead",
            },
        )
        assert rejected.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_b038_project_settings_endpoint_updates_env_setup_and_cleanup(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("repo\n", encoding="utf-8")
    init_git_repo(repo)

    connection = connect(tmp_path / "haao.sqlite3")
    from orchestrator.db.sqlite import ProjectRepository

    project_repository = ProjectRepository(connection)
    project = project_repository.create(name="Repo", path=repo)

    app.dependency_overrides[get_project_repository] = lambda: project_repository
    try:
        client = TestClient(app)
        response = client.put(
            f"/projects/{project.id}/settings",
            json={
                "env": {"HAAO_FLAG": "ok"},
                "setup_cmd": "python -c \"print('setup')\"",
                "cleanup_cmd": "python -c \"print('cleanup')\"",
                "default_branch": "develop",
            },
        )
        assert response.status_code == 200
        body = response.json()["project"]
        assert body["env"] == {"HAAO_FLAG": "ok"}
        assert body["setup_cmd"] == "python -c \"print('setup')\""
        assert body["cleanup_cmd"] == "python -c \"print('cleanup')\""
        assert body["default_branch"] == "develop"
    finally:
        app.dependency_overrides.clear()


def test_b032_local_model_endpoint_settings_and_discovery(tmp_path, monkeypatch) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    from orchestrator.db.sqlite import SettingsRepository
    from orchestrator.local_models import LocalModelEndpointResult

    settings_repository = SettingsRepository(connection)
    app.dependency_overrides[get_settings_repository] = lambda: settings_repository

    def fake_discover(endpoints):
        return [
            LocalModelEndpointResult(
                endpoint=endpoints[0],
                ok=True,
                models=["custom-coder", "custom-gatekeeper"],
            )
        ]

    monkeypatch.setattr("orchestrator.api.discover_local_models", fake_discover)

    try:
        client = TestClient(app)
        response = client.put(
            "/models/local/endpoints",
            json={
                "endpoints": [
                    {
                        "id": "local",
                        "label": "Local",
                        "base_url": "http://localhost:9999/v1",
                        "api_key": "secret",
                    }
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()["endpoints"][0]["base_url"] == "http://localhost:9999/v1"

        response = client.get("/models/local/available")
        assert response.status_code == 200
        body = response.json()
        assert body["models"] == ["custom-coder", "custom-gatekeeper"]
        assert body["endpoints"][0]["ok"] is True
        assert settings_repository.get_json("local_model_cache") == [
            "custom-coder",
            "custom-gatekeeper",
        ]
    finally:
        app.dependency_overrides.clear()


def test_b017_confirm_duplicate_ticket_id_returns_409(fresh_ticket_dict) -> None:
    ticket = make_api_ticket(fresh_ticket_dict)

    def override_requirement_service():
        return DuplicateTicketRequirementService(ticket)

    app.dependency_overrides[get_requirement_service] = override_requirement_service
    try:
        client = TestClient(app)
        response = client.post(
            "/requirements/R-001/confirm",
            json={"tickets": [ticket.to_dict()]},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Ticket id already exists: T-001"
    finally:
        app.dependency_overrides.clear()


def test_b012_websocket_streams_ticket_logs(tmp_path, fresh_ticket_dict, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    repository = TicketRepository(connect(db_path))
    repository.create(make_api_ticket(fresh_ticket_dict))
    repository.append_log("T-300", "hello from worker")

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from orchestrator.config import get_settings

    get_settings.cache_clear()
    try:
        client = TestClient(app)
        with client.websocket_connect("/tickets/T-300/logs") as websocket:
            assert websocket.receive_json()["message"] == "hello from worker"
    finally:
        get_settings.cache_clear()


def test_auto_orchestrator_run_endpoints() -> None:
    class FakeAutoOrchestrator:
        allow_dirty_workspace = False

        def run_once(self):
            return SimpleNamespace(
                idle=False,
                skipped_reason="",
                executed_ticket_ids=["T-1"],
                reviewed_ticket_ids=["T-1"],
                escalated_ticket_ids=[],
                final_tickets=[],
            )

        def run_until_idle(self, max_cycles: int = 10):
            assert max_cycles == 2
            assert self.allow_dirty_workspace is True
            return [
                SimpleNamespace(
                    idle=True,
                    skipped_reason="",
                    executed_ticket_ids=[],
                    reviewed_ticket_ids=[],
                    escalated_ticket_ids=[],
                    final_tickets=[],
                )
            ]

    app.dependency_overrides[get_auto_orchestrator] = lambda: FakeAutoOrchestrator()
    try:
        client = TestClient(app)
        response = client.post("/orchestrator/run-once")
        assert response.status_code == 200
        assert response.json()["results"][0]["executed_ticket_ids"] == ["T-1"]

        response = client.post(
            "/orchestrator/run-until-idle",
            json={"max_cycles": 2, "allow_dirty_workspace": True},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["idle"] is True
    finally:
        app.dependency_overrides.clear()


def test_b020_auto_worker_status_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/orchestrator/worker/status")

    assert response.status_code == 200
    assert response.json()["running"] is False
