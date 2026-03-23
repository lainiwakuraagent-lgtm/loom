"""Business-logic layer — validation, constraints, service-level logging.

Both services wrap their repository counterpart and log every public method call
via the service logger (INFO on entry/exit, ERROR on exception).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from .filters import ProjectFilter, SortSpec, TaskFilter
from .logging_config import get_service_logger
from .models import EventType, Project, Status, Task, TaskEvent
from .repository import ProjectRepository, TaskEventRepository, TaskRepository

# Sentinel for "caller did not supply this argument" (distinct from None).
_MISSING = object()

# Fields tracked in the lifecycle event log.
_TRACKED_FIELDS = ("name", "description", "tags", "deadline", "status", "project_id")

# ------------------------------------------------------------------ helpers

_VALID_STATUSES = {s.value for s in Status}


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
    ) -> Project:
        _slog("ProjectService.create", f"name={name!r}")
        try:
            project = Project(
                name=name,
                description=description,
                start_date=start_date,
                deployment_date=deployment_date,
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
        status: str = Status.TODO.value,
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
            )
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
