from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.db.migrations import (
    CLAUDE_MODEL_SETTINGS_KEY,
    CLOUD_REASONER_SETTINGS_KEY,
    MODEL_ADDITIONAL_INSTRUCTIONS_KEY,
    ROLE_ROUTING_SETTINGS_KEY,
    ensure_schema_compatibility,
    run_migrations,
)
from orchestrator.models.project import Project, validate_project_path
from orchestrator.models.requirement import Requirement, RequirementStatus
from orchestrator.models.ticket import Result, ResultLog, Ticket, TicketStatus


class DuplicateTicketError(ValueError):
    """Raised when a ticket id already exists in persistent storage."""


class TicketDeletionError(ValueError):
    """Raised when a ticket cannot be safely deleted."""


class AmbiguousTicketError(ValueError):
    """Raised when an unscoped ticket id exists in multiple projects."""


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
            INSERT INTO projects (id, name, path, default_branch, env_json, setup_cmd, cleanup_cmd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                project.name,
                project.path,
                project.default_branch,
                json.dumps(project.env, ensure_ascii=False, sort_keys=True),
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
            SELECT id, name, path, default_branch, env_json, setup_cmd, cleanup_cmd, created_at
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
            SELECT id, name, path, default_branch, env_json, setup_cmd, cleanup_cmd, created_at
            FROM projects
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        return [_project_from_row(row) for row in rows]

    def save(self, project: Project) -> Project:
        cursor = self.connection.execute(
            """
            UPDATE projects
            SET name = ?, path = ?, default_branch = ?, env_json = ?, setup_cmd = ?, cleanup_cmd = ?
            WHERE id = ?
            """,
            (
                project.name,
                project.path,
                project.default_branch,
                json.dumps(project.env, ensure_ascii=False, sort_keys=True),
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
        log = ResultLog(ts=timestamp, level=level, message=message)

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
            (ticket_id, timestamp, level, message),
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
    try:
        env = json.loads(env_json)
    except json.JSONDecodeError:
        env = {}
    data["env"] = env if isinstance(env, dict) else {}
    return Project.model_validate(data)
