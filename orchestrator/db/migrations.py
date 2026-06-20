from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

Migration = Callable[[sqlite3.Connection], None]

ROLE_ROUTING_SETTINGS_KEY = "role_routing"
MODEL_ADDITIONAL_INSTRUCTIONS_KEY = "model_additional_instructions"
CLAUDE_MODEL_SETTINGS_KEY = "claude_model"
CLOUD_REASONER_SETTINGS_KEY = "cloud_reasoner"
SCHEMA_VERSION_KEY = "schema_version"


def run_migrations(connection: sqlite3.Connection) -> None:
    current_version = _get_schema_version(connection)
    for index, migration in enumerate(MIGRATIONS, start=1):
        if index <= current_version:
            continue
        migration(connection)
        _set_schema_version(connection, index)
    connection.commit()


def ensure_schema_compatibility(connection: sqlite3.Connection) -> None:
    _ensure_projects_table(connection)
    connection.commit()


def migrate_escalate_to_tech_lead(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT id, ticket_json FROM tickets").fetchall()
    for row in rows:
        ticket_json = json.loads(row["ticket_json"])
        changed = False

        execution = ticket_json.get("execution", {})
        if execution.get("escalate_to") == "cloud_po":
            execution["escalate_to"] = "tech_lead"
            changed = True

        metadata = ticket_json.get("metadata")
        if isinstance(metadata, dict) and metadata.get("escalated_to") == "cloud_po":
            metadata["escalated_to"] = "tech_lead"
            changed = True

        if changed:
            connection.execute(
                "UPDATE tickets SET ticket_json = ? WHERE id = ?",
                (json.dumps(ticket_json, ensure_ascii=False, sort_keys=True), row["id"]),
            )


def migrate_project_scope(connection: sqlite3.Connection) -> None:
    _ensure_projects_table(connection)
    _ensure_default_project(connection)
    _rebuild_tickets_for_project_scope(connection)
    _rebuild_ticket_logs_for_project_scope(connection)
    _rebuild_requirements_for_project_scope(connection)
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_tickets_project_status ON tickets(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_ticket_logs_project_ticket_id ON ticket_logs(project_id, ticket_id);
        CREATE INDEX IF NOT EXISTS idx_requirements_project_status ON requirements(project_id, status);
        """
    )


def _ensure_projects_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            default_branch TEXT NOT NULL,
            env_json TEXT NOT NULL DEFAULT '{}',
            setup_cmd TEXT NOT NULL DEFAULT '',
            cleanup_cmd TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    columns = _column_names(connection, "projects")
    if "env_json" not in columns:
        connection.execute("ALTER TABLE projects ADD COLUMN env_json TEXT NOT NULL DEFAULT '{}'")
    if "setup_cmd" not in columns:
        connection.execute("ALTER TABLE projects ADD COLUMN setup_cmd TEXT NOT NULL DEFAULT ''")
    if "cleanup_cmd" not in columns:
        connection.execute("ALTER TABLE projects ADD COLUMN cleanup_cmd TEXT NOT NULL DEFAULT ''")


def _ensure_default_project(connection: sqlite3.Connection) -> None:
    now = datetime.now(UTC).isoformat()
    project_root = Path(__file__).resolve().parents[2]
    connection.execute(
        """
        INSERT OR IGNORE INTO projects (id, name, path, default_branch, env_json, setup_cmd, cleanup_cmd, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("default", "HAAO", str(project_root), "main", "{}", "", "", now),
    )


def _rebuild_tickets_for_project_scope(connection: sqlite3.Connection) -> None:
    columns = _column_names(connection, "tickets")
    if not columns:
        return
    if _has_composite_primary_key(connection, "tickets", ["project_id", "id"]):
        return

    connection.execute("ALTER TABLE tickets RENAME TO tickets_old")
    connection.execute(
        """
        CREATE TABLE tickets (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL,
            ticket_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, id)
        )
        """
    )

    old_columns = _column_names(connection, "tickets_old")
    project_expr = "project_id" if "project_id" in old_columns else "'default'"
    rows = connection.execute(
        f"SELECT id, {project_expr} AS project_id, status, ticket_json, created_at, updated_at FROM tickets_old"
    ).fetchall()
    for row in rows:
        ticket_json = json.loads(row["ticket_json"])
        metadata = ticket_json.setdefault("metadata", {})
        metadata.setdefault("project_id", row["project_id"] or "default")
        connection.execute(
            """
            INSERT INTO tickets (id, project_id, status, ticket_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["project_id"] or "default",
                row["status"],
                json.dumps(ticket_json, ensure_ascii=False, sort_keys=True),
                row["created_at"],
                row["updated_at"],
            ),
        )
    connection.execute("DROP TABLE tickets_old")


def _rebuild_ticket_logs_for_project_scope(connection: sqlite3.Connection) -> None:
    columns = _column_names(connection, "ticket_logs")
    if not columns:
        return
    if "project_id" in columns:
        return

    connection.execute("ALTER TABLE ticket_logs RENAME TO ticket_logs_old")
    connection.execute(
        """
        CREATE TABLE ticket_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO ticket_logs (id, ticket_id, project_id, ts, level, message)
        SELECT id, ticket_id, 'default', ts, level, message FROM ticket_logs_old
        """
    )
    connection.execute("DROP TABLE ticket_logs_old")


def _rebuild_requirements_for_project_scope(connection: sqlite3.Connection) -> None:
    columns = _column_names(connection, "requirements")
    if not columns:
        return
    if _has_composite_primary_key(connection, "requirements", ["project_id", "id"]):
        return

    connection.execute("ALTER TABLE requirements RENAME TO requirements_old")
    connection.execute(
        """
        CREATE TABLE requirements (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL,
            requirement_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, id)
        )
        """
    )

    old_columns = _column_names(connection, "requirements_old")
    project_expr = "project_id" if "project_id" in old_columns else "'default'"
    rows = connection.execute(
        f"SELECT id, {project_expr} AS project_id, status, requirement_json, created_at, updated_at FROM requirements_old"
    ).fetchall()
    for row in rows:
        requirement_json = json.loads(row["requirement_json"])
        requirement_json.setdefault("project_id", row["project_id"] or "default")
        connection.execute(
            """
            INSERT INTO requirements (id, project_id, status, requirement_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["project_id"] or "default",
                row["status"],
                json.dumps(requirement_json, ensure_ascii=False, sort_keys=True),
                row["created_at"],
                row["updated_at"],
            ),
        )
    connection.execute("DROP TABLE requirements_old")


def _column_names(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in rows]


def _has_composite_primary_key(
    connection: sqlite3.Connection,
    table: str,
    expected_columns: list[str],
) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    primary_key = sorted(
        ((row["pk"], row["name"]) for row in rows if row["pk"]),
        key=lambda item: item[0],
    )
    return [name for _, name in primary_key] == expected_columns


MIGRATIONS: list[Migration] = [
    migrate_escalate_to_tech_lead,
    migrate_project_scope,
]


def _get_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value_json FROM app_settings WHERE key = ?",
        (SCHEMA_VERSION_KEY,),
    ).fetchone()
    if row is None:
        return 0
    return int(json.loads(row["value_json"]))


def _set_schema_version(connection: sqlite3.Connection, version: int) -> None:
    now = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT INTO app_settings (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = excluded.updated_at
        """,
        (SCHEMA_VERSION_KEY, json.dumps(version), now),
    )
