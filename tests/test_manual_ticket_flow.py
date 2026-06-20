from __future__ import annotations

import sys

import pytest

from orchestrator.context.injector import ContextInjector
from orchestrator.db.sqlite import ProjectRepository, TicketRepository, connect
from orchestrator.manual_ticket_flow import (
    ManualTicketCreatePayload,
    ManualTicketError,
    ManualTicketService,
    is_unverified_ticket,
)
from orchestrator.model_policy import local_execution_model
from tests.conftest import init_git_repo


def test_manual_ticket_service_creates_ready_ticket_with_context(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(repo)

    connection = connect(tmp_path / "haao.sqlite3")
    project_repository = ProjectRepository(connection)
    project = project_repository.create(name="Calc", path=repo)
    repository = TicketRepository(connection, project_id=project.id)

    service = ManualTicketService(
        repository,
        ContextInjector(repo),
        project_id=project.id,
    )
    created = service.create(
        ManualTicketCreatePayload(
            title="Increment add_one",
            type="bugfix",
            target_files=["calc.py"],
            task_description="Make add_one return value + 1",
            dod_tests=[f'{sys.executable} -c "import calc; assert calc.add_one(1) == 2"'],
            assigned_model="qwen3-coder-next",
            project_id=project.id,
        )
    )

    assert created.status == "ready"
    assert created.metadata.needs_approval is False
    assert created.metadata.human_authored is True
    assert is_unverified_ticket(created) is False
    assert created.context.files[0].path == "calc.py"
    assert "return value" in created.context.files[0].content
    assert created.execution.assigned_model == local_execution_model("qwen3-coder-next")


def test_manual_ticket_service_marks_unverified_without_dod_tests(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("x = 1\n", encoding="utf-8")
    init_git_repo(repo)

    connection = connect(tmp_path / "haao.sqlite3")
    project_repository = ProjectRepository(connection)
    project = project_repository.create(name="Calc", path=repo)
    repository = TicketRepository(connection, project_id=project.id)

    service = ManualTicketService(
        repository,
        ContextInjector(repo),
        project_id=project.id,
    )
    created = service.create(
        ManualTicketCreatePayload(
            title="Tweak constant",
            type="chore",
            target_files=["calc.py"],
            task_description="Set x to 2",
            project_id=project.id,
        )
    )

    assert is_unverified_ticket(created) is True
    assert created.metadata.unverified is True
    assert len(created.definition_of_done.tests) == 1


def test_manual_ticket_service_rejects_cloud_execution_model(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("x = 1\n", encoding="utf-8")
    init_git_repo(repo)

    connection = connect(tmp_path / "haao.sqlite3")
    project_repository = ProjectRepository(connection)
    project = project_repository.create(name="Calc", path=repo)
    repository = TicketRepository(connection, project_id=project.id)

    service = ManualTicketService(
        repository,
        ContextInjector(repo),
        project_id=project.id,
    )

    with pytest.raises(ManualTicketError, match="known local model"):
        service.create(
            ManualTicketCreatePayload(
                title="Bad model",
                type="feature",
                target_files=["calc.py"],
                task_description="Do something",
                assigned_model="claude-tech-lead",
                project_id=project.id,
            )
        )


def test_manual_ticket_service_rejects_missing_target_file(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("repo\n", encoding="utf-8")
    init_git_repo(repo)

    connection = connect(tmp_path / "haao.sqlite3")
    project_repository = ProjectRepository(connection)
    project = project_repository.create(name="Calc", path=repo)
    repository = TicketRepository(connection, project_id=project.id)

    service = ManualTicketService(
        repository,
        ContextInjector(repo),
        project_id=project.id,
    )

    with pytest.raises(ManualTicketError, match="not found"):
        service.create(
            ManualTicketCreatePayload(
                title="Missing file",
                type="feature",
                target_files=["missing.py"],
                task_description="Touch a file that does not exist",
                project_id=project.id,
            )
        )
