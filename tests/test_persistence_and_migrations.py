import copy
import json

import pytest

from orchestrator.db.migrations import MIGRATIONS, migrate_escalate_to_tech_lead
from orchestrator.db.sqlite import ProjectRepository, SettingsRepository, connect
from orchestrator.execution_safety import DiffScopeError, validate_diff_target_files
from orchestrator.role_routing import DEFAULT_ROLE_ROUTING, RoleRoutingStore


def test_role_routing_persists_across_store_instances(tmp_path) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    settings_repository = SettingsRepository(connection)
    store = RoleRoutingStore()
    store.bind_settings_repository(settings_repository)
    store.update({"dev_team": "gemma-4-26b-a4b"})
    connection.close()

    reloaded_connection = connect(db_path)
    reloaded_settings = SettingsRepository(reloaded_connection)
    reloaded_store = RoleRoutingStore()
    reloaded_store.bind_settings_repository(reloaded_settings)

    assert reloaded_store.get()["dev_team"] == "gemma-4-26b-a4b"
    assert reloaded_store.get()["tech_lead"] == DEFAULT_ROLE_ROUTING["tech_lead"]
    reloaded_connection.close()


def test_migration_rewrites_cloud_po_escalate_to(tmp_path, fresh_ticket_dict) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    settings_repository = SettingsRepository(connection)
    payload = copy.deepcopy(fresh_ticket_dict)
    payload["execution"]["escalate_to"] = "cloud_po"
    payload["metadata"] = {"escalated_to": "cloud_po"}
    now = "2026-06-17T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO tickets (id, status, ticket_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            payload["id"],
            payload["status"],
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )
    connection.commit()

    migrate_escalate_to_tech_lead(connection)
    connection.commit()

    row = connection.execute(
        "SELECT ticket_json FROM tickets WHERE id = ?",
        (payload["id"],),
    ).fetchone()
    stored = json.loads(row["ticket_json"])
    assert stored["execution"]["escalate_to"] == "tech_lead"
    assert stored["metadata"]["escalated_to"] == "tech_lead"
    settings_repository.connection.close()


def test_initialize_database_repairs_legacy_project_columns(tmp_path) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    now = "2026-06-18T00:00:00+00:00"
    connection.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            default_branch TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
        ("schema_version", json.dumps(len(MIGRATIONS)), now),
    )
    connection.commit()

    ProjectRepository(connection)

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(projects)").fetchall()
    }
    assert {"env_json", "setup_cmd", "cleanup_cmd"}.issubset(columns)
