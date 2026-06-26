from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from orchestrator.db.sqlite import RunEventRepository, TicketRepository, connect
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.models.ticket import Ticket
from orchestrator.policies import ExecutionPolicy
from orchestrator.runner import sandbox
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo


def test_docker_primitive_disables_network_and_filters_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(sandbox, "choose_primitive", lambda mode: sandbox.PrimitiveChoice("docker"))
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    result = sandbox.run_with_policy(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        env={"HAAO_FLAG": "ok", "SECRET_TOKEN": "nope"},
        timeout=5,
        command="python -c \"print('ok')\"",
        policy=ExecutionPolicy(
            test_allow_network=False,
            env_allowlist=("PATH", "PYTHONPATH", "HAAO_FLAG"),
            sandbox_mode="auto",
        ),
    )

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "--network=none" in argv
    assert f"type=bind,src={tmp_path},dst=/workspace" in argv
    assert "HAAO_FLAG=ok" in argv
    assert all("SECRET_TOKEN" not in str(part) for part in argv)
    assert result.primitive == "docker"
    assert result.network_disabled is True


def test_missing_primitive_falls_back_and_audits(monkeypatch, tmp_path: Path) -> None:
    audits: list[sandbox.SandboxAudit] = []

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(
        sandbox,
        "choose_primitive",
        lambda mode: sandbox.PrimitiveChoice(None, "no sandbox primitive"),
    )
    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    result = sandbox.run_with_policy(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        env={},
        timeout=5,
        command="python -c \"print('ok')\"",
        policy=ExecutionPolicy(test_allow_network=False, sandbox_mode="auto"),
        audit_sink=audits.append,
    )

    assert result.primitive == "local"
    assert result.network_disabled is False
    assert [audit.event_type for audit in audits] == ["egress_attempt", "error"]
    assert {audit.reason for audit in audits} == {"sandbox_unavailable"}
    assert all(audit.blocked is False for audit in audits)


def test_network_failure_under_sandbox_emits_egress_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audits: list[sandbox.SandboxAudit] = []

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "Network is unreachable")

    monkeypatch.setattr(sandbox, "choose_primitive", lambda mode: sandbox.PrimitiveChoice("unshare"))
    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    result = sandbox.run_with_policy(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        env={},
        timeout=5,
        command="python -c \"print('ok')\"",
        policy=ExecutionPolicy(test_allow_network=False, sandbox_mode="auto"),
        audit_sink=audits.append,
    )

    assert result.primitive == "unshare"
    assert result.network_disabled is True
    assert [audit.event_type for audit in audits] == ["egress_attempt"]
    assert audits[0].reason == "network_blocked_by_sandbox"
    assert audits[0].blocked is True


def test_allow_network_runs_without_sandbox(monkeypatch, tmp_path: Path) -> None:
    def fail_choose(mode):  # pragma: no cover - proves this path is not consulted
        raise AssertionError("sandbox primitive should not be selected")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(sandbox, "choose_primitive", fail_choose)
    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    result = sandbox.run_with_policy(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        env={},
        timeout=5,
        command="python -c \"print('ok')\"",
        policy=ExecutionPolicy(test_allow_network=True, sandbox_mode="auto"),
    )

    assert result.primitive == "local"
    assert result.network_disabled is False


def test_execution_loop_records_missing_sandbox_warning_run_events(
    tmp_path: Path,
    fresh_ticket_dict,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "calc.py").write_text("old", encoding="utf-8")
    init_git_repo(repo_root)
    connection = connect(tmp_path / "haao.sqlite3")
    tickets = TicketRepository(connection)
    payload = fresh_ticket_dict
    payload["status"] = "ready"
    payload["task"]["target_files"] = ["calc.py"]
    payload["context"]["files"] = [{"path": "calc.py", "content": "old"}]
    payload["definition_of_done"]["tests"] = [
        {"command": "python -c pass", "expect": "pass", "timeout_sec": 120}
    ]
    tickets.create(Ticket.from_dict(payload))

    class Model:
        def chat_completion(self, **kwargs):
            return "new"

    monkeypatch.setattr(
        sandbox,
        "choose_primitive",
        lambda mode: sandbox.PrimitiveChoice(None, "no sandbox primitive"),
    )
    result = ExecutionLoop(
        tickets,
        TicketStateService(tickets),
        Model(),
        repo_root=repo_root,
        test_runner=TestRunner(
            cwd=repo_root,
            execution_policy=ExecutionPolicy(test_allow_network=False, sandbox_mode="auto"),
        ),
    ).run_ticket("T-012")

    events = RunEventRepository(connection).list_run_events("default")
    sandbox_events = [
        event
        for event in events
        if event.payload and event.payload.get("stage") == "sandbox"
    ]
    assert result.passed is True
    assert [event.event_type for event in sandbox_events] == ["egress_attempt", "error"]
    assert all(event.run_id == events[0].run_id for event in sandbox_events)
