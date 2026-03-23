"""Data models for JAR — projects, tasks, and supporting enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Status(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TagEnum(str, Enum):
    """Suggested tag values. Tasks may also use arbitrary free-text tags."""
    BUG = "bug"
    FEATURE = "feature"
    CHORE = "chore"
    DOCS = "docs"
    RESEARCH = "research"
    DESIGN = "design"


SUGGESTED_TAGS: list[str] = [t.value for t in TagEnum]


class EventType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


@dataclass
class Task:
    name: str
    status: Status = Status.TODO
    id: Optional[int] = None
    description: Optional[str] = None
    # Stored as a comma-separated string in the DB; exposed as a list here.
    tags: list[str] = field(default_factory=list)
    deadline: Optional[str] = None          # ISO-8601 date string, e.g. "2025-06-01"
    project_id: Optional[int] = None
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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "deadline": self.deadline,
            "project_id": self.project_id,
            "status": self.status.value if isinstance(self.status, Status) else self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "start_date": self.start_date,
            "deployment_date": self.deployment_date,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
