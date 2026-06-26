from __future__ import annotations

from orchestrator.models.ticket import Ticket
from orchestrator.benchmark_runner import (
    RequirementTrialResult,
    TicketTrialSnapshot,
    aggregate_trial_results,
    check_baseline_failed_first,
    classify_requirement_result,
    classify_trial,
    discover_active_task_ids,
    finalize_verified_result,
    install_probe,
    is_mechanical_failure,
    load_manifest,
    measure_target_file_sizes,
    probe_dest_relative,
)


def test_classify_trial_matches_measure_oneshot() -> None:
    assert classify_trial("diff_pending", 0) == "one_shot"
    assert classify_trial("diff_pending", 2) == "retry_then_pass"
    assert classify_trial("blocked", 4) == "blocked"


def test_mechanical_failure_detects_empty_diff_and_write_errors() -> None:
    assert is_mechanical_failure(outcome="test_failed", test_output="", diff="")
    assert is_mechanical_failure(
        outcome="error",
        test_output="Local model returned empty file content",
        diff="diff",
    )
    assert not is_mechanical_failure(
        outcome="test_failed",
        test_output="AssertionError: expected 2",
        diff="diff --git a/x b/x",
    )


def test_classify_requirement_result_all_one_shot() -> None:
    snapshots = [
        TicketTrialSnapshot(
            ticket_id="T-001",
            status="diff_pending",
            attempts=0,
            outcome="success",
            test_output="ok",
            diff="diff",
            result="one_shot",
            mechanical_failure=False,
        )
    ]
    assert classify_requirement_result(snapshots) == ("one_shot", False)


def test_manifest_loads_only_reviewed_active_tasks() -> None:
    manifest_path = "benchmarks/r102_manifest.json"
    manifest = load_manifest(manifest_path)

    assert manifest.version == 8
    assert {task.id for task in manifest.tasks} == discover_active_task_ids(manifest_path)
    for task in manifest.tasks:
        assert task.existing_tests.startswith("pytest")
        assert "haao_r102" in task.dod
        assert probe_dest_relative(task.id) in task.dod
        assert task.target_files
        assert all("test" not in path.lower() for path in task.target_files)
        assert "do not add or modify any test files" in task.requirement


def test_probe_dest_relative() -> None:
    assert probe_dest_relative("C-01") == "tests/haao_r102_C01_probe.py"


def test_finalize_verified_result_requires_baseline_and_existing_green() -> None:
    trial = RequirementTrialResult(
        task_id="C-01",
        trial=1,
        repo="click",
        category="bugfix",
        result="one_shot",
        mechanical_failure=False,
        ticket_count=1,
        max_attempts=0,
        baseline_failed_first=True,
        dod_passed_after=True,
        existing_tests_still_green=True,
    )
    finalize_verified_result(trial)
    assert trial.counted_in_metrics is True
    assert trial.verified_one_shot is True
    assert trial.verified_local_finish is True

    trial.result = "one_shot"
    trial.existing_tests_still_green = False
    finalize_verified_result(trial)
    assert trial.verified_one_shot is False


def test_aggregate_excludes_baseline_passing_trials_from_rates() -> None:
    trials = [
        RequirementTrialResult(
            task_id="C-01",
            trial=1,
            repo="click",
            category="bugfix",
            result="excluded_baseline_passed",
            mechanical_failure=False,
            ticket_count=0,
            max_attempts=0,
            baseline_failed_first=False,
        ),
        RequirementTrialResult(
            task_id="C-02",
            trial=1,
            repo="click",
            category="bugfix",
            result="one_shot",
            mechanical_failure=False,
            ticket_count=1,
            max_attempts=0,
            baseline_failed_first=True,
            counted_in_metrics=True,
            verified_one_shot=True,
            verified_local_finish=True,
            existing_tests_still_green=True,
            dod_passed_after=True,
        ),
    ]
    summary = aggregate_trial_results(trials)
    assert summary["trials_total"] == 2
    assert summary["trials_counted"] == 1
    assert summary["trials_excluded_baseline_passed"] == 1
    assert summary["one_shot"] == 1
    assert summary["one_shot_rate"] == 1.0


def test_aggregate_excludes_infra_errors_from_one_shot_denominator() -> None:
    trials = [
        RequirementTrialResult(
            task_id="C-01",
            trial=1,
            repo="click",
            category="bugfix",
            result="infra_error",
            mechanical_failure=True,
            ticket_count=0,
            max_attempts=0,
            baseline_failed_first=True,
        )
    ]
    summary = aggregate_trial_results(trials)
    assert summary["infra_errors"] == 1
    assert summary["trials_counted"] == 0
    assert summary["one_shot_rate"] == 0.0


def test_measure_target_file_sizes_records_missing_and_existing(tmp_path, fresh_ticket_dict) -> None:
    (tmp_path / "small.py").write_text("1234", encoding="utf-8")
    payload = fresh_ticket_dict.copy()
    payload["task"] = payload["task"].copy()
    payload["task"]["target_files"] = ["small.py", "missing.py"]
    sizes = measure_target_file_sizes(tmp_path, [Ticket.from_dict(payload)])
    assert sizes == {"small.py": 4, "missing.py": -1}


def test_install_probe_writes_expected_path(tmp_path) -> None:
    probes_root = tmp_path / "probes"
    probes_root.mkdir()
    (probes_root / "C-01.py").write_text("def test_x(): assert False\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    dest = install_probe(repo, "C-01", probes_root=probes_root)
    assert dest.name == "haao_r102_C01_probe.py"
    assert "assert False" in dest.read_text(encoding="utf-8")


def test_check_baseline_failed_first_on_failing_probe(tmp_path) -> None:
    probes_root = tmp_path / "probes"
    probes_root.mkdir()
    (probes_root / "C-99.py").write_text(
        "def test_haao_probe(): assert False\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    install_probe(repo, "C-99", probes_root=probes_root)
    ok, _output = check_baseline_failed_first(repo, "pytest -q tests/haao_r102_C99_probe.py")
    assert ok is True
