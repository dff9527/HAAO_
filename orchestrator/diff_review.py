from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.db.sqlite import RunEventRepository, TicketRepository
from orchestrator.execution_safety import DiffScopeError, GitWorkspaceGuard
from orchestrator.git_flow import GitTicketFlow, now_iso
from orchestrator.models.ticket import Result, Ticket, TicketStatus
from orchestrator.state_machine import InvalidTransitionError, TicketStateService
from orchestrator.supply_chain import build_supply_chain_signal


@dataclass(frozen=True)
class DiffReviewResult:
    ticket: Ticket
    applied: bool


class DiffReviewService:
    def __init__(
        self,
        repository: TicketRepository,
        state_service: TicketStateService,
        *,
        repo_root: str | Path,
        workspace_guard: GitWorkspaceGuard | None = None,
        git_flow: GitTicketFlow | None = None,
    ) -> None:
        self.repository = repository
        self.state_service = state_service
        self.repo_root = Path(repo_root).resolve()
        self.workspace_guard = workspace_guard or GitWorkspaceGuard(self.repo_root)
        self.git_flow = git_flow or GitTicketFlow(
            self.repo_root,
            workspace_guard=self.workspace_guard,
        )
        self.run_events = RunEventRepository(repository.connection)

    def approve_diff(self, ticket_id: str) -> DiffReviewResult:
        ticket = self._require_ticket(ticket_id)
        if TicketStatus(ticket.status) != TicketStatus.DIFF_PENDING:
            raise InvalidTransitionError(
                "Only diff_pending tickets can have their diff approved"
            )

        diff = ticket.result.diff if ticket.result and ticket.result.diff else ""
        if not diff.strip():
            raise DiffScopeError("Ticket has no pending diff to approve")

        try:
            commit = self.git_flow.approve_diff_to_branch(ticket, diff)
        except DiffScopeError as exc:
            self.run_events.append_run_event(
                project_id=_ticket_project_id(ticket),
                requirement_id=_ticket_requirement_id(ticket),
                ticket_id=ticket.id,
                run_id=_ticket_run_id(ticket),
                event_type="diff_scope_reject",
                model_id=ticket.execution.assigned_model,
                payload={
                    "detail": str(exc),
                    "target_files": ticket.task.target_files,
                    "reason": "unrelated-change attempt",
                },
            )
            raise
        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        metadata["git_branch"] = commit.branch
        metadata["git_commit"] = commit.commit
        metadata["git_base_branch"] = commit.base_branch
        metadata["diff_approved_at"] = now_iso()
        metadata["supply_chain"] = build_supply_chain_signal(diff)
        ticket_json["audit"] = {"verdict": "pending", "feedback": "", "reviewed_by": ""}
        self.repository.save(Ticket.from_dict(ticket_json))
        moved = self.state_service.move(ticket.id, TicketStatus.REVIEW).ticket
        self.repository.append_log(
            moved.id,
            f"Diff approved, committed to {commit.branch} at {commit.commit[:12]}; moved to technical review",
        )
        return DiffReviewResult(ticket=moved, applied=True)

    def reject_diff(self, ticket_id: str, feedback: str) -> DiffReviewResult:
        ticket = self._require_ticket(ticket_id)
        if TicketStatus(ticket.status) != TicketStatus.DIFF_PENDING:
            raise InvalidTransitionError(
                "Only diff_pending tickets can have their diff rejected"
            )

        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        metadata["diff_rejection_feedback"] = feedback
        ticket_json["execution"]["attempts"] = 0
        ticket_json["result"] = Result(outcome="pending").model_dump(mode="json", exclude_none=True)
        ticket = self.repository.save(Ticket.from_dict(ticket_json))
        moved = self.state_service.move(ticket.id, TicketStatus.IN_PROGRESS).ticket
        self.repository.append_log(
            moved.id,
            f"Diff rejected: {feedback}",
            level="warn",
        )
        return DiffReviewResult(ticket=moved, applied=False)

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.repository.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return ticket


def _ticket_project_id(ticket: Ticket) -> str:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    project_id = metadata.get("project_id")
    return project_id if isinstance(project_id, str) and project_id else "default"


def _ticket_requirement_id(ticket: Ticket) -> str | None:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    requirement_id = metadata.get("requirement_id")
    return requirement_id if isinstance(requirement_id, str) and requirement_id else None


def _ticket_run_id(ticket: Ticket) -> str | None:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    run_id = metadata.get("last_run_id")
    return run_id if isinstance(run_id, str) and run_id else None
