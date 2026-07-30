"""Tests for jar.service — ProjectService and TaskService business logic."""

import pytest
from loom.db import get_connection, init_db
from loom.filters import ProjectFilter, SortSpec, TaskFilter
import json
from loom.models import EventType, Status
from loom.service import GoalService, ProjectService, TaskService


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
def gs(conn):
    return GoalService(conn)


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


class TestProjectServiceDoneGuard:
    """loom.service's logger sets propagate=False, so pytest's caplog (which
    listens via root propagation) can't see these records — attach a
    collecting handler directly to the logger instead.
    """

    @pytest.fixture
    def service_log_records(self):
        import logging
        from loom.logging_config import get_service_logger

        records = []

        class _Collector(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Collector()
        logger = get_service_logger()
        logger.addHandler(handler)
        try:
            yield records
        finally:
            logger.removeHandler(handler)

    def test_warns_when_no_milestone_review_task(self, ps, ts, service_log_records):
        p = ps.create("No verification")
        ts.create("Just a task", project_id=p.id, tags=["feature"])
        ps.update(p.id, status="done")
        assert any("no milestone_review-tagged task" in r.getMessage() for r in service_log_records)

    def test_no_warning_when_milestone_review_task_present(self, ps, ts, service_log_records):
        p = ps.create("Verified")
        ts.create("Checked", project_id=p.id, tags=["milestone_review"])
        ps.update(p.id, status="done")
        assert not any("no milestone_review-tagged task" in r.getMessage() for r in service_log_records)

    def test_no_warning_for_non_done_transitions(self, ps, ts, service_log_records):
        p = ps.create("In flight")
        ts.create("Just a task", project_id=p.id, tags=["feature"])
        ps.update(p.id, status="in_progress")
        assert not any("no milestone_review-tagged task" in r.getMessage() for r in service_log_records)

    def test_guard_does_not_block_the_update(self, ps, ts):
        p = ps.create("Closes anyway")
        ts.create("Just a task", project_id=p.id, tags=["feature"])
        result = ps.update(p.id, status="done")
        assert result.status.value == "done"


class TestProjectServiceResolveActive:
    def test_picks_highest_priority_under_goal(self, ps, gs):
        goal = gs.create("Goal A", status="scheduled")
        ps.create("Low", goal_id=goal.id, status="scheduled", priority=1)
        high = ps.create("High", goal_id=goal.id, status="scheduled", priority=5)
        assert ps.resolve_active(goal.id).id == high.id

    def test_ignores_projects_under_other_goals(self, ps, gs):
        goal_a = gs.create("Goal A", status="scheduled")
        goal_b = gs.create("Goal B", status="scheduled")
        ps.create("Under B", goal_id=goal_b.id, status="scheduled", priority=10)
        assert ps.resolve_active(goal_a.id) is None

    def test_excludes_desire_and_needs_plan(self, ps, gs):
        goal = gs.create("Goal A", status="scheduled")
        ps.create("Unvetted", goal_id=goal.id, status="desire", priority=10)
        assert ps.resolve_active(goal.id) is None

    def test_tiebreak_lower_id_wins(self, ps, gs):
        goal = gs.create("Goal A", status="scheduled")
        first = ps.create("First", goal_id=goal.id, status="scheduled", priority=3)
        ps.create("Second", goal_id=goal.id, status="scheduled", priority=3)
        assert ps.resolve_active(goal.id).id == first.id


# ════════════════════════════════════════════════ GoalService

class TestGoalServiceResolveActive:
    def test_picks_highest_priority(self, gs):
        gs.create("Low", status="scheduled", priority=1)
        high = gs.create("High", status="scheduled", priority=5)
        assert gs.resolve_active().id == high.id

    def test_in_progress_counts_as_eligible(self, gs):
        g = gs.create("Working", status="in_progress", priority=1)
        assert gs.resolve_active().id == g.id

    def test_excludes_desire_and_suspended(self, gs):
        gs.create("Idea", status="desire", priority=10)
        gs.create("Paused", status="suspended", priority=10)
        assert gs.resolve_active() is None

    def test_tiebreak_lower_id_wins(self, gs):
        first = gs.create("First", status="scheduled", priority=3)
        gs.create("Second", status="scheduled", priority=3)
        assert gs.resolve_active().id == first.id

    def test_no_eligible_goals_returns_none(self, gs):
        assert gs.resolve_active() is None


class TestGoalServiceActivate:
    def test_bumps_priority_above_current_max(self, gs):
        gs.create("Existing", status="scheduled", priority=5)
        target = gs.create("New", status="desire", priority=0)
        activated = gs.activate(target.id)
        assert activated.priority > 5
        assert activated.status.value == "scheduled"
        assert gs.resolve_active().id == target.id

    def test_does_not_touch_other_goals_status_or_priority(self, gs):
        other = gs.create("Other", status="scheduled", priority=5)
        target = gs.create("Target", status="scheduled", priority=1)
        gs.activate(target.id)
        reloaded = gs.get(other.id)
        assert reloaded.status.value == "scheduled"
        assert reloaded.priority == 5

    def test_keeps_own_priority_if_already_highest(self, gs):
        gs.create("Lower", status="scheduled", priority=1)
        target = gs.create("Already top", status="scheduled", priority=10)
        activated = gs.activate(target.id)
        assert activated.priority == 10

    def test_unknown_goal_raises(self, gs):
        with pytest.raises(ValueError):
            gs.activate(9999)


# ════════════════════════════════════════════════ TaskService

class TestTaskServiceCreate:
    def test_creates_standalone(self, ts):
        t = ts.create("Solo task")
        assert t.id is not None
        assert t.project_id is None
        assert t.status == Status.TRIAGE

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
        for status in ("triage", "in_progress", "done"):
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


class TestTaskServiceAutoUnblock:
    def test_marking_done_unblocks_dependent_with_satisfied_deps(self, ts, project):
        blocker = ts.create("Blocker", project_id=project.id)
        dependent = ts.create("Dependent", project_id=project.id)
        ts.update(dependent.id, status="blocked_dep", depends=[blocker.id])

        ts.update(blocker.id, status="done")

        assert ts.get(dependent.id).status == Status.SCHEDULED

    def test_marking_done_leaves_dependent_blocked_if_other_dep_open(self, ts, project):
        blocker_a = ts.create("Blocker A", project_id=project.id)
        blocker_b = ts.create("Blocker B", project_id=project.id)
        dependent = ts.create("Dependent", project_id=project.id)
        ts.update(dependent.id, status="blocked_dep", depends=[blocker_a.id, blocker_b.id])

        ts.update(blocker_a.id, status="done")

        assert ts.get(dependent.id).status == Status.BLOCKED_DEP

    def test_marking_done_does_not_touch_unrelated_blocked_task(self, ts, project):
        blocker = ts.create("Blocker", project_id=project.id)
        other_blocker = ts.create("Other blocker", project_id=project.id)
        unrelated = ts.create("Unrelated", project_id=project.id)
        ts.update(unrelated.id, status="blocked_dep", depends=[other_blocker.id])

        ts.update(blocker.id, status="done")

        assert ts.get(unrelated.id).status == Status.BLOCKED_DEP

    def test_non_done_transition_does_not_unblock(self, ts, project):
        blocker = ts.create("Blocker", project_id=project.id)
        dependent = ts.create("Dependent", project_id=project.id)
        ts.update(dependent.id, status="blocked_dep", depends=[blocker.id])

        ts.update(blocker.id, status="in_progress")

        assert ts.get(dependent.id).status == Status.BLOCKED_DEP


class TestTaskServiceDelete:
    def test_deletes_task(self, ts, task):
        assert ts.delete(task.id) is True
        assert ts.get(task.id) is None

    def test_returns_false_for_missing(self, ts):
        assert ts.delete(99999) is False


class TestTaskServiceListFiltered:
    def _seed(self, ts, project_id):
        ts.create("A", status="triage", tags=["bug"], deadline="2025-03-01",
                   project_id=project_id)
        ts.create("B", status="in_progress", tags=["docs"], project_id=project_id)
        ts.create("C", status="done", tags=["chore"])

    def test_all_tasks(self, ts, project):
        self._seed(ts, project.id)
        assert len(ts.list_filtered()) == 3

    def test_filter_status(self, ts, project):
        self._seed(ts, project.id)
        results = ts.list_filtered(TaskFilter(status="triage"))
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
        ts.create("Overdue task", status="triage", deadline="2020-01-01")
        ts.create("Done old",     status="done", deadline="2020-01-01")
        ts.create("Future task",  status="triage", deadline="2099-01-01")
        ts.create("No deadline",  status="triage")

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
        assert updated[0].old_value == "triage"
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
        t = ts.create(name="Stable", status="triage")
        # Pass same values — no actual change
        ts.update(t.id, name="Stable", status="triage")
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


# ════════════════════════════════════════════════ ProjectService.status_report

class TestProjectServiceStatusReport:
    def _setup(self, ps, ts):
        """Return (project, tasks) with a mix of statuses."""
        p = ps.create("Rollup Project")
        t_sched_h = ts.create("High priority", project_id=p.id, status="scheduled", priority=3)
        t_sched_m = ts.create("Med priority",  project_id=p.id, status="scheduled", priority=2)
        t_done1   = ts.create("Done first",    project_id=p.id, status="done")
        t_done2   = ts.create("Done second",   project_id=p.id, status="done")
        t_block   = ts.create("Blocked task",  project_id=p.id, status="blocked_owner")
        ts.update(t_block.id, blocked_reason="waiting_decision", blocked_note="ask owner")
        return p, (t_sched_h, t_sched_m, t_done1, t_done2, t_block)

    def test_counts_add_up_to_total(self, ps, ts):
        p, _ = self._setup(ps, ts)
        r = ps.status_report(p.id)
        assert sum(r.counts.values()) == r.total
        assert r.total == 5

    def test_counts_zero_filled_for_all_statuses(self, ps, ts):
        p, _ = self._setup(ps, ts)
        r = ps.status_report(p.id)
        from loom.models import Status
        for s in Status:
            assert s.value in r.counts

    def test_blocked_items_carry_reason_and_note(self, ps, ts):
        p, _ = self._setup(ps, ts)
        r = ps.status_report(p.id)
        assert len(r.blocked) == 1
        b = r.blocked[0]
        assert b.blocked_reason == "waiting_decision"
        assert b.blocked_note == "ask owner"

    def test_recently_completed_contains_only_done_tasks(self, ps, ts):
        p, _ = self._setup(ps, ts)
        r = ps.status_report(p.id)
        assert len(r.recently_completed) == 2
        for t in r.recently_completed:
            assert t.status.value == "done"

    def test_next_actionable_sorted_by_urgency_score(self, ps, ts):
        p, (t_high, t_med, _, _, _) = self._setup(ps, ts)
        r = ps.status_report(p.id)
        assert len(r.next_actionable) == 2
        assert r.next_actionable[0].id == t_high.id

    def test_empty_project_returns_zero_filled_counts(self, ps):
        p = ps.create("Empty")
        r = ps.status_report(p.id)
        assert r.total == 0
        assert all(v == 0 for v in r.counts.values())
        assert r.blocked == []
        assert r.recently_completed == []
        assert r.next_actionable == []

    def test_recent_limit_respected(self, ps, ts):
        p = ps.create("Many done")
        for i in range(8):
            ts.create(f"Task {i}", project_id=p.id, status="done")
        r = ps.status_report(p.id, recent_limit=3)
        assert len(r.recently_completed) == 3
        assert r.counts["done"] == 8

    def test_not_found_raises(self, ps):
        with pytest.raises(ValueError, match="not found"):
            ps.status_report(99999)


# ════════════════════════════════════════════════ TaskService.get_dependency_tree

class TestTaskServiceDependencyTree:
    def test_leaf_task_has_empty_chains(self, ts):
        t = ts.create("Leaf")
        tree = ts.get_dependency_tree(t.id)
        assert tree.task.id == t.id
        assert tree.upstream == []
        assert tree.downstream == []

    def test_linear_chain_upstream(self, ts):
        t1 = ts.create("Root")
        t2 = ts.create("Middle", depends=[t1.id])
        t3 = ts.create("Tip", depends=[t2.id])
        tree = ts.get_dependency_tree(t3.id)
        up_ids = [t.id for t in tree.upstream]
        assert t2.id in up_ids
        assert t1.id in up_ids
        assert tree.downstream == []

    def test_linear_chain_downstream(self, ts):
        t1 = ts.create("Root")
        t2 = ts.create("Middle", depends=[t1.id])
        t3 = ts.create("Tip", depends=[t2.id])
        tree = ts.get_dependency_tree(t1.id)
        down_ids = [t.id for t in tree.downstream]
        assert t2.id in down_ids
        assert t3.id in down_ids
        assert tree.upstream == []

    def test_branching_downstream(self, ts):
        root = ts.create("Shared root")
        child_a = ts.create("Child A", depends=[root.id])
        child_b = ts.create("Child B", depends=[root.id])
        tree = ts.get_dependency_tree(root.id)
        down_ids = {t.id for t in tree.downstream}
        assert child_a.id in down_ids
        assert child_b.id in down_ids

    def test_cycle_does_not_infinite_loop(self, ts):
        t1 = ts.create("Cycle A")
        t2 = ts.create("Cycle B", depends=[t1.id])
        # Manually inject a cycle: t1 depends on t2
        ts.update(t1.id, depends=[t2.id])
        # Should terminate without hanging
        tree = ts.get_dependency_tree(t1.id)
        assert tree is not None

    def test_not_found_raises(self, ts):
        with pytest.raises(ValueError, match="not found"):
            ts.get_dependency_tree(99999)


# ════════════════════════════════════════════════ TaskService.get_blocked_digest

class TestGetBlockedDigest:
    """Tests for TaskService.get_blocked_digest / TaskRepository.get_blocked_digest."""

    def _make_goal(self, gs, name="Active Goal", status="scheduled"):
        return gs.create(name, description="", priority=5)

    def test_returns_all_blocked_variants_when_no_status_filter(self, ts, conn):
        from loom.service import GoalService
        gs = GoalService(conn)
        g = self._make_goal(gs)
        t1 = ts.create("Task dep", status="blocked_dep", goal_id=g.id)
        t2 = ts.create("Task owner", status="blocked_owner", goal_id=g.id)
        t3 = ts.create("Task external", status="blocked_external", goal_id=g.id)
        result = ts.get_blocked_digest()
        ids = {e.task_id for e in result}
        assert t1.id in ids
        assert t2.id in ids
        assert t3.id in ids

    def test_status_filter_blocked_owner_only(self, ts, conn):
        from loom.service import GoalService
        gs = GoalService(conn)
        g = self._make_goal(gs)
        t1 = ts.create("blocked_dep task", status="blocked_dep", goal_id=g.id)
        t2 = ts.create("blocked_owner task", status="blocked_owner", goal_id=g.id)
        result = ts.get_blocked_digest(status="blocked_owner")
        ids = {e.task_id for e in result}
        assert t2.id in ids
        assert t1.id not in ids

    def test_goal_exclusion_abandoned(self, ts, conn):
        from loom.service import GoalService
        gs = GoalService(conn)
        active = self._make_goal(gs, "Active")
        abandoned = self._make_goal(gs, "Abandoned")
        gs.update(abandoned.id, status="abandoned")
        t_active = ts.create("Active goal task", status="blocked_owner", goal_id=active.id)
        t_excl = ts.create("Abandoned goal task", status="blocked_owner", goal_id=abandoned.id)
        result = ts.get_blocked_digest()
        ids = {e.task_id for e in result}
        assert t_active.id in ids
        assert t_excl.id not in ids

    def test_goal_exclusion_suspended(self, ts, conn):
        from loom.service import GoalService
        gs = GoalService(conn)
        active = self._make_goal(gs, "Active")
        suspended = self._make_goal(gs, "Suspended")
        gs.update(suspended.id, status="suspended")
        t_active = ts.create("Active", status="blocked_owner", goal_id=active.id)
        t_excl = ts.create("Suspended", status="blocked_owner", goal_id=suspended.id)
        result = ts.get_blocked_digest()
        ids = {e.task_id for e in result}
        assert t_active.id in ids
        assert t_excl.id not in ids

    def test_project_id_filter(self, ts, conn):
        from loom.service import GoalService, ProjectService
        gs = GoalService(conn)
        ps = ProjectService(conn)
        g = self._make_goal(gs)
        p1 = ps.create("Project 1", start_date="2025-01-01")
        p2 = ps.create("Project 2", start_date="2025-01-01")
        t1 = ts.create("T in p1", status="blocked_owner", project_id=p1.id, goal_id=g.id)
        t2 = ts.create("T in p2", status="blocked_owner", project_id=p2.id, goal_id=g.id)
        result = ts.get_blocked_digest(project_id=p1.id)
        ids = {e.task_id for e in result}
        assert t1.id in ids
        assert t2.id not in ids

    def test_empty_result_returns_clean_list(self, ts):
        result = ts.get_blocked_digest()
        assert result == []

    def test_invalid_status_raises_value_error(self, ts):
        with pytest.raises(ValueError, match="status must be one of"):
            ts.get_blocked_digest(status="unknown_status")


# ════════════════════════════════════════════════ TaskService.reconcile_blocked_dep

class TestReconcileBlockedDep:
    """Tests for reconcile_blocked_dep and its integration with get_ready_queue."""

    def test_replays_t366_scenario(self, ts):
        """A blocked_dep task whose dep is done should be promoted by get_ready_queue."""
        dep = ts.create("Dep task", status="scheduled")
        # Mark dep done (simulates it completing via normal path)
        ts.update(dep.id, status="done")
        blocked = ts.create("Blocked task", status="blocked_dep", depends=[dep.id])
        # Ensure blocked was NOT promoted by auto_unblock (it wasn't — dep done before
        # blocked was created, so no done-transition event fired for blocked)
        stuck = ts.get(blocked.id)
        assert stuck.status.value == "blocked_dep"
        # get_ready_queue reconciles on call
        queue = ts.get_ready_queue()
        ids = {t.id for t in queue}
        assert blocked.id in ids
        # Verify status actually changed in DB
        refreshed = ts.get(blocked.id)
        assert refreshed.status.value == "scheduled"

    def test_genuinely_blocked_task_stays_blocked(self, ts):
        """A task whose dep is still in_progress must not be promoted."""
        dep = ts.create("In-progress dep", status="in_progress")
        blocked = ts.create("Still blocked", status="blocked_dep", depends=[dep.id])
        ts.reconcile_blocked_dep()
        refreshed = ts.get(blocked.id)
        assert refreshed.status.value == "blocked_dep"

    def test_reconcile_is_idempotent(self, ts):
        """Running reconcile twice produces the same result as running it once."""
        dep = ts.create("Done dep", status="done")
        blocked = ts.create("Blocked", status="blocked_dep", depends=[dep.id])
        promoted1 = ts.reconcile_blocked_dep()
        promoted2 = ts.reconcile_blocked_dep()
        assert len(promoted1) == 1
        assert promoted2 == []  # Second call: no stuck tasks remain

    def test_multiple_deps_all_must_be_done(self, ts):
        """Task with two deps: only promotes when BOTH are done."""
        d1 = ts.create("Dep 1", status="done")
        d2 = ts.create("Dep 2", status="scheduled")
        blocked = ts.create("Needs both", status="blocked_dep", depends=[d1.id, d2.id])
        ts.reconcile_blocked_dep()
        assert ts.get(blocked.id).status.value == "blocked_dep"
        # Complete dep 2 — now should promote
        ts.update(d2.id, status="done")
        ts.reconcile_blocked_dep()
        assert ts.get(blocked.id).status.value == "scheduled"


# ════════════════════════════════════════════════ TaskService.get_activity

class TestGetActivity:
    """Tests for TaskService.get_activity and get_last_session_start."""

    def test_empty_window_returns_clean_list(self, ts):
        # Far-future since timestamp — no events should match
        result = ts.get_activity(since="2099-01-01T00:00:00Z")
        assert result == []

    def test_since_boundary_includes_events_on_or_after(self, ts):
        t = ts.create("Alpha")
        ts.update(t.id, status="done")
        # Find the changed_at of the done event
        history = ts.get_history(t.id)
        done_evt = next(e for e in history if e.new_value == "done")
        # Query exactly at that timestamp — should include it
        result = ts.get_activity(since=done_evt.changed_at)
        task_ids = [e.task_id for e in result]
        assert t.id in task_ids

    def test_since_boundary_excludes_events_before(self, ts):
        t = ts.create("Beta")
        ts.update(t.id, status="done")
        history = ts.get_history(t.id)
        done_evt = next(e for e in history if e.new_value == "done")
        # Far-past since — both created and done events should appear
        result = ts.get_activity(since="2020-01-01T00:00:00Z")
        task_ids = [e.task_id for e in result]
        assert t.id in task_ids
        # Far-future — no events
        result_empty = ts.get_activity(since="2099-01-01T00:00:00Z")
        assert result_empty == []

    def test_project_scoping_excludes_other_project(self, ts, ps):
        p1 = ps.create("P1")
        p2 = ps.create("P2")
        t1 = ts.create("In P1", project_id=p1.id)
        t2 = ts.create("In P2", project_id=p2.id)
        result = ts.get_activity(since="2020-01-01T00:00:00Z", project_id=p1.id)
        task_ids = {e.task_id for e in result}
        assert t1.id in task_ids
        assert t2.id not in task_ids

    def test_unscoped_returns_all_projects(self, ts, ps):
        p1 = ps.create("P1")
        p2 = ps.create("P2")
        t1 = ts.create("In P1", project_id=p1.id)
        t2 = ts.create("In P2", project_id=p2.id)
        result = ts.get_activity(since="2020-01-01T00:00:00Z")
        task_ids = {e.task_id for e in result}
        assert t1.id in task_ids
        assert t2.id in task_ids

    def test_activity_entry_contains_task_name_from_snapshot(self, ts):
        t = ts.create("Named Task")
        ts.update(t.id, status="done")
        result = ts.get_activity(since="2020-01-01T00:00:00Z")
        entry = next((e for e in result if e.task_id == t.id), None)
        assert entry is not None
        # Name should come from task_snapshot, not fallback to "T{id}"
        assert entry.task_name == "Named Task"

    def test_get_last_session_start_returns_none_when_no_sessions(self, ts):
        result = ts.get_last_session_start()
        assert result is None

    def test_get_last_session_start_returns_most_recent(self, ts, conn):
        from loom.repository import LoomSessionRepository
        from loom.models import LoomSession
        repo = LoomSessionRepository(conn)
        repo.insert(LoomSession(date="2026-07-28", session_number=1, started_at="2026-07-28T01:00:00Z"))
        repo.insert(LoomSession(date="2026-07-29", session_number=1, started_at="2026-07-29T02:00:00Z"))
        result = ts.get_last_session_start()
        # list_recent returns newest first (ORDER BY id DESC)
        assert result == "2026-07-29T02:00:00Z"


# ════════════════════════════════════════════════ Task.priority INTEGER migration

class TestTaskPriorityIntegerMigration:
    """Tests for the v7 migration: Task.priority TEXT → INTEGER."""

    def test_default_priority_is_zero(self, ts):
        t = ts.create("No priority given")
        assert t.priority == 0
        assert isinstance(t.priority, int)

    def test_integer_priority_round_trips_via_create(self, ts):
        t = ts.create("High pri task", priority=5)
        assert t.priority == 5
        assert isinstance(t.priority, int)

    def test_integer_priority_round_trips_via_update(self, ts):
        t = ts.create("Low pri task", priority=1)
        updated = ts.update(t.id, priority=8)
        assert updated.priority == 8
        assert isinstance(updated.priority, int)

    def test_urgency_score_preserved_for_high_equivalent(self, ts):
        # Old: priority='H' → PRIORITY_VALUE['H']=3 → score contribution = 3*6 = 18
        # New: priority=3 → (3 or 0)*6 = 18
        t_new = ts.create("Pri 3", priority=3, status="scheduled")
        # urgency_score = priority*6 + age component; priority*6 should be 18
        assert t_new.urgency_score >= 18  # at minimum the priority component

    def test_urgency_score_zero_for_no_priority(self, ts):
        t = ts.create("No pri", priority=0, status="scheduled")
        # urgency_score = 0*6 + small age component; age in seconds gives tiny score
        assert t.urgency_score < 1  # effectively zero, only age component

    def test_v7_migration_backfills_old_schema_db(self, conn):
        """Simulate a pre-v7 DB and confirm migration converts text→int correctly."""
        import sqlite3
        # Build a v6-style DB with TEXT priority values
        test_conn = sqlite3.connect(":memory:")
        test_conn.row_factory = sqlite3.Row
        from loom.db import _run_migrations, _DDL_SCHEMA_VERSION, _DDL_TASKS, _DDL_PROJECTS
        from loom.db import _DDL_TASK_STATUS_CHECK_V4, _DDL_TASK_STATUS_CHECK_UPDATE_V4
        test_conn.execute(_DDL_SCHEMA_VERSION)
        test_conn.execute(_DDL_PROJECTS)
        test_conn.execute(_DDL_TASKS)
        test_conn.execute(_DDL_TASK_STATUS_CHECK_V4)
        test_conn.execute(_DDL_TASK_STATUS_CHECK_UPDATE_V4)
        # Add v4/v5/v6 columns including the TEXT priority
        test_conn.execute("ALTER TABLE tasks ADD COLUMN goal_id INTEGER")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'none'")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN wait_until TEXT")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN depends TEXT")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN blocked_reason TEXT")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN blocked_note TEXT")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN urgency_score REAL DEFAULT 0")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN context_tag TEXT")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN estimated_sessions INTEGER")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN actual_sessions INTEGER DEFAULT 0")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN handoff_note TEXT")
        test_conn.execute("ALTER TABLE tasks ADD COLUMN files TEXT DEFAULT NULL")
        test_conn.execute("INSERT INTO schema_version (version) VALUES (6)")
        # Seed rows with old TEXT priority values
        test_conn.execute(
            "INSERT INTO tasks (name, status, created_at, updated_at, priority) VALUES (?,?,?,?,?)",
            ("H task", "scheduled", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "H"),
        )
        test_conn.execute(
            "INSERT INTO tasks (name, status, created_at, updated_at, priority) VALUES (?,?,?,?,?)",
            ("M task", "scheduled", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "M"),
        )
        test_conn.execute(
            "INSERT INTO tasks (name, status, created_at, updated_at, priority) VALUES (?,?,?,?,?)",
            ("L task", "scheduled", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "L"),
        )
        test_conn.execute(
            "INSERT INTO tasks (name, status, created_at, updated_at, priority) VALUES (?,?,?,?,?)",
            ("none task", "scheduled", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "none"),
        )
        test_conn.execute(
            "INSERT INTO tasks (name, status, created_at, updated_at, priority) VALUES (?,?,?,?,?)",
            ("numeric task", "scheduled", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "7"),
        )
        test_conn.commit()
        # Run the migration
        with test_conn:
            _run_migrations(test_conn, from_version=6)
            test_conn.execute("UPDATE schema_version SET version = 7")
        # Verify conversion
        rows = {
            r["name"]: r["priority"]
            for r in test_conn.execute("SELECT name, priority FROM tasks").fetchall()
        }
        assert rows["H task"] == 3
        assert rows["M task"] == 2
        assert rows["L task"] == 1
        assert rows["none task"] == 0
        assert rows["numeric task"] == 7
        test_conn.close()
