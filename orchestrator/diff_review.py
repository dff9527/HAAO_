from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.db.sqlite import TicketRepository
from orchestrator.execution_safety import DiffScopeError, GitWorkspaceGuard
from orchestrator.git_flow import GitTicketFlow, now_iso
from orchestrator.models.ticket import Result, Ticket, TicketStatus
from orchestrator.state_machine import InvalidTransitionError, TicketStateService


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

    def approve_diff(self, ticket_id: str) -> DiffReviewResult:
        ticket = self._require_ticket(ticket_id)
        if TicketStatus(ticket.status) != TicketStatus.DIFF_PENDING:
            raise InvalidTransitionError(
                "Only diff_pending tickets can have their diff approved"
            )

        diff = ticket.result.diff if ticket.result and ticket.result.diff else ""
        if not diff.strip():
            raise DiffScopeError("Ticket has no pending diff to approve")

        commit = self.git_flow.approve_diff_to_branch(ticket, diff)
        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        metadata["git_branch"] = commit.branch
        metadata["git_commit"] = commit.commit
        metadata["git_base_branch"] = commit.base_branch
        metadata["diff_approved_at"] = now_iso()
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
