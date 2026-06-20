from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from orchestrator.context.injector import ContextInjector
from orchestrator.db.sqlite import SettingsRepository, TicketRepository
from orchestrator.model_policy import (
    enforce_local_execution_model,
    is_local_execution_model,
    local_execution_model,
)
from orchestrator.models.ticket import Ticket

TicketType = Literal["feature", "bugfix", "refactor", "test", "chore"]


class ManualTicketError(ValueError):
    """Raised when a manual ticket payload cannot be created."""


@dataclass(frozen=True)
class ManualTicketCreatePayload:
    title: str
    type: TicketType
    target_files: list[str]
    task_description: str
    constraints: list[str] | None = None
    dod_tests: list[str] | None = None
    acceptance_criteria: list[str] | None = None
    assigned_model: str | None = None
    project_id: str | None = None


class ManualTicketService:
    def __init__(
        self,
        ticket_repository: TicketRepository,
        context_injector: ContextInjector,
        *,
        project_id: str | None = None,
        settings_repository: SettingsRepository | None = None,
    ) -> None:
        self.ticket_repository = ticket_repository
        self.context_injector = context_injector
        self.project_id = project_id or getattr(ticket_repository, "project_id", None) or "default"
        self.settings_repository = settings_repository

    def create(self, payload: ManualTicketCreatePayload) -> Ticket:
        title = payload.title.strip()
        description = payload.task_description.strip()
        if not title:
            raise ManualTicketError("title is required")
        if not description:
            raise ManualTicketError("task_description is required")
        if not payload.target_files:
            raise ManualTicketError("target_files cannot be empty")
        if len(payload.target_files) > 5:
            raise ManualTicketError("target_files supports at most 5 files")

        if payload.assigned_model and not is_local_execution_model(
            payload.assigned_model,
            self.settings_repository,
        ):
            raise ManualTicketError(
                "assigned_model must be a known local model; cloud models cannot execute code"
            )

        project_id = payload.project_id or self.project_id
        dod_tests = [command.strip() for command in (payload.dod_tests or []) if command.strip()]
        unverified = len(dod_tests) == 0
        if unverified:
            dod_tests = [f'{sys.executable} -c "pass"']

        ticket_dict: dict = {
            "id": self.ticket_repository.next_ticket_id(project_id),
            "title": title[:120],
            "type": payload.type,
            "status": "ready",
            "priority": "medium",
            "created_by": "human",
            "dependencies": [],
            "task": {
                "description": description,
                "target_files": payload.target_files,
                "constraints": list(payload.constraints or []),
            },
            "context": {"files": [], "related_symbols": [], "notes": ""},
            "definition_of_done": {
                "tests": [
                    {"command": command, "expect": "pass", "timeout_sec": 120}
                    for command in dod_tests
                ],
                "static_checks": [],
                "acceptance_criteria": list(payload.acceptance_criteria or []),
            },
            "execution": {
                "assigned_model": local_execution_model(
                    payload.assigned_model,
                    self.settings_repository,
                ),
                "retry_budget": 3,
                "attempts": 0,
                "escalate_to": "tech_lead",
            },
            "audit": {"verdict": "pending", "feedback": "", "reviewed_by": ""},
            "metadata": {
                "project_id": project_id,
                "needs_approval": False,
                "human_authored": True,
                "unverified": unverified,
                "created_at": datetime.now(UTC).isoformat(),
            },
        }
        enforce_local_execution_model(ticket_dict, repository=self.settings_repository)

        try:
            ticket = self.context_injector.inject(Ticket.from_dict(ticket_dict))
        except FileNotFoundError as exc:
            raise ManualTicketError(str(exc)) from exc
        except ValueError as exc:
            raise ManualTicketError(str(exc)) from exc

        created = self.ticket_repository.create(ticket)
        self.ticket_repository.append_log(
            created.id,
            "Manual ticket created; ready for execution"
            + (" (unverified — no machine test gate)" if unverified else ""),
        )
        return created


def is_unverified_ticket(ticket: Ticket) -> bool:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    return bool(metadata.get("unverified"))
