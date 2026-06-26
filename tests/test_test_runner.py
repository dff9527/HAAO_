import sys
from pathlib import Path

import pytest

from orchestrator.models.ticket import TestCommand, Ticket
from orchestrator.policies import ExecutionPolicy
from orchestrator.runner.dod_runner import (
    TestRunner,
    TestRunnerError,
    normalize_command,
    parse_command,
)


def test_parse_command_splits_argv_safely() -> None:
    assert parse_command("pytest tests/test_health.py -q") == [
        "pytest",
        "tests/test_health.py",
        "-q",
    ]


def test_normalize_command_uses_current_python_for_pytest() -> None:
    assert normalize_command(["pytest", "tests/test_health.py", "-q"]) == [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_health.py",
        "-q",
    ]


def test_normalize_command_uses_current_python_for_bare_python() -> None:
    assert normalize_command(["python", "-c", "print('ok')"]) == [
        sys.executable,
        "-c",
        "print('ok')",
    ]


def test_parse_command_rejects_shell_operator_tokens() -> None:
    with pytest.raises(TestRunnerError, match="shell operator tokens"):
        parse_command("pytest ; rm -rf /")


def test_parse_command_rejects_disallowed_executable() -> None:
    with pytest.raises(TestRunnerError, match="not allowed"):
        parse_command("bash -c 'echo pwned'")


def test_run_test_passes_for_successful_command() -> None:
    runner = TestRunner()
    result = runner.run_test_safe(
        TestCommand(
            command=f"{sys.executable} -c \"print('ok')\"",
            expect="pass",
            timeout_sec=5,
        )
    )

    assert result.status == "pass"
    assert "ok" in result.stdout
    assert result.timed_out is False


def test_run_test_fails_for_nonzero_exit() -> None:
    runner = TestRunner()
    result = runner.run_test_safe(
        TestCommand(
            command=f"{sys.executable} -c \"import sys; sys.exit(3)\"",
            expect="pass",
            timeout_sec=5,
        )
    )

    assert result.status == "fail"
    assert result.return_code == 3


def test_run_test_times_out() -> None:
    runner = TestRunner()
    result = runner.run_test_safe(
        TestCommand(
            command=f"{sys.executable} -c \"import time; time.sleep(2)\"",
            expect="pass",
            timeout_sec=1,
        )
    )

    assert result.status == "fail"
    assert result.timed_out is True


def test_run_test_uses_argument_array_not_shell(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    malicious = f"pytest; echo pwned > {marker}"

    with pytest.raises(TestRunnerError):
        TestRunner(cwd=tmp_path).run_test_safe(
            TestCommand(command=malicious, expect="pass", timeout_sec=5)
        )

    assert not marker.exists()


def test_run_test_injects_project_env() -> None:
    runner = TestRunner(
        env={"HAAO_FLAG": "ok"},
        execution_policy=ExecutionPolicy(
            test_allow_network=True,
            env_allowlist=("PATH", "PYTHONPATH", "HAAO_FLAG"),
        ),
    )

    result = runner.run_test_safe(
        TestCommand(
            command=f"{sys.executable} -c \"import os; print(os.environ['HAAO_FLAG'])\"",
            expect="pass",
            timeout_sec=5,
        )
    )

    assert result.status == "pass"
    assert result.stdout.strip() == "ok"


def test_run_test_filters_env_to_allowlist() -> None:
    runner = TestRunner(
        env={"HAAO_FLAG": "ok", "SECRET_TOKEN": "nope"},
        execution_policy=ExecutionPolicy(
            test_allow_network=True,
            env_allowlist=("PATH", "PYTHONPATH", "HAAO_FLAG"),
        ),
    )

    result = runner.run_test_safe(
        TestCommand(
            command=(
                f"{sys.executable} -c \"import os; "
                "print(os.environ.get('HAAO_FLAG')); "
                "print(os.environ.get('SECRET_TOKEN', 'missing'))\""
            ),
            expect="pass",
            timeout_sec=5,
        )
    )

    assert result.status == "pass"
    assert result.stdout.splitlines() == ["ok", "missing"]


def test_run_ticket_tests_runs_setup_and_cleanup(tmp_path: Path, fresh_ticket_dict) -> None:
    fresh_ticket_dict["definition_of_done"]["tests"] = [
        {
            "command": f"{sys.executable} -c \"from pathlib import Path; assert Path('setup.txt').read_text() == 'ok'\"",
            "expect": "pass",
            "timeout_sec": 5,
        }
    ]
    runner = TestRunner(
        cwd=tmp_path,
        setup_cmd=f"{sys.executable} -c \"from pathlib import Path; Path('setup.txt').write_text('ok')\"",
        cleanup_cmd=f"{sys.executable} -c \"from pathlib import Path; Path('cleanup.txt').write_text('done')\"",
    )

    results = runner.run_ticket_tests(Ticket.from_dict(fresh_ticket_dict))

    assert [result.status for result in results] == ["pass", "pass", "pass"]
    assert [result.command for result in results] == [
        runner.setup_cmd,
        fresh_ticket_dict["definition_of_done"]["tests"][0]["command"],
        runner.cleanup_cmd,
    ]
    assert (tmp_path / "cleanup.txt").read_text(encoding="utf-8") == "done"


def test_run_ticket_tests_skips_dod_when_setup_fails_and_still_cleans_up(
    tmp_path: Path,
    fresh_ticket_dict,
) -> None:
    fresh_ticket_dict["definition_of_done"]["tests"] = [
        {
            "command": f"{sys.executable} -c \"from pathlib import Path; Path('should_not_run.txt').write_text('bad')\"",
            "expect": "pass",
            "timeout_sec": 5,
        }
    ]
    runner = TestRunner(
        cwd=tmp_path,
        setup_cmd=f"{sys.executable} -c \"import sys; sys.exit(7)\"",
        cleanup_cmd=f"{sys.executable} -c \"from pathlib import Path; Path('cleanup.txt').write_text('done')\"",
    )

    results = runner.run_ticket_tests(Ticket.from_dict(fresh_ticket_dict))

    assert [result.status for result in results] == ["fail", "pass"]
    assert not (tmp_path / "should_not_run.txt").exists()
    assert (tmp_path / "cleanup.txt").read_text(encoding="utf-8") == "done"
