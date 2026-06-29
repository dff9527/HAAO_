from __future__ import annotations

import asyncio
import copy
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator import auto_worker as auto_worker_module
from orchestrator.api import _effective_sandbox_mode, get_settings
from orchestrator.auto_orchestrator import AutoOrchestrator
from orchestrator.auto_worker import AutoWorker
from orchestrator.db.sqlite import RunEventRepository, TicketRepository, connect
from orchestrator.main import app
from orchestrator.models.ticket import Ticket
from orchestrator.policies import ExecutionPolicy
from orchestrator.runner import sandbox
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.config import Settings


def test_ticket_lease_prevents_double_claim_and_expires(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(_ticket(fresh_ticket_dict, ticket_id="T-701", status="ready"))

    first = repository.lease("T-701", worker_id="worker-a", ttl_sec=60)
    second = repository.lease("T-701", worker_id="worker-b", ttl_sec=60)
    assert first is not None
    assert second is None

    expired = first.to_dict()
    expired["metadata"]["lease_expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    repository.save(Ticket.from_dict(expired))

    reclaimed = repository.lease("T-701", worker_id="worker-b", ttl_sec=60)
    assert reclaimed is not None
    assert reclaimed.metadata.model_dump(mode="json")["lease_worker_id"] == "worker-b"


def test_claim_next_ready_respects_depends_on_and_reclaims_after_dependency_done(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(_ticket(fresh_ticket_dict, ticket_id="T-700", status="ready"))
    child = _ticket(fresh_ticket_dict, ticket_id="T-701", status="ready")
    payload = child.to_dict()
    payload["depends_on"] = ["T-700"]
    payload["dependencies"] = ["T-700"]
    repository.create(Ticket.from_dict(payload))

    first = repository.claim_next_ready_ticket(worker_id="worker-a", ttl_sec=60)
    assert first.ticket.id == "T-700"
    second = repository.claim_next_ready_ticket(worker_id="worker-b", ttl_sec=60)
    assert second.ticket is None
    assert second.skipped_reason == "dependencies_pending"

    done = first.ticket.to_dict()
    done["status"] = "done"
    repository.save(Ticket.from_dict(done))
    repository.release_lease("T-700", worker_id="worker-a")

    third = repository.claim_next_ready_ticket(worker_id="worker-b", ttl_sec=60)
    assert third.ticket.id == "T-701"


def test_overlapping_target_files_serialize_and_emit_conflict_event(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    repository = TicketRepository(connection)
    repository.create(_ticket(fresh_ticket_dict, ticket_id="T-701", status="ready", target_files=["calc.py"]))
    repository.create(_ticket(fresh_ticket_dict, ticket_id="T-702", status="ready", target_files=["calc.py"]))
    assert repository.claim_next_ready_ticket(worker_id="worker-a", ttl_sec=60).ticket.id == "T-701"

    orchestrator = AutoOrchestrator(
        repository,
        execution_loop=SimpleNamespace(run_ticket=lambda ticket_id: None),
        review_service=SimpleNamespace(review_ticket=lambda ticket_id: None),
        escalation_service=SimpleNamespace(handle_blocked_ticket=lambda ticket_id: None),
        repo_root=tmp_path,
        worker_id="worker-b",
    )
    result = orchestrator.run_once()

    assert result.skipped_reason == "target_file_conflict"
    assert result.waiting_ticket_ids == ["T-701", "T-702"]
    event = RunEventRepository(connection).list_run_events("default", ticket_id="T-702")[0]
    assert event.event_type == "conflict"
    assert event.payload["reason"] == "target_file_overlap"
    assert event.payload["conflicting_ticket_ids"] == ["T-701"]


def test_tickets_graph_endpoint_shape_and_ready_states(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    repository = TicketRepository(connect(db_path))
    repository.create(_ticket(fresh_ticket_dict, ticket_id="T-700", status="done"))
    child = _ticket(fresh_ticket_dict, ticket_id="T-701", status="ready")
    payload = child.to_dict()
    payload["depends_on"] = ["T-700"]
    payload["dependencies"] = ["T-700"]
    repository.create(Ticket.from_dict(payload))

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        response = TestClient(app).get("/api/tickets/graph?project_id=default")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "default"
    assert {"source": "T-700", "target": "T-701", "kind": "depends_on"} in body["edges"]
    node = next(node for node in body["nodes"] if node["id"] == "T-701")
    assert node["depends_on"] == ["T-700"]
    assert node["ready_state"] == "ready"


def test_auto_worker_pool_runs_ticks_concurrently(monkeypatch, tmp_path: Path) -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_run_tick(*args, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.08)
        with lock:
            active -= 1
        return ""

    monkeypatch.setattr(auto_worker_module, "_run_tick", fake_run_tick)
    worker = AutoWorker()

    async def scenario() -> None:
        snapshot = await worker.start(
            settings=_settings(tmp_path / "haao.sqlite3"),
            project_id="default",
            repo_root=tmp_path,
            database_root=tmp_path,
            interval_sec=60,
            max_cycles_per_tick=1,
            max_workers=2,
        )
        assert snapshot.max_workers == 2
        await asyncio.sleep(0.12)
        stopped = await worker.stop()
        assert len(stopped.worker_statuses) == 2

    asyncio.run(scenario())
    assert max_active == 2


def test_sandbox_env_override_selects_restricted_mode_and_degrades_loudly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(HAAO_SANDBOX_MODE="docker")
    assert _effective_sandbox_mode("none", settings) == "docker"

    audits: list[sandbox.SandboxAudit] = []
    monkeypatch.setattr(
        sandbox,
        "choose_primitive",
        lambda mode: sandbox.PrimitiveChoice(None, "docker unavailable"),
    )
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda argv, **kwargs: __import__("subprocess").CompletedProcess(argv, 0, "ok", ""),
    )
    result = TestRunner(
        cwd=tmp_path,
        execution_policy=ExecutionPolicy(test_allow_network=False, sandbox_mode="docker"),
        audit_sink=audits.append,
    ).run_command_safe("python -c pass")

    assert result.status == "pass"
    assert [audit.event_type for audit in audits] == ["egress_attempt", "error"]
    assert all(audit.reason == "sandbox_unavailable" for audit in audits)


def _ticket(
    base: dict,
    *,
    ticket_id: str,
    status: str,
    target_files: list[str] | None = None,
) -> Ticket:
    payload = copy.deepcopy(base)
    payload["id"] = ticket_id
    payload["status"] = status
    payload["dependencies"] = []
    payload["depends_on"] = []
    payload["task"]["target_files"] = target_files or ["calc.py"]
    payload["context"]["files"] = [
        {"path": path, "content": "value = 1\n"}
        for path in payload["task"]["target_files"]
    ]
    payload["result"] = {"outcome": "pending"}
    payload["audit"] = {"verdict": "pending", "feedback": "", "reviewed_by": ""}
    payload["metadata"] = {"project_id": "default", "requirement_id": "R-700"}
    return Ticket.from_dict(payload)


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
        haao_sandbox_mode="",
    )
