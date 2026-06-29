from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.crash_recovery import CrashRecoveryService
from orchestrator.db.sqlite import RunEventRepository, TicketRepository
from orchestrator.escalation import EscalationResult, EscalationService
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.execution_safety import GitWorkspaceGuard
from orchestrator.models.ticket import Ticket, TicketStatus
from orchestrator.review_flow import ReviewService


@dataclass(frozen=True)
class AutoRunResult:
    idle: bool
    skipped_reason: str = ""
    executed_ticket_ids: list[str] = field(default_factory=list)
    reviewed_ticket_ids: list[str] = field(default_factory=list)
    escalated_ticket_ids: list[str] = field(default_factory=list)
    recovered_ticket_ids: list[str] = field(default_factory=list)
    cleaned_worktrees: list[str] = field(default_factory=list)
    waiting_ticket_ids: list[str] = field(default_factory=list)
    final_tickets: list[dict] = field(default_factory=list)


class AutoOrchestrator:
    def __init__(
        self,
        repository: TicketRepository,
        execution_loop: ExecutionLoop,
        review_service: ReviewService,
        escalation_service: EscalationService,
        *,
        repo_root: str | Path,
        workspace_guard: GitWorkspaceGuard | None = None,
        allow_dirty_workspace: bool = False,
        worker_id: str = "auto-worker-1",
        lease_ttl_sec: int = 300,
    ) -> None:
        self.repository = repository
        self.execution_loop = execution_loop
        self.review_service = review_service
        self.escalation_service = escalation_service
        self.repo_root = Path(repo_root).resolve()
        self.workspace_guard = workspace_guard or GitWorkspaceGuard(self.repo_root)
        self.allow_dirty_workspace = allow_dirty_workspace
        self.worker_id = worker_id
        self.lease_ttl_sec = lease_ttl_sec
        self.crash_recovery = CrashRecoveryService(repository, self.workspace_guard)
        self.run_events = RunEventRepository(repository.connection)

    def run_once(self) -> AutoRunResult:
        recovery = self.crash_recovery.recover_orphaned_execution()
        review_ticket = self._first_ticket(TicketStatus.REVIEW)
        if review_ticket is not None:
            review_result = self.review_service.review_ticket(review_ticket.id)
            return AutoRunResult(
                idle=False,
                reviewed_ticket_ids=[review_ticket.id],
                recovered_ticket_ids=recovery.recovered_ticket_ids,
                cleaned_worktrees=recovery.removed_worktrees,
                final_tickets=[review_result.ticket.to_dict()],
            )

        blocked_ticket = self._first_auto_escalation_ticket()
        if blocked_ticket is not None:
            escalation = self.escalation_service.handle_blocked_ticket(blocked_ticket.id)
            return _result_for_escalation(escalation, recovery)

        claim = self.repository.claim_next_ready_ticket(
            worker_id=self.worker_id,
            ttl_sec=self.lease_ttl_sec,
        )
        execution_ticket = claim.ticket
        if execution_ticket is None:
            in_progress = self._first_ticket(TicketStatus.IN_PROGRESS)
            if in_progress is not None:
                execution_ticket = in_progress
            else:
                ready_tickets = self.repository.list(status=TicketStatus.READY)
                waiting_ticket_ids = [ticket.id for ticket in ready_tickets]
                if claim.skipped_reason == "target_file_conflict":
                    self._record_conflicts(claim.conflict_ticket_ids or [])
                return AutoRunResult(
                    idle=not recovery.changed,
                    skipped_reason=claim.skipped_reason or ("dependencies_pending" if waiting_ticket_ids else ""),
                    recovered_ticket_ids=recovery.recovered_ticket_ids,
                    cleaned_worktrees=recovery.removed_worktrees,
                    waiting_ticket_ids=waiting_ticket_ids,
                )
        if execution_ticket is None:
            return AutoRunResult(
                idle=not recovery.changed,
                recovered_ticket_ids=recovery.recovered_ticket_ids,
                cleaned_worktrees=recovery.removed_worktrees,
            )

        if not self.allow_dirty_workspace and self.workspace_guard.is_dirty():
            return AutoRunResult(
                idle=False,
                skipped_reason="workspace_dirty",
                recovered_ticket_ids=recovery.recovered_ticket_ids,
                cleaned_worktrees=recovery.removed_worktrees,
            )

        try:
            execution = self.execution_loop.run_ticket(execution_ticket.id)
        finally:
            self.repository.release_lease(execution_ticket.id, worker_id=self.worker_id)
        executed_ids = [execution_ticket.id]
        final_tickets = [execution.ticket.to_dict()]
        reviewed_ids: list[str] = []
        escalated_ids: list[str] = []

        if execution.ticket.status == TicketStatus.DIFF_PENDING.value:
            return AutoRunResult(
                idle=False,
                executed_ticket_ids=executed_ids,
                recovered_ticket_ids=recovery.recovered_ticket_ids,
                cleaned_worktrees=recovery.removed_worktrees,
                final_tickets=final_tickets,
            )

        if execution.ticket.status == TicketStatus.REVIEW.value:
            review_result = self.review_service.review_ticket(execution.ticket.id)
            reviewed_ids.append(execution.ticket.id)
            final_tickets = [review_result.ticket.to_dict()]
        elif execution.escalated:
            escalation = self.escalation_service.handle_blocked_ticket(execution.ticket.id)
            if escalation.handled:
                escalated_ids.append(execution.ticket.id)
                final_tickets = [escalation.ticket.to_dict()]

        return AutoRunResult(
            idle=False,
            executed_ticket_ids=executed_ids,
            reviewed_ticket_ids=reviewed_ids,
            escalated_ticket_ids=escalated_ids,
            recovered_ticket_ids=recovery.recovered_ticket_ids,
            cleaned_worktrees=recovery.removed_worktrees,
            final_tickets=final_tickets,
        )

    def run_until_idle(self, max_cycles: int = 10) -> list[AutoRunResult]:
        results: list[AutoRunResult] = []
        for _ in range(max_cycles):
            result = self.run_once()
            results.append(result)
            if result.idle or result.skipped_reason:
                break
        return results

    def _first_ticket(self, status: TicketStatus) -> Ticket | None:
        tickets = self.repository.list(status=status)
        return tickets[0] if tickets else None

    def _first_auto_escalation_ticket(self) -> Ticket | None:
        for ticket in self.repository.list(status=TicketStatus.BLOCKED):
            metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
            if metadata.get("escalated_to", ticket.execution.escalate_to) == "tech_lead":
                if metadata.get("escalation_handled_by") != "claude-tech-lead":
                    return ticket
        return None

    def _dependencies_satisfied(self, ticket: Ticket) -> bool:
        for dependency_id in [*ticket.dependencies, *ticket.depends_on]:
            dependency = self.repository.get(dependency_id)
            if dependency is None or dependency.status != TicketStatus.DONE.value:
                return False

            # A completed ticket with a Git branch is not available to a
            # dependent worktree until that branch has been merged to base.
            metadata = dependency.metadata.model_dump(mode="json") if dependency.metadata else {}
            if metadata.get("git_branch") and not metadata.get("git_merge_commit"):
                return False
        return True

    def _record_conflicts(self, ticket_ids: list[str]) -> None:
        for ticket_id in ticket_ids:
            ticket = self.repository.get(ticket_id)
            if ticket is None:
                continue
            metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
            active = self.repository.active_leases()
            conflicting = [
                active_ticket.id
                for active_ticket in active
                if active_ticket.id != ticket.id
                and set(active_ticket.task.target_files).intersection(ticket.task.target_files)
            ]
            self.run_events.append_run_event(
                project_id=metadata.get("project_id") if isinstance(metadata.get("project_id"), str) else "default",
                requirement_id=metadata.get("requirement_id") if isinstance(metadata.get("requirement_id"), str) else None,
                ticket_id=ticket.id,
                run_id=None,
                event_type="conflict",
                model_id=ticket.execution.assigned_model,
                payload={
                    "reason": "target_file_overlap",
                    "detail": "Ticket held because another leased ticket overlaps target_files",
                    "target_files": ticket.task.target_files,
                    "conflicting_ticket_ids": conflicting,
                },
            )


def _result_for_escalation(escalation: EscalationResult, recovery) -> AutoRunResult:
    return AutoRunResult(
        idle=False,
        escalated_ticket_ids=[escalation.ticket.id] if escalation.handled else [],
        recovered_ticket_ids=recovery.recovered_ticket_ids,
        cleaned_worktrees=recovery.removed_worktrees,
        final_tickets=[escalation.ticket.to_dict()],
    )
