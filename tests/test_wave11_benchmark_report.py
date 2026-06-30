from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from orchestrator.benchmark_runner import RepoDefinition, RequirementTrialResult, TaskDefinition
from scripts.wave4_real_repo_benchmark_report import _markdown_report, _select_tasks, build_report


def test_wave11_report_runs_full_fake_task_set_and_renders_markdown(tmp_path: Path) -> None:
    tasks = [
        _task("A-01", "alpha"),
        _task("A-02", "alpha"),
        _task("B-01", "beta"),
    ]
    manifest = SimpleNamespace(
        repos={
            "alpha": RepoDefinition(
                name="alpha",
                github="https://example.invalid/alpha.git",
                ref="v1",
                local_path=tmp_path / "alpha",
                default_scope=[],
            ),
            "beta": RepoDefinition(
                name="beta",
                github="https://example.invalid/beta.git",
                ref="v1",
                local_path=tmp_path / "beta",
                default_scope=[],
            ),
        },
        tasks=tasks,
    )
    calls: list[tuple[str, int]] = []

    def fake_runner(**kwargs):
        task = kwargs["task"]
        trial = kwargs["trial"]
        calls.append((task.id, trial))
        if task.id == "A-02" and trial == 1:
            return _trial(task, trial, result="blocked", one_shot=False, local_finish=False)
        if task.id == "A-02" and trial == 2:
            return _trial(
                task,
                trial,
                result="infra_error",
                counted=False,
                one_shot=False,
                local_finish=False,
                error="LM Studio unavailable",
            )
        return _trial(task, trial, result="one_shot", one_shot=True, local_finish=True)

    report = build_report(
        manifest=manifest,
        manifest_path=tmp_path / "fake_manifest.json",
        tasks=_select_tasks(manifest, "", one_per_repo=False),
        settings=SimpleNamespace(claude_model="claude-test"),
        trials_per_task=2,
        local_model="fake-local",
        local_timeout_sec=1,
        max_target_file_bytes=100,
        max_output_tokens=256,
        patch_mode_threshold_tokens=128,
        trial_runner=fake_runner,
        generated_at="2026-06-29T00:00:00+00:00",
    )

    assert calls == [
        ("A-01", 1),
        ("A-01", 2),
        ("A-02", 1),
        ("A-02", 2),
        ("B-01", 1),
        ("B-01", 2),
    ]
    assert report["task_count"] == 3
    assert report["summary"]["trials_total"] == 6
    assert report["summary"]["trials_counted"] == 5
    assert report["summary"]["infra_errors"] == 1
    assert report["summary"]["trial_group_stats"]["one_shot_rate"]["range"] == 0.3333
    assert set(report["by_task"]) == {"A-01", "A-02", "B-01"}
    assert report["by_task"]["A-02"]["trials_counted"] == 1
    assert report["by_repo"]["alpha"]["trials_counted"] == 3

    markdown = _markdown_report(report)
    assert "# Wave 11 Real-Repo Benchmark Proof" in markdown
    assert "## By Task" in markdown
    assert "## By Repo" in markdown
    assert "## Exclusions / Infra Errors" in markdown
    assert "trial mean" in markdown
    assert "LM Studio unavailable" in markdown


def test_wave11_select_tasks_defaults_to_all_and_keeps_legacy_one_per_repo(tmp_path: Path) -> None:
    manifest = SimpleNamespace(
        tasks=[
            _task("A-01", "alpha"),
            _task("A-02", "alpha"),
            _task("B-01", "beta"),
        ],
    )

    assert [task.id for task in _select_tasks(manifest, "", one_per_repo=False)] == [
        "A-01",
        "A-02",
        "B-01",
    ]
    assert [task.id for task in _select_tasks(manifest, "", one_per_repo=True)] == ["A-01", "B-01"]
    assert [task.id for task in _select_tasks(manifest, "A-02", one_per_repo=True)] == ["A-02"]


def _task(task_id: str, repo: str) -> TaskDefinition:
    return TaskDefinition(
        id=task_id,
        repo=repo,
        category="bugfix",
        requirement=f"Fix {task_id}",
        dod="pytest -q tests/probe.py",
        existing_tests="pytest -q",
        target_files=["pkg/example.py"],
    )


def _trial(
    task: TaskDefinition,
    trial: int,
    *,
    result: str,
    counted: bool = True,
    one_shot: bool,
    local_finish: bool,
    error: str = "",
) -> RequirementTrialResult:
    return RequirementTrialResult(
        task_id=task.id,
        trial=trial,
        repo=task.repo,
        category=task.category,
        result=result,
        mechanical_failure=False,
        ticket_count=1 if counted else 0,
        max_attempts=0,
        baseline_failed_first=True,
        existing_tests_still_green=local_finish,
        dod_passed_after=local_finish,
        counted_in_metrics=counted,
        verified_one_shot=one_shot,
        verified_local_finish=local_finish,
        cloud_cost_usd=0.01 if counted else 0.0,
        cloud_input_tokens=10 if counted else 0,
        cloud_output_tokens=5 if counted else 0,
        local_inference_sec=float(10 * trial) if counted else 0.0,
        total_duration_sec=float(20 * trial),
        error=error,
    )
