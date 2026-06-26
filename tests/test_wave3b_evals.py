from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator import evals
from orchestrator.api import get_settings
from orchestrator.benchmark_runner import (
    BenchmarkManifest,
    RepoDefinition,
    RequirementTrialResult,
)
from orchestrator.db.sqlite import EvalRunRepository, connect
from orchestrator.evals import EvalService
from orchestrator.main import app


def test_eval_task_set_listing_uses_manifest(tmp_path: Path) -> None:
    service = EvalService(EvalRunRepository(connect(tmp_path / "haao.sqlite3")))
    service._load_manifest = lambda: _manifest(tmp_path)  # type: ignore[method-assign]

    task_sets = service.list_task_sets()

    assert [task_set.id for task_set in task_sets] == ["r102-active", "r102-smoke"]
    assert task_sets[0].task_ids == ["T-1", "T-2"]
    assert task_sets[1].task_ids == ["T-1"]


def test_eval_run_stores_summary_and_api_lists_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    monkeypatch.setattr(EvalService, "_load_manifest", lambda self: _manifest(tmp_path))
    monkeypatch.setattr(evals, "run_requirement_trial", _fake_runner(["one_shot"]))
    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        client = TestClient(app)
        created = client.post(
            "/api/evals/run",
            json={"model_id": "qwen3-coder-next", "task_set_id": "r102-smoke", "trials": 1},
        )
        assert created.status_code == 200
        run = created.json()["eval_run"]
        assert run["status"] == "running"

        listed = client.get("/api/evals?model_id=qwen3-coder-next&task_set_id=r102-smoke").json()["eval_runs"]
        assert listed[0]["status"] == "completed"
        assert listed[0]["summary"]["summary"]["one_shot_rate"] == 1.0
        assert listed[0]["summary"]["summary"]["pass_rate"] == 1.0
        assert listed[0]["summary"]["baseline"]["reason"] == "no_baseline"
        assert listed[0]["regressed"] is False
    finally:
        app.dependency_overrides.clear()


def test_eval_run_flags_regression_against_previous_baseline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    monkeypatch.setattr(EvalService, "_load_manifest", lambda self: _manifest(tmp_path))
    monkeypatch.setattr(evals, "run_requirement_trial", _fake_runner(["one_shot", "blocked"]))
    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        client = TestClient(app)
        first = client.post(
            "/api/evals/run",
            json={"model_id": "qwen3-coder-next", "task_set_id": "r102-smoke", "trials": 1},
        ).json()["eval_run"]
        second = client.post(
            "/api/evals/run",
            json={"model_id": "qwen3-coder-next", "task_set_id": "r102-smoke", "trials": 1},
        ).json()["eval_run"]

        runs = client.get("/api/evals?model_id=qwen3-coder-next&task_set_id=r102-smoke").json()["eval_runs"]
        latest = runs[0]
        assert latest["id"] == second["id"]
        assert latest["status"] == "completed"
        assert latest["baseline_run_id"] == first["id"]
        assert latest["regressed"] is True
        assert latest["summary"]["baseline"]["diff"]["pass_rate"] == -1.0
    finally:
        app.dependency_overrides.clear()


def _manifest(tmp_path: Path) -> BenchmarkManifest:
    from orchestrator.benchmark_runner import TaskDefinition

    repo = RepoDefinition(
        name="fake",
        github="https://example.invalid/fake.git",
        ref="HEAD",
        local_path=tmp_path / "repo",
        default_scope=["calc.py"],
    )
    return BenchmarkManifest(
        version=1,
        repos={"fake": repo},
        tasks=[
            TaskDefinition(
                id="T-1",
                repo="fake",
                category="bugfix",
                requirement="Fix one",
                dod="pytest tests/test_one.py",
                existing_tests="pytest",
                target_files=["calc.py"],
            ),
            TaskDefinition(
                id="T-2",
                repo="fake",
                category="refactor",
                requirement="Fix two",
                dod="pytest tests/test_two.py",
                existing_tests="pytest",
                target_files=["calc.py"],
            ),
        ],
    )


def _fake_runner(results: list[str]):
    calls = {"count": 0}

    def fake_run_requirement_trial(**kwargs):
        index = min(calls["count"], len(results) - 1)
        calls["count"] += 1
        result_name = results[index]
        trial = RequirementTrialResult(
            task_id=kwargs["task"].id,
            trial=kwargs["trial"],
            repo=kwargs["task"].repo,
            category=kwargs["task"].category,
            result=result_name,
            mechanical_failure=False,
            ticket_count=1,
            max_attempts=0,
            baseline_failed_first=True,
            existing_tests_still_green=result_name != "blocked",
            dod_passed_after=result_name != "blocked",
            counted_in_metrics=True,
            verified_one_shot=result_name == "one_shot",
            verified_local_finish=result_name == "one_shot",
        )
        return trial

    return fake_run_requirement_trial


def _settings(db_path: Path):
    return SimpleNamespace(
        claude_api_key="test-key",
        openai_api_key="",
        gemini_api_key="",
        lmstudio_base_url="http://localhost:1234/v1",
        local_max_output_tokens=4096,
        local_patch_mode_threshold_tokens=2048,
        database_url=f"sqlite:///{db_path}",
        claude_model="claude-sonnet-4-6",
        haao_api_token="",
    )
