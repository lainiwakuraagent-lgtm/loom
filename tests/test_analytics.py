"""Tests for jar.analytics — AnalyticsService metric computation."""

import pytest
from jar.db import get_connection, init_db
from jar.service import ProjectService, TaskService
from jar.analytics import AnalyticsService


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
def svc(conn):
    return AnalyticsService(conn)


# ════════════════════════════════════════════════ TestDeadlineHealth


class TestDeadlineHealth:
    def test_empty_db(self, svc):
        data = svc.deadline_health()
        assert data["deadline_pushes"]["push_rate"] == 0.0
        assert data["miss_rate"]["overall_miss_rate"] == 0.0

    def test_detects_deadline_push(self, ts, svc):
        t = ts.create("Task A", deadline="2026-03-01")
        ts.update(t.id, deadline="2026-04-01")
        data = svc.deadline_health()
        pushes = data["deadline_pushes"]
        assert pushes["push_count"] == 1
        assert pushes["pull_count"] == 0
        assert pushes["push_rate"] == 1.0

    def test_detects_deadline_pull(self, ts, svc):
        t = ts.create("Task B", deadline="2026-04-01")
        ts.update(t.id, deadline="2026-03-01")
        data = svc.deadline_health()
        pushes = data["deadline_pushes"]
        assert pushes["pull_count"] == 1
        assert pushes["push_count"] == 0

    def test_miss_rate_zero_when_all_on_time(self, ts, svc):
        t = ts.create("On time", deadline="2099-12-31", status="todo")
        ts.update(t.id, status="done")
        data = svc.deadline_health()
        assert data["miss_rate"]["overall_miss_rate"] == 0.0

    def test_miss_rate_one_when_late(self, ts, svc):
        t = ts.create("Late", deadline="2020-01-01", status="todo")
        ts.update(t.id, status="done")
        data = svc.deadline_health()
        assert data["miss_rate"]["overall_miss_rate"] == 1.0

    def test_miss_by_tag(self, ts, svc):
        t1 = ts.create("Feature", tags=["feature"], deadline="2099-12-31")
        ts.update(t1.id, status="done")
        t2 = ts.create("Bug",     tags=["bug"],     deadline="2020-01-01")
        ts.update(t2.id, status="done")
        data = svc.deadline_health()
        miss = data["miss_rate"]["by_tag"]
        assert miss["feature"]["miss_rate"] == 0.0
        assert miss["bug"]["miss_rate"] == 1.0


# ════════════════════════════════════════════════ TestVelocity


class TestVelocity:
    def test_empty_returns_insufficient_data(self, svc):
        data = svc.velocity()
        assert data["time_to_done"].get("insufficient_data") is True

    def test_time_to_done_single_task(self, ts, svc):
        t = ts.create("Quick", status="todo")
        ts.update(t.id, status="done")
        data = svc.velocity()
        ttd = data["time_to_done"]
        assert ttd["count"] == 1
        assert ttd.get("insufficient_data") is None
        assert ttd["stats"]["min_days"] >= 0

    def test_completion_velocity_counts_tasks(self, ts, svc):
        for i in range(3):
            t = ts.create(f"Task {i}")
            ts.update(t.id, status="done")
        data = svc.velocity()
        assert data["time_to_done"]["count"] == 3
        assert data["completion_velocity"]["overall"]["total_completed"] == 3


# ════════════════════════════════════════════════ TestCapacity


class TestCapacity:
    def test_empty_db(self, svc):
        data = svc.capacity()
        assert data["deadline_clustering"]["avg_per_week"] == 0.0

    def test_overloaded_week_detection(self, ts, svc):
        # 1 heavy week (10 tasks) + several light weeks (1 task each)
        # This gives avg << 10, so the heavy week exceeds avg + std_dev.
        for i in range(10):
            ts.create(f"Heavy {i}", deadline="2026-03-02")
        for i, date in enumerate(["2026-01-05", "2026-01-12", "2026-01-19",
                                   "2026-01-26", "2026-02-02", "2026-02-09"]):
            ts.create(f"Light {i}", deadline=date)
        data = svc.capacity()
        clustering = data["deadline_clustering"]
        assert len(clustering["overloaded_weeks"]) >= 1
        overloaded_week_counts = [w["count"] for w in clustering["overloaded_weeks"]]
        assert max(overloaded_week_counts) == 10


