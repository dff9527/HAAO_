from __future__ import annotations

import subprocess
import sys
import copy
from pathlib import Path

from orchestrator.attachment_audit import record_attachment_egress
from orchestrator.db.sqlite import RunEventRepository, TicketRepository, connect
from orchestrator.diff_review import DiffReviewService
from orchestrator.models.ticket import Ticket
from orchestrator.runner import sandbox
from orchestrator.state_machine import TicketStateService
from orchestrator.supply_chain import build_supply_chain_signal
from orchestrator.trust import build_acceptance_summary
from tests.conftest import init_git_repo


def test_strict_sandbox_uses_docker_resource_limits_and_worktree_mount(
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
        env={"PATH": "/bin", "AWS_SECRET_ACCESS_KEY": "nope"},
        timeout=7,
        command="python -c \"print('ok')\"",
        policy=sandbox.ExecutionPolicy(
            test_allow_network=True,
            sandbox_mode="strict",
            cpu_limit=0.5,
            memory_mb=256,
            pids_limit=32,
        ),
    )

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert result.primitive == "docker"
    assert result.network_disabled is True
    assert "--network=none" in argv
    assert f"type=bind,src={tmp_path},dst=/workspace" in argv
    assert argv[argv.index("--cpus") + 1] == "0.5"
    assert argv[argv.index("--memory") + 1] == "256m"
    assert argv[argv.index("--pids-limit") + 1] == "32"
    assert "--cap-drop" in argv
    assert "no-new-privileges" in argv
    assert "--read-only" in argv
    assert all("AWS_SECRET_ACCESS_KEY" not in str(part) for part in argv)


def test_strict_sandbox_degrades_loudly_when_runtime_missing(monkeypatch, tmp_path: Path) -> None:
    audits: list[sandbox.SandboxAudit] = []
    monkeypatch.setattr(
        sandbox,
        "choose_primitive",
        lambda mode: sandbox.PrimitiveChoice(None, "strict docker unavailable"),
    )
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "ok", ""),
    )

    result = sandbox.run_with_policy(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        env={},
        timeout=5,
        command="python -c \"print('ok')\"",
        policy=sandbox.ExecutionPolicy(sandbox_mode="strict"),
        audit_sink=audits.append,
    )

    assert result.primitive == "local"
    assert result.network_disabled is False
    assert [audit.event_type for audit in audits] == ["egress_attempt", "error"]
    assert {audit.reason for audit in audits} == {"sandbox_strict_unavailable"}


def test_strict_sandbox_emits_egress_attempt_on_blocked_network(monkeypatch, tmp_path: Path) -> None:
    audits: list[sandbox.SandboxAudit] = []

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "Temporary failure in name resolution")

    monkeypatch.setattr(sandbox, "choose_primitive", lambda mode: sandbox.PrimitiveChoice("docker"))
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    sandbox.run_with_policy(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        env={},
        timeout=5,
        command="python -c \"print('ok')\"",
        policy=sandbox.ExecutionPolicy(sandbox_mode="strict"),
        audit_sink=audits.append,
    )

    assert [audit.event_type for audit in audits] == ["egress_attempt"]
    assert audits[0].reason == "network_blocked_by_sandbox"
    assert audits[0].blocked is True


def test_attachment_egress_event_shape_and_redaction(tmp_path: Path) -> None:
    connection = connect(tmp_path / "haao.sqlite3")

    record_attachment_egress(
        connection,
        project_id="default",
        attachment_id="ATT-001",
        provider="openai",
        model="sk-secret123456789",
        requirement_id="R-001",
        chat_message_id="CM-001",
    )

    event = RunEventRepository(connection).list_run_events("default")[0]
    assert event.event_type == "attachment_egress"
    assert event.model_id == "***redacted***"
    assert event.payload == {
        "attachment_id": "ATT-001",
        "chat_message_id": "CM-001",
        "kind": "attachment_egress",
        "model": "***redacted***",
        "provider": "openai",
        "requirement_id": "R-001",
        "detail": "attachment_egress",
        "run_id": None,
        "ticket_id": None,
        "ts": event.ts,
    }


def test_supply_chain_signal_detects_added_deps_and_acceptance_summary(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    diff = (
        "diff --git a/requirements.txt b/requirements.txt\n"
        "--- a/requirements.txt\n"
        "+++ b/requirements.txt\n"
        "@@ -1 +1,2 @@\n"
        " pytest==8.0.0\n"
        "+requests==2.32.0\n"
        "diff --git a/package.json b/package.json\n"
        "--- a/package.json\n"
        "+++ b/package.json\n"
        "@@ -2,6 +2,7 @@\n"
        "   \"name\": \"demo\",\n"
        '   "dependencies": {\n'
        '     "vite": "latest",\n'
        '+    "left-pad": "1.3.0",\n'
        '     "react": "latest"\n'
        "   }\n"
        " }\n"
    )

    class Checker:
        def check(self, added_deps, changed_manifests):
            return [
                {
                    "severity": "medium",
                    "source": "test-checker",
                    "package": added_deps[0]["name"],
                    "detail": "simulated advisory",
                }
            ]

    signal = build_supply_chain_signal(diff, checker=Checker())
    assert signal["changed_manifests"] == ["package.json", "requirements.txt"]
    assert {"manifest": "requirements.txt", "name": "requests", "version": "==2.32.0"} in signal["added_deps"]
    assert {"manifest": "package.json", "name": "left-pad", "version": "1.3.0"} in signal["added_deps"]
    assert signal["findings"][0]["source"] == "test-checker"

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "requirements.txt").write_text("pytest==8.0.0\n", encoding="utf-8")
    (repo_root / "package.json").write_text(
        '{\n  "name": "demo",\n  "dependencies": {\n    "vite": "latest",\n    "react": "latest"\n  }\n}\n',
        encoding="utf-8",
    )
    init_git_repo(repo_root)
    connection = connect(tmp_path / "haao.sqlite3")
    tickets = TicketRepository(connection)
    payload = copy.deepcopy(fresh_ticket_dict)
    payload["id"] = "T-910"
    payload["status"] = "diff_pending"
    payload["task"]["target_files"] = ["requirements.txt", "package.json"]
    payload["context"]["files"] = [
        {"path": "requirements.txt", "content": "pytest==8.0.0\n"},
        {
            "path": "package.json",
            "content": '{\n  "name": "demo",\n  "dependencies": {\n    "vite": "latest",\n    "react": "latest"\n  }\n}\n',
        },
    ]
    payload["result"] = {"outcome": "success", "diff": diff, "test_output": "ok"}
    ticket = tickets.create(Ticket.from_dict(payload))

    approved = DiffReviewService(
        tickets,
        TicketStateService(tickets),
        repo_root=repo_root,
    ).approve_diff(ticket.id).ticket
    approved_payload = approved.to_dict()
    approved_payload["status"] = "awaiting_acceptance"
    approved_payload["audit"] = {"verdict": "approved", "feedback": "", "reviewed_by": "gatekeeper"}
    awaiting = Ticket.from_dict(approved_payload)

    summary = build_acceptance_summary(awaiting)
    assert summary["supply_chain"] == approved.metadata.model_dump(mode="json")["supply_chain"]
    assert summary["supply_chain"] == {
        "changed_manifests": ["package.json", "requirements.txt"],
        "added_deps": [
            {"manifest": "requirements.txt", "name": "requests", "version": "==2.32.0"},
            {"manifest": "package.json", "name": "left-pad", "version": "1.3.0"},
        ],
        "findings": [],
    }
