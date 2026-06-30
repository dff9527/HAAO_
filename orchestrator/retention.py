from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchestrator.db.sqlite import AuditRepository, SettingsRepository

RETENTION_POLICY_SETTINGS_PREFIX = "retention_policy:"
RETENTION_REDACTED = "[redacted by retention policy]"
RETENTION_FIELDS = (
    "run_events_days",
    "ticket_logs_days",
    "diffs_days",
    "prompts_days",
    "attachments_days",
)


@dataclass(frozen=True)
class RetentionPolicy:
    run_events_days: int | None = None
    ticket_logs_days: int | None = None
    diffs_days: int | None = None
    prompts_days: int | None = None
    attachments_days: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {field: getattr(self, field) for field in RETENTION_FIELDS}

    @classmethod
    def from_dict(cls, payload: dict | None) -> RetentionPolicy:
        values: dict[str, int | None] = {}
        for field in RETENTION_FIELDS:
            raw = (payload or {}).get(field)
            if raw is None:
                values[field] = None
                continue
            value = int(raw)
            if value < 0:
                raise ValueError(f"{field} must be null or >= 0")
            values[field] = value
        return cls(**values)


class RetentionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.settings = SettingsRepository(connection)

    def get_policy(self, workspace_id: str) -> RetentionPolicy:
        stored = self.settings.get_json(_policy_key(workspace_id), default={})
        if not isinstance(stored, dict):
            return RetentionPolicy()
        return RetentionPolicy.from_dict(stored)

    def set_policy(self, workspace_id: str, policy: RetentionPolicy) -> RetentionPolicy:
        self.settings.set_json(_policy_key(workspace_id), policy.to_dict())
        return self.get_policy(workspace_id)


class RetentionPurgeService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def purge(
        self,
        *,
        workspace_id: str,
        policy: RetentionPolicy,
        actor_id: str,
        now: datetime | None = None,
    ) -> dict[str, int]:
        reference = now or datetime.now(UTC)
        counts = {
            "run_events_deleted": self._delete_run_events(workspace_id, policy.run_events_days, reference),
            "ticket_logs_deleted": self._delete_ticket_logs(workspace_id, policy.ticket_logs_days, reference),
            "diff_events_deleted": self._delete_diff_events(workspace_id, policy.diffs_days, reference),
            "ticket_diffs_redacted": self._redact_ticket_diffs(workspace_id, policy.diffs_days, reference),
            "requirement_prompts_redacted": self._redact_requirement_prompts(
                workspace_id,
                policy.prompts_days,
                reference,
            ),
            "chat_messages_redacted": self._redact_chat_messages(
                workspace_id,
                policy.prompts_days,
                reference,
            ),
            "attachments_deleted": self._delete_attachments(
                workspace_id,
                policy.attachments_days,
                reference,
            ),
        }
        AuditRepository(self.connection).append(
            actor_id=actor_id,
            workspace_id=workspace_id,
            action="retention.purge",
            target=workspace_id,
            payload={"policy": policy.to_dict(), "counts": counts},
        )
        return counts

    def _delete_run_events(self, workspace_id: str, days: int | None, now: datetime) -> int:
        if days is None:
            return 0
        cursor = self.connection.execute(
            "DELETE FROM run_events WHERE project_id = ? AND ts < ?",
            (workspace_id, _cutoff(days, now)),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def _delete_ticket_logs(self, workspace_id: str, days: int | None, now: datetime) -> int:
        if days is None:
            return 0
        cursor = self.connection.execute(
            "DELETE FROM ticket_logs WHERE project_id = ? AND ts < ?",
            (workspace_id, _cutoff(days, now)),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def _delete_diff_events(self, workspace_id: str, days: int | None, now: datetime) -> int:
        if days is None:
            return 0
        cursor = self.connection.execute(
            """
            DELETE FROM run_events
            WHERE project_id = ? AND event_type = 'diff_produced' AND ts < ?
            """,
            (workspace_id, _cutoff(days, now)),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def _redact_ticket_diffs(self, workspace_id: str, days: int | None, now: datetime) -> int:
        if days is None:
            return 0
        rows = self.connection.execute(
            """
            SELECT id, ticket_json
            FROM tickets
            WHERE project_id = ? AND updated_at < ?
            """,
            (workspace_id, _cutoff(days, now)),
        ).fetchall()
        count = 0
        for row in rows:
            payload = _loads_json(row["ticket_json"])
            result = payload.get("result")
            if not isinstance(result, dict):
                continue
            diff = result.get("diff")
            if not isinstance(diff, str) or not diff or diff == RETENTION_REDACTED:
                continue
            result["diff"] = RETENTION_REDACTED
            count += 1
            self.connection.execute(
                "UPDATE tickets SET ticket_json = ? WHERE project_id = ? AND id = ?",
                (_dumps(payload), workspace_id, row["id"]),
            )
        self.connection.commit()
        return count

    def _redact_requirement_prompts(self, workspace_id: str, days: int | None, now: datetime) -> int:
        if days is None:
            return 0
        rows = self.connection.execute(
            """
            SELECT id, requirement_json
            FROM requirements
            WHERE project_id = ? AND created_at < ?
            """,
            (workspace_id, _cutoff(days, now)),
        ).fetchall()
        count = 0
        for row in rows:
            payload = _loads_json(row["requirement_json"])
            prompt = payload.get("prompt")
            if not isinstance(prompt, str) or not prompt or prompt == RETENTION_REDACTED:
                continue
            payload["prompt"] = RETENTION_REDACTED
            count += 1
            self.connection.execute(
                "UPDATE requirements SET requirement_json = ? WHERE project_id = ? AND id = ?",
                (_dumps(payload), workspace_id, row["id"]),
            )
        self.connection.commit()
        return count

    def _redact_chat_messages(self, workspace_id: str, days: int | None, now: datetime) -> int:
        if days is None:
            return 0
        cursor = self.connection.execute(
            """
            UPDATE chat_messages
            SET text = ?
            WHERE project_id = ? AND created_at < ? AND text <> ?
            """,
            (RETENTION_REDACTED, workspace_id, _cutoff(days, now), RETENTION_REDACTED),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def _delete_attachments(self, workspace_id: str, days: int | None, now: datetime) -> int:
        if days is None:
            return 0
        rows = self.connection.execute(
            """
            SELECT id, stored_path
            FROM chat_attachments
            WHERE project_id = ? AND created_at < ?
            """,
            (workspace_id, _cutoff(days, now)),
        ).fetchall()
        if not rows:
            return 0
        attachment_ids = [row["id"] for row in rows]
        placeholders = ",".join("?" for _ in attachment_ids)
        for row in rows:
            _unlink_attachment(row["stored_path"])
        self.connection.execute(
            f"DELETE FROM chat_message_attachments WHERE attachment_id IN ({placeholders})",
            attachment_ids,
        )
        cursor = self.connection.execute(
            f"DELETE FROM chat_attachments WHERE id IN ({placeholders})",
            attachment_ids,
        )
        self.connection.commit()
        return int(cursor.rowcount)


def _policy_key(workspace_id: str) -> str:
    return f"{RETENTION_POLICY_SETTINGS_PREFIX}{workspace_id}"


def _cutoff(days: int, now: datetime) -> str:
    return (now - timedelta(days=days)).isoformat()


def _loads_json(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _unlink_attachment(stored_path: str) -> None:
    try:
        path = Path(stored_path)
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        return
