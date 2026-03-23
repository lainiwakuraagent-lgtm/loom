"""Tests for jar.service — ProjectService and TaskService business logic."""

import pytest
from jar.db import get_connection, init_db
from jar.filters import ProjectFilter, SortSpec, TaskFilter
import json
from jar.models import EventType, Status
from jar.service import ProjectService, TaskService


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def ps(conn):
    return ProjectService(conn)


@pytest.fixture
def ts(conn):
    return TaskService(conn)


@pytest.fixture
def project(ps):
    return ps.create("Alpha", start_date="2025-01-01", deployment_date="2025-06-01")


@pytest.fixture
def task(ts, project):
    return ts.create("Fix bug", tags=["bug"], project_id=project.id, deadline="2025-04-01")


# ════════════════════════════════════════════════ ProjectService

class TestProjectServiceCreate:
    def test_creates_with_name_only(self, ps):
        p = ps.create("Minimal")
        assert p.id is not None
        assert p.name == "Minimal"

    def test_creates_with_all_fields(self, ps):
        p = ps.create("Full", description="desc", start_date="2025-01-01",
                       deployment_date="2025-12-31")
        assert p.description == "desc"
        assert p.start_date == "2025-01-01"

    def test_description_accepts_any_text(self, ps):
        # No constraint — any description is accepted
        p = ps.create("P", description="Just some free text, no sections required.")
        assert p.description == "Just some free text, no sections required."

    def test_empty_description_accepted(self, ps):
        p = ps.create("P", description=None)
        assert ps.get(p.id).description is None


class TestProjectServiceGet:
    def test_returns_project(self, ps, project):
        fetched = ps.get(project.id)
        assert fetched.id == project.id
        assert fetched.name == project.name

    def test_returns_none_for_missing(self, ps):
        assert ps.get(99999) is None


class TestProjectServiceUpdate:
    def test_updates_name(self, ps, project):
        ps.update(project.id, name="Beta")
        assert ps.get(project.id).name == "Beta"

    def test_does_not_overwrite_unmentioned_fields(self, ps, project):
        original_start = project.start_date
        ps.update(project.id, name="New Name")
        assert ps.get(project.id).start_date == original_start

    def test_clears_date_with_none(self, ps, project):
        ps.update(project.id, start_date=None)
        assert ps.get(project.id).start_date is None

    def test_not_found_raises(self, ps):
        with pytest.raises(ValueError, match="not found"):
            ps.update(99999, name="Ghost")

    def test_updates_description(self, ps, project):
        ps.update(project.id, description="Updated description")
        assert ps.get(project.id).description == "Updated description"


class TestProjectServiceDelete:
    def test_deletes_project(self, ps, project):
        result = ps.delete(project.id)
        assert result is True
        assert ps.get(project.id) is None

    def test_returns_false_for_missing(self, ps):
        assert ps.delete(99999) is False

    def test_cascade_deletes_tasks(self, conn, ps, ts, project):
        """Core requirement: deleting a project must remove all its tasks."""
        t1 = ts.create("Task 1", project_id=project.id)
        t2 = ts.create("Task 2", project_id=project.id)
        standalone = ts.create("Standalone")

        ps.delete(project.id)

        assert ts.get(t1.id) is None
        assert ts.get(t2.id) is None
        # Standalone task must be untouched
        assert ts.get(standalone.id) is not None

    def test_project_and_tasks_both_gone(self, conn, ps, ts, project):
        ts.create("Child", project_id=project.id)
        ps.delete(project.id)
        assert ps.get(project.id) is None
        remaining = ts.list_filtered(TaskFilter(project_id=project.id))
        assert remaining == []


class TestProjectServiceListFiltered:
    def test_returns_all_when_no_filter(self, ps):
        ps.create("P1")
        ps.create("P2")
        assert len(ps.list_filtered()) == 2

    def test_search_filter(self, ps):
        ps.create("API Gateway")
        ps.create("Worker Service")
        results = ps.list_filtered(ProjectFilter(search="API"))
        assert len(results) == 1

    def test_sort_by_name(self, ps):
        ps.create("Zeta")
        ps.create("Alpha")
        results = ps.list_filtered(sort=SortSpec("name", "asc"))
        assert results[0].name == "Alpha"

    def test_has_tasks_filter(self, ps, ts):
        p_with = ps.create("With tasks")
        ps.create("Empty")
        ts.create("Child", project_id=p_with.id)
        assert len(ps.list_filtered(ProjectFilter(has_tasks=True))) == 1
        assert len(ps.list_filtered(ProjectFilter(has_tasks=False))) == 1


