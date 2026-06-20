from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from orchestrator.models.ticket import TestCommand, Ticket

ALLOWED_EXECUTABLES = frozenset(
    {
        "pytest",
        "npm",
        "python",
        "python3",
        "ruff",
        "mypy",
        "node",
    }
)

SHELL_OPERATOR_TOKENS = frozenset({";", "|", "||", "&", "&&", ">", "<"})


class TestRunnerError(ValueError):
    """Raised when a test command cannot be executed safely."""

    __test__ = False


@dataclass(frozen=True)
class TestRunResult:
    command: str
    status: Literal["pass", "fail"]
    expect: Literal["pass", "fail"]
    stdout: str
    stderr: str
    timed_out: bool = False
    return_code: int | None = None


class TestRunner:
    """Run ticket DoD test commands without invoking a shell."""

    __test__ = False

    def __init__(
        self,
        cwd: str | Path | None = None,
        *,
        env: dict[str, str] | None = None,
        setup_cmd: str = "",
        cleanup_cmd: str = "",
        setup_timeout_sec: int = 120,
        cleanup_timeout_sec: int = 120,
    ) -> None:
        self.cwd = Path(cwd).resolve() if cwd is not None else None
        self.env = dict(env or {})
        self.setup_cmd = setup_cmd.strip()
        self.cleanup_cmd = cleanup_cmd.strip()
        self.setup_timeout_sec = setup_timeout_sec
        self.cleanup_timeout_sec = cleanup_timeout_sec

    def run_ticket_tests(self, ticket: Ticket) -> list[TestRunResult]:
        results: list[TestRunResult] = []
        setup_result: TestRunResult | None = None
        if self.setup_cmd:
            setup_result = self.run_command_safe(
                self.setup_cmd,
                expect="pass",
                timeout_sec=self.setup_timeout_sec,
            )
            results.append(setup_result)
            if setup_result.status != "pass":
                cleanup_result = self._cleanup_result()
                if cleanup_result is not None:
                    results.append(cleanup_result)
                return results

        try:
            results.extend(
                self.run_test_safe(test) for test in ticket.definition_of_done.tests
            )
        finally:
            cleanup_result = self._cleanup_result()
            if cleanup_result is not None:
                results.append(cleanup_result)
        return results

    def run_test(self, test: TestCommand) -> TestRunResult:
        return self.run_command(test.command, expect=test.expect, timeout_sec=test.timeout_sec)

    def run_command(
        self,
        command: str,
        *,
        expect: Literal["pass", "fail"] = "pass",
        timeout_sec: int = 120,
    ) -> TestRunResult:
        argv = normalize_command(parse_command(command))
        completed = subprocess.run(
            argv,
            cwd=self.cwd,
            env=_subprocess_env(self.env),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            shell=False,
        )
        actual_status: Literal["pass", "fail"] = (
            "pass" if completed.returncode == 0 else "fail"
        )
        status: Literal["pass", "fail"] = (
            "pass" if actual_status == expect else "fail"
        )
        return TestRunResult(
            command=command,
            status=status,
            expect=expect,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timed_out=False,
            return_code=completed.returncode,
        )

    def run_test_safe(self, test: TestCommand) -> TestRunResult:
        return self.run_command_safe(
            test.command,
            expect=test.expect,
            timeout_sec=test.timeout_sec,
        )

    def run_command_safe(
        self,
        command: str,
        *,
        expect: Literal["pass", "fail"] = "pass",
        timeout_sec: int = 120,
    ) -> TestRunResult:
        try:
            return self.run_command(command, expect=expect, timeout_sec=timeout_sec)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return TestRunResult(
                command=command,
                status="fail",
                expect=expect,
                stdout=stdout,
                stderr=stderr or f"Command timed out after {timeout_sec}s",
                timed_out=True,
                return_code=None,
            )

    def _cleanup_result(self) -> TestRunResult | None:
        if not self.cleanup_cmd:
            return None
        return self.run_command_safe(
            self.cleanup_cmd,
            expect="pass",
            timeout_sec=self.cleanup_timeout_sec,
        )


def parse_command(command: str) -> list[str]:
    stripped = command.strip()
    if not stripped:
        raise TestRunnerError("Test command cannot be empty")

    if "\n" in stripped or "\r" in stripped:
        raise TestRunnerError("Test command cannot contain newlines")

    try:
        argv = shlex.split(stripped, posix=True)
    except ValueError as exc:
        raise TestRunnerError(f"Invalid test command: {exc}") from exc

    if not argv:
        raise TestRunnerError("Test command cannot be empty")

    if any(token in SHELL_OPERATOR_TOKENS for token in argv):
        raise TestRunnerError(
            "Test command contains shell operator tokens and cannot be executed safely"
        )

    executable = Path(argv[0]).name
    if executable not in ALLOWED_EXECUTABLES:
        raise TestRunnerError(
            f"Executable {executable!r} is not allowed. "
            f"Allowed executables: {', '.join(sorted(ALLOWED_EXECUTABLES))}"
        )

    return argv


def normalize_command(argv: list[str]) -> list[str]:
    executable = Path(argv[0]).name
    if executable == "pytest":
        return [sys.executable, "-m", "pytest", *argv[1:]]
    if executable in {"python", "python3"} and not Path(argv[0]).is_absolute():
        return [sys.executable, *argv[1:]]
    return argv


def _subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for key, value in (extra_env or {}).items():
        env[str(key)] = str(value)
    executable_dir = str(Path(sys.executable).resolve().parent)
    current_path = env.get("PATH", "")
    env["PATH"] = (
        executable_dir if not current_path else os.pathsep.join([executable_dir, current_path])
    )
    return env
