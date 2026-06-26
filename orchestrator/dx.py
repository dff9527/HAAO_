from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from orchestrator.config import get_settings
from orchestrator.db.sqlite import (
    ProjectRepository,
    RequirementRepository,
    RunEventRepository,
    SettingsRepository,
    TicketRepository,
)
from orchestrator.git_flow import now_iso
from orchestrator.models.requirement import Requirement, RequirementStatus
from orchestrator.models.ticket import Ticket
from orchestrator.redaction import current_known_secrets, redact_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_PROJECT_ID = "P-000"
DEMO_FIXTURE_ROOT = Path(__file__).resolve().parent / "demo_fixture"
DEFAULT_DEMO_ROOT = PROJECT_ROOT / ".haao" / "demo-project"


class DemoSeedService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        demo_root: str | Path = DEFAULT_DEMO_ROOT,
        fixture_root: str | Path = DEMO_FIXTURE_ROOT,
    ) -> None:
        self.connection = connection
        self.demo_root = Path(demo_root).resolve()
        self.fixture_root = Path(fixture_root).resolve()

    def seed(self) -> dict[str, Any]:
        self._reset_demo_repo()
        self._purge_demo_project()
        project = ProjectRepository(self.connection).create(
            project_id=DEMO_PROJECT_ID,
            name="HAAO Demo",
            path=self.demo_root,
            default_branch="main",
        )
        requirement = _demo_requirement(self.demo_root)
        stored_requirement = RequirementRepository(
            self.connection,
            project_id=DEMO_PROJECT_ID,
        ).create(requirement)
        return {
            "project": project.to_dict(),
            "requirement": stored_requirement.to_dict(),
            "proposed_tickets": stored_requirement.proposed_tickets,
        }

    def _reset_demo_repo(self) -> None:
        if not self.fixture_root.is_dir():
            raise FileNotFoundError(f"Demo fixture missing: {self.fixture_root}")
        if self.demo_root.exists():
            shutil.rmtree(self.demo_root)
        self.demo_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.fixture_root, self.demo_root)
        self._git("init", "--template=")
        self._git("add", ".")
        self._git("commit", "-m", "demo baseline")

    def _purge_demo_project(self) -> None:
        tables = [
            "ticket_logs",
            "tickets",
            "requirements",
            "chat_message_attachments",
            "chat_messages",
            "chat_segments",
            "chat_attachments",
            "run_events",
            "notifications",
        ]
        for table in tables:
            if table == "chat_message_attachments":
                self.connection.execute(
                    """
                    DELETE FROM chat_message_attachments
                    WHERE message_id IN (
                        SELECT id FROM chat_messages WHERE project_id = ?
                    )
                    """,
                    (DEMO_PROJECT_ID,),
                )
                continue
            self.connection.execute(f"DELETE FROM {table} WHERE project_id = ?", (DEMO_PROJECT_ID,))
        self.connection.execute("DELETE FROM projects WHERE id = ?", (DEMO_PROJECT_ID,))
        self.connection.commit()

    def _git(self, *args: str) -> None:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "HAAO Demo",
            "GIT_AUTHOR_EMAIL": "demo@example.invalid",
            "GIT_COMMITTER_NAME": "HAAO Demo",
            "GIT_COMMITTER_EMAIL": "demo@example.invalid",
        }
        completed = subprocess.run(
            ["git", *args],
            cwd=self.demo_root,
            capture_output=True,
            text=True,
            shell=False,
            env=env,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Demo git command failed: {' '.join(args)}\n{completed.stderr.strip()}")


def build_requirement_summary(
    connection: sqlite3.Connection,
    *,
    requirement_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    requirements = RequirementRepository(connection, project_id=project_id)
    requirement = requirements.get(requirement_id)
    if requirement is None:
        raise KeyError(f"Requirement not found: {requirement_id}")
    effective_project_id = requirement.project_id or project_id or "default"
    tickets = _tickets_for_requirement(connection, requirement, effective_project_id)
    events = _events_for_requirement(connection, requirement.id, effective_project_id)
    summary = {
        "requirement": {
            "id": requirement.id,
            "project_id": effective_project_id,
            "status": requirement.status,
            "prompt": requirement.prompt,
            "scope_paths": requirement.scope_paths,
            "constraints": requirement.constraints,
            "acceptance_notes": requirement.acceptance_notes,
            "created_at": requirement.created_at.isoformat() if requirement.created_at else None,
            "updated_at": requirement.updated_at.isoformat() if requirement.updated_at else None,
        },
        "tickets": [_ticket_summary(ticket) for ticket in tickets],
        "run_events": [event.to_dict() for event in events],
        "cost": _cost_summary(requirement, events),
    }
    return redact_json(
        summary,
        extra_secrets=current_known_secrets(get_settings(), SettingsRepository(connection)),
    )


def _demo_requirement(demo_root: Path) -> Requirement:
    proposed_tickets = [_demo_ticket_one(), _demo_ticket_two()]
    return Requirement(
        id="R-001",
        project_id=DEMO_PROJECT_ID,
        prompt="Make the tiny calculator demo pass its tests, then keep the formatting helper covered.",
        repo=str(demo_root),
        branch="main",
        scope_paths=["calc.py", "tests/test_calc.py"],
        constraints=["Only touch the demo repository.", "Keep the changes easy to inspect."],
        priority="medium",
        intent="bugfix",
        granularity="fine",
        allow_new_files=False,
        test_command="pytest -q",
        acceptance_notes="The demo should show one failing implementation task and one small verification task.",
        status=RequirementStatus.PREVIEW_READY,
        proposed_tickets=proposed_tickets,
    )


def _demo_ticket_one() -> dict[str, Any]:
    return _demo_ticket(
        "T-001",
        "Fix add_one",
        "Update add_one so it increments the provided integer.",
        ["calc.py"],
        ["pytest -q tests/test_calc.py::test_add_one_increments_value"],
    )


def _demo_ticket_two() -> dict[str, Any]:
    return _demo_ticket(
        "T-002",
        "Verify total formatting",
        "Keep format_total behavior covered and ready for review.",
        ["calc.py", "tests/test_calc.py"],
        ["pytest -q tests/test_calc.py::test_format_total_labels_sum"],
    )


def _demo_ticket(
    ticket_id: str,
    title: str,
    description: str,
    target_files: list[str],
    tests: list[str],
) -> dict[str, Any]:
    return Ticket.from_dict(
        {
            "id": ticket_id,
            "title": title,
            "type": "bugfix",
            "status": "backlog",
            "priority": "medium",
            "created_by": "haao-demo",
            "dependencies": [],
            "task": {
                "description": description,
                "target_files": target_files,
                "constraints": ["Use the demo repo only."],
            },
            "context": {
                "files": [
                    {
                        "path": "calc.py",
                        "content": (DEMO_FIXTURE_ROOT / "calc.py").read_text(encoding="utf-8"),
                        "reason": "Demo source file",
                    }
                ],
                "related_symbols": [],
                "notes": "Seeded demo proposal.",
            },
            "definition_of_done": {
                "tests": [
                    {"command": command, "expect": "pass", "timeout_sec": 120}
                    for command in tests
                ],
                "static_checks": [],
                "acceptance_criteria": ["The referenced demo test passes."],
            },
            "execution": {
                "assigned_model": "qwen3-coder-next",
                "retry_budget": 1,
                "attempts": 0,
                "escalate_to": "tech_lead",
            },
            "result": {"outcome": "pending"},
            "audit": {"verdict": "pending", "feedback": "", "reviewed_by": ""},
            "metadata": {
                "project_id": DEMO_PROJECT_ID,
                "requirement_id": "R-001",
                "demo_seeded": True,
                "created_at": now_iso(),
            },
        }
    ).to_dict()


def _tickets_for_requirement(
    connection: sqlite3.Connection,
    requirement: Requirement,
    project_id: str,
) -> list[Ticket]:
    repository = TicketRepository(connection, project_id=project_id)
    tickets: list[Ticket] = []
    for ticket_id in requirement.generated_ticket_ids:
        ticket = repository.get(ticket_id)
        if ticket is not None:
            tickets.append(ticket)
    if tickets:
        return tickets
    return [Ticket.from_dict(payload) for payload in requirement.proposed_tickets]


def _events_for_requirement(
    connection: sqlite3.Connection,
    requirement_id: str,
    project_id: str,
) -> list:
    return RunEventRepository(connection).list_run_events(
        project_id,
        requirement_id=requirement_id,
        limit=500,
    )


def _ticket_summary(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": ticket.id,
        "title": ticket.title,
        "status": ticket.status,
        "type": ticket.type,
        "target_files": ticket.task.target_files,
        "outcome": ticket.result.outcome if ticket.result else "pending",
        "diff": ticket.result.diff if ticket.result else "",
        "test_output": ticket.result.test_output if ticket.result else "",
        "audit": ticket.audit.model_dump(mode="json") if ticket.audit else {},
    }


def _cost_summary(requirement: Requirement, events: list) -> dict[str, Any]:
    event_cost = round(sum(float(event.cost_usd or 0.0) for event in events), 4)
    by_status: dict[str, float] = {"actual": 0.0, "estimated": 0.0, "unknown": 0.0}
    for event in events:
        status = event.cost_status or "unknown"
        by_status[status] = round(by_status.get(status, 0.0) + float(event.cost_usd or 0.0), 4)
    return {
        "requirement_cloud_input_tokens": requirement.cloud_input_tokens,
        "requirement_cloud_output_tokens": requirement.cloud_output_tokens,
        "requirement_cloud_cost_usd": requirement.cloud_cost_usd,
        "run_event_cost_usd": event_cost,
        "run_event_cost_by_status": by_status,
        "total_usd": round(float(requirement.cloud_cost_usd or 0.0) + event_cost, 4),
    }