# ════════════════════════════════════════════════ TaskService

class TestTaskServiceCreate:
    def test_creates_standalone(self, ts):
        t = ts.create("Solo task")
        assert t.id is not None
        assert t.project_id is None
        assert t.status == Status.TODO

    def test_creates_with_project(self, ts, project):
        t = ts.create("With project", project_id=project.id)
        assert t.project_id == project.id

    def test_invalid_project_raises(self, ts):
        with pytest.raises(ValueError, match="does not exist"):
            ts.create("Orphan", project_id=99999)

    def test_invalid_status_raises(self, ts):
        with pytest.raises(ValueError, match="Invalid status"):
            ts.create("Bad status task", status="flying")

    def test_all_statuses_accepted(self, ts):
        for status in ("todo", "in_progress", "done"):
            t = ts.create(f"Task {status}", status=status)
            assert t.status == Status(status)

    def test_tags_stored_and_returned(self, ts):
        t = ts.create("Tagged", tags=["bug", "feature"])
        fetched = ts.get(t.id)
        assert fetched.tags == ["bug", "feature"]

    def test_empty_tags_list(self, ts):
        t = ts.create("No tags", tags=[])
        assert ts.get(t.id).tags == []


class TestTaskServiceGet:
    def test_returns_task(self, ts, task):
        fetched = ts.get(task.id)
        assert fetched.id == task.id

    def test_returns_none_for_missing(self, ts):
        assert ts.get(99999) is None


class TestTaskServiceUpdate:
    def test_updates_status(self, ts, task):
        ts.update(task.id, status="in_progress")
        assert ts.get(task.id).status == Status.IN_PROGRESS

    def test_invalid_status_raises(self, ts, task):
        with pytest.raises(ValueError):
            ts.update(task.id, status="unknown")

    def test_reassign_project(self, ts, ps, task):
        p2 = ps.create("Beta")
        ts.update(task.id, project_id=p2.id)
        assert ts.get(task.id).project_id == p2.id

    def test_detach_project(self, ts, task):
        ts.update(task.id, project_id=None)
        assert ts.get(task.id).project_id is None

    def test_reassign_to_nonexistent_project_raises(self, ts, task):
        with pytest.raises(ValueError, match="does not exist"):
            ts.update(task.id, project_id=99999)

    def test_update_tags(self, ts, task):
        ts.update(task.id, tags=["chore", "docs"])
        assert ts.get(task.id).tags == ["chore", "docs"]

    def test_clear_tags(self, ts, task):
        ts.update(task.id, tags=[])
        assert ts.get(task.id).tags == []

    def test_clear_deadline(self, ts, task):
        ts.update(task.id, deadline=None)
        assert ts.get(task.id).deadline is None

    def test_not_found_raises(self, ts):
        with pytest.raises(ValueError, match="not found"):
            ts.update(99999, name="Ghost")

    def test_unmentioned_fields_unchanged(self, ts, task):
        original_tags = task.tags[:]
        ts.update(task.id, status="done")
        assert ts.get(task.id).tags == original_tags


class TestTaskServiceDelete:
    def test_deletes_task(self, ts, task):
        assert ts.delete(task.id) is True
        assert ts.get(task.id) is None

    def test_returns_false_for_missing(self, ts):
        assert ts.delete(99999) is False


