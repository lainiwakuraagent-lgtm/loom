"""Data-access layer — raw CRUD over SQLite, no business logic."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .filters import ProjectFilter, SortSpec, TaskFilter, build_project_query, build_task_query
from .logging_config import get_db_logger
from .models import EventType, Goal, GoalStatus, LoomSession, Project, Status, Task, TaskEvent


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(op: str, table: str, detail: str = "") -> None:
    get_db_logger().debug("%s | table=%s | %s", op, table, detail)


# ══════════════════════════════════════════════════════════════════ Task repo


class TaskRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ read

    def get_by_id(self, task_id: int) -> Optional[Task]:
        _log("SELECT", "tasks", f"id={task_id}")
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _row_to_task(row) if row else None

    def list_all(self) -> list[Task]:
        _log("SELECT", "tasks", "all")
        rows = self._conn.execute("SELECT * FROM tasks").fetchall()
        return [_row_to_task(r) for r in rows]

    def list_filtered(
        self,
        f: Optional[TaskFilter] = None,
        sort: Optional[SortSpec] = None,
    ) -> list[Task]:
        sql, params = build_task_query(f, sort)
        _log("SELECT", "tasks", f"filter={f!r} sort={sort!r}")
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_task(r) for r in rows]

    # ------------------------------------------------------------------ write

    def insert(self, task: Task) -> Task:
        now = _now_utc()
        _log("INSERT", "tasks", f"name={task.name!r}")
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO tasks (
                    name, description, tags, deadline, project_id, status,
                    goal_id, priority, wait_until, depends, blocked_reason, blocked_note,
                    urgency_score, context_tag, estimated_sessions, actual_sessions,
                    handoff_note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.name,
                    task.description,
                    task.tags_str(),
                    task.deadline,
                    task.project_id,
                    task.status.value if isinstance(task.status, Status) else task.status,
                    task.goal_id,
                    task.priority,
                    task.wait_until,
                    task.depends_str(),
                    task.blocked_reason,
                    task.blocked_note,
                    task.urgency_score,
                    task.context_tag,
                    task.estimated_sessions,
                    task.actual_sessions,
                    task.handoff_note,
                    now,
                    now,
                ),
            )
        task.id = cur.lastrowid
        task.created_at = now
        task.updated_at = now
        return task

    def update(self, task: Task) -> Task:
        if task.id is None:
            raise ValueError("Cannot update a Task with no id")
        now = _now_utc()
        _log("UPDATE", "tasks", f"id={task.id}")
        with self._conn:
            self._conn.execute(
                """
                UPDATE tasks
                SET name=?, description=?, tags=?, deadline=?, project_id=?, status=?,
                    goal_id=?, priority=?, wait_until=?, depends=?, blocked_reason=?,
                    blocked_note=?, urgency_score=?, context_tag=?, estimated_sessions=?,
                    actual_sessions=?, handoff_note=?, updated_at=?
                WHERE id=?
                """,
                (
                    task.name,
                    task.description,
                    task.tags_str(),
                    task.deadline,
                    task.project_id,
                    task.status.value if isinstance(task.status, Status) else task.status,
                    task.goal_id,
                    task.priority,
                    task.wait_until,
                    task.depends_str(),
                    task.blocked_reason,
                    task.blocked_note,
                    task.urgency_score,
                    task.context_tag,
                    task.estimated_sessions,
                    task.actual_sessions,
                    task.handoff_note,
                    now,
                    task.id,
                ),
            )
        task.updated_at = now
        return task

    def delete(self, task_id: int) -> bool:
        _log("DELETE", "tasks", f"id={task_id}")
        with self._conn:
            cur = self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


# ══════════════════════════════════════════════════════════════════ TaskEvent repo


class TaskEventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, event: TaskEvent) -> TaskEvent:
        _log("INSERT", "task_events", f"task_id={event.task_id} type={event.event_type} field={event.field_name}")
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO task_events
                    (task_id, event_type, field_name, old_value, new_value, changed_at, task_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.task_id,
                    event.event_type.value if isinstance(event.event_type, EventType) else event.event_type,
                    event.field_name,
                    event.old_value,
                    event.new_value,
                    event.changed_at,
                    event.task_snapshot,
                ),
            )
        event.id = cur.lastrowid
        return event

    def list_for_task(self, task_id: int) -> list[TaskEvent]:
        _log("SELECT", "task_events", f"task_id={task_id}")
        rows = self._conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY changed_at ASC, id ASC",
            (task_id,),
        ).fetchall()
        return [_row_to_task_event(r) for r in rows]


# ══════════════════════════════════════════════════════════════════ Project repo


class ProjectRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ read

    def get_by_id(self, project_id: int) -> Optional[Project]:
        _log("SELECT", "projects", f"id={project_id}")
        row = self._conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return _row_to_project(row) if row else None

    def list_all(self) -> list[Project]:
        _log("SELECT", "projects", "all")
        rows = self._conn.execute("SELECT * FROM projects").fetchall()
        return [_row_to_project(r) for r in rows]

    def list_filtered(
        self,
        f: Optional[ProjectFilter] = None,
        sort: Optional[SortSpec] = None,
    ) -> list[Project]:
        sql, params = build_project_query(f, sort)
        _log("SELECT", "projects", f"filter={f!r} sort={sort!r}")
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_project(r) for r in rows]

    # ------------------------------------------------------------------ write

    def insert(self, project: Project) -> Project:
        now = _now_utc()
        _log("INSERT", "projects", f"name={project.name!r}")
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO projects (name, description, start_date, deployment_date, goal_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.name,
                    project.description,
                    project.start_date,
                    project.deployment_date,
                    project.goal_id,
                    project.status,
                    now,
                    now,
                ),
            )
        project.id = cur.lastrowid
        project.created_at = now
        project.updated_at = now
        return project

    def update(self, project: Project) -> Project:
        if project.id is None:
            raise ValueError("Cannot update a Project with no id")
        now = _now_utc()
        _log("UPDATE", "projects", f"id={project.id}")
        with self._conn:
            self._conn.execute(
                """
                UPDATE projects
                SET name=?, description=?, start_date=?, deployment_date=?, goal_id=?, status=?, updated_at=?
                WHERE id=?
                """,
                (
                    project.name,
                    project.description,
                    project.start_date,
                    project.deployment_date,
                    project.goal_id,
                    project.status,
                    now,
                    project.id,
                ),
            )
        project.updated_at = now
        return project

    def delete(self, project_id: int) -> bool:
        """Hard delete: removes all child tasks first, then the project — single transaction."""
        _log("DELETE", "tasks", f"cascade for project_id={project_id}")
        _log("DELETE", "projects", f"id={project_id}")
        with self._conn:
            self._conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
            cur = self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0

    def tasks_for_project(self, project_id: int) -> list[Task]:
        """Return all tasks belonging to a project (used by show/detail views)."""
        _log("SELECT", "tasks", f"project_id={project_id}")
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY status, deadline NULLS LAST",
            (project_id,),
        ).fetchall()
        return [_row_to_task(r) for r in rows]


# ══════════════════════════════════════════════════════════════════ Goal repo


class GoalRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_by_id(self, goal_id: int) -> Optional[Goal]:
        _log("SELECT", "goals", f"id={goal_id}")
        row = self._conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return _row_to_goal(row) if row else None

    def list_all(self) -> list[Goal]:
        _log("SELECT", "goals", "all")
        rows = self._conn.execute("SELECT * FROM goals ORDER BY priority DESC, id ASC").fetchall()
        return [_row_to_goal(r) for r in rows]

    def list_by_status(self, status: str) -> list[Goal]:
        _log("SELECT", "goals", f"status={status}")
        rows = self._conn.execute(
            "SELECT * FROM goals WHERE status = ? ORDER BY priority DESC, id ASC", (status,)
        ).fetchall()
        return [_row_to_goal(r) for r in rows]

    def insert(self, goal: Goal) -> Goal:
        now = _now_utc()
        _log("INSERT", "goals", f"name={goal.name!r}")
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO goals (
                    name, description, status, priority, started_at, completed_at,
                    estimated_sessions, actual_sessions, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    goal.name,
                    goal.description,
                    goal.status.value if isinstance(goal.status, GoalStatus) else goal.status,
                    goal.priority,
                    goal.started_at,
                    goal.completed_at,
                    goal.estimated_sessions,
                    goal.actual_sessions,
                    now,
                    now,
                ),
            )
        goal.id = cur.lastrowid
        goal.created_at = now
        goal.updated_at = now
        return goal

    def update(self, goal: Goal) -> Goal:
        if goal.id is None:
            raise ValueError("Cannot update a Goal with no id")
        now = _now_utc()
        _log("UPDATE", "goals", f"id={goal.id}")
        with self._conn:
            self._conn.execute(
                """
                UPDATE goals
                SET name=?, description=?, status=?, priority=?, started_at=?,
                    completed_at=?, estimated_sessions=?, actual_sessions=?, updated_at=?
                WHERE id=?
                """,
                (
                    goal.name,
                    goal.description,
                    goal.status.value if isinstance(goal.status, GoalStatus) else goal.status,
                    goal.priority,
                    goal.started_at,
                    goal.completed_at,
                    goal.estimated_sessions,
                    goal.actual_sessions,
                    now,
                    goal.id,
                ),
            )
        goal.updated_at = now
        return goal

    def delete(self, goal_id: int) -> bool:
        _log("DELETE", "goals", f"id={goal_id}")
        with self._conn:
            cur = self._conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
        return cur.rowcount > 0


# ══════════════════════════════════════════════════════════════════ LoomSession repo


class LoomSessionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_by_id(self, session_id: int) -> Optional[LoomSession]:
        _log("SELECT", "loom_sessions", f"id={session_id}")
        row = self._conn.execute(
            "SELECT * FROM loom_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _row_to_loom_session(row) if row else None

    def list_recent(self, limit: int = 20) -> list[LoomSession]:
        _log("SELECT", "loom_sessions", f"limit={limit}")
        rows = self._conn.execute(
            "SELECT * FROM loom_sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_loom_session(r) for r in rows]

    def insert(self, session: LoomSession) -> LoomSession:
        _log("INSERT", "loom_sessions", f"date={session.date} n={session.session_number}")
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO loom_sessions (
                    date, session_number, type, active_goal_id, started_at, ended_at,
                    duration_minutes, context_pct_at_exit, exit_reason, handoff_note,
                    tasks_started, tasks_completed
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.date,
                    session.session_number,
                    session.type,
                    session.active_goal_id,
                    session.started_at,
                    session.ended_at,
                    session.duration_minutes,
                    session.context_pct_at_exit,
                    session.exit_reason,
                    session.handoff_note,
                    session.tasks_started,
                    session.tasks_completed,
                ),
            )
        session.id = cur.lastrowid
        return session

    def update(self, session: LoomSession) -> LoomSession:
        if session.id is None:
            raise ValueError("Cannot update a LoomSession with no id")
        _log("UPDATE", "loom_sessions", f"id={session.id}")
        with self._conn:
            self._conn.execute(
                """
                UPDATE loom_sessions
                SET ended_at=?, duration_minutes=?, context_pct_at_exit=?,
                    exit_reason=?, handoff_note=?, tasks_started=?, tasks_completed=?
                WHERE id=?
                """,
                (
                    session.ended_at,
                    session.duration_minutes,
                    session.context_pct_at_exit,
                    session.exit_reason,
                    session.handoff_note,
                    session.tasks_started,
                    session.tasks_completed,
                    session.id,
                ),
            )
        return session


# ------------------------------------------------------------------ row mappers


def _row_to_task(row: sqlite3.Row) -> Task:
    keys = row.keys()
    return Task(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        tags=Task.tags_from_str(row["tags"]),
        deadline=row["deadline"],
        project_id=row["project_id"],
        status=Status(row["status"]),
        goal_id=row["goal_id"] if "goal_id" in keys else None,
        priority=row["priority"] if "priority" in keys else "none",
        wait_until=row["wait_until"] if "wait_until" in keys else None,
        depends=Task.depends_from_str(row["depends"] if "depends" in keys else None),
        blocked_reason=row["blocked_reason"] if "blocked_reason" in keys else None,
        blocked_note=row["blocked_note"] if "blocked_note" in keys else None,
        urgency_score=row["urgency_score"] if "urgency_score" in keys else 0.0,
        context_tag=row["context_tag"] if "context_tag" in keys else None,
        estimated_sessions=row["estimated_sessions"] if "estimated_sessions" in keys else None,
        actual_sessions=row["actual_sessions"] if "actual_sessions" in keys else 0,
        handoff_note=row["handoff_note"] if "handoff_note" in keys else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_task_event(row: sqlite3.Row) -> TaskEvent:
    return TaskEvent(
        id=row["id"],
        task_id=row["task_id"],
        event_type=EventType(row["event_type"]),
        field_name=row["field_name"],
        old_value=row["old_value"],
        new_value=row["new_value"],
        changed_at=row["changed_at"],
        task_snapshot=row["task_snapshot"],
    )


def _row_to_project(row: sqlite3.Row) -> Project:
    keys = row.keys()
    return Project(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        start_date=row["start_date"],
        deployment_date=row["deployment_date"],
        goal_id=row["goal_id"] if "goal_id" in keys else None,
        status=row["status"] if "status" in keys else "planned",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        status=GoalStatus(row["status"]),
        priority=row["priority"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        estimated_sessions=row["estimated_sessions"],
        actual_sessions=row["actual_sessions"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_loom_session(row: sqlite3.Row) -> LoomSession:
    return LoomSession(
        id=row["id"],
        date=row["date"],
        session_number=row["session_number"],
        type=row["type"],
        active_goal_id=row["active_goal_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        duration_minutes=row["duration_minutes"],
        context_pct_at_exit=row["context_pct_at_exit"],
        exit_reason=row["exit_reason"],
        handoff_note=row["handoff_note"],
        tasks_started=row["tasks_started"],
        tasks_completed=row["tasks_completed"],
    )
