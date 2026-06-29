from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from clients.lmstudio import LMStudioClient
from orchestrator.auto_orchestrator import AutoOrchestrator
from orchestrator.cloud_reasoner_config import build_cloud_reasoner
from orchestrator.config import Settings
from orchestrator.db.sqlite import RequirementRepository, SettingsRepository, TicketRepository, connect
from orchestrator.escalation import EscalationService
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.execution_safety import GitWorkspaceGuard
from orchestrator.policies import ExecutionPolicy
from orchestrator.review_flow import ReviewService
from orchestrator.role_routing import role_routing_store
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService


@dataclass(frozen=True)
class AutoWorkerSnapshot:
    running: bool
    interval_sec: float
    max_cycles_per_tick: int
    allow_dirty_workspace: bool
    last_started_at: str | None
    last_run_at: str | None
    last_error: str
    last_skipped_reason: str = ""
    project_id: str | None = None
    max_workers: int = 1
    worker_statuses: list[dict] = field(default_factory=list)


@dataclass
class WorkerStatus:
    worker_id: str
    running: bool = False
    last_run_at: str | None = None
    last_error: str = ""
    last_skipped_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "worker_id": self.worker_id,
            "running": self.running,
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
            "last_skipped_reason": self.last_skipped_reason,
        }


class AutoWorker:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []
        self._stop_event: asyncio.Event | None = None
        self.interval_sec = 5.0
        self.max_cycles_per_tick = 10
        self.max_workers = 1
        self.allow_dirty_workspace = False
        self.project_id: str | None = None
        self.last_started_at: str | None = None
        self.last_run_at: str | None = None
        self.last_error = ""
        self.last_skipped_reason = ""
        self.worker_statuses: dict[str, WorkerStatus] = {}

    @property
    def running(self) -> bool:
        return any(not task.done() for task in self._tasks)

    async def start(
        self,
        *,
        settings: Settings,
        project_id: str | None,
        repo_root: Path,
        database_root: Path,
        env: dict[str, str] | None = None,
        env_allowlist: list[str] | None = None,
        test_allow_network: bool = False,
        sandbox_mode: str = "auto",
        setup_cmd: str = "",
        cleanup_cmd: str = "",
        interval_sec: float = 5.0,
        max_cycles_per_tick: int = 10,
        max_workers: int = 1,
        allow_dirty_workspace: bool = False,
    ) -> AutoWorkerSnapshot:
        if self.running:
            if project_id == self.project_id:
                return self.snapshot()
            # Auto-run follows the active board project: when asked to run a
            # different project, rebind by stopping the current worker first.
            await self.stop()

        self.project_id = project_id
        self.interval_sec = interval_sec
        self.max_cycles_per_tick = max_cycles_per_tick
        self.max_workers = max(1, min(int(max_workers), 16))
        self.allow_dirty_workspace = allow_dirty_workspace
        self.last_started_at = _now()
        self.last_error = ""
        self._stop_event = asyncio.Event()
        self.worker_statuses = {
            f"worker-{index}": WorkerStatus(worker_id=f"worker-{index}", running=True)
            for index in range(1, self.max_workers + 1)
        }
        self._tasks = [
            asyncio.create_task(
                self._run(
                    worker_id=worker_id,
                    settings=settings,
                    project_id=project_id,
                    repo_root=repo_root.resolve(),
                    database_root=database_root.resolve(),
                    env=dict(env or {}),
                    env_allowlist=list(env_allowlist or ["PATH", "PYTHONPATH"]),
                    test_allow_network=test_allow_network,
                    sandbox_mode=sandbox_mode,
                    setup_cmd=setup_cmd,
                    cleanup_cmd=cleanup_cmd,
                )
            )
            for worker_id in self.worker_statuses
        ]
        return self.snapshot()

    async def stop(self) -> AutoWorkerSnapshot:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=max(self.interval_sec + 1, 2),
                )
            except TimeoutError:
                for task in self._tasks:
                    task.cancel()
        for status in self.worker_statuses.values():
            status.running = False
        return self.snapshot()

    def snapshot(self) -> AutoWorkerSnapshot:
        return AutoWorkerSnapshot(
            running=self.running,
            interval_sec=self.interval_sec,
            max_cycles_per_tick=self.max_cycles_per_tick,
            allow_dirty_workspace=self.allow_dirty_workspace,
            last_started_at=self.last_started_at,
            last_run_at=self.last_run_at,
            last_error=self.last_error,
            last_skipped_reason=self.last_skipped_reason,
            project_id=self.project_id,
            max_workers=self.max_workers,
            worker_statuses=[status.to_dict() for status in self.worker_statuses.values()],
        )

    async def _run(
        self,
        *,
        worker_id: str,
        settings: Settings,
        project_id: str | None,
        repo_root: Path,
        database_root: Path,
        env: dict[str, str],
        env_allowlist: list[str],
        test_allow_network: bool,
        sandbox_mode: str,
        setup_cmd: str,
        cleanup_cmd: str,
    ) -> None:
        assert self._stop_event is not None
        status = self.worker_statuses[worker_id]
        while not self._stop_event.is_set():
            try:
                status.last_skipped_reason = await asyncio.to_thread(
                    _run_tick,
                    settings,
                    project_id,
                    repo_root,
                    database_root,
                    env,
                    env_allowlist,
                    test_allow_network,
                    sandbox_mode,
                    setup_cmd,
                    cleanup_cmd,
                    self.max_cycles_per_tick,
                    self.allow_dirty_workspace,
                    worker_id,
                ) or ""
                status.last_run_at = _now()
                status.last_error = ""
                self.last_run_at = status.last_run_at
                self.last_skipped_reason = status.last_skipped_reason
                self.last_error = ""
            except Exception as exc:  # noqa: BLE001 - worker keeps running and reports status
                status.last_error = str(exc)
                self.last_error = status.last_error

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_sec)
            except TimeoutError:
                continue
        status.running = False


