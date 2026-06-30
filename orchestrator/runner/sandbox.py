from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from orchestrator.policies import ExecutionPolicy

SandboxPrimitive = Literal["docker", "unshare", "local", "none"]
SandboxAuditType = Literal["egress_attempt", "error"]
SandboxAuditSink = Callable[["SandboxAudit"], None]

DEFAULT_DOCKER_IMAGE = "python:3.11-slim"
NETWORK_ERROR_PATTERNS = (
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "connection refused",
    "connection timed out",
    "could not resolve",
    "getaddrinfo failed",
)


@dataclass(frozen=True)
class SandboxAudit:
    event_type: SandboxAuditType
    reason: str
    command: str
    primitive: SandboxPrimitive
    blocked: bool = True
    message: str = ""


@dataclass(frozen=True)
class SandboxRunResult:
    completed: subprocess.CompletedProcess[str]
    primitive: SandboxPrimitive
    network_disabled: bool


@dataclass(frozen=True)
class PrimitiveChoice:
    primitive: SandboxPrimitive | None
    reason: str = ""


def run_with_policy(
    argv: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    timeout: int,
    command: str,
    policy: ExecutionPolicy,
    audit_sink: SandboxAuditSink | None = None,
) -> SandboxRunResult:
    """Run a DoD command under the strongest available network restriction.

    Enforcement by platform:
    - Docker: runs a local image with ``--network=none`` and only the worktree
      mounted at ``/workspace``. The image must already exist locally.
    - Linux unshare: runs the host command in a new network namespace.
    - Fallback: runs locally and emits audit warnings; it is not called a sandbox.
    """

    env = _filter_env(env, policy)

    strict = policy.sandbox_mode == "strict"
    if not strict and (policy.test_allow_network or policy.sandbox_mode == "none"):
        completed = _run(argv, cwd=cwd, env=env, timeout=timeout)
        primitive: SandboxPrimitive = "none" if policy.sandbox_mode == "none" else "local"
        return SandboxRunResult(completed=completed, primitive=primitive, network_disabled=False)

    choice = choose_primitive(policy.sandbox_mode)
    if choice.primitive == "docker" and cwd is None:
        choice = PrimitiveChoice(None, "docker sandbox requires a worktree cwd")

    if choice.primitive == "docker":
        completed = _run_docker(argv, cwd=cwd, env=env, timeout=timeout, policy=policy, strict=strict)
        primitive = "docker"
    elif choice.primitive == "unshare":
        completed = _run(["unshare", "-n", "--", *argv], cwd=cwd, env=env, timeout=timeout)
        primitive = "unshare"
    else:
        primitive = "local"
        reason = "sandbox_strict_unavailable" if strict else "sandbox_unavailable"
        message = (
            "Strict sandbox unavailable; refusing to claim multi-tenant isolation. "
            if strict
            else "Network isolation unavailable; running DoD/test command without a sandbox. "
        )
        message += f"Reason: {choice.reason or 'no supported primitive available'}"
        _emit(
            audit_sink,
            SandboxAudit(
                event_type="egress_attempt",
                reason=reason,
                command=command,
                primitive=primitive,
                blocked=False,
                message=message,
            ),
        )
        _emit(
            audit_sink,
            SandboxAudit(
                event_type="error",
                reason=reason,
                command=command,
                primitive=primitive,
                blocked=False,
                message=message,
            ),
        )
        completed = _run(argv, cwd=cwd, env=env, timeout=timeout)

    if completed.returncode != 0 and _looks_like_network_failure(completed):
        _emit(
            audit_sink,
            SandboxAudit(
                event_type="egress_attempt",
                reason="network_blocked_by_sandbox",
                command=command,
                primitive=primitive,
                blocked=True,
                message="Command output looks like an outbound network attempt blocked by isolation.",
            ),
        )

    return SandboxRunResult(completed=completed, primitive=primitive, network_disabled=primitive in {"docker", "unshare"})


def choose_primitive(mode: str) -> PrimitiveChoice:
    if mode == "strict":
        return PrimitiveChoice("docker") if docker_available() else PrimitiveChoice(None, "strict docker unavailable")
    if mode == "docker":
        return PrimitiveChoice("docker") if docker_available() else PrimitiveChoice(None, "docker unavailable")
    if mode == "unshare":
        return PrimitiveChoice("unshare") if unshare_available() else PrimitiveChoice(None, "unshare unavailable")
    if mode != "auto":
        return PrimitiveChoice(None, f"unsupported sandbox mode: {mode}")
    if docker_available():
        return PrimitiveChoice("docker")
    if unshare_available():
        return PrimitiveChoice("unshare")
    return PrimitiveChoice(None, "docker and unshare unavailable")


def _filter_env(env: dict[str, str], policy: ExecutionPolicy) -> dict[str, str]:
    allowed = {str(item) for item in policy.env_allowlist if str(item).strip()}
    return {str(key): str(value) for key, value in env.items() if str(key) in allowed}


def docker_available() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    image = os.environ.get("HAAO_SANDBOX_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)
    try:
        info = subprocess.run(
            [docker, "info"],
            capture_output=True,
            text=True,
            timeout=3,
            shell=False,
        )
        if info.returncode != 0:
            return False
        inspected = subprocess.run(
            [docker, "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=3,
            shell=False,
        )
        return inspected.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def unshare_available() -> bool:
    if platform.system().lower() != "linux":
        return False
    unshare = shutil.which("unshare")
    if not unshare:
        return False
    try:
        probe = subprocess.run(
            [unshare, "-n", "--", "true"],
            capture_output=True,
            text=True,
            timeout=3,
            shell=False,
        )
        return probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run(
    argv: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


def _run_docker(
    argv: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    timeout: int,
    policy: ExecutionPolicy,
    strict: bool = False,
) -> subprocess.CompletedProcess[str]:
    if cwd is None:
        raise ValueError("Docker sandbox requires a worktree cwd")
    docker = shutil.which("docker") or "docker"
    image = os.environ.get("HAAO_SANDBOX_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)
    docker_argv = [
        docker,
        "run",
        "--rm",
        "--network=none",
        "--mount",
        f"type=bind,src={cwd},dst=/workspace",
        "-w",
        "/workspace",
    ]
    if strict:
        docker_argv.extend(
            [
                "--cpus",
                str(policy.cpu_limit),
                "--memory",
                f"{policy.memory_mb}m",
                "--pids-limit",
                str(policy.pids_limit),
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=64m",
            ]
        )
    for key, value in sorted(env.items()):
        if key == "PATH":
            continue
        docker_argv.extend(["--env", f"{key}={value}"])
    docker_argv.append(image)
    docker_argv.extend(_container_argv(argv, cwd))
    return subprocess.run(
        docker_argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


def _container_argv(argv: list[str], cwd: Path) -> list[str]:
    if not argv:
        return argv
    executable = Path(argv[0])
    if _same_executable(executable, Path(sys.executable)):
        return ["python", *argv[1:]]
    if executable.is_absolute():
        try:
            relative = executable.resolve().relative_to(cwd.resolve())
        except ValueError:
            return [executable.name, *argv[1:]]
        return [str(Path("/workspace") / relative), *argv[1:]]
    return argv


def _same_executable(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _looks_like_network_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    return any(pattern in output for pattern in NETWORK_ERROR_PATTERNS)


def _emit(audit_sink: SandboxAuditSink | None, audit: SandboxAudit) -> None:
    if audit_sink is not None:
        audit_sink(audit)
