from __future__ import annotations

import json
import re
import sqlite3
import uuid
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from orchestrator.chat_flow import ChatMessage, ReportKind, Role
from orchestrator.attachments import StoredUpload
from orchestrator.db.migrations import (
    CLAUDE_MODEL_SETTINGS_KEY,
    CLOUD_REASONER_SETTINGS_KEY,
    MODEL_ADDITIONAL_INSTRUCTIONS_KEY,
    ROLE_ROUTING_SETTINGS_KEY,
    _ensure_chat_tables,
    _ensure_eval_runs_table,
    _ensure_notifications_table,
    _ensure_requirement_templates_table,
    ensure_schema_compatibility,
    run_migrations,
)
from orchestrator.models.project import Project, validate_project_path
from orchestrator.models.requirement import Requirement, RequirementStatus
from orchestrator.models.ticket import Result, ResultLog, Ticket, TicketStatus
from orchestrator.config import get_settings
from orchestrator.redaction import current_known_secrets, redact_json, redact_text
from orchestrator.secrets_crypto import SecretEncryptionError, decrypt_secret, encrypt_secret

RunEventType = Literal[
    "run_started",
    "model_call",
    "diff_produced",
    "dod_check",
    "retry",
    "escalation",
    "egress_attempt",
    "report",
    "run_finished",
    "error",
]
CostStatus = Literal["actual", "estimated", "unknown"]
IntegrationProvider = Literal["github", "gitlab", "slack"]
NotificationKind = Literal["needs_you", "done", "blocked"]
EvalRunStatus = Literal["running", "completed", "failed"]


class DuplicateTicketError(ValueError):
    """Raised when a ticket id already exists in persistent storage."""


class TicketDeletionError(ValueError):
    """Raised when a ticket cannot be safely deleted."""


class AmbiguousTicketError(ValueError):
    """Raised when an unscoped ticket id exists in multiple projects."""


@dataclass(frozen=True)
class RunEvent:
    id: int | None
    project_id: str
    event_type: RunEventType
    ts: str
    requirement_id: str | None = None
    ticket_id: str | None = None
    run_id: str | None = None
    model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_status: CostStatus | None = None
    payload: dict | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "requirement_id": self.requirement_id,
            "ticket_id": self.ticket_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "ts": self.ts,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "cost_status": self.cost_status,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class IntegrationCredential:
    provider: IntegrationProvider
    id: str
    label: str
    scopes: list[str]
    configured: bool
    created_at: str
    updated_at: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "id": self.id,
            "label": self.label,
            "scopes": self.scopes,
            "configured": self.configured,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class NotificationRecord:
    id: int
    project_id: str
    kind: NotificationKind
    title: str
    created_at: str
    dedupe_key: str
    ticket_id: str | None = None
    requirement_id: str | None = None
    read_at: str | None = None

    @property
    def unread(self) -> bool:
        return self.read_at is None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "ticket_id": self.ticket_id,
            "requirement_id": self.requirement_id,
            "kind": self.kind,
            "title": self.title,
            "created_at": self.created_at,
            "read_at": self.read_at,
            "dedupe_key": self.dedupe_key,
            "unread": self.unread,
        }


@dataclass(frozen=True)
class EvalRunRecord:
    id: str
    model_id: str
    task_set_id: str
    status: EvalRunStatus
    trials: int
    started_at: str
    finished_at: str | None = None
    summary: dict | None = None
    baseline_run_id: str | None = None
    regressed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "task_set_id": self.task_set_id,
            "status": self.status,
            "trials": self.trials,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary or {},
            "baseline_run_id": self.baseline_run_id,
            "regressed": self.regressed,
            "error": self.error,
        }


@dataclass(frozen=True)
class RequirementTemplateRecord:
    id: str
    title: str
    prompt: str
    scope_paths: list[str]
    constraints: list[str]
    created_at: str
    updated_at: str
    built_in: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "scope_paths": self.scope_paths,
            "constraints": self.constraints,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "built_in": self.built_in,
        }


BUILT_IN_REQUIREMENT_TEMPLATES: tuple[RequirementTemplateRecord, ...] = (
    RequirementTemplateRecord(
        id="builtin-add-endpoint",
        title="Add an API endpoint",
        prompt="Add a small API endpoint for the requested resource, including validation and tests.",
        scope_paths=[],
        constraints=["Keep the endpoint narrow and consistent with existing API patterns."],
        created_at="built-in",
        updated_at="built-in",
        built_in=True,
    ),
    RequirementTemplateRecord(
        id="builtin-fix-failing-test",
        title="Fix a failing test",
        prompt="Investigate the failing test, identify the root cause, and make the minimal code change to pass it.",
        scope_paths=[],
        constraints=["Do not weaken or delete the failing test unless it is demonstrably invalid."],
        created_at="built-in",
        updated_at="built-in",
        built_in=True,
    ),
    RequirementTemplateRecord(
        id="builtin-refactor-small",
        title="Small refactor",
        prompt="Refactor the selected code for clarity while preserving behavior.",
        scope_paths=[],
        constraints=["Keep public APIs stable and add or update tests only when behavior is clarified."],
        created_at="built-in",
        updated_at="built-in",
        built_in=True,
    ),
    RequirementTemplateRecord(
        id="builtin-add-observability",
        title="Add observability",
        prompt="Add useful logging or run-event instrumentation around this workflow.",
        scope_paths=[],
        constraints=["Redact secrets and avoid noisy logs in hot paths."],
        created_at="built-in",
        updated_at="built-in",
        built_in=True,
    ),
)


def connect(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, check_same_thread=False, timeout=30.0)
    connection.row_factory = sqlite3.Row
    # Concurrency hardening (A1): WAL lets reads proceed while the auto-worker is
    # writing, and busy_timeout makes a competing writer (e.g. a DELETE request)
    # wait politely for the short write window instead of hanging/erroring on a
    # lock. All write methods commit immediately, so the lock window is tiny.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL,
            ticket_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, id)
        );

        CREATE TABLE IF NOT EXISTS ticket_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_ticket_logs_ticket_id ON ticket_logs(ticket_id);

        CREATE TABLE IF NOT EXISTS requirements (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL,
            requirement_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, id)
        );

        CREATE INDEX IF NOT EXISTS idx_requirements_status ON requirements(status);

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            default_branch TEXT NOT NULL,
            env_json TEXT NOT NULL DEFAULT '{}',
            env_allowlist_json TEXT NOT NULL DEFAULT '["PATH", "PYTHONPATH"]',
            test_allow_network INTEGER NOT NULL DEFAULT 0,
            sandbox_mode TEXT NOT NULL DEFAULT 'auto',
            setup_cmd TEXT NOT NULL DEFAULT '',
            cleanup_cmd TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    _ensure_chat_tables(connection)
    _ensure_eval_runs_table(connection)
    _ensure_requirement_templates_table(connection)
    connection.commit()
    run_migrations(connection)
    ensure_schema_compatibility(connection)


class ProjectRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        initialize_database(connection)

    def create(
        self,
        *,
        name: str,
        path: str | Path,
        default_branch: str = "main",
        project_id: str | None = None,
    ) -> Project:
        repo_path = validate_project_path(path)
        project = Project(
            id=project_id or self.next_id(),
            name=name,
            path=str(repo_path),
            default_branch=default_branch,
            created_at=datetime.now(UTC),
        )
        self.connection.execute(
            """
            INSERT INTO projects (
                id, name, path, default_branch, env_json, env_allowlist_json,
                test_allow_network, sandbox_mode, setup_cmd, cleanup_cmd, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                project.name,
                project.path,
                project.default_branch,
                json.dumps(project.env, ensure_ascii=False, sort_keys=True),
                json.dumps(project.env_allowlist, ensure_ascii=False),
                int(project.test_allow_network),
                project.sandbox_mode,
                project.setup_cmd,
                project.cleanup_cmd,
                project.created_at.isoformat() if project.created_at else _now(),
            ),
        )
        self.connection.commit()
        return project

    def get(self, project_id: str) -> Project | None:
        row = self.connection.execute(
            """
            SELECT id, name, path, default_branch, env_json, env_allowlist_json,
                   test_allow_network, sandbox_mode, setup_cmd, cleanup_cmd, created_at
            FROM projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        return _project_from_row(row)

    def list(self) -> list[Project]:
        rows = self.connection.execute(
            """
            SELECT id, name, path, default_branch, env_json, env_allowlist_json,
                   test_allow_network, sandbox_mode, setup_cmd, cleanup_cmd, created_at
            FROM projects
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        return [_project_from_row(row) for row in rows]

    def save(self, project: Project) -> Project:
        cursor = self.connection.execute(
            """
            UPDATE projects
            SET name = ?, path = ?, default_branch = ?, env_json = ?,
                env_allowlist_json = ?, test_allow_network = ?, sandbox_mode = ?,
                setup_cmd = ?, cleanup_cmd = ?
            WHERE id = ?
            """,
            (
                project.name,
                project.path,
                project.default_branch,
                json.dumps(project.env, ensure_ascii=False, sort_keys=True),
                json.dumps(project.env_allowlist, ensure_ascii=False),
                int(project.test_allow_network),
                project.sandbox_mode,
                project.setup_cmd,
                project.cleanup_cmd,
                project.id,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Project not found: {project.id}")
        self.connection.commit()
        return project

    def update_settings(
        self,
        project_id: str,
        *,
        env: dict[str, str] | None = None,
        env_allowlist: list[str] | None = None,
        test_allow_network: bool | None = None,
        sandbox_mode: str | None = None,
        setup_cmd: str | None = None,
        cleanup_cmd: str | None = None,
        default_branch: str | None = None,
    ) -> Project:
        project = self.get(project_id)
        if project is None:
            raise KeyError(f"Project not found: {project_id}")
        updated = project.model_copy(
            update={
                "env": env if env is not None else project.env,
                "env_allowlist": env_allowlist if env_allowlist is not None else project.env_allowlist,
                "test_allow_network": (
                    test_allow_network
                    if test_allow_network is not None
                    else project.test_allow_network
                ),
                "sandbox_mode": sandbox_mode if sandbox_mode is not None else project.sandbox_mode,
                "setup_cmd": setup_cmd if setup_cmd is not None else project.setup_cmd,
                "cleanup_cmd": cleanup_cmd if cleanup_cmd is not None else project.cleanup_cmd,
                "default_branch": default_branch if default_branch is not None else project.default_branch,
            }
        )
        return self.save(Project.model_validate(updated.to_dict()))

    def delete(self, project_id: str) -> None:
        if project_id == "default":
            raise ValueError("The default project cannot be deleted")
        if self.get(project_id) is None:
            raise KeyError(f"Project not found: {project_id}")
        self.connection.execute("DELETE FROM ticket_logs WHERE project_id = ?", (project_id,))
        self.connection.execute("DELETE FROM tickets WHERE project_id = ?", (project_id,))
        self.connection.execute("DELETE FROM requirements WHERE project_id = ?", (project_id,))
        self.connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self.connection.commit()

    def next_id(self) -> str:
        rows = self.connection.execute("SELECT id FROM projects").fetchall()
        max_number = 0
        for row in rows:
            match = re.fullmatch(r"P-(\d+)", row["id"])
            if match:
                max_number = max(max_number, int(match.group(1)))
        return f"P-{max_number + 1:03d}"


class TicketRepository:
    def __init__(self, connection: sqlite3.Connection, project_id: str | None = None):
        self.connection = connection
        self.project_id = project_id
        initialize_database(connection)

    def scoped(self, project_id: str | None) -> TicketRepository:
        return TicketRepository(self.connection, project_id=project_id)

    def create(self, ticket: Ticket, project_id: str | None = None) -> Ticket:
        now = _now()
        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        effective_project_id = self._effective_project_id(project_id, metadata)
        metadata["project_id"] = effective_project_id
        metadata.setdefault("created_at", now)
        metadata["updated_at"] = now
        stored_ticket = Ticket.from_dict(ticket_json)

        try:
            self.connection.execute(
                """
                INSERT INTO tickets (id, project_id, status, ticket_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_ticket.id,
                    effective_project_id,
                    stored_ticket.status,
                    _dumps(stored_ticket),
                    metadata["created_at"],
                    metadata["updated_at"],
                ),
            )
        except sqlite3.IntegrityError as exc:
            if _is_duplicate_ticket_error(exc):
                raise DuplicateTicketError(
                    f"Ticket id already exists: {stored_ticket.id} in project {effective_project_id}"
                ) from exc
            raise
        self.connection.commit()
        return stored_ticket

    def next_ticket_number(self, project_id: str | None = None) -> int:
        effective_project_id = self._effective_project_id(project_id)
        rows = self.connection.execute(
            "SELECT id FROM tickets WHERE project_id = ?",
            (effective_project_id,),
        ).fetchall()
        max_number = 0
        for row in rows:
            match = re.fullmatch(r"T-(\d+)", row["id"])
            if match:
                max_number = max(max_number, int(match.group(1)))
        return max_number + 1

    def next_ticket_id(self, project_id: str | None = None) -> str:
        return f"T-{self.next_ticket_number(project_id):03d}"

    def get(self, ticket_id: str, project_id: str | None = None) -> Ticket | None:
        effective_project_id = self._optional_project_id(project_id)
        if effective_project_id is None:
            rows = self.connection.execute(
                "SELECT ticket_json FROM tickets WHERE id = ?",
                (ticket_id,),
            ).fetchall()
            if len(rows) > 1:
                raise AmbiguousTicketError(
                    f"Ticket id {ticket_id} exists in multiple projects; pass project_id"
                )
            row = rows[0] if rows else None
        else:
            row = self.connection.execute(
                """
                SELECT ticket_json FROM tickets
                WHERE id = ? AND project_id = ?
                """,
                (ticket_id, effective_project_id),
            ).fetchone()
        if row is None:
            return None
        return Ticket.from_dict(json.loads(row["ticket_json"]))

    def save(self, ticket: Ticket, project_id: str | None = None) -> Ticket:
        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        effective_project_id = self._effective_project_id(project_id, metadata)
        metadata["project_id"] = effective_project_id
        metadata["updated_at"] = _now()
        updated_ticket = Ticket.from_dict(ticket_json)

        cursor = self.connection.execute(
            """
            UPDATE tickets
            SET status = ?, ticket_json = ?, updated_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                updated_ticket.status,
                _dumps(updated_ticket),
                ticket_json["metadata"]["updated_at"],
                updated_ticket.id,
                effective_project_id,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Ticket not found: {updated_ticket.id}")
        self.connection.commit()
        return updated_ticket

    def list(
        self,
        status: TicketStatus | str | None = None,
        project_id: str | None = None,
    ) -> list[Ticket]:
        effective_project_id = self._optional_project_id(project_id)
        if status is None and effective_project_id is None:
            rows = self.connection.execute(
                "SELECT ticket_json FROM tickets ORDER BY created_at ASC, project_id ASC, id ASC"
            ).fetchall()
        elif status is None:
            rows = self.connection.execute(
                """
                SELECT ticket_json FROM tickets
                WHERE project_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (effective_project_id,),
            ).fetchall()
        elif effective_project_id is None:
            rows = self.connection.execute(
                """
                SELECT ticket_json FROM tickets
                WHERE status = ?
                ORDER BY created_at ASC, project_id ASC, id ASC
                """,
                (str(status),),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT ticket_json FROM tickets
                WHERE status = ? AND project_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (str(status), effective_project_id),
            ).fetchall()
        return [Ticket.from_dict(json.loads(row["ticket_json"])) for row in rows]

    def update_status(self, ticket_id: str, status: TicketStatus | str) -> Ticket:
        ticket = self._require_ticket(ticket_id)
        ticket_json = ticket.to_dict()
        ticket_json["status"] = TicketStatus(status).value
        return self.save(Ticket.from_dict(ticket_json))

    def append_log(
        self,
        ticket_id: str,
        message: str,
        level: str = "info",
        ts: datetime | None = None,
    ) -> Ticket:
        ticket = self._require_ticket(ticket_id)
        project_id = _ticket_project_id(ticket)
        timestamp = (ts or datetime.now(UTC)).isoformat()
        redacted_message = redact_text(message, extra_secrets=_configured_secret_values(self.connection))
        log = ResultLog(ts=timestamp, level=level, message=redacted_message)

        ticket_json = ticket.to_dict()
        result = ticket_json.setdefault(
            "result",
            Result().model_dump(mode="json", exclude_none=True),
        )
        logs = result.setdefault("logs", [])
        logs.append(log.model_dump(mode="json"))
        ticket_json.setdefault("metadata", {})["updated_at"] = _now()
        updated_ticket = Ticket.from_dict(ticket_json)

        self.connection.execute(
            """
            INSERT INTO ticket_logs (ticket_id, ts, level, message)
            VALUES (?, ?, ?, ?)
            """,
            (ticket_id, timestamp, level, redacted_message),
        )
        self.connection.execute(
            """
            UPDATE tickets
            SET ticket_json = ?, updated_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                _dumps(updated_ticket),
                ticket_json["metadata"]["updated_at"],
                ticket_id,
                project_id,
            ),
        )
        self.connection.execute(
            """
            UPDATE ticket_logs
            SET project_id = ?
            WHERE id = last_insert_rowid()
            """,
            (project_id,),
        )
        self.connection.commit()
        return updated_ticket

    def delete(self, ticket_id: str, *, force: bool = False) -> None:
        ticket = self._require_ticket(ticket_id)
        project_id = _ticket_project_id(ticket)
        if TicketStatus(ticket.status) in {TicketStatus.IN_PROGRESS, TicketStatus.TESTING} and not force:
            raise TicketDeletionError(
                f"Ticket {ticket_id} is {ticket.status}; pass force=true to delete it"
            )

        self.connection.execute(
            "DELETE FROM ticket_logs WHERE ticket_id = ? AND project_id = ?",
            (ticket_id, project_id),
        )
        self.connection.execute(
            "DELETE FROM tickets WHERE id = ? AND project_id = ?",
            (ticket_id, project_id),
        )
        self.connection.commit()

    def logs_for_ticket(
        self,
        ticket_id: str,
        project_id: str | None = None,
    ) -> list[dict[str, str]]:
        effective_project_id = self._optional_project_id(project_id)
        if effective_project_id is None:
            rows = self.connection.execute(
                """
                SELECT ts, level, message FROM ticket_logs
                WHERE ticket_id = ?
                ORDER BY id ASC
                """,
                (ticket_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT ts, level, message FROM ticket_logs
                WHERE ticket_id = ? AND project_id = ?
                ORDER BY id ASC
                """,
                (ticket_id, effective_project_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return ticket

    def _optional_project_id(self, project_id: str | None = None) -> str | None:
        return project_id or self.project_id

    def _effective_project_id(
        self,
        project_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        candidate = project_id or self.project_id
        if candidate:
            return candidate
        if metadata and isinstance(metadata.get("project_id"), str):
            return metadata["project_id"]
        return "default"


@dataclass(frozen=True)
class ChatSegment:
    id: str
    project_id: str
    title: str
    summary: str
    created_at: str
    is_active: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "summary": self.summary,
            "created_at": self.created_at,
            "is_active": self.is_active,
        }


@dataclass(frozen=True)
class ChatAttachment:
    id: str
    project_id: str
    filename: str
    mime: str
    size: int
    kind: str
    stored_path: str
    created_at: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "filename": self.filename,
            "mime": self.mime,
            "size": self.size,
            "kind": self.kind,
            "stored_path": self.stored_path,
        }


class ChatRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        initialize_database(connection)

    def append_message(
        self,
        *,
        project_id: str,
        role: Role,
        text: str,
        segment_id: str,
        requirement_id: str | None = None,
        ticket_id: str | None = None,
        report_kind: ReportKind | None = None,
        attachment_ids: list[str] | None = None,
    ) -> ChatMessage:
        cleaned_attachment_ids = _dedupe_attachment_ids(attachment_ids)
        if cleaned_attachment_ids:
            found = self.attachments_by_ids(project_id, cleaned_attachment_ids)
            found_ids = {attachment.id for attachment in found}
            missing = [item for item in cleaned_attachment_ids if item not in found_ids]
            if missing:
                raise KeyError(f"Attachment not found: {missing[0]}")
        message = ChatMessage(
            id=_next_chat_id("CM"),
            project_id=project_id,
            role=role,
            text=text,
            segment_id=segment_id,
            created_at=_now(),
            requirement_id=requirement_id,
            ticket_id=ticket_id,
            report_kind=report_kind,
            attachment_ids=cleaned_attachment_ids,
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO chat_messages (
                    id, project_id, role, text, segment_id, created_at,
                    requirement_id, ticket_id, report_kind
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.project_id,
                    message.role,
                    message.text,
                    message.segment_id,
                    message.created_at,
                    message.requirement_id,
                    message.ticket_id,
                    message.report_kind,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO chat_message_attachments (message_id, attachment_id)
                VALUES (?, ?)
                """,
                [(message.id, attachment_id) for attachment_id in cleaned_attachment_ids],
            )
        if message.role == "system_report" and message.report_kind:
            _record_notification_from_chat_report(self.connection, message)
        return message

    def list_messages(
        self,
        project_id: str,
        *,
        segment_id: str | None = None,
        after: str | None = None,
        limit: int | None = None,
    ) -> list[ChatMessage]:
        clauses = ["project_id = ?"]
        params: list[object] = [project_id]
        if segment_id is not None:
            clauses.append("segment_id = ?")
            params.append(segment_id)
        if after is not None:
            clauses.append("id > ?")
            params.append(after)

        where = " AND ".join(clauses)
        bounded_limit = _bounded_chat_limit(limit)
        if after is None and bounded_limit is not None:
            rows = self.connection.execute(
                f"""
                SELECT id, project_id, role, text, segment_id, created_at,
                       requirement_id, ticket_id, report_kind
                FROM chat_messages
                WHERE {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                (*params, bounded_limit),
            ).fetchall()
            return self._attach_ids([_chat_message_from_row(row) for row in reversed(rows)])

        sql = f"""
            SELECT id, project_id, role, text, segment_id, created_at,
                   requirement_id, ticket_id, report_kind
            FROM chat_messages
            WHERE {where}
            ORDER BY id ASC
        """
        if bounded_limit is not None:
            sql += " LIMIT ?"
            params.append(bounded_limit)
        rows = self.connection.execute(sql, params).fetchall()
        return self._attach_ids([_chat_message_from_row(row) for row in rows])

    def create_attachment(self, *, project_id: str, upload: StoredUpload) -> ChatAttachment:
        attachment = ChatAttachment(
            id=upload.id,
            project_id=project_id,
            filename=upload.filename,
            mime=upload.mime,
            size=upload.size,
            kind=upload.kind,
            stored_path=upload.stored_path,
            created_at=_now(),
        )
        self.connection.execute(
            """
            INSERT INTO chat_attachments (
                id, project_id, filename, mime, size, kind, stored_path, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attachment.id,
                attachment.project_id,
                attachment.filename,
                attachment.mime,
                attachment.size,
                attachment.kind,
                attachment.stored_path,
                attachment.created_at,
            ),
        )
        self.connection.commit()
        return attachment

    def attachments_by_ids(
        self,
        project_id: str,
        attachment_ids: list[str],
    ) -> list[ChatAttachment]:
        cleaned = _dedupe_attachment_ids(attachment_ids)
        if not cleaned:
            return []
        placeholders = ",".join("?" for _ in cleaned)
        rows = self.connection.execute(
            f"""
            SELECT id, project_id, filename, mime, size, kind, stored_path, created_at
            FROM chat_attachments
            WHERE project_id = ? AND id IN ({placeholders})
            ORDER BY created_at ASC, id ASC
            """,
            (project_id, *cleaned),
        ).fetchall()
        by_id = {_chat_attachment_from_row(row).id: _chat_attachment_from_row(row) for row in rows}
        return [by_id[item] for item in cleaned if item in by_id]

    def _attach_ids(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        if not messages:
            return messages
        ids = [message.id for message in messages]
        placeholders = ",".join("?" for _ in ids)
        rows = self.connection.execute(
            f"""
            SELECT message_id, attachment_id
            FROM chat_message_attachments
            WHERE message_id IN ({placeholders})
            ORDER BY attachment_id ASC
            """,
            ids,
        ).fetchall()
        by_message: dict[str, list[str]] = {message.id: [] for message in messages}
        for row in rows:
            by_message.setdefault(row["message_id"], []).append(row["attachment_id"])
        return [
            ChatMessage(
                id=message.id,
                project_id=message.project_id,
                role=message.role,
                text=message.text,
                segment_id=message.segment_id,
                created_at=message.created_at,
                requirement_id=message.requirement_id,
                ticket_id=message.ticket_id,
                report_kind=message.report_kind,
                attachment_ids=by_message.get(message.id, []),
            )
            for message in messages
        ]

    def active_segment_id(self, project_id: str) -> str:
        row = self.connection.execute(
            """
            SELECT id FROM chat_segments
            WHERE project_id = ? AND is_active = 1
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if row is not None:
            return row["id"]
        return self.create_segment(project_id=project_id, title="Sprint").id

    def create_segment(self, *, project_id: str, title: str) -> ChatSegment:
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("Segment title cannot be empty")
        segment = ChatSegment(
            id=_next_chat_id("CS"),
            project_id=project_id,
            title=cleaned_title,
            summary="",
            created_at=_now(),
            is_active=True,
        )
        with self.connection:
            self.connection.execute(
                "UPDATE chat_segments SET is_active = 0 WHERE project_id = ?",
                (project_id,),
            )
            self.connection.execute(
                """
                INSERT INTO chat_segments (id, project_id, title, summary, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    segment.id,
                    segment.project_id,
                    segment.title,
                    segment.summary,
                    segment.created_at,
                    1,
                ),
            )
        return segment

    def list_segments(self, project_id: str) -> list[ChatSegment]:
        rows = self.connection.execute(
            """
            SELECT id, project_id, title, summary, created_at, is_active
            FROM chat_segments
            WHERE project_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (project_id,),
        ).fetchall()
        return [_chat_segment_from_row(row) for row in rows]

    def get_summary(self, project_id: str, segment_id: str) -> str:
        row = self.connection.execute(
            """
            SELECT summary FROM chat_segments
            WHERE project_id = ? AND id = ?
            """,
            (project_id, segment_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Chat segment not found: {segment_id}")
        return row["summary"]

    def set_summary(self, project_id: str, segment_id: str, summary: str) -> None:
        cursor = self.connection.execute(
            """
            UPDATE chat_segments
            SET summary = ?
            WHERE project_id = ? AND id = ?
            """,
            (summary, project_id, segment_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Chat segment not found: {segment_id}")
        self.connection.commit()


class RunEventRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        initialize_database(connection)

    def append_run_event(
        self,
        *,
        project_id: str,
        event_type: RunEventType,
        requirement_id: str | None = None,
        ticket_id: str | None = None,
        run_id: str | None = None,
        model_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        cost_status: CostStatus | None = None,
        payload: dict | None = None,
        ts: datetime | str | None = None,
    ) -> RunEvent:
        timestamp = ts.isoformat() if isinstance(ts, datetime) else (ts or _now())
        redacted_payload = redact_json(
            payload or {},
            extra_secrets=_configured_secret_values(self.connection),
        )
        payload_json = json.dumps(redacted_payload, ensure_ascii=False, sort_keys=True)
        cursor = self.connection.execute(
            """
            INSERT INTO run_events (
                project_id, requirement_id, ticket_id, run_id, event_type, ts,
                model_id, input_tokens, output_tokens, cost_usd, cost_status, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                requirement_id,
                ticket_id,
                run_id,
                event_type,
                timestamp,
                model_id,
                input_tokens,
                output_tokens,
                cost_usd,
                cost_status,
                payload_json,
            ),
        )
        self.connection.commit()
        event = RunEvent(
            id=int(cursor.lastrowid),
            project_id=project_id,
            requirement_id=requirement_id,
            ticket_id=ticket_id,
            run_id=run_id,
            event_type=event_type,
            ts=timestamp,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            cost_status=cost_status,
            payload=redacted_payload if isinstance(redacted_payload, dict) else {},
        )
        _record_notification_from_run_event(self.connection, event)
        return event

    def list_run_events(
        self,
        project_id: str,
        *,
        after: int | None = None,
        limit: int | None = None,
        requirement_id: str | None = None,
        ticket_id: str | None = None,
    ) -> list[RunEvent]:
        bounded_limit = max(0, min(int(limit), 500)) if limit is not None else 100
        clauses = ["project_id = ?"]
        params: list[object] = [project_id]
        if after is not None:
            clauses.append("id > ?")
            params.append(after)
        if ticket_id is not None:
            clauses.append("ticket_id = ?")
            params.append(ticket_id)
        if requirement_id is not None:
            clauses.append("requirement_id = ?")
            params.append(requirement_id)
        params.append(bounded_limit)
        rows = self.connection.execute(
            f"""
            SELECT * FROM run_events
            WHERE {' AND '.join(clauses)}
            ORDER BY id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_run_event_from_row(row) for row in rows]


class NotificationRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        initialize_database(connection)

    def record_notification(
        self,
        *,
        project_id: str,
        kind: NotificationKind,
        title: str,
        dedupe_key: str,
        ticket_id: str | None = None,
        requirement_id: str | None = None,
        created_at: datetime | str | None = None,
    ) -> NotificationRecord:
        return record_notification(
            self.connection,
            project_id=project_id,
            kind=kind,
            title=title,
            dedupe_key=dedupe_key,
            ticket_id=ticket_id,
            requirement_id=requirement_id,
            created_at=created_at,
        )

    def list(
        self,
        *,
        project_id: str | None = None,
        unread_only: bool = False,
        limit: int | None = None,
    ) -> list[NotificationRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if unread_only:
            clauses.append("read_at IS NULL")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        bounded_limit = max(0, min(int(limit), 500)) if limit is not None else 100
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM notifications
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (*params, bounded_limit),
        ).fetchall()
        return [_notification_from_row(row) for row in rows]

    def get(self, notification_id: int) -> NotificationRecord | None:
        row = self.connection.execute(
            "SELECT * FROM notifications WHERE id = ?",
            (notification_id,),
        ).fetchone()
        return _notification_from_row(row) if row is not None else None

    def mark_read(self, notification_id: int) -> NotificationRecord:
        now = _now()
        cursor = self.connection.execute(
            """
            UPDATE notifications
            SET read_at = COALESCE(read_at, ?)
            WHERE id = ?
            """,
            (now, notification_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Notification not found: {notification_id}")
        self.connection.commit()
        return self.get(notification_id)  # type: ignore[return-value]

    def mark_all_read(self, *, project_id: str | None = None) -> int:
        now = _now()
        if project_id:
            cursor = self.connection.execute(
                """
                UPDATE notifications
                SET read_at = COALESCE(read_at, ?)
                WHERE project_id = ? AND read_at IS NULL
                """,
                (now, project_id),
            )
        else:
            cursor = self.connection.execute(
                """
                UPDATE notifications
                SET read_at = COALESCE(read_at, ?)
                WHERE read_at IS NULL
                """,
                (now,),
            )
        self.connection.commit()
        return int(cursor.rowcount)

    def unread_counts(self) -> dict[str, object]:
        rows = self.connection.execute(
            """
            SELECT project_id, COUNT(*) AS count
            FROM notifications
            WHERE read_at IS NULL
            GROUP BY project_id
            ORDER BY project_id ASC
            """
        ).fetchall()
        by_project = {row["project_id"]: int(row["count"]) for row in rows}
        return {"total": sum(by_project.values()), "by_project": by_project}


def record_notification(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    kind: NotificationKind,
    title: str,
    dedupe_key: str,
    ticket_id: str | None = None,
    requirement_id: str | None = None,
    created_at: datetime | str | None = None,
) -> NotificationRecord:
    _ensure_notifications_table(connection)
    timestamp = created_at.isoformat() if isinstance(created_at, datetime) else (created_at or _now())
    redacted_title = redact_text(title, extra_secrets=_configured_secret_values(connection))
    safe_dedupe_key = redact_text(dedupe_key, extra_secrets=_configured_secret_values(connection))
    connection.execute(
        """
        INSERT OR IGNORE INTO notifications (
            project_id, ticket_id, requirement_id, kind, title, created_at, dedupe_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            ticket_id,
            requirement_id,
            kind,
            redacted_title,
            timestamp,
            safe_dedupe_key,
        ),
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM notifications WHERE dedupe_key = ?",
        (safe_dedupe_key,),
    ).fetchone()
    return _notification_from_row(row)


class EvalRunRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        initialize_database(connection)

    def create(
        self,
        *,
        eval_id: str,
        model_id: str,
        task_set_id: str,
        trials: int,
        started_at: datetime | str | None = None,
    ) -> EvalRunRecord:
        _ensure_eval_runs_table(self.connection)
        timestamp = started_at.isoformat() if isinstance(started_at, datetime) else (started_at or _now())
        self.connection.execute(
            """
            INSERT INTO eval_runs (
                id, model_id, task_set_id, status, trials, started_at, summary_json
            )
            VALUES (?, ?, ?, 'running', ?, ?, '{}')
            """,
            (eval_id, model_id, task_set_id, max(1, int(trials)), timestamp),
        )
        self.connection.commit()
        record = self.get(eval_id)
        if record is None:
            raise KeyError(f"Eval run not found after create: {eval_id}")
        return record

    def get(self, eval_id: str) -> EvalRunRecord | None:
        row = self.connection.execute(
            "SELECT * FROM eval_runs WHERE id = ?",
            (eval_id,),
        ).fetchone()
        return _eval_run_from_row(row) if row is not None else None

    def list(
        self,
        *,
        model_id: str | None = None,
        task_set_id: str | None = None,
        limit: int | None = None,
    ) -> list[EvalRunRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if model_id:
            clauses.append("model_id = ?")
            params.append(model_id)
        if task_set_id:
            clauses.append("task_set_id = ?")
            params.append(task_set_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        bounded_limit = max(0, min(int(limit), 500)) if limit is not None else 100
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM eval_runs
            {where}
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (*params, bounded_limit),
        ).fetchall()
        return [_eval_run_from_row(row) for row in rows]

    def latest_completed_before(
        self,
        *,
        model_id: str,
        task_set_id: str,
        started_before: str,
        exclude_id: str | None = None,
    ) -> EvalRunRecord | None:
        clauses = [
            "model_id = ?",
            "task_set_id = ?",
            "status = 'completed'",
            "started_at < ?",
        ]
        params: list[object] = [model_id, task_set_id, started_before]
        if exclude_id:
            clauses.append("id != ?")
            params.append(exclude_id)
        row = self.connection.execute(
            f"""
            SELECT *
            FROM eval_runs
            WHERE {' AND '.join(clauses)}
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return _eval_run_from_row(row) if row is not None else None

    def complete(
        self,
        eval_id: str,
        *,
        summary: dict,
        baseline_run_id: str | None,
        regressed: bool,
        finished_at: datetime | str | None = None,
    ) -> EvalRunRecord:
        timestamp = finished_at.isoformat() if isinstance(finished_at, datetime) else (finished_at or _now())
        redacted_summary = redact_json(summary, extra_secrets=_configured_secret_values(self.connection))
        cursor = self.connection.execute(
            """
            UPDATE eval_runs
            SET status = 'completed',
                finished_at = ?,
                summary_json = ?,
                baseline_run_id = ?,
                regressed = ?,
                error = ''
            WHERE id = ?
            """,
            (
                timestamp,
                json.dumps(redacted_summary, ensure_ascii=False, sort_keys=True),
                baseline_run_id,
                int(regressed),
                eval_id,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Eval run not found: {eval_id}")
        self.connection.commit()
        return self.get(eval_id)  # type: ignore[return-value]

    def fail(
        self,
        eval_id: str,
        *,
        error: str,
        summary: dict | None = None,
        finished_at: datetime | str | None = None,
    ) -> EvalRunRecord:
        timestamp = finished_at.isoformat() if isinstance(finished_at, datetime) else (finished_at or _now())
        redacted_summary = redact_json(summary or {}, extra_secrets=_configured_secret_values(self.connection))
        redacted_error = redact_text(error, extra_secrets=_configured_secret_values(self.connection))
        cursor = self.connection.execute(
            """
            UPDATE eval_runs
            SET status = 'failed',
                finished_at = ?,
                summary_json = ?,
                error = ?
            WHERE id = ?
            """,
            (
                timestamp,
                json.dumps(redacted_summary, ensure_ascii=False, sort_keys=True),
                redacted_error,
                eval_id,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Eval run not found: {eval_id}")
        self.connection.commit()
        return self.get(eval_id)  # type: ignore[return-value]


class RequirementTemplateRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        initialize_database(connection)

    def list(self) -> list[RequirementTemplateRecord]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM requirement_templates
            ORDER BY updated_at DESC, id ASC
            """
        ).fetchall()
        custom = [_requirement_template_from_row(row) for row in rows]
        custom_ids = {template.id for template in custom}
        built_ins = [
            template
            for template in BUILT_IN_REQUIREMENT_TEMPLATES
            if template.id not in custom_ids
        ]
        return [*built_ins, *custom]

    def get(self, template_id: str) -> RequirementTemplateRecord | None:
        for template in BUILT_IN_REQUIREMENT_TEMPLATES:
            if template.id == template_id:
                return template
        row = self.connection.execute(
            "SELECT * FROM requirement_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        return _requirement_template_from_row(row) if row is not None else None

    def upsert(
        self,
        *,
        template_id: str | None,
        title: str,
        prompt: str,
        scope_paths: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> RequirementTemplateRecord:
        cleaned_id = (template_id or "").strip() or self.next_id()
        if _is_builtin_requirement_template(cleaned_id):
            raise ValueError(f"Built-in requirement template cannot be overwritten: {cleaned_id}")
        cleaned_title = title.strip()
        cleaned_prompt = prompt.strip()
        if not cleaned_title:
            raise ValueError("Template title cannot be empty")
        if not cleaned_prompt:
            raise ValueError("Template prompt cannot be empty")
        now = _now()
        existing = self.get(cleaned_id)
        created_at = existing.created_at if existing and not existing.built_in else now
        self.connection.execute(
            """
            INSERT INTO requirement_templates (
                id, title, prompt, scope_paths_json, constraints_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                prompt = excluded.prompt,
                scope_paths_json = excluded.scope_paths_json,
                constraints_json = excluded.constraints_json,
                updated_at = excluded.updated_at
            """,
            (
                cleaned_id,
                cleaned_title,
                cleaned_prompt,
                json.dumps(_clean_string_list(scope_paths), ensure_ascii=False),
                json.dumps(_clean_string_list(constraints), ensure_ascii=False),
                created_at,
                now,
            ),
        )
        self.connection.commit()
        template = self.get(cleaned_id)
        if template is None:
            raise KeyError(f"Requirement template not found after save: {cleaned_id}")
        return template

    def delete(self, template_id: str) -> None:
        if _is_builtin_requirement_template(template_id):
            raise ValueError(f"Built-in requirement template cannot be deleted: {template_id}")
        cursor = self.connection.execute(
            "DELETE FROM requirement_templates WHERE id = ?",
            (template_id,),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Requirement template not found: {template_id}")
        self.connection.commit()

    def next_id(self) -> str:
        rows = self.connection.execute("SELECT id FROM requirement_templates").fetchall()
        max_number = 0
        for row in rows:
            match = re.fullmatch(r"tmpl-(\d+)", row["id"])
            if match:
                max_number = max(max_number, int(match.group(1)))
        return f"tmpl-{max_number + 1:03d}"


class IntegrationRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        initialize_database(connection)

    def list(self, provider: IntegrationProvider | None = None) -> list[IntegrationCredential]:
        if provider is None:
            rows = self.connection.execute(
                """
                SELECT provider, id, label, encrypted_token, scopes_json, created_at, updated_at
                FROM integrations
                ORDER BY provider ASC, label ASC, id ASC
                """
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT provider, id, label, encrypted_token, scopes_json, created_at, updated_at
                FROM integrations
                WHERE provider = ?
                ORDER BY label ASC, id ASC
                """,
                (provider,),
            ).fetchall()
        return [_integration_from_row(row) for row in rows]

    def upsert(
        self,
        *,
        provider: IntegrationProvider,
        token: str,
        scopes: list[str] | None = None,
        label: str = "",
        credential_id: str | None = None,
    ) -> IntegrationCredential:
        cleaned_token = token.strip()
        if not cleaned_token:
            raise SecretEncryptionError("token cannot be empty")
        now = _now()
        entry_id = credential_id or f"{provider}-{uuid.uuid4().hex[:12]}"
        cleaned_scopes = [scope.strip() for scope in (scopes or []) if scope.strip()]
        self.connection.execute(
            """
            INSERT INTO integrations (
                provider, id, label, encrypted_token, scopes_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, id) DO UPDATE SET
                label = excluded.label,
                encrypted_token = excluded.encrypted_token,
                scopes_json = excluded.scopes_json,
                updated_at = excluded.updated_at
            """,
            (
                provider,
                entry_id,
                label.strip() or provider,
                encrypt_secret(cleaned_token),
                json.dumps(cleaned_scopes, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        self.connection.commit()
        return self.get(provider, entry_id)  # type: ignore[return-value]

    def get(self, provider: IntegrationProvider, credential_id: str) -> IntegrationCredential | None:
        row = self.connection.execute(
            """
            SELECT provider, id, label, encrypted_token, scopes_json, created_at, updated_at
            FROM integrations
            WHERE provider = ? AND id = ?
            """,
            (provider, credential_id),
        ).fetchone()
        return _integration_from_row(row) if row is not None else None

    def delete(self, provider: IntegrationProvider, credential_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM integrations WHERE provider = ? AND id = ?",
            (provider, credential_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def decrypted_token(self, provider: IntegrationProvider, credential_id: str) -> str:
        row = self.connection.execute(
            "SELECT encrypted_token FROM integrations WHERE provider = ? AND id = ?",
            (provider, credential_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Integration credential not found: {provider}/{credential_id}")
        return decrypt_secret(row["encrypted_token"])


class SettingsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        initialize_database(connection)

    def get_json(self, key: str, default: object | None = None) -> object | None:
        row = self.connection.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return default
        return json.loads(row["value_json"])

    def set_json(self, key: str, value: object) -> None:
        now = _now()
        self.connection.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True), now),
        )
        self.connection.commit()

    def get_role_routing(self, default: dict) -> dict:
        stored = self.get_json(ROLE_ROUTING_SETTINGS_KEY)
        if not isinstance(stored, dict):
            return dict(default)
        merged = dict(default)
        for role, model in stored.items():
            if not isinstance(role, str) or not role.strip():
                continue
            if isinstance(model, str) and model.strip():
                merged[role] = model
            elif isinstance(model, list):
                models = [item.strip() for item in model if isinstance(item, str) and item.strip()]
                if models:
                    merged[role] = models
        return merged

    def set_role_routing(self, routing: dict) -> dict:
        self.set_json(ROLE_ROUTING_SETTINGS_KEY, routing)
        return dict(routing)

    def get_claude_model(self, default: str) -> str:
        stored = self.get_json(CLAUDE_MODEL_SETTINGS_KEY)
        return stored if isinstance(stored, str) and stored.strip() else default

    def set_claude_model(self, model: str) -> str:
        cleaned = model.strip()
        if not cleaned:
            raise ValueError("Claude model cannot be empty")
        self.set_json(CLAUDE_MODEL_SETTINGS_KEY, cleaned)
        return cleaned

    def get_cloud_reasoner(self, default: str = "") -> str:
        stored = self.get_json(CLOUD_REASONER_SETTINGS_KEY)
        return stored if isinstance(stored, str) and stored.strip() else default

    def set_cloud_reasoner(self, model_id: str) -> str:
        cleaned = model_id.strip()
        if not cleaned:
            raise ValueError("Cloud reasoner id cannot be empty")
        self.set_json(CLOUD_REASONER_SETTINGS_KEY, cleaned)
        return cleaned

    def get_model_addon(self, model_id: str) -> str:
        from orchestrator.model_instructions import normalize_model_settings_id

        key = normalize_model_settings_id(model_id)
        stored = self.get_json(MODEL_ADDITIONAL_INSTRUCTIONS_KEY, default={})
        if not isinstance(stored, dict):
            return ""
        value = stored.get(key, "")
        return value if isinstance(value, str) else ""

    def set_model_addon(self, model_id: str, text: str) -> str:
        from orchestrator.model_instructions import normalize_model_settings_id

        key = normalize_model_settings_id(model_id)
        stored = self.get_json(MODEL_ADDITIONAL_INSTRUCTIONS_KEY, default={})
        mapping = dict(stored) if isinstance(stored, dict) else {}
        cleaned = (text or "").strip()
        if cleaned:
            mapping[key] = cleaned
        else:
            mapping.pop(key, None)
        self.set_json(MODEL_ADDITIONAL_INSTRUCTIONS_KEY, mapping)
        return cleaned


class RequirementRepository:
    def __init__(self, connection: sqlite3.Connection, project_id: str | None = None):
        self.connection = connection
        self.project_id = project_id
        initialize_database(connection)

    def scoped(self, project_id: str | None) -> RequirementRepository:
        return RequirementRepository(self.connection, project_id=project_id)

    def next_id(self, project_id: str | None = None) -> str:
        effective_project_id = self._effective_project_id(project_id)
        rows = self.connection.execute(
            "SELECT id FROM requirements WHERE project_id = ?",
            (effective_project_id,),
        ).fetchall()
        max_number = 0
        for row in rows:
            match = re.fullmatch(r"R-(\d+)", row["id"])
            if match:
                max_number = max(max_number, int(match.group(1)))
        return f"R-{max_number + 1:03d}"

    def create(
        self,
        requirement: Requirement,
        project_id: str | None = None,
    ) -> Requirement:
        now = _now()
        requirement_json = requirement.to_dict()
        effective_project_id = self._effective_project_id(
            project_id,
            requirement_json.get("project_id"),
        )
        requirement_json["project_id"] = effective_project_id
        requirement_json.setdefault("created_at", now)
        requirement_json["updated_at"] = now
        stored = Requirement.model_validate(requirement_json)

        self.connection.execute(
            """
            INSERT INTO requirements (id, project_id, status, requirement_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                stored.id,
                effective_project_id,
                stored.status,
                _requirement_dumps(stored),
                requirement_json["created_at"],
                requirement_json["updated_at"],
            ),
        )
        self.connection.commit()
        return stored

    def get(
        self,
        requirement_id: str,
        project_id: str | None = None,
    ) -> Requirement | None:
        effective_project_id = self._optional_project_id(project_id)
        if effective_project_id is None:
            rows = self.connection.execute(
                "SELECT requirement_json FROM requirements WHERE id = ?",
                (requirement_id,),
            ).fetchall()
            if len(rows) > 1:
                raise ValueError(
                    f"Requirement id {requirement_id} exists in multiple projects; pass project_id"
                )
            row = rows[0] if rows else None
        else:
            row = self.connection.execute(
                """
                SELECT requirement_json FROM requirements
                WHERE id = ? AND project_id = ?
                """,
                (requirement_id, effective_project_id),
            ).fetchone()
        if row is None:
            return None
        return Requirement.model_validate(json.loads(row["requirement_json"]))

    def list(
        self,
        status: RequirementStatus | str | None = None,
        project_id: str | None = None,
    ) -> list[Requirement]:
        effective_project_id = self._optional_project_id(project_id)
        if status is None and effective_project_id is None:
            rows = self.connection.execute(
                "SELECT requirement_json FROM requirements ORDER BY created_at ASC, project_id ASC, id ASC"
            ).fetchall()
        elif status is None:
            rows = self.connection.execute(
                """
                SELECT requirement_json FROM requirements
                WHERE project_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (effective_project_id,),
            ).fetchall()
        elif effective_project_id is None:
            rows = self.connection.execute(
                """
                SELECT requirement_json FROM requirements
                WHERE status = ?
                ORDER BY created_at ASC, project_id ASC, id ASC
                """,
                (str(status),),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT requirement_json FROM requirements
                WHERE status = ? AND project_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (str(status), effective_project_id),
            ).fetchall()
        return [
            Requirement.model_validate(json.loads(row["requirement_json"]))
            for row in rows
        ]

    def save(
        self,
        requirement: Requirement,
        project_id: str | None = None,
    ) -> Requirement:
        requirement_json = requirement.to_dict()
        effective_project_id = self._effective_project_id(
            project_id,
            requirement_json.get("project_id"),
        )
        requirement_json["project_id"] = effective_project_id
        requirement_json["updated_at"] = _now()
        updated = Requirement.model_validate(requirement_json)

        cursor = self.connection.execute(
            """
            UPDATE requirements
            SET status = ?, requirement_json = ?, updated_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                updated.status,
                _requirement_dumps(updated),
                requirement_json["updated_at"],
                updated.id,
                effective_project_id,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Requirement not found: {updated.id}")
        self.connection.commit()
        return updated

    def _optional_project_id(self, project_id: str | None = None) -> str | None:
        return project_id or self.project_id

    def _effective_project_id(
        self,
        project_id: str | None = None,
        stored_project_id: object | None = None,
    ) -> str:
        candidate = project_id or self.project_id
        if candidate:
            return candidate
        if isinstance(stored_project_id, str) and stored_project_id:
            return stored_project_id
        return "default"


def _dumps(ticket: Ticket) -> str:
    return json.dumps(ticket.to_dict(), ensure_ascii=False, sort_keys=True)


def _requirement_dumps(requirement: Requirement) -> str:
    return json.dumps(requirement.to_dict(), ensure_ascii=False, sort_keys=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _next_chat_id(prefix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def _bounded_chat_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    return max(0, min(int(limit), 500))


def _clean_string_list(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        item = str(value).strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def _loads_string_list(raw: str) -> list[str]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return _clean_string_list([str(item) for item in payload])


def _is_builtin_requirement_template(template_id: str) -> bool:
    return any(template.id == template_id for template in BUILT_IN_REQUIREMENT_TEMPLATES)


def _chat_message_from_row(row: sqlite3.Row) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        project_id=row["project_id"],
        role=row["role"],
        text=row["text"],
        segment_id=row["segment_id"],
        created_at=row["created_at"],
        requirement_id=row["requirement_id"],
        ticket_id=row["ticket_id"],
        report_kind=row["report_kind"],
    )


def _chat_attachment_from_row(row: sqlite3.Row) -> ChatAttachment:
    return ChatAttachment(
        id=row["id"],
        project_id=row["project_id"],
        filename=row["filename"],
        mime=row["mime"],
        size=int(row["size"]),
        kind=row["kind"],
        stored_path=row["stored_path"],
        created_at=row["created_at"],
    )


def _run_event_from_row(row: sqlite3.Row) -> RunEvent:
    payload = {}
    if row["payload_json"]:
        try:
            decoded = json.loads(row["payload_json"])
            if isinstance(decoded, dict):
                payload = decoded
        except json.JSONDecodeError:
            payload = {}
    return RunEvent(
        id=int(row["id"]),
        project_id=row["project_id"],
        requirement_id=row["requirement_id"],
        ticket_id=row["ticket_id"],
        run_id=row["run_id"],
        event_type=row["event_type"],
        ts=row["ts"],
        model_id=row["model_id"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_usd"],
        cost_status=row["cost_status"],
        payload=payload,
    )


def _notification_from_row(row: sqlite3.Row) -> NotificationRecord:
    return NotificationRecord(
        id=int(row["id"]),
        project_id=row["project_id"],
        ticket_id=row["ticket_id"],
        requirement_id=row["requirement_id"],
        kind=row["kind"],
        title=row["title"],
        created_at=row["created_at"],
        read_at=row["read_at"],
        dedupe_key=row["dedupe_key"],
    )


def _eval_run_from_row(row: sqlite3.Row) -> EvalRunRecord:
    try:
        summary = json.loads(row["summary_json"] or "{}")
    except json.JSONDecodeError:
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    return EvalRunRecord(
        id=row["id"],
        model_id=row["model_id"],
        task_set_id=row["task_set_id"],
        status=row["status"],
        trials=int(row["trials"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        summary=summary,
        baseline_run_id=row["baseline_run_id"],
        regressed=bool(row["regressed"]),
        error=row["error"] or "",
    )


def _requirement_template_from_row(row: sqlite3.Row) -> RequirementTemplateRecord:
    return RequirementTemplateRecord(
        id=row["id"],
        title=row["title"],
        prompt=row["prompt"],
        scope_paths=_loads_string_list(row["scope_paths_json"]),
        constraints=_loads_string_list(row["constraints_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        built_in=False,
    )


def _record_notification_from_chat_report(
    connection: sqlite3.Connection,
    message: ChatMessage,
) -> None:
    kind = _notification_kind(message.report_kind)
    if kind is None:
        return
    cause = _chat_report_cause(message.text, kind)
    record_notification(
        connection,
        project_id=message.project_id,
        requirement_id=message.requirement_id,
        ticket_id=message.ticket_id,
        kind=kind,
        title=message.text,
        created_at=message.created_at,
        dedupe_key=_notification_dedupe_key(
            project_id=message.project_id,
            kind=kind,
            ticket_id=message.ticket_id,
            requirement_id=message.requirement_id,
            cause=cause,
        ),
    )


def _record_notification_from_run_event(
    connection: sqlite3.Connection,
    event: RunEvent,
) -> None:
    if event.event_type not in {"report", "error"}:
        return
    payload = event.payload or {}
    if event.event_type == "error":
        kind: NotificationKind = "needs_you"
        cause = "error:" + str(payload.get("stage") or payload.get("reason") or event.run_id or "unknown")
    else:
        kind = _notification_kind(payload.get("report_kind"))
        if kind is None:
            kind = "done" if payload.get("stage") == "pr" else "needs_you"
        cause = str(
            payload.get("reason")
            or payload.get("stage")
            or payload.get("state")
            or payload.get("status")
            or event.event_type
        )
    title = _run_event_notification_title(connection, event, kind, cause)
    record_notification(
        connection,
        project_id=event.project_id,
        requirement_id=event.requirement_id,
        ticket_id=event.ticket_id,
        kind=kind,
        title=title,
        created_at=event.ts,
        dedupe_key=_notification_dedupe_key(
            project_id=event.project_id,
            kind=kind,
            ticket_id=event.ticket_id,
            requirement_id=event.requirement_id,
            cause=cause,
        ),
    )


def _notification_kind(value: object) -> NotificationKind | None:
    if value in {"needs_you", "done", "blocked"}:
        return value  # type: ignore[return-value]
    return None


def _chat_report_cause(text: str, kind: NotificationKind) -> str:
    if kind == "blocked" and "needs a decision:" in text:
        return text.split("needs a decision:", 1)[1].strip()
    if kind == "needs_you" and "needs you -" in text:
        tail = text.split("needs you -", 1)[1].strip()
        return tail.split(":", 1)[0].strip()
    if kind == "done" and "done - in " in text:
        tail = text.split("done - in ", 1)[1].strip()
        return tail.split(":", 1)[0].strip().lower()
    return kind


def _notification_dedupe_key(
    *,
    project_id: str,
    kind: NotificationKind,
    ticket_id: str | None,
    requirement_id: str | None,
    cause: str,
) -> str:
    entity = ticket_id or requirement_id or "project"
    normalized_cause = redact_text(cause).strip().lower() or kind
    cause_hash = hashlib.sha256(normalized_cause.encode("utf-8")).hexdigest()[:16]
    return f"{project_id}:{kind}:{entity}:{cause_hash}"


def _run_event_notification_title(
    connection: sqlite3.Connection,
    event: RunEvent,
    kind: NotificationKind,
    cause: str,
) -> str:
    ticket_title = _ticket_title(connection, event.project_id, event.ticket_id)
    label = event.ticket_id or event.requirement_id or event.project_id
    if kind == "done":
        return f"{label} done: {ticket_title or cause}"
    if kind == "blocked":
        return f"{label} blocked: {ticket_title or cause}"
    return f"{label} needs you: {ticket_title or cause}"


def _ticket_title(
    connection: sqlite3.Connection,
    project_id: str,
    ticket_id: str | None,
) -> str:
    if not ticket_id:
        return ""
    row = connection.execute(
        """
        SELECT ticket_json
        FROM tickets
        WHERE project_id = ? AND id = ?
        """,
        (project_id, ticket_id),
    ).fetchone()
    if row is None:
        return ""
    try:
        ticket_json = json.loads(row["ticket_json"])
    except json.JSONDecodeError:
        return ""
    title = ticket_json.get("title") if isinstance(ticket_json, dict) else ""
    return title if isinstance(title, str) else ""


def _integration_from_row(row: sqlite3.Row) -> IntegrationCredential:
    try:
        scopes = json.loads(row["scopes_json"])
    except json.JSONDecodeError:
        scopes = []
    if not isinstance(scopes, list):
        scopes = []
    return IntegrationCredential(
        provider=row["provider"],
        id=row["id"],
        label=row["label"],
        scopes=[str(scope) for scope in scopes],
        configured=bool(row["encrypted_token"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _configured_secret_values(connection: sqlite3.Connection) -> list[str]:
    return list(current_known_secrets(get_settings(), SettingsRepository(connection)))


def _chat_segment_from_row(row: sqlite3.Row) -> ChatSegment:
    return ChatSegment(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        summary=row["summary"],
        created_at=row["created_at"],
        is_active=bool(row["is_active"]),
    )


def _dedupe_attachment_ids(attachment_ids: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for attachment_id in attachment_ids or []:
        if not isinstance(attachment_id, str):
            continue
        stripped = attachment_id.strip()
        if stripped and stripped not in seen:
            cleaned.append(stripped)
            seen.add(stripped)
    return cleaned


def _is_duplicate_ticket_error(exc: sqlite3.IntegrityError) -> bool:
    message = str(exc).lower()
    return "unique constraint failed" in message and "tickets.id" in message


def _ticket_project_id(ticket: Ticket) -> str:
    if ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        project_id = metadata.get("project_id")
        if isinstance(project_id, str) and project_id:
            return project_id
    return "default"


def _project_from_row(row: sqlite3.Row) -> Project:
    data = dict(row)
    env_json = data.pop("env_json", "{}")
    env_allowlist_json = data.pop("env_allowlist_json", '["PATH", "PYTHONPATH"]')
    try:
        env = json.loads(env_json)
    except json.JSONDecodeError:
        env = {}
    try:
        env_allowlist = json.loads(env_allowlist_json)
    except json.JSONDecodeError:
        env_allowlist = ["PATH", "PYTHONPATH"]
    data["env"] = env if isinstance(env, dict) else {}
    data["env_allowlist"] = env_allowlist if isinstance(env_allowlist, list) else ["PATH", "PYTHONPATH"]
    data["test_allow_network"] = bool(data.get("test_allow_network", 0))
    return Project.model_validate(data)