def _run_tick(
    settings: Settings,
    project_id: str | None,
    repo_root: Path,
    database_root: Path,
    env: dict[str, str],
    env_allowlist: list[str],
    test_allow_network: bool,
    sandbox_mode: str,
    setup_cmd: str,
    cleanup_cmd: str,
    max_cycles_per_tick: int,
    allow_dirty_workspace: bool,
    worker_id: str = "worker-1",
) -> str:
    connection = connect(_sqlite_path(settings.database_url, database_root))
    lmstudio = LMStudioClient(settings.lmstudio_base_url)
    try:
        repository = TicketRepository(connection, project_id=project_id)
        settings_repository = SettingsRepository(connection)
        tech_lead = build_cloud_reasoner(settings, settings_repository)
        requirement_repository = RequirementRepository(connection, project_id=project_id)
        role_routing_store.bind_settings_repository(settings_repository)
        state_service = TicketStateService(repository)
        execution_loop = ExecutionLoop(
            repository,
            state_service,
            lmstudio,
            repo_root=repo_root,
            test_runner=TestRunner(
                cwd=repo_root,
                env=env,
                execution_policy=ExecutionPolicy(
                    test_allow_network=test_allow_network,
                    env_allowlist=tuple(env_allowlist),
                    sandbox_mode=sandbox_mode if sandbox_mode in {"auto", "docker", "unshare", "none"} else "auto",
                ),
                setup_cmd=setup_cmd,
                cleanup_cmd=cleanup_cmd,
            ),
            settings_repository=settings_repository,
            requirement_repository=requirement_repository,
            max_output_tokens=settings.local_max_output_tokens,
            patch_mode_threshold_tokens=settings.local_patch_mode_threshold_tokens,
        )
        orchestrator = AutoOrchestrator(
            repository,
            execution_loop,
            ReviewService(repository, state_service, tech_lead, requirement_repository),
            EscalationService(repository, tech_lead, settings_repository),
            repo_root=repo_root,
            workspace_guard=GitWorkspaceGuard(repo_root),
            allow_dirty_workspace=allow_dirty_workspace,
            worker_id=worker_id,
        )
        results = orchestrator.run_until_idle(max_cycles=max_cycles_per_tick)
        last = results[-1] if results else None
        # Surface why a tick did nothing (e.g. "workspace_dirty") so the UI can
        # explain a "running but not progressing" Auto-run instead of being silent.
        return last.skipped_reason if last is not None else ""
    finally:
        lmstudio.close()
        if "tech_lead" in locals():
            tech_lead.close()
        connection.close()


def _sqlite_path(database_url: str, database_root: Path) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Only sqlite:/// DATABASE_URL values are supported")
    path = database_url[len(prefix) :]
    if path.startswith("./"):
        return str(database_root / path[2:])
    return path


def _now() -> str:
    return datetime.now(UTC).isoformat()


auto_worker = AutoWorker()
