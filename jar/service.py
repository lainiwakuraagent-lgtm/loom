"""Business-logic layer — validation, constraints, service-level logging.

Both services wrap their repository counterpart and log every public method call
via the service logger (INFO on entry/exit, ERROR on exception).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Optional

from .filters import ProjectFilter, SortSpec, TaskFilter
from .logging_config import get_service_logger
from .models import EventType, Goal, GoalStatus, LoomSession, Project, Status, Task, TaskEvent
from .repository import (
    GoalRepository,
    LoomSessionRepository,
    ProjectRepository,
    TaskEventRepository,
    TaskRepository,
)

# Sentinel for "caller did not supply this argument" (distinct from None).
_MISSING = object()

# Fields tracked in the lifecycle event log.
_TRACKED_FIELDS = ("name", "description", "tags", "deadline", "status", "project_id")

# ------------------------------------------------------------------ helpers

_VALID_STATUSES = {s.value for s in Status}
_VALID_GOAL_STATUSES = {s.value for s in GoalStatus}

PRIORITY_VALUE = {"H": 3, "M": 2, "L": 1, "none": 0}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _field_to_str(field: str, value: object) -> Optional[str]:
    """Normalise a task field value to a storable string for event comparison."""
    if value is None:
        return None
    if field == "tags":
        # Sort for stable comparison — tag order is not semantically meaningful.
        tags = list(value)  # type: ignore[arg-type]
        return ",".join(sorted(tags)) if tags else None
    if isinstance(value, Status):
        return value.value
    return str(value)


def _slog(method: str, detail: str = "") -> None:
    get_service_logger().info("%s | %s", method, detail)


def _slog_result(method: str, result_summary: str) -> None:
    get_service_logger().info("%s | result: %s", method, result_summary)


def _slog_error(method: str, exc: Exception) -> None:
    get_service_logger().error("%s | ERROR: %s", method, exc, exc_info=True)


def _validate_status(status: str) -> None:
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: {sorted(_VALID_STATUSES)}"
        )


def _validate_goal_status(status: str) -> None:
    if status not in _VALID_GOAL_STATUSES:
        raise ValueError(
            f"Invalid goal status {status!r}. Must be one of: {sorted(_VALID_GOAL_STATUSES)}"
        )


def _compute_urgency(task: Task) -> float:
    """Compute urgency score from priority, deadline, and age."""
    score = PRIORITY_VALUE.get(task.priority or "none", 0) * 6
    if task.deadline:
        try:
            days_until = (date.fromisoformat(task.deadline) - date.today()).days
            score += max(0, 14 - days_until) * 12
        except ValueError:
            pass
    if task.created_at:
        try:
            created = datetime.fromisoformat(task.created_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created).days
            score += age_days * 0.003
        except ValueError:
            pass
    return round(score, 3)


# ══════════════════════════════════════════════════════════════════ ProjectService


class ProjectService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._repo = ProjectRepository(conn)
        self._task_repo = TaskRepository(conn)

    # ------------------------------------------------------------------ read

    def get(self, project_id: int) -> Optional[Project]:
        _slog("ProjectService.get", f"id={project_id}")
        try:
            result = self._repo.get_by_id(project_id)
            _slog_result("ProjectService.get", f"found={result is not None}")
            return result
        except Exception as exc:
            _slog_error("ProjectService.get", exc)
            raise

    def list_filtered(
        self,
        f: Optional[ProjectFilter] = None,
        sort: Optional[SortSpec] = None,
    ) -> list[Project]:
        _slog("ProjectService.list_filtered", f"filter={f!r} sort={sort!r}")
        try:
            result = self._repo.list_filtered(f, sort)
            _slog_result("ProjectService.list_filtered", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("ProjectService.list_filtered", exc)
            raise

    # ------------------------------------------------------------------ write

    def create(
        self,
        name: str,
        description: Optional[str] = None,
        start_date: Optional[str] = None,
        deployment_date: Optional[str] = None,
        goal_id: Optional[int] = None,
        status: str = "planned",
    ) -> Project:
        _slog("ProjectService.create", f"name={name!r}")
        try:
            project = Project(
                name=name,
                description=description,
                start_date=start_date,
                deployment_date=deployment_date,
                goal_id=goal_id,
                status=status,
            )
            result = self._repo.insert(project)
            _slog_result("ProjectService.create", f"id={result.id}")
            return result
        except Exception as exc:
            _slog_error("ProjectService.create", exc)
            raise

    def update(
        self,
        project_id: int,
        name: Optional[str] = None,
        description: Optional[str] = _MISSING,
        start_date: Optional[str] = _MISSING,
        deployment_date: Optional[str] = _MISSING,
        goal_id: Optional[int] = _MISSING,
        status: Optional[str] = None,
    ) -> Project:
        _slog("ProjectService.update", f"id={project_id}")
        try:
            project = self._repo.get_by_id(project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")

            if name is not None:
                project.name = name
            if description is not _MISSING:
                project.description = description
            if start_date is not _MISSING:
                project.start_date = start_date
            if deployment_date is not _MISSING:
                project.deployment_date = deployment_date
            if goal_id is not _MISSING:
                project.goal_id = goal_id
            if status is not None:
                project.status = status

            result = self._repo.update(project)
            _slog_result("ProjectService.update", f"id={result.id}")
            return result
        except Exception as exc:
            _slog_error("ProjectService.update", exc)
            raise

    def delete(self, project_id: int) -> bool:
        """Hard delete — cascades to all child tasks. NOT exposed via CLI."""
        _slog("ProjectService.delete", f"id={project_id}")
        try:
            result = self._repo.delete(project_id)
            _slog_result("ProjectService.delete", f"deleted={result}")
            return result
        except Exception as exc:
            _slog_error("ProjectService.delete", exc)
            raise

    def tasks_for_project(self, project_id: int) -> list[Task]:
        _slog("ProjectService.tasks_for_project", f"project_id={project_id}")
        try:
            result = self._repo.tasks_for_project(project_id)
            _slog_result("ProjectService.tasks_for_project", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("ProjectService.tasks_for_project", exc)
            raise


# ══════════════════════════════════════════════════════════════════ TaskService


class TaskService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._repo = TaskRepository(conn)
        self._project_repo = ProjectRepository(conn)
        self._event_repo = TaskEventRepository(conn)

    # ------------------------------------------------------------------ read

    def get(self, task_id: int) -> Optional[Task]:
        _slog("TaskService.get", f"id={task_id}")
        try:
            result = self._repo.get_by_id(task_id)
            _slog_result("TaskService.get", f"found={result is not None}")
            return result
        except Exception as exc:
            _slog_error("TaskService.get", exc)
            raise

    def list_filtered(
        self,
        f: Optional[TaskFilter] = None,
        sort: Optional[SortSpec] = None,
    ) -> list[Task]:
        _slog("TaskService.list_filtered", f"filter={f!r} sort={sort!r}")
        try:
            result = self._repo.list_filtered(f, sort)
            _slog_result("TaskService.list_filtered", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("TaskService.list_filtered", exc)
            raise

    # ------------------------------------------------------------------ write

    def create(
        self,
        name: str,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        deadline: Optional[str] = None,
        project_id: Optional[int] = None,
        status: str = Status.TRIAGE.value,
        goal_id: Optional[int] = None,
        priority: str = "none",
        wait_until: Optional[str] = None,
        depends: Optional[list[int]] = None,
        context_tag: Optional[str] = None,
        estimated_sessions: Optional[int] = None,
    ) -> Task:
        _slog("TaskService.create", f"name={name!r} project_id={project_id}")
        try:
            _validate_status(status)
            if project_id is not None:
                self._assert_project_exists(project_id)

            task = Task(
                name=name,
                description=description,
                tags=tags or [],
                deadline=deadline,
                project_id=project_id,
                status=Status(status),
                goal_id=goal_id,
                priority=priority,
                wait_until=wait_until,
                depends=depends,
                context_tag=context_tag,
                estimated_sessions=estimated_sessions,
            )
            task.urgency_score = _compute_urgency(task)
            result = self._repo.insert(task)
            self._record_created(result)
            _slog_result("TaskService.create", f"id={result.id}")
            return result
        except Exception as exc:
            _slog_error("TaskService.create", exc)
            raise

    def update(
        self,
        task_id: int,
        name: Optional[str] = None,
        description: Optional[str] = _MISSING,
        tags: Optional[list[str]] = _MISSING,
        deadline: Optional[str] = _MISSING,
        project_id: Optional[int] = _MISSING,
        status: Optional[str] = None,
        goal_id: Optional[int] = _MISSING,
        priority: Optional[str] = None,
        wait_until: Optional[str] = _MISSING,
        depends: Optional[list[int]] = _MISSING,
        blocked_reason: Optional[str] = _MISSING,
        blocked_note: Optional[str] = _MISSING,
        context_tag: Optional[str] = _MISSING,
        estimated_sessions: Optional[int] = _MISSING,
        actual_sessions: Optional[int] = None,
        handoff_note: Optional[str] = _MISSING,
    ) -> Task:
        _slog("TaskService.update", f"id={task_id}")
        try:
            task = self._repo.get_by_id(task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")

            # Snapshot old state before any mutations.
            old_task = replace(task, tags=list(task.tags))

            if name is not None:
                task.name = name
            if description is not _MISSING:
                task.description = description
            if tags is not _MISSING:
                task.tags = tags or []
            if deadline is not _MISSING:
                task.deadline = deadline
            if project_id is not _MISSING:
                if project_id is not None:
                    self._assert_project_exists(project_id)
                task.project_id = project_id
            if status is not None:
                _validate_status(status)
                task.status = Status(status)
            if goal_id is not _MISSING:
                task.goal_id = goal_id
            if priority is not None:
                task.priority = priority
            if wait_until is not _MISSING:
                task.wait_until = wait_until
            if depends is not _MISSING:
                task.depends = depends
            if blocked_reason is not _MISSING:
                task.blocked_reason = blocked_reason
            if blocked_note is not _MISSING:
                task.blocked_note = blocked_note
            if context_tag is not _MISSING:
                task.context_tag = context_tag
            if estimated_sessions is not _MISSING:
                task.estimated_sessions = estimated_sessions
            if actual_sessions is not None:
                task.actual_sessions = actual_sessions
            if handoff_note is not _MISSING:
                task.handoff_note = handoff_note

            task.urgency_score = _compute_urgency(task)
            result = self._repo.update(task)
            self._record_field_changes(old_task, result)
            _slog_result("TaskService.update", f"id={result.id}")
            return result
        except Exception as exc:
            _slog_error("TaskService.update", exc)
            raise

    def delete(self, task_id: int) -> bool:
        _slog("TaskService.delete", f"id={task_id}")
        try:
            task = self._repo.get_by_id(task_id)
            if task is None:
                _slog_result("TaskService.delete", "deleted=False (not found)")
                return False
            self._record_deleted(task)
            result = self._repo.delete(task_id)
            _slog_result("TaskService.delete", f"deleted={result}")
            return result
        except Exception as exc:
            _slog_error("TaskService.delete", exc)
            raise

    def get_history(self, task_id: int) -> list[TaskEvent]:
        _slog("TaskService.get_history", f"task_id={task_id}")
        try:
            result = self._event_repo.list_for_task(task_id)
            _slog_result("TaskService.get_history", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("TaskService.get_history", exc)
            raise

    def auto_unblock_dependents(self, completed_task_id: int) -> list[Task]:
        """After a task is done, unblock any tasks whose all deps are now met."""
        _slog("TaskService.auto_unblock_dependents", f"completed_id={completed_task_id}")
        unblocked = []
        try:
            candidates = self._repo.list_filtered(
                TaskFilter(status="blocked_dep")
            )
            for task in candidates:
                if not task.depends or completed_task_id not in task.depends:
                    continue
                # Check all deps are done
                all_done = all(
                    self._is_done(dep_id) for dep_id in task.depends
                )
                if all_done:
                    task.status = Status.SCHEDULED
                    task.urgency_score = _compute_urgency(task)
                    self._repo.update(task)
                    unblocked.append(task)
            _slog_result("TaskService.auto_unblock_dependents", f"unblocked={len(unblocked)}")
            return unblocked
        except Exception as exc:
            _slog_error("TaskService.auto_unblock_dependents", exc)
            raise

    def _is_done(self, task_id: int) -> bool:
        task = self._repo.get_by_id(task_id)
        return task is not None and task.status == Status.DONE

    # ------------------------------------------------------------------ private

    def _assert_project_exists(self, project_id: int) -> None:
        if self._project_repo.get_by_id(project_id) is None:
            raise ValueError(f"Project {project_id} does not exist")

    def _record_created(self, task: Task) -> None:
        event = TaskEvent(
            task_id=task.id,  # type: ignore[arg-type]
            event_type=EventType.CREATED,
            changed_at=task.created_at,  # type: ignore[arg-type]
            task_snapshot=json.dumps(task.to_dict(), default=str),
        )
        self._event_repo.insert(event)

    def _record_field_changes(self, old: Task, new: Task) -> None:
        snapshot = json.dumps(new.to_dict(), default=str)
        changed_at = new.updated_at  # type: ignore[arg-type]

        for field in _TRACKED_FIELDS:
            old_str = _field_to_str(field, getattr(old, field))
            new_str = _field_to_str(field, getattr(new, field))
            if old_str == new_str:
                continue
            event = TaskEvent(
                task_id=new.id,  # type: ignore[arg-type]
                event_type=EventType.UPDATED,
                field_name=field,
                old_value=old_str,
                new_value=new_str,
                changed_at=changed_at,
                task_snapshot=snapshot,
            )
            self._event_repo.insert(event)

    def _record_deleted(self, task: Task) -> None:
        event = TaskEvent(
            task_id=task.id,  # type: ignore[arg-type]
            event_type=EventType.DELETED,
            changed_at=_now_utc(),
            task_snapshot=json.dumps(task.to_dict(), default=str),
        )
        self._event_repo.insert(event)


# ══════════════════════════════════════════════════════════════════ GoalService


class GoalService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._repo = GoalRepository(conn)

    def get(self, goal_id: int) -> Optional[Goal]:
        _slog("GoalService.get", f"id={goal_id}")
        try:
            result = self._repo.get_by_id(goal_id)
            _slog_result("GoalService.get", f"found={result is not None}")
            return result
        except Exception as exc:
            _slog_error("GoalService.get", exc)
            raise

    def list_all(self) -> list[Goal]:
        _slog("GoalService.list_all")
        try:
            result = self._repo.list_all()
            _slog_result("GoalService.list_all", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("GoalService.list_all", exc)
            raise

    def list_active(self) -> list[Goal]:
        _slog("GoalService.list_active")
        try:
            result = self._repo.list_by_status(GoalStatus.ACTIVE.value)
            _slog_result("GoalService.list_active", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("GoalService.list_active", exc)
            raise

    def create(
        self,
        name: str,
        description: Optional[str] = None,
        status: str = GoalStatus.DESIRE.value,
        priority: int = 0,
        estimated_sessions: Optional[int] = None,
    ) -> Goal:
        _slog("GoalService.create", f"name={name!r}")
        try:
            _validate_goal_status(status)
            goal = Goal(
                name=name,
                description=description,
                status=GoalStatus(status),
                priority=priority,
                estimated_sessions=estimated_sessions,
            )
            result = self._repo.insert(goal)
            _slog_result("GoalService.create", f"id={result.id}")
            return result
        except Exception as exc:
            _slog_error("GoalService.create", exc)
            raise

    def update(
        self,
        goal_id: int,
        name: Optional[str] = None,
        description: Optional[str] = _MISSING,
        status: Optional[str] = None,
        priority: Optional[int] = None,
        started_at: Optional[str] = _MISSING,
        completed_at: Optional[str] = _MISSING,
        estimated_sessions: Optional[int] = _MISSING,
        actual_sessions: Optional[int] = None,
    ) -> Goal:
        _slog("GoalService.update", f"id={goal_id}")
        try:
            goal = self._repo.get_by_id(goal_id)
            if goal is None:
                raise ValueError(f"Goal {goal_id} not found")

            if name is not None:
                goal.name = name
            if description is not _MISSING:
                goal.description = description
            if status is not None:
                _validate_goal_status(status)
                goal.status = GoalStatus(status)
            if priority is not None:
                goal.priority = priority
            if started_at is not _MISSING:
                goal.started_at = started_at
            if completed_at is not _MISSING:
                goal.completed_at = completed_at
            if estimated_sessions is not _MISSING:
                goal.estimated_sessions = estimated_sessions
            if actual_sessions is not None:
                goal.actual_sessions = actual_sessions

            result = self._repo.update(goal)
            _slog_result("GoalService.update", f"id={result.id}")
            return result
        except Exception as exc:
            _slog_error("GoalService.update", exc)
            raise

    def delete(self, goal_id: int) -> bool:
        _slog("GoalService.delete", f"id={goal_id}")
        try:
            result = self._repo.delete(goal_id)
            _slog_result("GoalService.delete", f"deleted={result}")
            return result
        except Exception as exc:
            _slog_error("GoalService.delete", exc)
            raise


# ══════════════════════════════════════════════════════════════════ LoomSessionService


class LoomSessionService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._repo = LoomSessionRepository(conn)

    def start_session(
        self,
        date: str,
        session_number: int,
        session_type: Optional[str] = None,
        active_goal_id: Optional[int] = None,
    ) -> LoomSession:
        _slog("LoomSessionService.start_session", f"date={date} n={session_number}")
        try:
            session = LoomSession(
                date=date,
                session_number=session_number,
                type=session_type,
                active_goal_id=active_goal_id,
                started_at=_now_utc(),
            )
            result = self._repo.insert(session)
            _slog_result("LoomSessionService.start_session", f"id={result.id}")
            return result
        except Exception as exc:
            _slog_error("LoomSessionService.start_session", exc)
            raise

    def end_session(
        self,
        session_id: int,
        exit_reason: str,
        context_pct_at_exit: Optional[float] = None,
        handoff_note: Optional[str] = None,
        tasks_started: Optional[list[int]] = None,
        tasks_completed: Optional[list[int]] = None,
    ) -> LoomSession:
        _slog("LoomSessionService.end_session", f"id={session_id}")
        try:
            session = self._repo.get_by_id(session_id)
            if session is None:
                raise ValueError(f"LoomSession {session_id} not found")

            ended = _now_utc()
            session.ended_at = ended
            session.exit_reason = exit_reason
            session.context_pct_at_exit = context_pct_at_exit
            session.handoff_note = handoff_note

            if session.started_at:
                from datetime import datetime as _dt
                start = _dt.fromisoformat(session.started_at.replace("Z", "+00:00"))
                end = _dt.fromisoformat(ended.replace("Z", "+00:00"))
                session.duration_minutes = int((end - start).total_seconds() / 60)

            if tasks_started is not None:
                session.tasks_started = json.dumps(tasks_started)
            if tasks_completed is not None:
                session.tasks_completed = json.dumps(tasks_completed)

            result = self._repo.update(session)
            _slog_result("LoomSessionService.end_session", f"id={result.id} duration={result.duration_minutes}m")
            return result
        except Exception as exc:
            _slog_error("LoomSessionService.end_session", exc)
            raise

    def list_recent(self, limit: int = 20) -> list[LoomSession]:
        _slog("LoomSessionService.list_recent", f"limit={limit}")
        try:
            result = self._repo.list_recent(limit)
            _slog_result("LoomSessionService.list_recent", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("LoomSessionService.list_recent", exc)
            raise
