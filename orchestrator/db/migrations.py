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
    _ensure_foundational_contract_tables(connection)
    _ensure_prompt_versions_table(connection)
    _ensure_team_plane_tables(connection)
    _ensure_notifications_table(connection)
    _ensure_eval_runs_table(connection)
    _ensure_requirement_templates_table(connection)
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


def migrate_chat_tables(connection: sqlite3.Connection) -> None:
    _ensure_chat_tables(connection)


def migrate_foundational_contract_tables(connection: sqlite3.Connection) -> None:
    _ensure_foundational_contract_tables(connection)


def migrate_notifications_table(connection: sqlite3.Connection) -> None:
    _ensure_notifications_table(connection)


def migrate_project_execution_policy(connection: sqlite3.Connection) -> None:
    _ensure_projects_table(connection)


def migrate_eval_runs_table(connection: sqlite3.Connection) -> None:
    _ensure_eval_runs_table(connection)


def migrate_requirement_templates_table(connection: sqlite3.Connection) -> None:
    _ensure_requirement_templates_table(connection)


def migrate_wave5_trust_tables(connection: sqlite3.Connection) -> None:
    _ensure_foundational_contract_tables(connection)
    _ensure_prompt_versions_table(connection)


def migrate_wave6_team_plane_tables(connection: sqlite3.Connection) -> None:
    _ensure_team_plane_tables(connection)


def migrate_wave9_enterprise_gate_tables(connection: sqlite3.Connection) -> None:
    _ensure_team_plane_tables(connection)
    _ensure_wave9_workspace_columns(connection)


def _ensure_chat_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_segments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_segments_one_active
        ON chat_segments(project_id)
        WHERE is_active = 1;

        CREATE INDEX IF NOT EXISTS idx_chat_segments_project_created
        ON chat_segments(project_id, created_at, id);

        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'agent', 'system_report')),
            text TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            requirement_id TEXT NULL,
            ticket_id TEXT NULL,
            report_kind TEXT NULL CHECK (report_kind IS NULL OR report_kind IN ('done', 'blocked', 'needs_you'))
        );

        CREATE INDEX IF NOT EXISTS idx_chat_messages_project_segment_id
        ON chat_messages(project_id, segment_id, id);

        CREATE TABLE IF NOT EXISTS chat_attachments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime TEXT NOT NULL,
            size INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('file', 'image')),
            stored_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chat_attachments_project_id
        ON chat_attachments(project_id, id);

        CREATE TABLE IF NOT EXISTS chat_message_attachments (
            message_id TEXT NOT NULL,
            attachment_id TEXT NOT NULL,
            PRIMARY KEY (message_id, attachment_id)
        );
        """
    )


def _ensure_foundational_contract_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            requirement_id TEXT NULL,
            ticket_id TEXT NULL,
            run_id TEXT NULL,
            event_type TEXT NOT NULL CHECK (
                event_type IN (
                    'run_started',
                    'model_call',
                    'diff_produced',
                    'dod_check',
                    'retry',
                    'escalation',
                    'egress_attempt',
                    'attachment_egress',
                    'diff_scope_reject',
                    'rollback',
                    'conflict',
                    'report',
                    'run_finished',
                    'error'
                )
            ),
            ts TEXT NOT NULL,
            model_id TEXT NULL,
            input_tokens INTEGER NULL,
            output_tokens INTEGER NULL,
            cost_usd REAL NULL,
            cost_status TEXT NULL CHECK (
                cost_status IS NULL OR cost_status IN ('actual', 'estimated', 'unknown')
            ),
            payload_json TEXT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_run_events_project_id
        ON run_events(project_id, id);

        CREATE TABLE IF NOT EXISTS integrations (
            provider TEXT NOT NULL CHECK (provider IN ('github', 'gitlab', 'slack')),
            id TEXT NOT NULL,
            label TEXT NOT NULL,
            encrypted_token TEXT NOT NULL,
            scopes_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (provider, id)
        );
        """
    )
    _rebuild_run_events_for_wave10_event_types(connection)


def _ensure_notifications_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            ticket_id TEXT NULL,
            requirement_id TEXT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('needs_you', 'done', 'blocked')),
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read_at TEXT NULL,
            dedupe_key TEXT NOT NULL UNIQUE
        );

        CREATE INDEX IF NOT EXISTS idx_notifications_project_created
        ON notifications(project_id, created_at DESC, id DESC);

        CREATE INDEX IF NOT EXISTS idx_notifications_unread
        ON notifications(project_id, read_at);
        """
    )


def _ensure_prompt_versions_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id TEXT PRIMARY KEY,
            template_hash TEXT NOT NULL,
            first_seen_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_prompt_versions_hash
        ON prompt_versions(template_hash);
        """
    )