# ════════════════════════════════════════════════ TestBehavior


class TestBehavior:
    def test_empty_db(self, svc):
        data = svc.behavior()
        assert data["task_rot"]["current_rotting_count"] == 0
        assert data["recovery_lag"]["missed_and_recovered_count"] == 0
        assert data["status_reversals"]["total_reversals"] == 0

    def test_recovery_lag_calculation(self, ts, svc):
        # Task with a past deadline that gets done after deadline
        t = ts.create("Late task", deadline="2020-06-01", status="todo")
        ts.update(t.id, status="done")
        data = svc.behavior()
        lag = data["recovery_lag"]
        assert lag["missed_and_recovered_count"] == 1
        assert lag["avg_lag_days"] > 0

    def test_status_reversal_detection(self, ts, svc):
        t = ts.create("Unstable")
        ts.update(t.id, status="done")
        ts.update(t.id, status="in_progress")  # reversal
        data = svc.behavior()
        rev = data["status_reversals"]
        assert rev["total_reversals"] >= 1
        assert rev["by_type"]["done_to_in_progress"] >= 1

    def test_no_reversal_for_forward_transitions(self, ts, svc):
        t = ts.create("Normal")
        ts.update(t.id, status="in_progress")
        ts.update(t.id, status="done")
        data = svc.behavior()
        assert data["status_reversals"]["total_reversals"] == 0


# ════════════════════════════════════════════════ TestRealism


class TestRealism:
    def test_empty_db(self, svc):
        data = svc.realism()
        assert data["deadline_realism"]["overall_drs"] == 0.0
        assert data["abandonment"]["abandonment_rate"] == 0.0

    def test_drs_full_on_time(self, ts, svc):
        # No deadline changes → committed deadline; done before deadline
        t = ts.create("On track", deadline="2099-12-31")
        ts.update(t.id, status="done")
        data = svc.realism()
        assert data["deadline_realism"]["overall_drs"] == 1.0

    def test_drs_excludes_pushed_deadline(self, ts, svc):
        t = ts.create("Pushed", deadline="2099-03-01")
        ts.update(t.id, deadline="2099-06-01")  # push → excluded from DRS
        ts.update(t.id, status="done")
        data = svc.realism()
        # This task had a deadline change, so it should be excluded from DRS
        assert data["deadline_realism"].get("insufficient_data") is True

    def test_abandonment_rate(self, ts, svc):
        t = ts.create("Abandoned")
        ts.delete(t.id)  # deleted without ever going to done
        data = svc.realism()
        ab = data["abandonment"]
        assert ab["abandoned_count"] >= 1
        assert ab["abandonment_rate"] > 0.0

    def test_abandonment_rate_excludes_completed(self, ts, svc):
        t1 = ts.create("Completed")
        ts.update(t1.id, status="done")
        ts.delete(t1.id)  # deleted but was done → not abandoned
        t2 = ts.create("Just abandoned")
        ts.delete(t2.id)
        data = svc.realism()
        ab = data["abandonment"]
        assert ab["abandoned_count"] == 1


# ════════════════════════════════════════════════ TestSummary


class TestSummary:
    def test_empty_db_returns_all_keys(self, svc):
        data = svc.summary()
        required_keys = [
            "miss_rate_overall", "active_overdue_count", "deadline_push_rate",
            "tasks_completed", "completion_velocity_4w_avg", "rotting_tasks_count",
            "abandonment_rate", "deadline_realism_score", "status_reversals",
            "avg_recovery_lag_days",
        ]
        for key in required_keys:
            assert key in data, f"Missing key: {key}"

    def test_summary_reflects_completed_tasks(self, ts, svc):
        t = ts.create("Done task")
        ts.update(t.id, status="done")
        data = svc.summary()
        assert data["tasks_completed"] == 1
