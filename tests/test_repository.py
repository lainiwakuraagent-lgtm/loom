"""Tests for jar.repository — CRUD and cascade-delete using in-memory SQLite."""

import pytest
from jar.db import get_connection, init_db
from jar.filters import ProjectFilter, SortSpec, TaskFilter
from jar.models import Project, Status, Task
from jar.models import EventType, TaskEvent
from jar.repository import ProjectRepository, TaskEventRepository, TaskRepository


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def project_repo(conn):
    return ProjectRepository(conn)


@pytest.fixture
def task_repo(conn):
    return TaskRepository(conn)


@pytest.fixture
def event_repo(conn):
    return TaskEventRepository(conn)


@pytest.fixture
def sample_project(project_repo):
    return project_repo.insert(Project(name="Alpha", start_date="2025-01-01"))


@pytest.fixture
def sample_task(task_repo, sample_project):
    return task_repo.insert(Task(name="Fix bug", tags=["bug"], status=Status.TODO,
                                  project_id=sample_project.id))


# ════════════════════════════════════════════════ ProjectRepository

class TestProjectRepositoryInsert:
    def test_assigns_id(self, project_repo):
        p = project_repo.insert(Project(name="Test"))
        assert p.id is not None
        assert p.id > 0

    def test_sets_timestamps(self, project_repo):
        p = project_repo.insert(Project(name="Test"))
        assert p.created_at is not None
        assert p.updated_at is not None
        assert p.created_at == p.updated_at

    def test_roundtrip(self, project_repo):
        project_repo.insert(Project(name="Alpha", start_date="2025-01-01",
                                     deployment_date="2025-06-01",
                                     description="Some description"))
        fetched = project_repo.get_by_id(1)
        assert fetched.name == "Alpha"
        assert fetched.start_date == "2025-01-01"
        assert fetched.deployment_date == "2025-06-01"
        assert fetched.description == "Some description"


class TestProjectRepositoryUpdate:
    def test_updates_name(self, project_repo, sample_project):
        sample_project.name = "Beta"
        project_repo.update(sample_project)
        assert project_repo.get_by_id(sample_project.id).name == "Beta"

    def test_updates_updated_at(self, project_repo, sample_project):
        original_ts = sample_project.updated_at
        import time; time.sleep(1)  # ensure clock tick
        sample_project.name = "Changed"
        project_repo.update(sample_project)
        fetched = project_repo.get_by_id(sample_project.id)
        assert fetched.updated_at >= original_ts

    def test_update_without_id_raises(self, project_repo):
        with pytest.raises(ValueError):
            project_repo.update(Project(name="No ID"))


class TestProjectRepositoryDelete:
    def test_deletes_project(self, project_repo, sample_project):
        result = project_repo.delete(sample_project.id)
        assert result is True
        assert project_repo.get_by_id(sample_project.id) is None

    def test_returns_false_for_missing_id(self, project_repo):
        assert project_repo.delete(99999) is False

    def test_cascade_deletes_tasks(self, conn, project_repo, task_repo, sample_project):
        """Deleting a project must remove all its tasks — hard cascade."""
        t1 = task_repo.insert(Task(name="T1", project_id=sample_project.id))
        t2 = task_repo.insert(Task(name="T2", project_id=sample_project.id))
        standalone = task_repo.insert(Task(name="Standalone"))

        project_repo.delete(sample_project.id)

        # Both project tasks gone
        assert task_repo.get_by_id(t1.id) is None
        assert task_repo.get_by_id(t2.id) is None
        # Standalone task untouched
        assert task_repo.get_by_id(standalone.id) is not None

    def test_project_not_found_after_cascade(self, project_repo, task_repo, sample_project):
        task_repo.insert(Task(name="Child", project_id=sample_project.id))
        project_repo.delete(sample_project.id)
        assert project_repo.get_by_id(sample_project.id) is None


