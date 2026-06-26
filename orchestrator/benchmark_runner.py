"""R-102 real-repo benchmark harness (decompose → execute → audit → gate)."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clients.claude_po import ClaudeTechLeadClient
from clients.lmstudio import LMStudioClient, LMStudioError
from orchestrator.auto_orchestrator import AutoOrchestrator
from orchestrator.cloud_usage import CloudUsage
from orchestrator.config import Settings
from orchestrator.context.injector import estimate_tokens
from orchestrator.db.sqlite import RequirementRepository, SettingsRepository, TicketRepository, connect
from orchestrator.diff_review import DiffReviewService
from orchestrator.escalation import EscalationService
from orchestrator.execution_loop import (
    DEFAULT_LOCAL_MAX_OUTPUT_TOKENS,
    DEFAULT_PATCH_MODE_THRESHOLD_TOKENS,
    ExecutionLoop,
    required_rewrite_output_tokens,
)
from orchestrator.execution_safety import GitWorkspaceGuard
from orchestrator.git_flow import GitTicketFlow
from orchestrator.models.requirement import Requirement
from orchestrator.models.ticket import Ticket, TicketStatus
from orchestrator.policies import ExecutionPolicy
from orchestrator.requirements_flow import RequirementService
from orchestrator.review_flow import ReviewService
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService

LOCAL_FINISH_STATES = frozenset({"diff_pending", "review", "awaiting_acceptance", "done"})
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROBES_ROOT = PROJECT_ROOT / "benchmarks" / "r102_probes"
DEFAULT_MAX_TARGET_FILE_BYTES = 100_000

MECHANICAL_MARKERS = (
    "empty file content",
    "WholeFileWriteError",
    "git apply",
    "outside ticket.task.target_files",
    "resolves outside repo_root",
    "path outside repo",
    "No pending diff",
    "DiffScopeError",
)


@dataclass(frozen=True)
class RepoDefinition:
    name: str
    github: str
    ref: str
    local_path: Path
    default_scope: list[str]


@dataclass(frozen=True)
class TaskDefinition:
    id: str
    repo: str
    category: str
    requirement: str
    dod: str
    existing_tests: str
    target_files: list[str]
    patch_mode_eligible: bool = False


@dataclass(frozen=True)
class BenchmarkManifest:
    version: int
    repos: dict[str, RepoDefinition]
    tasks: list[TaskDefinition]


@dataclass
class TicketTrialSnapshot:
    ticket_id: str
    status: str
    attempts: int
    outcome: str
    test_output: str
    diff: str
    result: str
    mechanical_failure: bool


@dataclass
class RequirementTrialResult:
    task_id: str
    trial: int
    repo: str
    category: str
    result: str
    mechanical_failure: bool
    ticket_count: int
    max_attempts: int
    baseline_failed_first: bool = False
    existing_tests_still_green: bool = False
    dod_passed_after: bool = False
    counted_in_metrics: bool = False
    verified_one_shot: bool = False
    verified_local_finish: bool = False
    tickets: list[TicketTrialSnapshot] = field(default_factory=list)
    cloud_cost_usd: float = 0.0
    cloud_input_tokens: int = 0
    cloud_output_tokens: int = 0
    local_inference_sec: float = 0.0
    decompose_sec: float = 0.0
    total_duration_sec: float = 0.0
    skipped_reason: str = ""
    error: str = ""
    baseline_check_output: str = ""
    dod_check_output: str = ""
    existing_tests_output: str = ""
    target_file_sizes: dict[str, int] = field(default_factory=dict)
    target_file_tokens: dict[str, int] = field(default_factory=dict)
    required_output_tokens: dict[str, int] = field(default_factory=dict)
    oversized_target_files: list[str] = field(default_factory=list)
    target_file_size_limit_bytes: int = DEFAULT_MAX_TARGET_FILE_BYTES
    max_output_tokens: int = DEFAULT_LOCAL_MAX_OUTPUT_TOKENS
    patch_mode_threshold_tokens: int = DEFAULT_PATCH_MODE_THRESHOLD_TOKENS


def expand_path(raw: str) -> Path:
    return Path(raw.strip().lstrip("<").rstrip(">")).expanduser().resolve()


def benchmark_python() -> str:
    py312 = PROJECT_ROOT / ".venv" / "bin" / "python3.12"
    if py312.is_file():
        return str(py312)
    return sys.executable


def repo_test_env(repo_path: Path, repo_def: RepoDefinition | None = None) -> dict[str, str]:
    env = dict(os.environ)
    src = repo_path / "src"
    if src.is_dir():
        prefix = str(src)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = prefix if not existing else f"{prefix}{os.pathsep}{existing}"
    if repo_def is not None and repo_def.name == "tablib":
        env["PYTEST_ADDOPTS"] = ""
    return env


def normalize_pytest_command(command: str, repo_def: RepoDefinition | None = None) -> str:
    raw = command.strip()
    if not raw.startswith("pytest "):
        return raw
    args = raw[len("pytest ") :]
    parts = [benchmark_python(), "-m", "pytest"]
    if repo_def is not None and repo_def.name == "tablib":
        parts.extend(["-o", "addopts="])
    return " ".join(parts) + " " + args


def ensure_tablib_test_deps() -> None:
    subprocess.run(
        [
            benchmark_python(),
            "-m",
            "pip",
            "install",
            "-q",
            "odfpy",
            "openpyxl>=2.6.0",
            "pyyaml",
            "tabulate",
            "xlrd",
            "xlwt",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def ensure_marshmallow_test_deps() -> None:
    subprocess.run(
        [
            benchmark_python(),
            "-m",
            "pip",
            "install",
            "-q",
            "packaging>=17.0",
            "pytest",
            "simplejson",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


class BenchmarkTestRunner(TestRunner):
    """TestRunner with pinned-repo PYTHONPATH and pytest invocation normalization."""

    def __init__(self, cwd: str | Path, repo_def: RepoDefinition) -> None:
        super().__init__(
            cwd=cwd,
            env=repo_test_env(Path(cwd), repo_def),
            execution_policy=ExecutionPolicy(
                env_allowlist=("PATH", "PYTHONPATH", "PYTEST_ADDOPTS"),
            ),
        )
        self.repo_def = repo_def

    def run_command_safe(self, command: str, **kwargs):  # type: ignore[no-untyped-def]
        if command.strip().startswith("pytest "):
            command = normalize_pytest_command(command, self.repo_def)
        return super().run_command_safe(command, **kwargs)


class BenchmarkExecutionLoop(ExecutionLoop):
    """ExecutionLoop whose DoD tests run inside the active worktree, not the main repo.

    The base `_test_runner_for` only rebinds the test runner when it is exactly a
    `TestRunner`; `BenchmarkTestRunner` is a subclass, so without this override the
    DoD would run with the cwd + PYTHONPATH bound at construction (the main repo)
    and never see the model's patch in the per-ticket worktree. This override binds
    the runner to the worktree the loop is executing in.
    """

    def __init__(self, *args, repo_def: RepoDefinition, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._repo_def = repo_def

    def _test_runner_for(self, cwd):  # type: ignore[no-untyped-def]
        return BenchmarkTestRunner(cwd=cwd, repo_def=self._repo_def)


def discover_active_task_ids(path: str | Path) -> set[str]:
    """Derive active R-102 IDs from reviewed assets and redesign exclusions."""
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    probes_root = manifest_path.parent / "r102_probes"
    patches_root = manifest_path.parent / "r102_reference_patches"
    probe_ids = {probe.stem for probe in probes_root.glob("*.py")}
    patch_ids = {patch.stem for patch in patches_root.glob("*.patch")}
    redesign_ids = {
        str(task["id"])
        for task in payload.get("excluded_tasks", [])
        if task.get("status") == "NEEDS_REDESIGN"
    }
    return (probe_ids & patch_ids) - redesign_ids


def load_manifest(path: str | Path) -> BenchmarkManifest:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    repos = {
        name: RepoDefinition(
            name=name,
            github=str(defn["github"]),
            ref=str(defn["ref"]),
            local_path=expand_path(defn["local_path"]),
            default_scope=list(defn.get("default_scope", [])),
        )
        for name, defn in payload["repos"].items()
    }
    tasks = [
        TaskDefinition(
            id=str(task["id"]),
            repo=str(task["repo"]),
            category=str(task["category"]),
            requirement=str(task["requirement"]),
            dod=str(task["dod"]),
            existing_tests=str(task["existing_tests"]),
            target_files=[str(path) for path in task["target_files"]],
            patch_mode_eligible=bool(task.get("patch_mode_eligible", False)),
        )
        for task in payload["tasks"]
    ]
    probes_root = manifest_path.parent / "r102_probes"
    patches_root = manifest_path.parent / "r102_reference_patches"
    if probes_root.is_dir() and patches_root.is_dir():
        expected_ids = discover_active_task_ids(manifest_path)
        actual_ids = {task.id for task in tasks}
        if actual_ids != expected_ids:
            missing = sorted(expected_ids - actual_ids)
            unexpected = sorted(actual_ids - expected_ids)
            raise ValueError(
                "Manifest active tasks do not match the probe/reference-patch rule: "
                f"missing={missing}, unexpected={unexpected}"
            )
    return BenchmarkManifest(version=int(payload.get("version", 1)), repos=repos, tasks=tasks)


def classify_trial(status: str, attempts: int) -> str:
    if status not in LOCAL_FINISH_STATES:
        return "blocked"
    return "one_shot" if attempts == 0 else "retry_then_pass"


def is_mechanical_failure(*, outcome: str, test_output: str, diff: str, error: str = "") -> bool:
    if outcome == "error" or error:
        return True
    if not (diff or "").strip():
        return True
    haystack = f"{test_output}\n{error}".lower()
    return any(marker.lower() in haystack for marker in MECHANICAL_MARKERS)


def classify_requirement_result(snapshots: list[TicketTrialSnapshot]) -> tuple[str, bool]:
    if not snapshots:
        return "error", True
    if any(snapshot.result == "error" for snapshot in snapshots):
        return "error", True
    if any(snapshot.result == "blocked" for snapshot in snapshots):
        mechanical = any(snapshot.mechanical_failure for snapshot in snapshots)
        return "blocked", mechanical
    if any(snapshot.result == "retry_then_pass" for snapshot in snapshots):
        mechanical = any(snapshot.mechanical_failure for snapshot in snapshots)
        return "retry_then_pass", mechanical
    mechanical = any(snapshot.mechanical_failure for snapshot in snapshots)
    return "one_shot", mechanical


def aggregate_trial_results(trials: list[RequirementTrialResult]) -> dict[str, Any]:
    excluded_baseline = sum(
        1 for trial in trials if trial.result == "excluded_baseline_passed"
    )
    counted = [trial for trial in trials if trial.counted_in_metrics]
    total_counted = len(counted)
    one_shot = sum(1 for trial in counted if trial.verified_one_shot)
    local_finish = sum(1 for trial in counted if trial.verified_local_finish)
    escalated = sum(1 for trial in counted if trial.result == "blocked")
    mechanical = sum(1 for trial in counted if trial.mechanical_failure)
    errors = sum(1 for trial in trials if trial.result == "error")
    size_excluded = sum(1 for trial in trials if trial.result == "size_excluded")
    infra_errors = sum(
        1 for trial in trials if trial.result in {"infra_error", "size_excluded"}
    )
    baseline_failed_first = sum(1 for trial in trials if trial.baseline_failed_first)
    existing_green = sum(1 for trial in counted if trial.existing_tests_still_green)
    local_durations = [trial.local_inference_sec for trial in counted if trial.local_inference_sec > 0]
    median_local_inference_sec = (
        round(statistics.median(local_durations), 2) if local_durations else 0.0
    )
    total_cloud_cost_usd = round(sum(trial.cloud_cost_usd for trial in counted), 4)
    return {
        "trials_total": len(trials),
        "trials_counted": total_counted,
        "trials_excluded_baseline_passed": excluded_baseline,
        "baseline_failed_first": baseline_failed_first,
        "existing_tests_still_green": existing_green,
        "one_shot": one_shot,
        "local_finish": local_finish,
        "escalated": escalated,
        "mechanical_failures": mechanical,
        "errors": errors,
        "infra_errors": infra_errors,
        "size_excluded": size_excluded,
        "one_shot_rate": round(one_shot / total_counted, 4) if total_counted else 0.0,
        "local_finish_rate": round(local_finish / total_counted, 4) if total_counted else 0.0,
        "escalation_rate": round(escalated / total_counted, 4) if total_counted else 0.0,
        "mechanical_failure_rate": round(mechanical / total_counted, 4) if total_counted else 0.0,
        "baseline_failed_first_rate": round(baseline_failed_first / len(trials), 4) if trials else 0.0,
        "existing_tests_still_green_rate": (
            round(existing_green / total_counted, 4) if total_counted else 0.0
        ),
        "median_local_inference_sec": median_local_inference_sec,
        "total_cloud_cost_usd": total_cloud_cost_usd,
        # Backward-compatible alias for older report readers.
        "trials": total_counted,
    }


def probe_dest_relative(task_id: str) -> str:
    safe = task_id.replace("-", "")
    return f"tests/haao_r102_{safe}_probe.py"


def measure_target_file_sizes(repo_path: Path, tickets: list[Ticket]) -> dict[str, int]:
    """Record source sizes for a trial without following paths outside the repo."""
    root = repo_path.resolve()
    sizes: dict[str, int] = {}
    for ticket in tickets:
        for raw_target in ticket.task.target_files:
            target = (root / raw_target).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                sizes[raw_target] = -1
                continue
            sizes[raw_target] = target.stat().st_size
    return sizes


def measure_target_file_tokens(repo_path: Path, target_files: list[str]) -> dict[str, int]:
    """Estimate target tokens using the same heuristic as execution dispatch."""
    root = repo_path.resolve()
    tokens: dict[str, int] = {}
    for raw_target in target_files:
        target = (root / raw_target).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            tokens[raw_target] = -1
            continue
        tokens[raw_target] = estimate_tokens(target.read_text(encoding="utf-8"))
    return tokens


def restrict_tickets_to_task_targets(
    tickets: list[Ticket],
    task: TaskDefinition,
) -> list[Ticket]:
    """Make benchmark dispatch source-only even if decomposition suggests tests."""
    restricted: list[Ticket] = []
    constraint = (
        "Modify only the listed implementation files; do not add or modify any "
        "test files. Validation is handled externally."
    )
    for ticket in tickets:
        payload = ticket.to_dict()
        payload["task"]["target_files"] = list(task.target_files)
        constraints = payload["task"].setdefault("constraints", [])
        if constraint not in constraints:
            constraints.append(constraint)
        restricted.append(Ticket.from_dict(payload))
    return restricted


def probe_source_path(task_id: str, probes_root: Path | None = None) -> Path:
    root = probes_root or DEFAULT_PROBES_ROOT
    return root / f"{task_id}.py"


def install_probe(repo_path: Path, task_id: str, probes_root: Path | None = None) -> Path:
    source = probe_source_path(task_id, probes_root)
    if not source.is_file():
        raise FileNotFoundError(f"Missing harness probe for {task_id}: {source}")
    destination = repo_path / probe_dest_relative(task_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def _format_command_output(result) -> str:
    chunks = [
        f"$ {result.command}",
        f"status={result.status} expected={result.expect}",
        f"return_code={result.return_code}",
        "stdout:",
        result.stdout,
        "stderr:",
        result.stderr,
    ]
    return "\n".join(chunks)


def run_expectation(
    repo_path: Path,
    command: str,
    *,
    expect: str,
    repo_def: RepoDefinition,
    timeout_sec: int = 300,
) -> tuple[bool, str]:
    runner = BenchmarkTestRunner(cwd=repo_path, repo_def=repo_def)
    normalized = (
        normalize_pytest_command(command, repo_def)
        if command.strip().startswith("pytest ")
        else command
    )
    result = runner.run_command_safe(
        normalized,
        expect=expect,  # type: ignore[arg-type]
        timeout_sec=timeout_sec,
    )
    return result.status == "pass", _format_command_output(result)


def check_baseline_failed_first(
    repo_path: Path,
    dod_command: str,
    *,
    repo_def: RepoDefinition | None = None,
) -> tuple[bool, str]:
    """Return True when the probe DoD fails on the clean baseline (expected)."""
    passed, output = run_expectation(
        repo_path,
        dod_command,
        expect="fail",
        repo_def=repo_def,
    )
    return passed, output


def apply_local_finish_diffs(
    repo_path: Path,
    repository: TicketRepository,
    workspace_guard: GitWorkspaceGuard,
) -> None:
    ordered = sorted(
        repository.list(),
        key=lambda ticket: ticket.id,
    )
    for ticket in ordered:
        if TicketStatus(ticket.status) not in LOCAL_FINISH_STATES:
            continue
        diff = ticket.result.diff if ticket.result and ticket.result.diff else ""
        if not diff.strip():
            continue
        workspace_guard.apply_unified_diff(diff, ticket.task.target_files)


def finalize_verified_result(result: RequirementTrialResult) -> None:
    result.counted_in_metrics = result.baseline_failed_first
    if not result.counted_in_metrics:
        return
    verified_finish = (
        result.dod_passed_after
        and result.existing_tests_still_green
        and result.result in {"one_shot", "retry_then_pass"}
    )
    result.verified_local_finish = verified_finish
    result.verified_one_shot = (
        verified_finish and result.result == "one_shot"
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        shell=False,
    )


def ensure_clean_repo(repo: Path) -> None:
    status = git(repo, "status", "--porcelain").stdout.strip()
    if status:
        raise RuntimeError(f"Repo is dirty after reset: {repo}\n{status}")


def reset_repo_to_baseline(repo: Path, ref: str) -> None:
    checkout = git(repo, "checkout", "--force", ref)
    if checkout.returncode != 0:
        raise RuntimeError(f"git checkout failed for {repo}@{ref}: {checkout.stderr.strip()}")
    reset = git(repo, "reset", "--hard", ref)
    if reset.returncode != 0:
        raise RuntimeError(f"git reset failed for {repo}@{ref}: {reset.stderr.strip()}")
    git(repo, "clean", "-fd")


def clone_repo(defn: RepoDefinition) -> Path:
    defn.local_path.parent.mkdir(parents=True, exist_ok=True)
    if defn.local_path.exists():
        return defn.local_path
    clone = subprocess.run(
        ["git", "clone", defn.github, str(defn.local_path)],
        capture_output=True,
        text=True,
        shell=False,
    )
    if clone.returncode != 0:
        raise RuntimeError(f"git clone failed for {defn.github}: {clone.stderr.strip()}")
    reset_repo_to_baseline(defn.local_path, defn.ref)
    return defn.local_path


def ensure_benchmark_repo(defn: RepoDefinition) -> Path:
    if not defn.local_path.exists():
        path = clone_repo(defn)
    elif not (defn.local_path / ".git").is_dir():
        raise RuntimeError(f"Path exists but is not a git repo: {defn.local_path}")
    else:
        reset_repo_to_baseline(defn.local_path, defn.ref)
        path = defn.local_path
    if defn.name == "tablib":
        ensure_tablib_test_deps()
    elif defn.name == "marshmallow":
        ensure_marshmallow_test_deps()
    return path


def _snapshot_ticket(ticket_dict: dict) -> TicketTrialSnapshot:
    execution = ticket_dict.get("execution", {})
    result = ticket_dict.get("result", {}) or {}
    status = str(ticket_dict.get("status", ""))
    attempts = int(execution.get("attempts") or 0)
    outcome = str(result.get("outcome") or "")
    test_output = str(result.get("test_output") or "")
    diff = str(result.get("diff") or "")
    classified = classify_trial(status, attempts)
    mechanical = is_mechanical_failure(
        outcome=outcome,
        test_output=test_output,
        diff=diff,
    )
    return TicketTrialSnapshot(
        ticket_id=str(ticket_dict.get("id", "")),
        status=status,
        attempts=attempts,
        outcome=outcome,
        test_output=test_output,
        diff=diff,
        result=classified,
        mechanical_failure=mechanical,
    )


def _auto_approve_diffs(repository: TicketRepository, diff_review: DiffReviewService) -> list[str]:
    approved: list[str] = []
    for ticket in repository.list(status=TicketStatus.DIFF_PENDING):
        try:
            diff_review.approve_diff(ticket.id)
            approved.append(ticket.id)
        except Exception:
            continue
    return approved


def _auto_merge_accepted(
    repository: TicketRepository,
    state_service: TicketStateService,
    git_flow: GitTicketFlow,
) -> list[str]:
    merged: list[str] = []
    for ticket in repository.list(status=TicketStatus.AWAITING_ACCEPTANCE):
        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        if metadata.get("git_branch") and not metadata.get("git_merge_commit"):
            try:
                merge_result = git_flow.merge_ticket_branch(ticket)
                ticket_json = ticket.to_dict()
                ticket_json.setdefault("metadata", {})["git_merge_commit"] = merge_result.merge_commit
                repository.save(Ticket.from_dict(ticket_json))
            except Exception:
                continue
        state_service.move(ticket.id, TicketStatus.DONE)
        merged.append(ticket.id)
    return merged


def drive_requirement_pipeline(
    *,
    repository: TicketRepository,
    orchestrator: AutoOrchestrator,
    diff_review: DiffReviewService,
    state_service: TicketStateService,
    git_flow: GitTicketFlow,
    max_cycles: int = 40,
) -> float:
    """Run execute → diff gate → audit until idle. Returns local inference seconds."""
    local_inference_sec = 0.0
    for _ in range(max_cycles):
        _auto_approve_diffs(repository, diff_review)
        _auto_merge_accepted(repository, state_service, git_flow)

        started = time.monotonic()
        result = orchestrator.run_once()
        elapsed = time.monotonic() - started
        if result.executed_ticket_ids:
            local_inference_sec += elapsed

        if result.idle and not result.skipped_reason:
            break
        if result.skipped_reason in {"dependencies_pending", "workspace_dirty"}:
            break
        if not (
            result.executed_ticket_ids
            or result.reviewed_ticket_ids
            or result.escalated_ticket_ids
            or result.recovered_ticket_ids
        ):
            break
    return round(local_inference_sec, 2)


@dataclass
class BaselineProbeResult:
    task_id: str
    repo: str
    category: str
    baseline_failed_first: bool
    existing_tests_still_green: bool
    baseline_check_output: str = ""
    existing_tests_output: str = ""
    error: str = ""


def run_baseline_probe_check(
    *,
    task: TaskDefinition,
    repo_def: RepoDefinition,
) -> BaselineProbeResult:
    """Probe + existing_tests on a clean pinned baseline (no model calls)."""
    result = BaselineProbeResult(
        task_id=task.id,
        repo=task.repo,
        category=task.category,
        baseline_failed_first=False,
        existing_tests_still_green=False,
    )
    repo_path: Path | None = None
    try:
        repo_path = ensure_benchmark_repo(repo_def)
        reset_repo_to_baseline(repo_path, repo_def.ref)
        install_probe(repo_path, task.id)
        baseline_ok, baseline_output = check_baseline_failed_first(
            repo_path,
            task.dod,
            repo_def=repo_def,
        )
        result.baseline_check_output = baseline_output
        result.baseline_failed_first = baseline_ok
        existing_ok, existing_output = run_expectation(
            repo_path,
            task.existing_tests,
            expect="pass",
            repo_def=repo_def,
        )
        result.existing_tests_output = existing_output
        result.existing_tests_still_green = existing_ok
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
    finally:
        if repo_path is not None:
            try:
                reset_repo_to_baseline(repo_path, repo_def.ref)
                ensure_clean_repo(repo_path)
            except Exception:
                pass
    return result


def repo_checkout_summary(repo_def: RepoDefinition) -> dict[str, str]:
    repo_path = ensure_benchmark_repo(repo_def)
    head = git(repo_path, "rev-parse", "HEAD").stdout.strip()
    describe = git(repo_path, "describe", "--tags", "--exact-match").stdout.strip()
    pinned_sha = git(repo_path, "rev-parse", f"{repo_def.ref}^{{commit}}").stdout.strip()
    return {
        "repo": repo_def.name,
        "path": str(repo_path),
        "pinned_ref": repo_def.ref,
        "pinned_sha": pinned_sha,
        "head_sha": head,
        "exact_tag": describe,
        "matches_pin": str(head == pinned_sha),
    }


def run_requirement_trial(
    *,
    task: TaskDefinition,
    repo_def: RepoDefinition,
    settings: Settings,
    trial: int,
    local_model: str,
    local_timeout_sec: float = 900.0,
    max_target_file_bytes: int = DEFAULT_MAX_TARGET_FILE_BYTES,
    max_output_tokens: int = DEFAULT_LOCAL_MAX_OUTPUT_TOKENS,
    patch_mode_threshold_tokens: int = DEFAULT_PATCH_MODE_THRESHOLD_TOKENS,
) -> RequirementTrialResult:
    repo_path = ensure_benchmark_repo(repo_def)
    started_at = time.monotonic()
    result = RequirementTrialResult(
        task_id=task.id,
        trial=trial,
        repo=task.repo,
        category=task.category,
        result="error",
        mechanical_failure=False,
        ticket_count=0,
        max_attempts=0,
        target_file_size_limit_bytes=max_target_file_bytes,
        max_output_tokens=max_output_tokens,
        patch_mode_threshold_tokens=patch_mode_threshold_tokens,
    )

    db_path = Path(tempfile.mkstemp(suffix=".sqlite3", prefix=f"haao-r102-{task.id}-")[1])
    connection = connect(db_path)
    repository = TicketRepository(connection)
    requirement_repository = RequirementRepository(connection)
    settings_repository = SettingsRepository(connection)
    state_service = TicketStateService(repository)
    workspace_guard = GitWorkspaceGuard(repo_path)
    git_flow = GitTicketFlow(repo_path, workspace_guard=workspace_guard)

    tech_lead = ClaudeTechLeadClient(settings.claude_api_key, model=settings.claude_model)
    lmstudio = LMStudioClient(settings.lmstudio_base_url, timeout_sec=local_timeout_sec)
    try:
        reset_repo_to_baseline(repo_path, repo_def.ref)
        install_probe(repo_path, task.id)
        git(repo_path, "add", "-A")
        committed = git(repo_path, "commit", "-q", "-m", "harness: r102 probe (throwaway)")
        if committed.returncode != 0:
            raise RuntimeError(f"probe commit failed: {committed.stderr.strip()}")
        baseline_ok, baseline_output = check_baseline_failed_first(
            repo_path,
            task.dod,
            repo_def=repo_def,
        )
        result.baseline_check_output = baseline_output
        result.baseline_failed_first = baseline_ok
        if not baseline_ok:
            result.result = "excluded_baseline_passed"
            result.skipped_reason = "baseline_probe_already_passing"
            result.total_duration_sec = round(time.monotonic() - started_at, 2)
            return result

        requirement_service = RequirementService(
            repository,
            requirement_repository,
            tech_lead,
            repo_root=repo_path,
            settings_repository=settings_repository,
        )
        decompose_started = time.monotonic()
        preview = requirement_service.decompose_preview(
            Requirement(
                id=requirement_service.next_requirement_id(),
                prompt=task.requirement,
                repo=str(repo_path),
                scope_paths=task.target_files,
                acceptance_notes=(
                    f"Definition of done: {task.dod}. "
                    f"Existing tests must stay green: {task.existing_tests}."
                ),
                allow_new_files=False,
            )
        )
        result.decompose_sec = round(time.monotonic() - decompose_started, 2)

        if not preview.proposed_tickets:
            result.result = "skipped"
            result.skipped_reason = "decompose_produced_no_tickets"
            result.total_duration_sec = round(time.monotonic() - started_at, 2)
            return result

        proposed_tickets = restrict_tickets_to_task_targets(
            preview.proposed_tickets,
            task,
        )

        result.target_file_sizes = measure_target_file_sizes(
            repo_path,
            proposed_tickets,
        )
        result.oversized_target_files = sorted(
            path
            for path, size in result.target_file_sizes.items()
            if size > max_target_file_bytes
        )
        if result.oversized_target_files:
            result.result = "infra_error"
            result.skipped_reason = "target_file_size_limit_exceeded"
            result.total_duration_sec = round(time.monotonic() - started_at, 2)
            return result

        result.target_file_tokens = measure_target_file_tokens(
            repo_path,
            task.target_files,
        )
        result.required_output_tokens = {
            path: required_rewrite_output_tokens(
                (repo_path / path).read_text(encoding="utf-8")
            )
            for path, token_count in result.target_file_tokens.items()
            if token_count >= 0
        }
        result.oversized_target_files = sorted(
            path
            for path, required in result.required_output_tokens.items()
            if required > min(patch_mode_threshold_tokens, max_output_tokens)
        )

        ticket_payloads: list[dict] = []
        for ticket in proposed_tickets:
            ticket_json = ticket.to_dict()
            execution = ticket_json.setdefault("execution", {})
            execution["assigned_model"] = local_model
            execution["retry_budget"] = 2
            ticket_payloads.append(ticket_json)

        confirmed = requirement_service.confirm(
            preview.requirement.id,
            ticket_payloads,
        )
        requirement = requirement_repository.get(confirmed.requirement.id)
        if requirement is not None:
            result.cloud_cost_usd = float(requirement.cloud_cost_usd)
            result.cloud_input_tokens = int(requirement.cloud_input_tokens)
            result.cloud_output_tokens = int(requirement.cloud_output_tokens)

        execution_loop = BenchmarkExecutionLoop(
            repository,
            state_service,
            lmstudio,
            repo_root=repo_path,
            workspace_guard=workspace_guard,
            settings_repository=settings_repository,
            max_output_tokens=max_output_tokens,
            patch_mode_threshold_tokens=patch_mode_threshold_tokens,
            repo_def=repo_def,
        )
        orchestrator = AutoOrchestrator(
            repository,
            execution_loop,
            ReviewService(
                repository,
                state_service,
                tech_lead,
                requirement_repository=requirement_repository,
                settings_repository=settings_repository,
            ),
            EscalationService(repository, tech_lead, settings_repository=settings_repository),
            repo_root=repo_path,
            workspace_guard=workspace_guard,
            allow_dirty_workspace=False,
        )
        diff_review = DiffReviewService(
            repository,
            state_service,
            repo_root=repo_path,
            workspace_guard=workspace_guard,
            git_flow=git_flow,
        )

        result.local_inference_sec = drive_requirement_pipeline(
            repository=repository,
            orchestrator=orchestrator,
            diff_review=diff_review,
            state_service=state_service,
            git_flow=git_flow,
        )

        requirement = requirement_repository.get(confirmed.requirement.id)
        if requirement is not None:
            result.cloud_cost_usd = float(requirement.cloud_cost_usd)
            result.cloud_input_tokens = int(requirement.cloud_input_tokens)
            result.cloud_output_tokens = int(requirement.cloud_output_tokens)

        snapshots = [
            _snapshot_ticket(ticket.to_dict())
            for ticket in repository.list()
        ]
        result.tickets = snapshots
        result.ticket_count = len(snapshots)
        result.max_attempts = max((snapshot.attempts for snapshot in snapshots), default=0)
        classified, mechanical = classify_requirement_result(snapshots)
        result.result = classified
        result.mechanical_failure = mechanical

        reset_repo_to_baseline(repo_path, repo_def.ref)
        install_probe(repo_path, task.id)
        apply_local_finish_diffs(repo_path, repository, workspace_guard)

        dod_ok, dod_output = run_expectation(
            repo_path,
            task.dod,
            expect="pass",
            repo_def=repo_def,
        )
        result.dod_check_output = dod_output
        result.dod_passed_after = dod_ok

        existing_ok, existing_output = run_expectation(
            repo_path,
            task.existing_tests,
            expect="pass",
            repo_def=repo_def,
        )
        result.existing_tests_output = existing_output
        result.existing_tests_still_green = existing_ok

        finalize_verified_result(result)
    except LMStudioError as exc:
        result.result = "infra_error"
        result.error = str(exc)
        result.mechanical_failure = True
    except Exception as exc:  # noqa: BLE001 - benchmark records all failures
        result.result = "error"
        result.error = str(exc)
        result.mechanical_failure = True
    finally:
        tech_lead.close()
        lmstudio.close()
        connection.close()
        db_path.unlink(missing_ok=True)
        reset_repo_to_baseline(repo_path, repo_def.ref)
        ensure_clean_repo(repo_path)
        result.total_duration_sec = round(time.monotonic() - started_at, 2)

    return result
