"""Tests for context.generate_context_snapshot — active goal/project resolution and lineage."""

import pytest
from loom.context import generate_context_snapshot
from loom.db import get_connection, init_db
from loom.service import GoalService, ProjectService, TaskService


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def gs(conn):
    return GoalService(conn)


@pytest.fixture
def ps(conn):
    return ProjectService(conn)


@pytest.fixture
def ts(conn):
    return TaskService(conn)


class TestGenerateContextSnapshot:
    def test_empty_db_returns_none_goal(self, conn):
        snapshot = generate_context_snapshot(conn)
        assert snapshot["active_goal_id"] is None
        assert snapshot["active_goal"] is None
        assert snapshot["active_project"] is None
        assert snapshot["current_task"] is None
        assert snapshot["current_task_lineage"] is None

    def test_resolves_active_goal_when_not_given(self, conn, gs):
        gs.create("Low", status="scheduled", priority=1)
        high = gs.create("High", status="scheduled", priority=5)
        snapshot = generate_context_snapshot(conn)
        assert snapshot["active_goal_id"] == high.id
        assert snapshot["active_goal"]["name"] == "High"

    def test_explicit_goal_id_overrides_resolution(self, conn, gs):
        gs.create("High", status="scheduled", priority=5)
        low = gs.create("Low", status="scheduled", priority=1)
        snapshot = generate_context_snapshot(conn, goal_id=low.id)
        assert snapshot["active_goal_id"] == low.id
        assert snapshot["active_goal"]["name"] == "Low"

    def test_active_project_resolved_under_active_goal(self, conn, gs, ps):
        goal = gs.create("Goal", status="scheduled", priority=1)
        proj = ps.create("Proj", goal_id=goal.id, status="scheduled", priority=1)
        snapshot = generate_context_snapshot(conn)
        assert snapshot["active_project"]["id"] == proj.id

    def test_current_task_lineage_composes_full_chain(self, conn, gs, ps, ts):
        goal = gs.create("Goal", description="Why this exists", status="scheduled", priority=1)
        proj = ps.create("Proj", description="Why this project", goal_id=goal.id,
                          status="scheduled", priority=1)
        ts.create("Do the thing", project_id=proj.id, goal_id=goal.id, status="scheduled", priority="H")

        snapshot = generate_context_snapshot(conn)
        lineage = snapshot["current_task_lineage"]
        assert lineage is not None
        assert lineage["project"]["id"] == proj.id
        assert lineage["project"]["description"] == "Why this project"
        assert lineage["goal"]["id"] == goal.id
        assert lineage["goal"]["description"] == "Why this exists"

    def test_lineage_goal_is_none_when_task_has_no_goal_id(self, conn, ts):
        # No active goal exists, so get_ready_queue runs system-wide and can
        # surface a task that was never assigned a goal_id at all. Lineage
        # must report None here, not fabricate a link to nothing.
        ts.create("No goal, no project", status="scheduled", priority="H")

        snapshot = generate_context_snapshot(conn)
        lineage = snapshot["current_task_lineage"]
        assert lineage is not None
        assert lineage["goal"] is None
        assert lineage["project"] is None
