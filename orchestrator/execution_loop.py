from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from clients.lmstudio import ChatMessage
from orchestrator.db.sqlite import SettingsRepository, TicketRepository
from orchestrator.execution_safety import GitWorkspaceGuard, normalize_repo_path
from orchestrator.model_policy import next_local_fallback_model
from orchestrator.models.ticket import Result, Ticket, TicketStatus
from orchestrator.runner.dod_runner import TestRunResult, TestRunner
from orchestrator.execution_registry import ExecutionCancelledError, execution_key, execution_registry
from orchestrator.manual_ticket_flow import is_unverified_ticket
from orchestrator.state_machine import InvalidTransitionError, TicketStateService
from orchestrator.notifications import NotificationService


class LocalModel(Protocol):
    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
    ) -> str:
        ...


class WholeFileWriteError(RuntimeError):
    """Raised when model output cannot be safely written to the workspace."""


@dataclass(frozen=True)
class ExecutionResult:
    ticket: Ticket
    passed: bool
    escalated: bool = False


class WholeFileWriter:
    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def write(self, target_file: str, content: str, allowed_target_files: list[str]) -> None:
        content = strip_code_fence(content)
        if not content:
            raise WholeFileWriteError("Local model returned empty file content")

        normalized_target = normalize_repo_path(target_file)
        allowed = {normalize_repo_path(path) for path in allowed_target_files}
        if normalized_target not in allowed:
            raise WholeFileWriteError(
                f"Target file {normalized_target} is outside ticket.task.target_files"
            )

        destination = (self.repo_root / normalized_target).resolve()
        if not destination.is_relative_to(self.repo_root):
            raise WholeFileWriteError(
                f"Target file {normalized_target} resolves outside repo_root"
            )
        if not destination.parent.exists():
            raise WholeFileWriteError(
                f"Parent directory does not exist for target file {normalized_target}"
            )

        destination.write_text(content, encoding="utf-8")


