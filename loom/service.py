"""Business-logic layer — validation, constraints, service-level logging.

Both services wrap their repository counterpart and log every public method call
via the service logger (INFO on entry/exit, ERROR on exception).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Optional

from .filters import ProjectFilter, SortSpec, TaskFilter
from .logging_config import get_service_logger
from .models import EventType, Goal, GoalStatus, LoomSession, Project, ProjectStatus, Status, Task, TaskEvent
from .repository import (
    GoalRepository,
    LoomSessionRepository,
    ProjectRepository,
    TaskEventRepository,
    TaskRepository,
)

_MISSING = object()


@dataclass
class DependencyTree:
    """Upstream and downstream dependency walk for a single task."""
    task: "Task"
    upstream: list    # Tasks this task depends on, BFS order (immediate deps first)
    downstream: list  # Tasks that depend on this task, BFS order


@dataclass
class BlockedDigestEntry:
    """One row in the blocked-tasks digest."""
    task_id: int
    name: str
    status: str            # blocked_dep | blocked_owner | blocked_external
    blocked_note: Optional[str]
    description: Optional[str]
    project_id: Optional[int]


@dataclass
class ActivityEntry:
    """One event in the activity/timeline digest."""
    task_id: int
    task_name: str
    event_type: str        # "created" | "updated" | "deleted"
    field_name: Optional[str]
    old_value: Optional[str]
    new_value: Optional[str]
    changed_at: str        # ISO-8601 UTC
    project_id: Optional[int]


@dataclass
class ProjectStatusReport:
    """Aggregated status snapshot for a single project."""
    project: "Project"
    counts: dict           # {status_value: count} — zero-filled for every Status
    blocked: list          # Tasks in any blocked_* status, with reason/note preserved
    recently_completed: list  # Done tasks, updated_at desc, capped at recent_limit
    next_actionable: list  # Scheduled/in_progress tasks, urgency_score desc, capped
    total: int

# Fields tracked in the lifecycle event log.
_TRACKED_FIELDS = ("name", "description", "tags", "deadline", "status", "project_id")

# ------------------------------------------------------------------ helpers

_VALID_STATUSES = {s.value for s in Status}
_VALID_PROJECT_STATUSES = {s.value for s in ProjectStatus}
_VALID_GOAL_STATUSES = {s.value for s in GoalStatus}

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


def _slog_warn(method: str, detail: str) -> None:
    get_service_logger().warning("%s | %s", method, detail)


def _validate_status(status: str) -> None:
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: {sorted(_VALID_STATUSES)}"
        )


def _validate_project_status(status: str) -> None:
    if status not in _VALID_PROJECT_STATUSES:
        raise ValueError(
            f"Invalid project status {status!r}. Must be one of: {sorted(_VALID_PROJECT_STATUSES)}"
        )


def _validate_goal_status(status: str) -> None:
    if status not in _VALID_GOAL_STATUSES:
        raise ValueError(
            f"Invalid goal status {status!r}. Must be one of: {sorted(_VALID_GOAL_STATUSES)}"
        )


def _compute_urgency(task: Task) -> float:
    """Compute urgency score from priority, deadline, and age."""
    score = (task.priority or 0) * 6
    if task.deadline:
        try:
            days_until = (date.fromisoformat(task.deadline) - date.today()).days
            score += max(0, 14 - days_until) * 12
        except ValueError:
            pass
    if task.created_at:
        try:
            created = datetime.fromisoformat(task.created_at.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
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
        status: str = ProjectStatus.SCHEDULED.value,
        priority: int = 0,
    ) -> Project:
        _slog("ProjectService.create", f"name={name!r}")
        try:
            _validate_project_status(status)
            project = Project(
                name=name,
                description=description,
                start_date=start_date,
                deployment_date=deployment_date,
                goal_id=goal_id,
                status=ProjectStatus(status),
                priority=priority,
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
        priority: Optional[int] = None,
        blocked_reason: Optional[str] = _MISSING,
        blocked_note: Optional[str] = _MISSING,
        handoff_note: Optional[str] = _MISSING,
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
                _validate_project_status(status)
                project.status = ProjectStatus(status)
            if priority is not None:
                project.priority = priority
            if blocked_reason is not _MISSING:
                project.blocked_reason = blocked_reason
            if blocked_note is not _MISSING:
                project.blocked_note = blocked_note
            if handoff_note is not _MISSING:
                project.handoff_note = handoff_note

            if project.status == ProjectStatus.DONE:
                self._warn_if_no_milestone_review(project_id)

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

    def status_report(self, project_id: int, recent_limit: int = 5) -> ProjectStatusReport:
        """Aggregate task counts and split by status group for a quick project health view."""
        _slog("ProjectService.status_report", f"project_id={project_id} recent_limit={recent_limit}")
        try:
            project = self._repo.get_by_id(project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")
            tasks = self._repo.tasks_for_project(project_id)

            counts = {s.value: 0 for s in Status}
            for t in tasks:
                sv = t.status.value if hasattr(t.status, "value") else str(t.status)
                counts[sv] = counts.get(sv, 0) + 1

            blocked_statuses = {"blocked_dep", "blocked_owner", "blocked_external"}
            actionable_statuses = {"scheduled", "in_progress"}

            blocked = [
                t for t in tasks
                if (t.status.value if hasattr(t.status, "value") else str(t.status)) in blocked_statuses
            ]

            done = [
                t for t in tasks
                if (t.status.value if hasattr(t.status, "value") else str(t.status)) == "done"
            ]
            done.sort(key=lambda t: t.updated_at or "", reverse=True)

            actionable = [
                t for t in tasks
                if (t.status.value if hasattr(t.status, "value") else str(t.status)) in actionable_statuses
            ]
            actionable.sort(key=lambda t: t.urgency_score, reverse=True)

            result = ProjectStatusReport(
                project=project,
                counts=counts,
                blocked=blocked,
                recently_completed=done[:recent_limit],
                next_actionable=actionable[:recent_limit],
                total=len(tasks),
            )
            _slog_result("ProjectService.status_report", f"total={result.total} blocked={len(blocked)}")
            return result
        except Exception as exc:
            _slog_error("ProjectService.status_report", exc)
            raise

    def resolve_active(self, goal_id: int) -> Optional[Project]:
        """Highest-priority project under goal_id currently eligible (scheduled/in_progress)."""
        _slog("ProjectService.resolve_active", f"goal_id={goal_id}")
        try:
            result = self._repo.resolve_active(goal_id)
            _slog_result("ProjectService.resolve_active", f"id={result.id if result else None}")
            return result
        except Exception as exc:
            _slog_error("ProjectService.resolve_active", exc)
            raise

    def _warn_if_no_milestone_review(self, project_id: int) -> None:
        """Lightweight guard, not a block: a project marked done is trusting the
        convention that some task under it was verified via a milestone_review
        tag. Log a warning if that convention wasn't actually followed, rather
        than silently trusting an unenforced convention — no new audit session
        type, just a flag at the point the status write actually happens.
        """
        tasks = self._repo.tasks_for_project(project_id)
        if not any("milestone_review" in t.tags for t in tasks):
            _slog_warn(
                "ProjectService.update",
                f"project {project_id} marked done with no milestone_review-tagged "
                f"task among its {len(tasks)} task(s) — closing without verification.",
            )


# ══════════════════════════════════════════════════════════════════ TaskService


class TaskService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
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

    def get_ready_queue(self, goal_id: Optional[int] = None, limit: int = 10) -> list[Task]:
        """Return top tasks ready to work on, ordered by urgency. Deps met, wait_until passed.

        Self-heals blocked_dep tasks before querying — any task stuck blocked_dep
        with all deps now done is promoted to scheduled on every call.
        """
        _slog("TaskService.get_ready_queue", f"goal_id={goal_id} limit={limit}")
        try:
            self.reconcile_blocked_dep()
            result = self._repo.get_ready_queue(goal_id=goal_id, limit=limit)
            _slog_result("TaskService.get_ready_queue", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("TaskService.get_ready_queue", exc)
            raise

    def get_blocked_digest(
        self,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> list[BlockedDigestEntry]:
        """Return blocked tasks, excluding those whose goal is abandoned/suspended.

        status: None → all three blocked_* variants; otherwise one of
                'blocked_dep', 'blocked_owner', 'blocked_external'.
        project_id: when given, restrict to that project.
        """
        _BLOCKED = {"blocked_dep", "blocked_owner", "blocked_external"}
        if status is not None and status not in _BLOCKED:
            raise ValueError(
                f"status must be one of {sorted(_BLOCKED)} or None, got {status!r}"
            )

        _slog("TaskService.get_blocked_digest", f"status={status} project_id={project_id}")
        try:
            rows = self._repo.get_blocked_digest(status=status, project_id=project_id)
            result = [
                BlockedDigestEntry(
                    task_id=r["task_id"],
                    name=r["name"],
                    status=r["status"],
                    blocked_note=r["blocked_note"],
                    description=r["description"],
                    project_id=r["project_id"],
                )
                for r in rows
            ]
            _slog_result("TaskService.get_blocked_digest", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("TaskService.get_blocked_digest", exc)
            raise

    def get_dependency_tree(self, task_id: int) -> DependencyTree:
        """Walk upstream (what this depends on) and downstream (what depends on this)."""
        _slog("TaskService.get_dependency_tree", f"task_id={task_id}")
        try:
            task = self._repo.get_by_id(task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")

            # Upstream: BFS over depends chain, visited-set guards against cycles
            upstream: list = []
            visited_up: set = {task_id}
            queue = list(task.depends or [])
            while queue:
                dep_id = queue.pop(0)
                if dep_id in visited_up:
                    continue
                visited_up.add(dep_id)
                dep = self._repo.get_by_id(dep_id)
                if dep:
                    upstream.append(dep)
                    for next_id in (dep.depends or []):
                        if next_id not in visited_up:
                            queue.append(next_id)

            # Downstream: BFS over reverse depends, visited-set guards against cycles
            downstream: list = []
            visited_down: set = {task_id}
            down_queue = [task_id]
            while down_queue:
                tid = down_queue.pop(0)
                for dep_task in self._repo.get_dependents(tid):
                    if dep_task.id not in visited_down:
                        visited_down.add(dep_task.id)
                        downstream.append(dep_task)
                        down_queue.append(dep_task.id)

            _slog_result("TaskService.get_dependency_tree",
                         f"upstream={len(upstream)} downstream={len(downstream)}")
            return DependencyTree(task=task, upstream=upstream, downstream=downstream)
        except Exception as exc:
            _slog_error("TaskService.get_dependency_tree", exc)
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
        priority: int = 0,
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
        priority: Optional[int] = None,
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
            if status is not None and Status(status) == Status.DONE:
                self.auto_unblock_dependents(result.id)
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

    def get_activity(
        self,
        since: str,
        project_id: Optional[int] = None,
    ) -> list[ActivityEntry]:
        """Return a flat list of ActivityEntry for all events on or after *since*.

        Task names are resolved from task_snapshot (no live-row dependency, so
        events for deleted tasks are included in the unscoped view).
        """
        _slog("TaskService.get_activity", f"since={since} project_id={project_id}")
        try:
            events = self._event_repo.list_since(since, project_id=project_id)
            result: list[ActivityEntry] = []
            for e in events:
                task_name = f"T{e.task_id}"
                ev_project_id: Optional[int] = None
                if e.task_snapshot:
                    try:
                        snap = json.loads(e.task_snapshot)
                        if snap.get("name"):
                            task_name = snap["name"]
                        ev_project_id = snap.get("project_id")
                    except (ValueError, KeyError, TypeError):
                        pass
                et = e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type)
                result.append(ActivityEntry(
                    task_id=e.task_id,
                    task_name=task_name,
                    event_type=et,
                    field_name=e.field_name,
                    old_value=e.old_value,
                    new_value=e.new_value,
                    changed_at=e.changed_at,
                    project_id=ev_project_id,
                ))
            _slog_result("TaskService.get_activity", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("TaskService.get_activity", exc)
            raise

    def get_last_session_start(self) -> Optional[str]:
        """Return the started_at ISO timestamp of the most recent LoomSession, or None."""
        _slog("TaskService.get_last_session_start", "")
        try:
            session_repo = LoomSessionRepository(self._conn)
            sessions = session_repo.list_recent(limit=1)
            if sessions and sessions[0].started_at:
                return sessions[0].started_at
            return None
        except Exception as exc:
            _slog_error("TaskService.get_last_session_start", exc)
            return None

    def reconcile_blocked_dep(self) -> list[Task]:
        """Sweep all blocked_dep tasks and promote any whose deps are fully done.

        Generalises auto_unblock_dependents from event-driven to batch — catches
        tasks stuck blocked_dep via hand-edits, retroactive depends, or any path
        that didn't go through TaskService.update's done-transition.
        Called at the start of get_ready_queue so the queue self-heals on every
        invocation.
        """
        _slog("TaskService.reconcile_blocked_dep", "full sweep")
        unblocked = []
        try:
            candidates = self._repo.list_filtered(TaskFilter(status="blocked_dep"))
            for task in candidates:
                if not task.depends:
                    # No deps declared — shouldn't be blocked_dep; promote it.
                    task.status = Status.SCHEDULED
                    task.urgency_score = _compute_urgency(task)
                    self._repo.update(task)
                    unblocked.append(task)
                    continue
                if all(self._is_done(dep_id) for dep_id in task.depends):
                    task.status = Status.SCHEDULED
                    task.urgency_score = _compute_urgency(task)
                    self._repo.update(task)
                    unblocked.append(task)
            _slog_result("TaskService.reconcile_blocked_dep", f"promoted={len(unblocked)}")
            return unblocked
        except Exception as exc:
            _slog_error("TaskService.reconcile_blocked_dep", exc)
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
        """Return goals that are actively being worked on (scheduled or in_progress)."""
        _slog("GoalService.list_active")
        try:
            scheduled = self._repo.list_by_status(GoalStatus.SCHEDULED.value)
            in_progress = self._repo.list_by_status(GoalStatus.IN_PROGRESS.value)
            result = scheduled + in_progress
            _slog_result("GoalService.list_active", f"count={len(result)}")
            return result
        except Exception as exc:
            _slog_error("GoalService.list_active", exc)
            raise

    def resolve_active(self) -> Optional[Goal]:
        """The single highest-priority goal among scheduled/in_progress (priority DESC, id ASC tiebreak).

        This is the sole "active goal" signal — no separate active status/flag.
        """
        _slog("GoalService.resolve_active")
        try:
            result = self._repo.resolve_active()
            _slog_result("GoalService.resolve_active", f"id={result.id if result else None}")
            return result
        except Exception as exc:
            _slog_error("GoalService.resolve_active", exc)
            raise

    def activate(self, goal_id: int) -> Goal:
        """Mark a goal as active: bump its priority above the current eligible max, set scheduled.

        Priority ordering is the only signal for "which goal is active" — this does not
        touch any other goal's status. Switching goals is purely a matter of which one
        currently has the highest priority among scheduled/in_progress.
        """
        _slog("GoalService.activate", f"id={goal_id}")
        try:
            current = self._repo.get_by_id(goal_id)
            if current is None:
                raise ValueError(f"Goal {goal_id} not found")
            others = [
                g for g in self.list_active()
                if g.id != goal_id
            ]
            max_other_priority = max((g.priority for g in others), default=0)
            new_priority = max(current.priority, max_other_priority + 1)
            result = self.update(goal_id=goal_id, status=GoalStatus.SCHEDULED.value, priority=new_priority)
            _slog_result("GoalService.activate", f"id={result.id} priority={result.priority}")
            return result
        except Exception as exc:
            _slog_error("GoalService.activate", exc)
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
        blocked_reason: Optional[str] = _MISSING,
        blocked_note: Optional[str] = _MISSING,
        handoff_note: Optional[str] = _MISSING,
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
            if blocked_reason is not _MISSING:
                goal.blocked_reason = blocked_reason
            if blocked_note is not _MISSING:
                goal.blocked_note = blocked_note
            if handoff_note is not _MISSING:
                goal.handoff_note = handoff_note

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
