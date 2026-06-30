from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from clients.lmstudio import LMStudioClient
from orchestrator.db.sqlite import (
    RequirementRepository,
    RunEventRepository,
    SettingsRepository,
    TicketRepository,
    connect,
)
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.models.ticket import Ticket
from orchestrator.policies import ExecutionPolicy
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService


@dataclass(frozen=True)
class LocalExecutionConfig:
    database_url: str
    repo_root: Path
    lmstudio_base_url: str
    sandbox_mode: str = "auto"


class LocalExecutionJobExecutor:
    def __init__(self, config: LocalExecutionConfig) -> None:
        self.config = config

    def execute(self, job: dict) -> tuple[list[dict], dict]:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        ticket_id = str(job.get("ticket_id") or payload.get("ticket_id") or "")
        if not ticket_id:
            raise ValueError("runner job payload must include ticket_id")
        project_id = str(payload.get("project_id") or job.get("workspace_id") or "default")
        connection = connect(_sqlite_path(self.config.database_url))
        try:
            tickets = TicketRepository(connection, project_id=project_id)
            if isinstance(payload.get("ticket"), dict):
                ticket = Ticket.from_dict(payload["ticket"])
                if tickets.get(ticket.id, project_id=project_id) is None:
                    tickets.create(ticket, project_id=project_id)
                else:
                    tickets.save(ticket, project_id=project_id)
                ticket_id = ticket.id
            before_cursor = _max_run_event_id(connection)
            run_events = RunEventRepository(connection)
            lmstudio = LMStudioClient(self.config.lmstudio_base_url)
            try:
                loop = ExecutionLoop(
                    tickets,
                    TicketStateService(tickets),
                    lmstudio,
                    repo_root=self.config.repo_root,
                    test_runner=TestRunner(
                        cwd=self.config.repo_root,
                        execution_policy=ExecutionPolicy(
                            test_allow_network=False,
                            env_allowlist=("PATH", "PYTHONPATH"),
                            sandbox_mode=self.config.sandbox_mode,
                        ),
                    ),
                    settings_repository=SettingsRepository(connection),
                    requirement_repository=RequirementRepository(connection, project_id=project_id),
                )
                result = loop.run_ticket(ticket_id)
            finally:
                lmstudio.close()
            events = [
                event.to_dict()
                for event in run_events.list_run_events(
                    project_id,
                    after=before_cursor,
                    ticket_id=ticket_id,
                    limit=500,
                )
            ]
            return events, {
                "outcome": "success" if result.passed else "error",
                "ticket_id": result.ticket.id,
                "ticket": result.ticket.to_dict(),
            }
        finally:
            connection.close()


def _sqlite_path(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Only sqlite:/// DATABASE_URL values are supported")
    return database_url[len(prefix) :]


def _max_run_event_id(connection) -> int | None:
    row = connection.execute("SELECT MAX(id) AS max_id FROM run_events").fetchone()
    if row is None or row["max_id"] is None:
        return None
    return int(row["max_id"])
