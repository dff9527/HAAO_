from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.db.sqlite import RequirementRepository, TicketRepository, connect
from orchestrator.models.requirement import Requirement
from orchestrator.requirements_flow import RequirementService

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "golden_tasks.json"


@dataclass(frozen=True)
class GoldenRegressionResult:
    task_count: int
    passed: bool
    checked_task_ids: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "task_count": self.task_count,
            "passed": self.passed,
            "checked_task_ids": self.checked_task_ids,
        }


class RecordedReasoner:
    def __init__(self, tickets: list[dict[str, Any]]) -> None:
        self.tickets = tickets

    def decompose(self, *_: object, **__: object) -> list[dict[str, Any]]:
        return json.loads(json.dumps(self.tickets))


def run_golden_task_regression(
    fixture_path: str | Path = DEFAULT_GOLDEN_FIXTURE,
) -> GoldenRegressionResult:
    fixture = _load_fixture(fixture_path)
    tasks = fixture.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise AssertionError("Golden fixture must contain at least one task")

    checked: list[str] = []
    with tempfile.TemporaryDirectory(prefix="haao-golden-") as temp_dir:
        root = Path(temp_dir)
        for index, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                raise AssertionError(f"Golden task {index} must be an object")
            task_id = _required_str(task, "id")
            checked.append(task_id)
            repo_root = root / task_id
            repo_root.mkdir(parents=True)
            _write_repo_files(repo_root, task.get("repo_files"))

            db_path = root / f"{task_id}.sqlite3"
            connection = connect(db_path)
            service = RequirementService(
                TicketRepository(connection),
                RequirementRepository(connection, project_id="default"),
                RecordedReasoner(_required_list(task, "recorded_tickets")),
                repo_root=repo_root,
                project_id="default",
            )
            requirement_payload = _required_dict(task, "requirement")
            preview = service.decompose_preview(Requirement.model_validate(requirement_payload))
            actual = [_ticket_shape(ticket.to_dict()) for ticket in preview.proposed_tickets]
            expected = _required_list(_required_dict(task, "expect"), "tickets")
            if actual != expected:
                raise AssertionError(
                    f"Golden task {task_id} drifted:\n"
                    f"expected={json.dumps(expected, sort_keys=True)}\n"
                    f"actual={json.dumps(actual, sort_keys=True)}"
                )
    return GoldenRegressionResult(
        task_count=len(tasks),
        passed=True,
        checked_task_ids=checked,
    )


def _ticket_shape(ticket: dict[str, Any]) -> dict[str, Any]:
    definition = ticket.get("definition_of_done") if isinstance(ticket, dict) else {}
    tests = definition.get("tests") if isinstance(definition, dict) else []
    task = ticket.get("task") if isinstance(ticket, dict) else {}
    return {
        "title": ticket.get("title"),
        "type": ticket.get("type"),
        "target_files": task.get("target_files") if isinstance(task, dict) else [],
        "has_dod_tests": bool(tests),
        "dod_commands": [
            item.get("command")
            for item in tests
            if isinstance(item, dict) and isinstance(item.get("command"), str)
        ],
    }


def _write_repo_files(repo_root: Path, files: object) -> None:
    if not isinstance(files, dict) or not files:
        raise AssertionError("Golden task repo_files must be a non-empty object")
    for raw_path, content in files.items():
        if not isinstance(raw_path, str) or not isinstance(content, str):
            raise AssertionError("Golden repo file paths and contents must be strings")
        path = (repo_root / raw_path).resolve()
        if not path.is_relative_to(repo_root.resolve()):
            raise AssertionError(f"Golden repo file escapes repo root: {raw_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _load_fixture(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fixture_file:
        payload = json.load(fixture_file)
    if not isinstance(payload, dict):
        raise AssertionError("Golden fixture root must be an object")
    return payload


def _required_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise AssertionError(f"Golden fixture field {key!r} must be an object")
    return value


def _required_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise AssertionError(f"Golden fixture field {key!r} must be a list")
    return value


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AssertionError(f"Golden fixture field {key!r} must be a non-empty string")
    return value