class TestProjectRepositoryList:
    def test_list_all(self, project_repo):
        project_repo.insert(Project(name="P1"))
        project_repo.insert(Project(name="P2"))
        assert len(project_repo.list_all()) == 2

    def test_list_filtered_search(self, project_repo):
        project_repo.insert(Project(name="Alpha API"))
        project_repo.insert(Project(name="Beta"))
        results = project_repo.list_filtered(ProjectFilter(search="API"))
        assert len(results) == 1
        assert results[0].name == "Alpha API"

    def test_list_filtered_has_tasks(self, project_repo, task_repo):
        p1 = project_repo.insert(Project(name="With tasks"))
        project_repo.insert(Project(name="Empty"))
        task_repo.insert(Task(name="Child", project_id=p1.id))

        with_tasks = project_repo.list_filtered(ProjectFilter(has_tasks=True))
        no_tasks = project_repo.list_filtered(ProjectFilter(has_tasks=False))
        assert len(with_tasks) == 1 and with_tasks[0].name == "With tasks"
        assert len(no_tasks) == 1 and no_tasks[0].name == "Empty"

    def test_list_sorted_by_name(self, project_repo):
        project_repo.insert(Project(name="Zeta"))
        project_repo.insert(Project(name="Alpha"))
        results = project_repo.list_filtered(sort=SortSpec("name", "asc"))
        assert results[0].name == "Alpha"
        assert results[1].name == "Zeta"


# ════════════════════════════════════════════════ TaskRepository

class TestTaskRepositoryInsert:
    def test_assigns_id(self, task_repo):
        t = task_repo.insert(Task(name="Do it"))
        assert t.id is not None

    def test_default_status_is_todo(self, task_repo):
        t = task_repo.insert(Task(name="Do it"))
        assert task_repo.get_by_id(t.id).status == Status.TODO

    def test_tags_roundtrip(self, task_repo):
        t = task_repo.insert(Task(name="T", tags=["bug", "docs"]))
        fetched = task_repo.get_by_id(t.id)
        assert fetched.tags == ["bug", "docs"]

    def test_standalone_task_has_null_project(self, task_repo):
        t = task_repo.insert(Task(name="Solo"))
        assert task_repo.get_by_id(t.id).project_id is None


class TestTaskRepositoryUpdate:
    def test_updates_status(self, task_repo, sample_task):
        sample_task.status = Status.DONE
        task_repo.update(sample_task)
        assert task_repo.get_by_id(sample_task.id).status == Status.DONE

    def test_updates_tags(self, task_repo, sample_task):
        sample_task.tags = ["feature", "design"]
        task_repo.update(sample_task)
        assert task_repo.get_by_id(sample_task.id).tags == ["feature", "design"]

    def test_clears_deadline(self, task_repo):
        t = task_repo.insert(Task(name="T", deadline="2025-04-01"))
        t.deadline = None
        task_repo.update(t)
        assert task_repo.get_by_id(t.id).deadline is None

    def test_update_without_id_raises(self, task_repo):
        with pytest.raises(ValueError):
            task_repo.update(Task(name="No ID"))


class TestTaskRepositoryDelete:
    def test_deletes_task(self, task_repo, sample_task):
        result = task_repo.delete(sample_task.id)
        assert result is True
        assert task_repo.get_by_id(sample_task.id) is None

    def test_returns_false_for_missing(self, task_repo):
        assert task_repo.delete(99999) is False


