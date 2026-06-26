from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import orchestrator.api as api_module
from orchestrator.api import get_settings
from orchestrator.db.sqlite import (
    ProjectRepository,
    RequirementRepository,
    RunEventRepository,
    TicketRepository,
    connect,
)
from orchestrator.dx import DemoSeedService
from orchestrator.main import app
from orchestrator.models.requirement import Requirement
from orchestrator.models.ticket import Ticket
from tests.conftest import init_git_repo


def test_demo_seed_endpoint_is_idempotent_and_preserves_user_projects(
    tmp_path: Path,
    fresh_ticket_dict,
    monkeypatch,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    (user_repo / "README.md").write_text("user\n", encoding="utf-8")
    init_git_repo(user_repo)
    projects = ProjectRepository(connection)
    projects.create(project_id="P-123", name="User Project", path=user_repo)

    ticket_payload = fresh_ticket_dict
    ticket_payload["id"] = "T-123"
    ticket_payload["metadata"] = {"project_id": "P-123"}
    TicketRepository(connection, project_id="P-123").create(Ticket.from_dict(ticket_payload))

    demo_root = tmp_path / "demo-project"

    class TestDemoSeedService(DemoSeedService):
        def __init__(self, connection):
            super().__init__(connection, demo_root=demo_root)

    monkeypatch.setattr(api_module, "DemoSeedService", TestDemoSeedService)
    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        client = TestClient(app)
        first = client.post("/api/demo/seed")
        second = client.post("/api/demo/seed")
        assert first.status_code == 200
        assert second.status_code == 200

        body = second.json()
        assert body["project"]["id"] == "P-000"
        assert body["requirement"]["id"] == "R-001"
        assert body["requirement"]["status"] == "preview_ready"
        assert len(body["proposed_tickets"]) == 2

        assert projects.get("P-123") is not None
        assert TicketRepository(connection, project_id="P-123").get("T-123") is not None
        demo_requirements = RequirementRepository(connection, project_id="P-000").list()
        assert [requirement.id for requirement in demo_requirements] == ["R-001"]
    finally:
        app.dependency_overrides.clear()


def test_requirement_templates_crud(tmp_path: Path) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(tmp_path / "haao.sqlite3")
    try:
        client = TestClient(app)
        builtins = client.get("/api/requirement-templates")
        assert builtins.status_code == 200
        assert len(builtins.json()["templates"]) >= 4
        assert any(template["built_in"] for template in builtins.json()["templates"])

        created = client.post(
            "/api/requirement-templates",
            json={
                "title": "Add cache",
                "prompt": "Add a small cache around the selected path.",
                "scope_paths": ["src/cache.py"],
                "constraints": ["Keep eviction simple."],
            },
        )
        assert created.status_code == 200
        template = created.json()["template"]
        assert template["id"].startswith("tmpl-")
        assert template["scope_paths"] == ["src/cache.py"]

        listed = client.get("/api/requirement-templates").json()["templates"]
        assert any(item["id"] == template["id"] for item in listed)

        deleted = client.delete(f"/api/requirement-templates/{template['id']}")
        assert deleted.status_code == 200

        builtin_delete = client.delete("/api/requirement-templates/builtin-add-endpoint")
        assert builtin_delete.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_requirement_summary_is_redacted_and_read_only(
    tmp_path: Path,
    fresh_ticket_dict,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    requirements = RequirementRepository(connection)
    tickets = TicketRepository(connection)
    secret = "sk-secret123456789"
    requirement = requirements.create(
        Requirement(
            id="R-001",
            project_id="default",
            prompt=f"Fix billing with {secret}",
            scope_paths=["billing.py"],
            constraints=[f"Do not log {secret}"],
            status="confirmed",
            generated_ticket_ids=["T-012"],
            cloud_input_tokens=10,
            cloud_output_tokens=5,
            cloud_cost_usd=0.0123,
        )
    )

    payload = fresh_ticket_dict
    payload["metadata"] = {"project_id": "default", "requirement_id": requirement.id}
    payload["result"] = {
        "outcome": "success",
        "diff": f"+ token = '{secret}'",
        "test_output": "ok",
    }
    tickets.create(Ticket.from_dict(payload))
    RunEventRepository(connection).append_run_event(
        project_id="default",
        requirement_id=requirement.id,
        ticket_id="T-012",
        run_id="RUN-1",
        event_type="model_call",
        cost_usd=0.02,
        cost_status="estimated",
        payload={"note": secret},
    )
    before = _table_counts(connection)

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        client = TestClient(app)
        response = client.get("/api/requirements/R-001/summary")
        assert response.status_code == 200
        raw = json.dumps(response.json(), ensure_ascii=False)
        assert secret not in raw
        summary = response.json()["summary"]
        assert summary["requirement"]["prompt"] == "Fix billing with ***redacted***"
        assert summary["tickets"][0]["diff"] == "+ token = '***redacted***'"
        assert summary["cost"]["total_usd"] == 0.0323
        assert _table_counts(connection) == before
    finally:
        app.dependency_overrides.clear()


def _settings(db_path: Path):
    return SimpleNamespace(
        claude_api_key="",
        openai_api_key="",
        gemini_api_key="",
        lmstudio_base_url="http://localhost:1234/v1",
        local_max_output_tokens=4096,
        local_patch_mode_threshold_tokens=2048,
        database_url=f"sqlite:///{db_path}",
        claude_model="claude-sonnet-4-6",
        haao_api_token="",
    )


def _table_counts(connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        for table in ["requirements", "tickets", "run_events", "projects"]
    }
