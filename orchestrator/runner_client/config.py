from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunnerClientConfig:
    control_plane_url: str
    workspace_id: str = "default"
    label: str = "local-runner"
    repo_root: Path = Path(".")
    database_url: str = "sqlite:///./haao.sqlite3"
    lmstudio_base_url: str = "http://localhost:1234/v1"
    state_path: Path = Path(".haao/runner-state.json")
    api_token: str = ""
    lease_ttl_sec: int = 300
    poll_interval_sec: float = 5.0
    max_idle_cycles: int | None = None

    @classmethod
    def from_env_file(cls, env_path: str | Path = ".env") -> RunnerClientConfig:
        values = _read_env_file(Path(env_path))
        merged = {**values, **os.environ}
        repo_root = Path(merged.get("HAAO_RUNNER_REPO_ROOT") or ".").expanduser().resolve()
        return cls(
            control_plane_url=_required(merged, "HAAO_CONTROL_PLANE_URL").rstrip("/"),
            workspace_id=merged.get("HAAO_RUNNER_WORKSPACE_ID", "default"),
            label=merged.get("HAAO_RUNNER_LABEL", "local-runner"),
            repo_root=repo_root,
            database_url=merged.get("DATABASE_URL", f"sqlite:///{repo_root / 'haao.sqlite3'}"),
            lmstudio_base_url=merged.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            state_path=Path(
                merged.get("HAAO_RUNNER_STATE_PATH") or repo_root / ".haao" / "runner-state.json"
            ).expanduser(),
            api_token=merged.get("HAAO_API_TOKEN", ""),
            lease_ttl_sec=int(merged.get("HAAO_RUNNER_LEASE_TTL_SEC", "300")),
            poll_interval_sec=float(merged.get("HAAO_RUNNER_POLL_INTERVAL_SEC", "5")),
            max_idle_cycles=_optional_int(merged.get("HAAO_RUNNER_MAX_IDLE_CYCLES")),
        )


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)