class TestTaskRepositoryFilter:
    def _seed(self, task_repo, project_id):
        task_repo.insert(Task(name="A", status=Status.TODO, tags=["bug"],
                               deadline="2025-03-01", project_id=project_id))
        task_repo.insert(Task(name="B", status=Status.IN_PROGRESS, tags=["docs"],
                               deadline="2025-06-01", project_id=project_id))
        task_repo.insert(Task(name="C", status=Status.DONE, tags=["chore"]))

    def test_filter_by_status(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(TaskFilter(status="todo"))
        assert all(t.status == Status.TODO for t in results)

    def test_filter_by_project(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(TaskFilter(project_id=sample_project.id))
        assert all(t.project_id == sample_project.id for t in results)
        assert len(results) == 2

    def test_filter_standalone(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(TaskFilter(project_id=-1))
        assert len(results) == 1
        assert results[0].name == "C"

    def test_filter_by_tag(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(TaskFilter(tags=["bug"]))
        assert len(results) == 1
        assert results[0].name == "A"

    def test_filter_deadline_before(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(TaskFilter(deadline_before="2025-04-01"))
        assert len(results) == 1
        assert results[0].deadline == "2025-03-01"

    def test_filter_search(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(TaskFilter(search="B"))
        assert any(t.name == "B" for t in results)

    def test_filter_deadline_on(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(TaskFilter(deadline_on="2025-03-01"))
        assert len(results) == 1
        assert results[0].deadline == "2025-03-01"

    def test_filter_deadline_on_no_match(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(TaskFilter(deadline_on="2099-01-01"))
        assert results == []

    def test_filter_overdue(self, task_repo):
        # Insert a past-due non-done task and a done task with past deadline
        task_repo.insert(Task(name="Past todo", status=Status.TODO, deadline="2020-01-01"))
        task_repo.insert(Task(name="Past done", status=Status.DONE, deadline="2020-01-01"))
        task_repo.insert(Task(name="Future todo", status=Status.TODO, deadline="2099-01-01"))
        task_repo.insert(Task(name="No deadline", status=Status.TODO))

        results = task_repo.list_filtered(TaskFilter(overdue=True))
        names = {t.name for t in results}
        assert "Past todo" in names        # overdue and not done → included
        assert "Past done" not in names    # done → excluded
        assert "Future todo" not in names  # not past → excluded
        assert "No deadline" not in names  # no deadline → excluded

    def test_filter_deadline_range(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        # Range covering only "A" (2025-03-01)
        results = task_repo.list_filtered(
            TaskFilter(deadline_after="2025-02-01", deadline_before="2025-04-01")
        )
        assert len(results) == 1
        assert results[0].name == "A"

    def test_sort_by_deadline_asc(self, task_repo, sample_project):
        self._seed(task_repo, sample_project.id)
        results = task_repo.list_filtered(
            TaskFilter(project_id=sample_project.id),
            sort=SortSpec("deadline", "asc"),
        )
        deadlines = [t.deadline for t in results if t.deadline]
        assert deadlines == sorted(deadlines)


# ══════════════════════════════════════════════════════ TaskEventRepository


def _make_event(task_id: int, event_type: EventType = EventType.CREATED, **kwargs) -> TaskEvent:
    return TaskEvent(
        task_id=task_id,
        event_type=event_type,
        changed_at="2026-01-01T00:00:00Z",
        **kwargs,
    )


class TestTaskEventRepository:
    def test_insert_returns_with_id(self, event_repo):
        event = event_repo.insert(_make_event(task_id=1))
        assert event.id is not None
        assert event.id > 0

    def test_roundtrip_created_event(self, event_repo):
        event = event_repo.insert(_make_event(task_id=42))
        results = event_repo.list_for_task(42)
        assert len(results) == 1
        r = results[0]
        assert r.task_id == 42
        assert r.event_type == EventType.CREATED
        assert r.field_name is None
        assert r.old_value is None
        assert r.new_value is None
        assert r.changed_at == "2026-01-01T00:00:00Z"

    def test_roundtrip_updated_event(self, event_repo):
        event_repo.insert(TaskEvent(
            task_id=5,
            event_type=EventType.UPDATED,
            changed_at="2026-02-01T12:00:00Z",
            field_name="status",
            old_value="todo",
            new_value="in_progress",
        ))
        results = event_repo.list_for_task(5)
        assert len(results) == 1
        r = results[0]
        assert r.event_type == EventType.UPDATED
        assert r.field_name == "status"
        assert r.old_value == "todo"
        assert r.new_value == "in_progress"

    def test_roundtrip_deleted_event(self, event_repo):
        event_repo.insert(TaskEvent(
            task_id=7,
            event_type=EventType.DELETED,
            changed_at="2026-03-01T08:00:00Z",
        ))
        results = event_repo.list_for_task(7)
        assert len(results) == 1
        assert results[0].event_type == EventType.DELETED
        assert results[0].new_value is None

    def test_list_empty_for_unknown_task(self, event_repo):
        assert event_repo.list_for_task(99999) == []

    def test_list_ordered_by_changed_at(self, event_repo):
        event_repo.insert(TaskEvent(task_id=10, event_type=EventType.UPDATED,
                                    changed_at="2026-01-02T00:00:00Z", field_name="name"))
        event_repo.insert(TaskEvent(task_id=10, event_type=EventType.CREATED,
                                    changed_at="2026-01-01T00:00:00Z"))
        results = event_repo.list_for_task(10)
        assert results[0].event_type == EventType.CREATED
        assert results[1].event_type == EventType.UPDATED

    def test_events_survive_task_deletion(self, conn, task_repo, event_repo):
        """Core requirement: history must outlive the task row."""
        task = task_repo.insert(Task(name="Mortal task"))
        event_repo.insert(_make_event(task_id=task.id))

        task_repo.delete(task.id)
        assert task_repo.get_by_id(task.id) is None

        # Events still accessible after task is gone
        remaining = event_repo.list_for_task(task.id)
        assert len(remaining) == 1
        assert remaining[0].event_type == EventType.CREATED