class ExecutionLoop:
    def __init__(
        self,
        repository: TicketRepository,
        state_service: TicketStateService,
        local_model: LocalModel,
        *,
        repo_root: str | Path,
        test_runner: TestRunner | None = None,
        file_writer: WholeFileWriter | None = None,
        workspace_guard: GitWorkspaceGuard | None = None,
        settings_repository: SettingsRepository | None = None,
    ) -> None:
        self.repository = repository
        self.state_service = state_service
        self.local_model = local_model
        self.repo_root = Path(repo_root).resolve()
        self.test_runner = test_runner
        self.file_writer = file_writer
        self.workspace_guard = workspace_guard or GitWorkspaceGuard(self.repo_root)
        self.settings_repository = settings_repository

    def run_ticket(self, ticket_id: str) -> ExecutionResult:
        registry_key = execution_key(self.repository.project_id, ticket_id)
        cancel_event = execution_registry.register(registry_key)
        try:
            ticket = self._prepare_ticket(ticket_id)
            with self.workspace_guard.worktree_for_ticket(ticket.id) as worktree_root:
                execution_registry.set_worktree(registry_key, worktree_root)
                return self._run_ticket_in_worktree(ticket, worktree_root, cancel_event, registry_key)
        except ExecutionCancelledError:
            ticket = self._require_ticket(ticket_id)
            ticket_json = ticket.to_dict()
            ticket_json["status"] = TicketStatus.READY.value
            ticket_json["execution"]["attempts"] = 0
            ticket_json["result"] = Result(outcome="pending").model_dump(mode="json", exclude_none=True)
            saved = self.repository.save(Ticket.from_dict(ticket_json))
            self.repository.append_log(ticket_id, "Execution cancelled by user", level="warn")
            return ExecutionResult(ticket=saved, passed=False)
        finally:
            execution_registry.unregister(registry_key)

    def _check_cancelled(self, registry_key: str) -> None:
        if execution_registry.is_cancelled(registry_key):
            raise ExecutionCancelledError(f"Execution cancelled for {registry_key}")

    def _run_ticket_in_worktree(
        self,
        ticket: Ticket,
        worktree_root: Path,
        cancel_event,
        registry_key: str,
    ) -> ExecutionResult:
        file_writer = self.file_writer or WholeFileWriter(worktree_root)
        test_runner = self._test_runner_for(worktree_root)
        while True:
            self._check_cancelled(registry_key)
            if ticket.execution.attempts > 0:
                self.workspace_guard.reset_worktree(worktree_root)
                self.repository.append_log(ticket.id, "Reset ticket worktree before retry")

            write_error: WholeFileWriteError | None = None
            try:
                for target_file in ticket.task.target_files:
                    self._check_cancelled(registry_key)
                    self.repository.append_log(
                        ticket.id,
                        f"Dispatching {target_file} to local model for whole-file rewrite",
                    )
                    model_output = self.local_model.chat_completion(
                        model=ticket.execution.assigned_model,
                        messages=[
                            ChatMessage(
                                role="user",
                                content=build_file_rewrite_prompt(ticket, target_file),
                            )
                        ],
                        temperature=0.2,
                    )
                    file_writer.write(target_file, model_output, ticket.task.target_files)
            except WholeFileWriteError as exc:
                write_error = exc

            ticket = self.state_service.move(ticket.id, TicketStatus.TESTING).ticket
            self._check_cancelled(registry_key)
            diff = git_diff(worktree_root, ticket.task.target_files)

            if write_error is not None:
                self._save_result(
                    ticket,
                    diff=diff,
                    outcome="error",
                    test_output=str(write_error),
                )
                fallback_ticket = self._fallback_after_budget_exhausted(ticket)
                if fallback_ticket is not None:
                    ticket = fallback_ticket
                    self.repository.append_log(ticket.id, str(write_error), level="error")
                    self.workspace_guard.reset_worktree(worktree_root)
                    continue
                failed = self.state_service.record_test_failure(ticket.id)
                ticket = failed.ticket
                self.repository.append_log(ticket.id, str(write_error), level="error")
                if failed.escalated:
                    self._notify_intervention(ticket, "ticket_blocked")
                    return ExecutionResult(ticket=ticket, passed=False, escalated=True)
                self.workspace_guard.reset_worktree(worktree_root)
                continue

            if is_unverified_ticket(ticket):
                output = "Machine test gate skipped (unverified manual ticket)"
                passed = True
            else:
                test_results = test_runner.run_ticket_tests(ticket)
                output = format_test_results(test_results)
                passed = all(result.status == "pass" for result in test_results)

            if passed:
                self._save_result(ticket, diff=diff, outcome="success", test_output=output)
                pending = self.state_service.move(ticket.id, TicketStatus.DIFF_PENDING)
                self.repository.append_log(
                    ticket.id,
                    "Tests passed; diff pending human approval",
                )
                self._notify_intervention(pending.ticket, "diff_review_required")
                return ExecutionResult(ticket=pending.ticket, passed=True)

            self._save_result(ticket, diff=diff, outcome="test_failed", test_output=output)
            fallback_ticket = self._fallback_after_budget_exhausted(ticket)
            if fallback_ticket is not None:
                ticket = fallback_ticket
                self.workspace_guard.reset_worktree(worktree_root)
                continue
            failed = self.state_service.record_test_failure(ticket.id)
            ticket = failed.ticket
            self.repository.append_log(ticket.id, "Tests failed; retry decision recorded", level="warn")
            if failed.escalated:
                self._notify_intervention(ticket, "ticket_blocked")
                return ExecutionResult(ticket=ticket, passed=False, escalated=True)
            self.workspace_guard.reset_worktree(worktree_root)
            continue

    def _fallback_after_budget_exhausted(self, ticket: Ticket) -> Ticket | None:
        if ticket.execution.attempts + 1 <= ticket.execution.retry_budget:
            return None
        next_model = next_local_fallback_model(
            ticket.execution.assigned_model,
            self.settings_repository,
        )
        if next_model is None:
            return None

        ticket_json = ticket.to_dict()
        previous_model = ticket.execution.assigned_model
        ticket_json["status"] = TicketStatus.IN_PROGRESS.value
        ticket_json["execution"]["assigned_model"] = next_model
        ticket_json["execution"]["attempts"] = 0
        metadata = ticket_json.setdefault("metadata", {})
        metadata["local_fallback_from"] = previous_model
        metadata["local_fallback_to"] = next_model
        metadata["local_fallback_reason"] = "retry_budget_exhausted"
        updated = self.repository.save(Ticket.from_dict(ticket_json))
        self.repository.append_log(
            ticket.id,
            f"Local retry budget exhausted for {previous_model}; falling back to {next_model}",
            level="warn",
        )
        return updated

    def _notify_intervention(self, ticket: Ticket, reason: str) -> None:
        NotificationService(self.repository, self.settings_repository).notify_intervention_needed(
            ticket,
            reason,
        )

    def _prepare_ticket(self, ticket_id: str) -> Ticket:
        ticket = self._require_ticket(ticket_id)
        status = TicketStatus(ticket.status)
        if status == TicketStatus.BACKLOG:
            ticket = self.state_service.move(ticket.id, TicketStatus.READY).ticket
            status = TicketStatus(ticket.status)
        if status == TicketStatus.READY:
            ticket = self.state_service.move(ticket.id, TicketStatus.IN_PROGRESS).ticket
            status = TicketStatus(ticket.status)
        if status != TicketStatus.IN_PROGRESS:
            raise InvalidTransitionError(
                f"Ticket must be backlog, ready, or in_progress to execute; got {status.value}"
            )
        return ticket

    def _save_result(self, ticket: Ticket, *, diff: str, outcome: str, test_output: str) -> Ticket:
        ticket_json = ticket.to_dict()
        existing_logs = ticket_json.get("result", {}).get("logs", [])
        ticket_json["result"] = Result(
            outcome=outcome,
            diff=diff,
            test_output=test_output,
            logs=existing_logs,
        ).model_dump(mode="json", exclude_none=True)
        return self.repository.save(Ticket.from_dict(ticket_json))

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.repository.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return ticket

    def _test_runner_for(self, cwd: Path) -> TestRunner:
        if self.test_runner is None or type(self.test_runner) is TestRunner:
            if self.test_runner is None:
                return TestRunner(cwd=cwd)
            return TestRunner(
                cwd=cwd,
                env=self.test_runner.env,
                setup_cmd=self.test_runner.setup_cmd,
                cleanup_cmd=self.test_runner.cleanup_cmd,
                setup_timeout_sec=self.test_runner.setup_timeout_sec,
                cleanup_timeout_sec=self.test_runner.cleanup_timeout_sec,
            )
        return self.test_runner


