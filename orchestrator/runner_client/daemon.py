from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from orchestrator.runner_client.config import RunnerClientConfig
from orchestrator.runner_client.executor import LocalExecutionConfig, LocalExecutionJobExecutor
from orchestrator.runner_client.redaction import RunnerSecretFilter
from orchestrator.runner_client.state import RunnerState, RunnerStateStore
from orchestrator.runner_client.transport import RunnerTransport, RunnerUnauthorized


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerJobResult:
    events: list[dict]
    result: dict


class RunnerExecutor(Protocol):
    def execute(self, job: dict) -> tuple[list[dict], dict] | RunnerJobResult:
        ...


class RunnerDaemon:
    def __init__(
        self,
        config: RunnerClientConfig,
        *,
        transport: RunnerTransport | None = None,
        state_store: RunnerStateStore | None = None,
        executor: RunnerExecutor | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or RunnerTransport(config.control_plane_url, api_token=config.api_token)
        self.state_store = state_store or RunnerStateStore(config.state_path)
        self.executor = executor or LocalExecutionJobExecutor(
            LocalExecutionConfig(
                database_url=config.database_url,
                repo_root=config.repo_root,
                lmstudio_base_url=config.lmstudio_base_url,
            )
        )
        self._current_job_id: str | None = None
        self._runner_token: str = ""
        logger.addFilter(RunnerSecretFilter())

    def run_forever(self) -> None:
        idle_cycles = 0
        try:
            while True:
                did_work = self.run_once()
                if did_work:
                    idle_cycles = 0
                else:
                    idle_cycles += 1
                    if self.config.max_idle_cycles is not None and idle_cycles >= self.config.max_idle_cycles:
                        return
                    time.sleep(self.config.poll_interval_sec)
        except RunnerUnauthorized:
            logger.warning("Runner token revoked; stopping client runner")
            self.state_store.clear()
        finally:
            self.release_current_lease()

    def run_once(self) -> bool:
        state = self._ensure_registered()
        self._runner_token = state.token
        self.transport.heartbeat(state.token)
        leased = self.transport.lease(state.token, ttl_sec=self.config.lease_ttl_sec)
        job = leased.get("job")
        if not isinstance(job, dict) or not job:
            logger.info("No runner job available")
            return False
        job_id = str(job.get("id") or "")
        if not job_id:
            raise ValueError("leased runner job missing id")
        self._current_job_id = job_id
        logger.info("Executing runner job %s", job_id)
        reported_terminal = False
        try:
            raw_result = self.executor.execute(job)
            if isinstance(raw_result, RunnerJobResult):
                events, result = raw_result.events, raw_result.result
            else:
                events, result = raw_result
            self.transport.heartbeat(state.token)
            if events:
                self.transport.send_events(state.token, job_id=job_id, events=events)
            self.transport.complete(state.token, job_id=job_id, result=result)
            reported_terminal = True
            logger.info("Completed runner job %s", job_id)
            return True
        except Exception as exc:
            error_result = {"outcome": "error", "error": str(exc)}
            self.transport.send_events(
                state.token,
                job_id=job_id,
                events=[
                    {
                        "event_type": "error",
                        "ticket_id": job.get("ticket_id"),
                        "payload": {"error": str(exc)},
                    }
                ],
            )
            self.transport.complete(state.token, job_id=job_id, result=error_result)
            reported_terminal = True
            logger.exception("Runner job %s failed", job_id)
            return True
        finally:
            if reported_terminal:
                self._current_job_id = None

    def release_current_lease(self) -> None:
        if not self._current_job_id or not self._runner_token:
            return
        try:
            self.transport.release(self._runner_token, job_id=self._current_job_id)
        except Exception:
            logger.warning("Could not release runner job %s during shutdown", self._current_job_id)
        finally:
            self._current_job_id = None

    def _ensure_registered(self) -> RunnerState:
        state = self.state_store.load()
        if state.registered:
            return state
        registered = self.transport.register(
            workspace_id=self.config.workspace_id,
            label=self.config.label,
        )
        runner = registered.get("runner") if isinstance(registered.get("runner"), dict) else {}
        token = str(registered.get("token") or "")
        runner_id = str(runner.get("id") or "")
        if not token or not runner_id:
            raise ValueError("runner register response missing runner id or token")
        state = RunnerState(runner_id=runner_id, token=token)
        self.state_store.save(state)
        logger.info("Registered runner %s for workspace %s", runner_id, self.config.workspace_id)
        return state