class TestTaskServiceListFiltered:
    def _seed(self, ts, project_id):
        ts.create("A", status="todo", tags=["bug"], deadline="2025-03-01",
                   project_id=project_id)
        ts.create("B", status="in_progress", tags=["docs"], project_id=project_id)
        ts.create("C", status="done", tags=["chore"])

    def test_all_tasks(self, ts, project):
        self._seed(ts, project.id)
        assert len(ts.list_filtered()) == 3

    def test_filter_status(self, ts, project):
        self._seed(ts, project.id)
        results = ts.list_filtered(TaskFilter(status="todo"))
        assert len(results) == 1 and results[0].name == "A"

    def test_filter_tag(self, ts, project):
        self._seed(ts, project.id)
        results = ts.list_filtered(TaskFilter(tags=["docs"]))
        assert len(results) == 1 and results[0].name == "B"

    def test_filter_standalone(self, ts, project):
        self._seed(ts, project.id)
        results = ts.list_filtered(TaskFilter(project_id=-1))
        assert len(results) == 1 and results[0].name == "C"

    def test_sort_by_deadline(self, ts, project):
        self._seed(ts, project.id)
        results = ts.list_filtered(
            TaskFilter(project_id=project.id),
            sort=SortSpec("deadline", "asc"),
        )
        # "A" has a deadline; "B" doesn't — NULLs last in ASC
        assert results[0].name == "A"

    def test_filter_deadline_on(self, ts, project):
        self._seed(ts, project.id)
        results = ts.list_filtered(TaskFilter(deadline_on="2025-03-01"))
        assert len(results) == 1
        assert results[0].name == "A"

    def test_filter_overdue(self, ts):
        ts.create("Overdue task", status="todo", deadline="2020-01-01")
        ts.create("Done old",     status="done", deadline="2020-01-01")
        ts.create("Future task",  status="todo", deadline="2099-01-01")
        ts.create("No deadline",  status="todo")

        results = ts.list_filtered(TaskFilter(overdue=True))
        names = {t.name for t in results}
        assert "Overdue task" in names
        assert "Done old"     not in names
        assert "Future task"  not in names
        assert "No deadline"  not in names

    def test_filter_deadline_range(self, ts, project):
        self._seed(ts, project.id)
        results = ts.list_filtered(
            TaskFilter(deadline_after="2025-02-01", deadline_before="2025-04-01")
        )
        assert len(results) == 1 and results[0].name == "A"


# ══════════════════════════════════════════════════════ TaskService history


class TestTaskServiceHistory:
    def test_create_emits_created_event(self, ts):
        t = ts.create(name="My task")
        events = ts.get_history(t.id)
        assert len(events) == 1
        assert events[0].event_type == EventType.CREATED
        assert events[0].task_id == t.id

    def test_create_event_has_valid_snapshot(self, ts):
        t = ts.create(name="Snapshot task", status="in_progress")
        events = ts.get_history(t.id)
        snapshot = json.loads(events[0].task_snapshot)
        assert snapshot["name"] == "Snapshot task"
        assert snapshot["status"] == "in_progress"

    def test_update_status_emits_field_event(self, ts):
        t = ts.create(name="Status task")
        ts.update(t.id, status="done")
        events = ts.get_history(t.id)
        updated = [e for e in events if e.event_type == EventType.UPDATED]
        assert len(updated) == 1
        assert updated[0].field_name == "status"
        assert updated[0].old_value == "todo"
        assert updated[0].new_value == "done"

    def test_update_multiple_fields_emits_multiple_events(self, ts):
        t = ts.create(name="Multi-field")
        ts.update(t.id, name="Renamed", status="in_progress")
        events = ts.get_history(t.id)
        updated = [e for e in events if e.event_type == EventType.UPDATED]
        fields = {e.field_name for e in updated}
        assert "name" in fields
        assert "status" in fields

    def test_update_no_change_emits_no_events(self, ts):
        t = ts.create(name="Stable", status="todo")
        # Pass same values — no actual change
        ts.update(t.id, name="Stable", status="todo")
        events = ts.get_history(t.id)
        # Only the initial CREATED event, no UPDATED events
        assert len(events) == 1
        assert events[0].event_type == EventType.CREATED

    def test_delete_emits_deleted_event(self, ts):
        t = ts.create(name="Doomed")
        ts.delete(t.id)
        events = ts.get_history(t.id)
        assert events[-1].event_type == EventType.DELETED

    def test_history_survives_task_deletion(self, ts):
        t = ts.create(name="Gone soon")
        ts.update(t.id, status="done")
        ts.delete(t.id)
        events = ts.get_history(t.id)
        event_types = [e.event_type for e in events]
        assert EventType.CREATED in event_types
        assert EventType.UPDATED in event_types
        assert EventType.DELETED in event_types

    def test_history_empty_for_unknown_task(self, ts):
        assert ts.get_history(99999) == []

    def test_tags_change_tracked_with_sorted_format(self, ts):
        t = ts.create(name="Tagger", tags=["feature"])
        ts.update(t.id, tags=["bug", "chore"])
        events = ts.get_history(t.id)
        tag_event = next(e for e in events if e.field_name == "tags")
        assert tag_event.old_value == "feature"
        # sorted: bug, chore
        assert tag_event.new_value == "bug,chore"

    def test_all_snapshots_are_valid_json(self, ts):
        t = ts.create(name="Snapshot check")
        ts.update(t.id, status="in_progress")
        ts.delete(t.id)
        for e in ts.get_history(t.id):
            assert e.task_snapshot is not None
            parsed = json.loads(e.task_snapshot)
            assert isinstance(parsed, dict)
            assert "name" in parsed