def build_file_rewrite_prompt(ticket: Ticket, target_file: str) -> str:
    context = "\n\n".join(
        f"File: {file.path}\n```\n{file.content}\n```" for file in ticket.context.files
    )
    tests = "\n".join(
        f"- {test.command} (expect {test.expect}, timeout {test.timeout_sec}s)"
        for test in ticket.definition_of_done.tests
    )
    previous_output = ""
    if ticket.result and ticket.result.test_output:
        previous_output = f"\nPrevious test output:\n{ticket.result.test_output}\n"

    return (
        "You are the local coder model for HAAO. Rewrite exactly one file.\n"
        "Return the complete updated file content for the requested target file only.\n"
        "Do not include markdown fences, explanations, comments about the change, "
        "diff markers, or file path headers.\n\n"
        f"Ticket {ticket.id}: {ticket.title}\n"
        f"Task:\n{ticket.task.description}\n"
        f"{_rework_guidance(ticket)}\n"
        f"Allowed target files:\n{', '.join(ticket.task.target_files)}\n\n"
        f"Requested target file:\n{target_file}\n\n"
        f"Constraints:\n" + "\n".join(f"- {item}" for item in ticket.task.constraints) + "\n\n"
        f"Context:\n{context}\n\n"
        f"Definition of done:\n{tests}\n"
        f"{previous_output}"
    )


def _rework_guidance(ticket: Ticket) -> str:
    """Feed prior review/audit rejection feedback back into the coder prompt (A6).

    Without this, a rejected ticket is re-dispatched with an identical prompt, so
    the model produces the same file, the audit rejects it again ("diff identical
    to previously rejected diff"), and the rework loop never converges.
    """
    notes: list[str] = []

    audit = ticket.audit
    if audit is not None and getattr(audit, "verdict", "") == "rejected":
        feedback = (getattr(audit, "feedback", "") or "").strip()
        if feedback:
            notes.append(feedback)

    meta = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    for key in ("diff_rejection_feedback", "product_rejection_feedback"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            notes.append(value.strip())
    value = meta.get("previous_review_feedback")
    if isinstance(value, str) and value.strip():
        notes.append(value.strip())

    if not notes:
        return ""

    joined = "\n".join(f"- {note}" for note in notes)
    previous_diff = ticket.result.diff if ticket.result and ticket.result.diff else ""
    if not previous_diff.strip():
        meta = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        previous_diff = meta.get("previous_rejected_diff", "")
    diff_block = ""
    if previous_diff.strip():
        diff_block = (
            "\nYour previous diff was REJECTED — do not resubmit it unchanged:\n"
            f"{previous_diff[:4000]}\n"
        )

    return (
        "\n*** REWORK REQUIRED ***\n"
        "Your previous attempt was rejected in review. You MUST fix every point "
        "below and return a corrected file; resubmitting the same content will be "
        "rejected again:\n"
        f"{joined}\n"
        f"{diff_block}"
    )


def strip_code_fence(model_output: str) -> str:
    probe = model_output.strip()
    if not probe.startswith("```"):
        return model_output

    lines = probe.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def git_diff(repo_root: Path, target_files: list[str]) -> str:
    completed = subprocess.run(
        ["git", "diff", "--", *target_files],
        cwd=repo_root,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        return completed.stderr.strip()
    return completed.stdout


def format_test_results(results: list[TestRunResult]) -> str:
    chunks: list[str] = []
    for result in results:
        chunks.append(
            "\n".join(
                [
                    f"$ {result.command}",
                    f"status={result.status} expected={result.expect}",
                    f"return_code={result.return_code}",
                    "stdout:",
                    result.stdout,
                    "stderr:",
                    result.stderr,
                ]
            )
        )
    return "\n\n".join(chunks)
