"""Business-logic layer — validation, constraints, service-level logging.

Both services wrap their repository counterpart and log every public method call
via the service logger (INFO on entry/exit, ERROR on exception).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .filters import ProjectFilter, SortSpec, TaskFilter
from .logging_config import get_service_logger
from .models import Project, Status, Task
from .repository import ProjectRepository, TaskRepository

# Sentinel for "caller did not supply this argument" (distinct from None).
_MISSING = object()

# ------------------------------------------------------------------ helpers

_VALID_STATUSES = {s.value for s in Status}


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
            _slog_result("TaskService.update", f"id={result.id}")
            return result
        except Exception as exc:
            _slog_error("TaskService.update", exc)
            raise

    def delete(self, task_id: int) -> bool:
        _slog("TaskService.delete", f"id={task_id}")
        try:
            result = self._repo.delete(task_id)
            _slog_result("TaskService.delete", f"deleted={result}")
            return result
        except Exception as exc:
            _slog_error("TaskService.delete", exc)
            raise

    # ------------------------------------------------------------------ private

    def _assert_project_exists(self, project_id: int) -> None:
        if self._project_repo.get_by_id(project_id) is None:
            raise ValueError(f"Project {project_id} does not exist")
