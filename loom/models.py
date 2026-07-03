"""Data models for JAR — projects, tasks, goals, sessions, and supporting enums."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Status(str, Enum):
    TRIAGE        = "triage"
    DESIRE        = "desire"
    SCHEDULED     = "scheduled"
    IN_PROGRESS   = "in_progress"
    BLOCKED_DEP   = "blocked_dep"
    BLOCKED_OWNER = "blocked_owner"
    SUSPENDED     = "suspended"
    DONE          = "done"
    FAILED        = "failed"


class GoalStatus(str, Enum):
    DESIRE      = "desire"
    ACTIVE      = "active"
    IN_PROGRESS = "in_progress"
    BLOCKED     = "blocked"
    REVIEW      = "review"
    COMPLETED   = "completed"
    ABANDONED   = "abandoned"


class TagEnum(str, Enum):
    """Suggested tag values. Tasks may also use arbitrary free-text tags."""
    BUG      = "bug"
    FEATURE  = "feature"
    CHORE    = "chore"
    DOCS     = "docs"
    RESEARCH = "research"
    DESIGN   = "design"


SUGGESTED_TAGS: list[str] = [t.value for t in TagEnum]


class EventType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


@dataclass
class Goal:
    name: str
    id: Optional[int] = None
    description: Optional[str] = None
    status: GoalStatus = GoalStatus.DESIRE
    priority: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    estimated_sessions: Optional[int] = None
    actual_sessions: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, GoalStatus) else self.status,
            "priority": self.priority,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "estimated_sessions": self.estimated_sessions,
            "actual_sessions": self.actual_sessions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Task:
    name: str
    status: Status = Status.TRIAGE
    id: Optional[int] = None
    description: Optional[str] = None
    # Stored as a comma-separated string in the DB; exposed as a list here.
    tags: list[str] = field(default_factory=list)
    deadline: Optional[str] = None          # ISO-8601 date string, e.g. "2025-06-01"
    project_id: Optional[int] = None
    # LOOM additions
    goal_id: Optional[int] = None
    priority: str = "none"                  # H | M | L | none
    wait_until: Optional[str] = None        # ISO-8601 date; defer until this date
    depends: Optional[list[int]] = None     # task IDs this task is blocked on
    blocked_reason: Optional[str] = None    # blocked_dep | blocked_owner
    blocked_note: Optional[str] = None
    urgency_score: float = 0.0
    context_tag: Optional[str] = None       # @research | @implement | @communicate | @plan | @blocked
    estimated_sessions: Optional[int] = None
    actual_sessions: int = 0
    handoff_note: Optional[str] = None
    created_at: Optional[str] = None        # ISO-8601 UTC datetime
    updated_at: Optional[str] = None        # ISO-8601 UTC datetime

    # ------------------------------------------------------------------ helpers

    def tags_str(self) -> str:
        """Return tags as a comma-separated string for DB storage."""
        return ",".join(self.tags)

    @staticmethod
    def tags_from_str(raw: Optional[str]) -> list[str]:
        """Parse a comma-separated tags string from the DB."""
        if not raw:
            return []
        return [t.strip() for t in raw.split(",") if t.strip()]

    def depends_str(self) -> Optional[str]:
        if not self.depends:
            return None
        return json.dumps(self.depends)

    @staticmethod
    def depends_from_str(raw: Optional[str]) -> Optional[list[int]]:
        if not raw:
            return None
        return json.loads(raw)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "deadline": self.deadline,
            "project_id": self.project_id,
            "goal_id": self.goal_id,
            "priority": self.priority,
            "wait_until": self.wait_until,
            "depends": self.depends,
            "blocked_reason": self.blocked_reason,
            "blocked_note": self.blocked_note,
            "urgency_score": self.urgency_score,
            "context_tag": self.context_tag,
            "estimated_sessions": self.estimated_sessions,
            "actual_sessions": self.actual_sessions,
            "handoff_note": self.handoff_note,
            "status": self.status.value if isinstance(self.status, Status) else self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class LoomSession:
    date: str
    session_number: int
    id: Optional[int] = None
    type: Optional[str] = None
    active_goal_id: Optional[int] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_minutes: Optional[int] = None
    context_pct_at_exit: Optional[float] = None
    exit_reason: Optional[str] = None
    handoff_note: Optional[str] = None
    tasks_started: Optional[str] = None    # JSON list of task IDs
    tasks_completed: Optional[str] = None  # JSON list of task IDs

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "session_number": self.session_number,
            "type": self.type,
            "active_goal_id": self.active_goal_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_minutes": self.duration_minutes,
            "context_pct_at_exit": self.context_pct_at_exit,
            "exit_reason": self.exit_reason,
            "handoff_note": self.handoff_note,
            "tasks_started": self.tasks_started,
            "tasks_completed": self.tasks_completed,
        }


@dataclass
class TaskEvent:
    task_id: int
    event_type: EventType
    changed_at: str                      # ISO-8601 UTC
    id: Optional[int] = None
    field_name: Optional[str] = None     # NULL for created/deleted events
    old_value: Optional[str] = None      # NULL for created events
    new_value: Optional[str] = None      # NULL for deleted events
    task_snapshot: Optional[str] = None  # JSON blob of full task state

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "event_type": self.event_type.value if isinstance(self.event_type, EventType) else self.event_type,
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "changed_at": self.changed_at,
            "task_snapshot": self.task_snapshot,
        }


@dataclass
class Project:
    name: str
    id: Optional[int] = None
    description: Optional[str] = None      # Should contain MVP: and EVALUATION: sections
    start_date: Optional[str] = None       # ISO-8601 date string
    deployment_date: Optional[str] = None  # ISO-8601 date string
    goal_id: Optional[int] = None
    status: str = "planned"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "start_date": self.start_date,
            "deployment_date": self.deployment_date,
            "goal_id": self.goal_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
