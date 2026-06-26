from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Literal

from orchestrator.db.sqlite import RunEventRepository, SettingsRepository

RUN_EVENT_RETENTION_SETTINGS_KEY = "run_event_retention_policy"
EXECUTION_POLICY_SETTINGS_KEY = "execution_policy"
SandboxMode = Literal["auto", "docker", "unshare", "none"]


@dataclass(frozen=True)
class RetentionPolicy:
    run_event_retention_days: int = 90
    diff_retention_runs_per_ticket: int = 10

    def to_dict(self) -> dict[str, int]:
        return {
            "run_event_retention_days": self.run_event_retention_days,
            "diff_retention_runs_per_ticket": self.diff_retention_runs_per_ticket,
        }


@dataclass(frozen=True)
class ExecutionPolicy:
    test_allow_network: bool = False
    env_allowlist: tuple[str, ...] = ("PATH", "PYTHONPATH")
    sandbox_mode: SandboxMode = "auto"

    def to_dict(self) -> dict[str, object]:
        return {
            "test_allow_network": self.test_allow_network,
            "env_allowlist": list(self.env_allowlist),
            "sandbox_mode": self.sandbox_mode,
        }


def get_retention_policy(repository: SettingsRepository) -> RetentionPolicy:
    stored = repository.get_json(RUN_EVENT_RETENTION_SETTINGS_KEY, {})
    if not isinstance(stored, dict):
        return RetentionPolicy()
    return RetentionPolicy(
        run_event_retention_days=max(1, int(stored.get("run_event_retention_days") or 90)),
        diff_retention_runs_per_ticket=max(0, int(stored.get("diff_retention_runs_per_ticket") or 10)),
    )


def set_retention_policy(repository: SettingsRepository, policy: RetentionPolicy) -> RetentionPolicy:
    repository.set_json(RUN_EVENT_RETENTION_SETTINGS_KEY, policy.to_dict())
    return policy


def get_execution_policy(repository: SettingsRepository) -> ExecutionPolicy:
    stored = repository.get_json(EXECUTION_POLICY_SETTINGS_KEY, {})
    if not isinstance(stored, dict):
        return ExecutionPolicy()
    allowlist = stored.get("env_allowlist", [])
    if not isinstance(allowlist, list):
        allowlist = []
    sandbox_mode = _normalize_sandbox_mode(stored.get("sandbox_mode", "auto"))
    return ExecutionPolicy(
        test_allow_network=bool(stored.get("test_allow_network", False)),
        env_allowlist=tuple(str(item) for item in allowlist if str(item).strip()) or ("PATH", "PYTHONPATH"),
        sandbox_mode=sandbox_mode,
    )


def set_execution_policy(repository: SettingsRepository, policy: ExecutionPolicy) -> ExecutionPolicy:
    repository.set_json(EXECUTION_POLICY_SETTINGS_KEY, policy.to_dict())
    return policy


def _normalize_sandbox_mode(value: object) -> SandboxMode:
    if value in {"auto", "docker", "unshare", "none"}:
        return value  # type: ignore[return-value]
    return "auto"


def record_egress_attempt(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    ticket_id: str | None = None,
    requirement_id: str | None = None,
    run_id: str | None = None,
    destination: str = "",
    command: str = "",
    blocked: bool = True,
    reason: str = "network_not_allowed",
) -> None:
    """Record an observed outbound attempt.

    Wave 1b only exposes the audit producer. True network namespace enforcement
    remains the Wave 3 sandbox work; callers should invoke this when a best-effort
    guard or runner observes outbound behavior.
    """
    RunEventRepository(connection).append_run_event(
        project_id=project_id,
        requirement_id=requirement_id,
        ticket_id=ticket_id,
        run_id=run_id,
        event_type="egress_attempt",
        payload={
            "destination": destination,
            "command": command,
            "blocked": blocked,
            "reason": reason,
        },
    )
