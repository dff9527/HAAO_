from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunnerState:
    runner_id: str = ""
    token: str = ""

    @property
    def registered(self) -> bool:
        return bool(self.runner_id and self.token)


class RunnerStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> RunnerState:
        if not self.path.exists():
            return RunnerState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return RunnerState()
        if not isinstance(payload, dict):
            return RunnerState()
        return RunnerState(
            runner_id=str(payload.get("runner_id") or ""),
            token=str(payload.get("token") or ""),
        )

    def save(self, state: RunnerState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"runner_id": state.runner_id, "token": state.token},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