def _ensure_team_plane_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memberships (
            user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, workspace_id)
        );

        CREATE INDEX IF NOT EXISTS idx_memberships_workspace
        ON memberships(workspace_id, user_id);

        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            ts TEXT NOT NULL,
            ip TEXT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_audit_events_workspace_id
        ON audit_events(workspace_id, id);

        CREATE TABLE IF NOT EXISTS runner_tokens (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            label TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            revoked_at TEXT NULL,
            last_heartbeat_at TEXT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_runner_tokens_workspace
        ON runner_tokens(workspace_id, id);

        CREATE TABLE IF NOT EXISTS runner_jobs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            ticket_id TEXT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'running', 'terminal')),
            lease_runner_id TEXT NULL,
            lease_expires_at TEXT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_runner_jobs_workspace_status
        ON runner_jobs(workspace_id, status, created_at, id);

        CREATE TABLE IF NOT EXISTS git_app_installations (
            workspace_id TEXT NOT NULL,
            provider TEXT NOT NULL CHECK (provider IN ('github', 'gitlab')),
            account TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT NULL,
            PRIMARY KEY (workspace_id, provider, account)
        );

        CREATE INDEX IF NOT EXISTS idx_git_app_installations_workspace
        ON git_app_installations(workspace_id, provider, account);
        """
    )
    _ensure_wave9_workspace_columns(connection)


def _ensure_wave9_workspace_columns(connection: sqlite3.Connection) -> None:
    columns = _column_names(connection, "workspaces")
    if "seat_limit" not in columns:
        connection.execute("ALTER TABLE workspaces ADD COLUMN seat_limit INTEGER NULL")
    if "plan" not in columns:
        connection.execute("ALTER TABLE workspaces ADD COLUMN plan TEXT NOT NULL DEFAULT 'self-host'")


def _rebuild_run_events_for_wave10_event_types(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'run_events'"
    ).fetchone()
    if row is None:
        return
    sql = row["sql"] or ""
    required = ("diff_scope_reject", "rollback", "conflict", "attachment_egress")
    if all(item in sql for item in required):
        return

    connection.execute("ALTER TABLE run_events RENAME TO run_events_old")
    connection.execute(
        """
        CREATE TABLE run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            requirement_id TEXT NULL,
            ticket_id TEXT NULL,
            run_id TEXT NULL,
            event_type TEXT NOT NULL CHECK (
                event_type IN (
                    'run_started',
                    'model_call',
                    'diff_produced',
                    'dod_check',
                    'retry',
                    'escalation',
                    'egress_attempt',
                    'attachment_egress',
                    'diff_scope_reject',
                    'rollback',
                    'conflict',
                    'report',
                    'run_finished',
                    'error'
                )
            ),
            ts TEXT NOT NULL,
            model_id TEXT NULL,
            input_tokens INTEGER NULL,
            output_tokens INTEGER NULL,
            cost_usd REAL NULL,
            cost_status TEXT NULL CHECK (
                cost_status IS NULL OR cost_status IN ('actual', 'estimated', 'unknown')
            ),
            payload_json TEXT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO run_events (
            id, project_id, requirement_id, ticket_id, run_id, event_type, ts,
            model_id, input_tokens, output_tokens, cost_usd, cost_status, payload_json
        )
        SELECT
            id, project_id, requirement_id, ticket_id, run_id, event_type, ts,
            model_id, input_tokens, output_tokens, cost_usd, cost_status, payload_json
        FROM run_events_old
        """
    )
    connection.execute("DROP TABLE run_events_old")
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_run_events_project_id
        ON run_events(project_id, id);
        """
    )


def _ensure_eval_runs_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS eval_runs (
            id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL,
            task_set_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            trials INTEGER NOT NULL DEFAULT 1,
            started_at TEXT NOT NULL,
            finished_at TEXT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}',
            baseline_run_id TEXT NULL,
            regressed INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_eval_runs_model_task_started
        ON eval_runs(model_id, task_set_id, started_at DESC);

        CREATE INDEX IF NOT EXISTS idx_eval_runs_task_started
        ON eval_runs(task_set_id, started_at DESC);
        """
    )


def _ensure_requirement_templates_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS requirement_templates (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            scope_paths_json TEXT NOT NULL DEFAULT '[]',
            constraints_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_requirement_templates_updated
        ON requirement_templates(updated_at DESC, id ASC);
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
            env_allowlist_json TEXT NOT NULL DEFAULT '["PATH", "PYTHONPATH"]',
            test_allow_network INTEGER NOT NULL DEFAULT 0,
            sandbox_mode TEXT NOT NULL DEFAULT 'auto',
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
    if "env_allowlist_json" not in columns:
        connection.execute(
            "ALTER TABLE projects ADD COLUMN env_allowlist_json TEXT NOT NULL DEFAULT '[\"PATH\", \"PYTHONPATH\"]'"
        )
    if "test_allow_network" not in columns:
        connection.execute("ALTER TABLE projects ADD COLUMN test_allow_network INTEGER NOT NULL DEFAULT 0")
    if "sandbox_mode" not in columns:
        connection.execute("ALTER TABLE projects ADD COLUMN sandbox_mode TEXT NOT NULL DEFAULT 'auto'")


def _ensure_default_project(connection: sqlite3.Connection) -> None:
    now = datetime.now(UTC).isoformat()
    project_root = Path(__file__).resolve().parents[2]
    connection.execute(
        """
        INSERT OR IGNORE INTO projects (
            id, name, path, default_branch, env_json, env_allowlist_json,
            test_allow_network, sandbox_mode, setup_cmd, cleanup_cmd, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("default", "HAAO", str(project_root), "main", "{}", '["PATH", "PYTHONPATH"]', 0, "auto", "", "", now),
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
    migrate_chat_tables,
    migrate_foundational_contract_tables,
    migrate_notifications_table,
    migrate_project_execution_policy,
    migrate_eval_runs_table,
    migrate_requirement_templates_table,
    migrate_wave5_trust_tables,
    migrate_wave6_team_plane_tables,
    migrate_wave9_enterprise_gate_tables,
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
