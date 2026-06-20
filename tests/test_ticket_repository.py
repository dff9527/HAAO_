import pytest

from orchestrator.db.sqlite import (
    DuplicateTicketError,
    ProjectRepository,
    TicketDeletionError,
    TicketRepository,
    connect,
)
from orchestrator.models.ticket import Ticket
from tests.conftest import init_git_repo


def test_ticket_crud_persists_after_reconnect(tmp_path, fresh_ticket_dict) -> None:
    database_path = tmp_path / "haao.sqlite3"

    connection = connect(database_path)
    repository = TicketRepository(connection)
    created = repository.create(Ticket.from_dict(fresh_ticket_dict))

    assert created.id == "T-012"
    assert repository.get("T-012").title == created.title

    updated = repository.update_status("T-012", "testing")
    assert updated.status == "testing"

    with_log = repository.append_log("T-012", "pytest passed")
    assert with_log.result.logs[-1].message == "pytest passed"
    connection.close()

    reopened = connect(database_path)
    repository = TicketRepository(reopened)

    persisted = repository.get("T-012")
    assert persisted.status == "testing"
    assert persisted.result.logs[-1].message == "pytest passed"
    assert repository.logs_for_ticket("T-012")[0]["message"] == "pytest passed"


def test_list_can_filter_by_status(tmp_path, fresh_ticket_dict) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(Ticket.from_dict(fresh_ticket_dict))

    assert len(repository.list()) == 1
    assert len(repository.list(status="in_progress")) == 1
    assert repository.list(status="backlog") == []


def test_next_ticket_id_uses_global_ticket_sequence(tmp_path, fresh_ticket_dict) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))

    assert repository.next_ticket_id() == "T-001"

    repository.create(Ticket.from_dict(fresh_ticket_dict))

    assert repository.next_ticket_id() == "T-013"


def test_create_duplicate_ticket_id_raises_clear_error(tmp_path, fresh_ticket_dict) -> None:
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    ticket = Ticket.from_dict(fresh_ticket_dict)
    repository.create(ticket)

    with pytest.raises(DuplicateTicketError, match="Ticket id already exists: T-012"):
        repository.create(ticket)


def test_delete_ticket_removes_ticket_and_logs(tmp_path, fresh_ticket_dict) -> None:
    fresh_ticket_dict["status"] = "backlog"
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(Ticket.from_dict(fresh_ticket_dict))
    repository.append_log("T-012", "delete me")

    repository.delete("T-012")

    assert repository.get("T-012") is None
    assert repository.logs_for_ticket("T-012") == []


def test_delete_running_ticket_requires_force(tmp_path, fresh_ticket_dict) -> None:
    fresh_ticket_dict["status"] = "in_progress"
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(Ticket.from_dict(fresh_ticket_dict))

    with pytest.raises(TicketDeletionError, match="force=true"):
        repository.delete("T-012")

    repository.delete("T-012", force=True)

    assert repository.get("T-012") is None


def test_project_repository_rejects_non_git_paths(tmp_path) -> None:
    repository = ProjectRepository(connect(tmp_path / "haao.sqlite3"))
    non_git = tmp_path / "plain"
    non_git.mkdir()

    with pytest.raises(ValueError, match="not a git repository"):
        repository.create(name="Plain", path=non_git)


def test_project_repository_canonicalizes_alias_to_git_root(tmp_path) -> None:
    repo = tmp_path / "Repo"
    repo.mkdir()
    (repo / "README.md").write_text("repo\n", encoding="utf-8")
    init_git_repo(repo)
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)

    repository = ProjectRepository(connect(tmp_path / "haao.sqlite3"))
    project = repository.create(name="Repo", path=alias)

    assert project.path == str(repo.resolve())


def test_project_repository_updates_setup_env_and_cleanup(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("repo\n", encoding="utf-8")
    init_git_repo(repo)

    repository = ProjectRepository(connect(tmp_path / "haao.sqlite3"))
    project = repository.create(name="Repo", path=repo)

    updated = repository.update_settings(
        project.id,
        env={"HAAO_FLAG": "ok"},
        setup_cmd="python -c \"print('setup')\"",
        cleanup_cmd="python -c \"print('cleanup')\"",
        default_branch="develop",
    )

    persisted = repository.get(project.id)
    assert updated.env == {"HAAO_FLAG": "ok"}
    assert updated.setup_cmd == "python -c \"print('setup')\""
    assert updated.cleanup_cmd == "python -c \"print('cleanup')\""
    assert updated.default_branch == "develop"
    assert persisted == updated


def test_ticket_ids_are_scoped_by_project(tmp_path, fresh_ticket_dict) -> None:
    db_path = tmp_path / "haao.sqlite3"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    (repo_a / "a.py").write_text("A = 1\n", encoding="utf-8")
    (repo_b / "b.py").write_text("B = 1\n", encoding="utf-8")
    init_git_repo(repo_a)
    init_git_repo(repo_b)

    connection = connect(db_path)
    projects = ProjectRepository(connection)
    project_a = projects.create(name="Repo A", path=repo_a)
    project_b = projects.create(name="Repo B", path=repo_b)

    ticket_a = Ticket.from_dict(fresh_ticket_dict)
    payload_b = fresh_ticket_dict.copy()
    payload_b["title"] = "Same id in another project"
    ticket_b = Ticket.from_dict(payload_b)

    tickets_a = TicketRepository(connection, project_id=project_a.id)
    tickets_b = TicketRepository(connection, project_id=project_b.id)
    tickets_a.create(ticket_a)
    tickets_b.create(ticket_b)

    assert tickets_a.get("T-012").title == ticket_a.title
    assert tickets_b.get("T-012").title == "Same id in another project"
    assert [ticket.metadata.project_id for ticket in tickets_a.list()] == [project_a.id]
    assert [ticket.metadata.project_id for ticket in tickets_b.list()] == [project_b.id]
