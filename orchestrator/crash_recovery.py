from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from orchestrator.db.sqlite import TicketRepository
from orchestrator.execution_registry import execution_key, execution_registry
from orchestrator.execution_safety import GitWorkspaceGuard
from orchestrator.models.ticket import Result, Ticket, TicketStatus


@dataclass(frozen=True)
class CrashRecoveryResult:
    recovered_ticket_ids: list[str] = field(default_factory=list)
    noted_ticket_ids: list[str] = field(default_factory=list)
    removed_worktrees: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.recovered_ticket_ids or self.noted_ticket_ids or self.removed_worktrees)


class CrashRecoveryService:
    """Repairs ticket states that were left behind by a crashed worker."""

    def __init__(
        self,
        repository: TicketRepository,
        workspace_guard: GitWorkspaceGuard,
    ) -> None:
        self.repository = repository
        self.workspace_guard = workspace_guard

    def recover_orphaned_execution(self) -> CrashRecoveryResult:
        recovered_ids: list[str] = []
        noted_ids: list[str] = []
        ticket_ids: list[str] = []

        for status in (TicketStatus.IN_PROGRESS, TicketStatus.TESTING):
            for ticket in self.repository.list(status=status):
                ticket_ids.append(ticket.id)
                if execution_registry.is_registered(execution_key(self.repository.project_id, ticket.id)):
                    continue
                if status == TicketStatus.TESTING:
                    self._recover_testing_ticket(ticket)
                    recovered_ids.append(ticket.id)
                elif self._note_in_progress_orphan(ticket):
                    noted_ids.append(ticket.id)

        removed_worktrees = self.workspace_guard.remove_orphaned_ticket_worktrees(ticket_ids)
        return CrashRecoveryResult(
            recovered_ticket_ids=recovered_ids,
            noted_ticket_ids=noted_ids,
            removed_worktrees=[str(path) for path in removed_worktrees],
        )

    def _recover_testing_ticket(self, ticket: Ticket) -> Ticket:
        ticket_json = ticket.to_dict()
        ticket_json["status"] = TicketStatus.IN_PROGRESS.value
        ticket_json["result"] = Result(outcome="pending").model_dump(
            mode="json",
            exclude_none=True,
        )
        metadata = ticket_json.setdefault("metadata", {})
        metadata["orphan_recovered_from"] = TicketStatus.TESTING.value
        metadata["orphan_recovered_to"] = TicketStatus.IN_PROGRESS.value
        metadata["orphan_recovery_reason"] = "worker_restart_without_registered_execution"
        metadata["orphan_recovered_at"] = _now()
        updated = self.repository.save(Ticket.from_dict(ticket_json))
        self.repository.append_log(
            ticket.id,
            "Recovered orphaned testing ticket after worker restart; queued for rerun",
            level="warn",
        )
        return updated

    def _note_in_progress_orphan(self, ticket: Ticket) -> bool:
        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        if metadata.get("orphan_recovered_from") == TicketStatus.IN_PROGRESS.value:
            return False
        if metadata.get("orphan_recovery_reason") == "worker_restart_without_registered_execution":
            return False

        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        metadata["orphan_recovered_from"] = TicketStatus.IN_PROGRESS.value
        metadata["orphan_recovered_to"] = TicketStatus.IN_PROGRESS.value
        metadata["orphan_recovery_reason"] = "worker_restart_without_registered_execution"
        metadata["orphan_recovered_at"] = _now()
        self.repository.save(Ticket.from_dict(ticket_json))
        self.repository.append_log(
            ticket.id,
            "Recovered orphaned in-progress ticket after worker restart; it will be retried",
            level="warn",
        )
        return True


def _now() -> str:
    return datetime.now(UTC).isoformat()
