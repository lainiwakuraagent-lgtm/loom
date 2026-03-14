"""Filter and sort specifications, plus parameterised SQL query builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ------------------------------------------------------------------ sort spec

_TASK_SORT_FIELDS = {"id", "name", "deadline", "status", "created_at", "updated_at"}
_PROJECT_SORT_FIELDS = {"id", "name", "start_date", "deployment_date", "created_at", "updated_at"}


@dataclass
class SortSpec:
    field: str
    direction: str = "asc"  # "asc" | "desc"

    def __post_init__(self) -> None:
        if self.direction not in ("asc", "desc"):
            raise ValueError(f"direction must be 'asc' or 'desc', got {self.direction!r}")

    def sql_fragment(self, allowed_fields: set[str]) -> str:
        if self.field not in allowed_fields:
            raise ValueError(
                f"Cannot sort by {self.field!r}. Allowed: {sorted(allowed_fields)}"
            )
        # NULLs last for ASC, first for DESC (explicit for clarity)
        null_order = "NULLS LAST" if self.direction == "asc" else "NULLS FIRST"
        return f"{self.field} {self.direction.upper()} {null_order}"


# ------------------------------------------------------------------ task filter

@dataclass
class TaskFilter:
    status: Optional[str] = None                 # "todo" | "in_progress" | "done"
    project_id: Optional[int] = None             # exact match; use -1 for "no project"
    tags: list[str] = field(default_factory=list) # task must contain ALL listed tags
    deadline_before: Optional[str] = None        # ISO-8601 date, inclusive upper bound
    deadline_after: Optional[str] = None         # ISO-8601 date, inclusive lower bound
    deadline_on: Optional[str] = None            # ISO-8601 date — exact deadline date match
    overdue: bool = False                        # deadline < today AND status != 'done'
    search: Optional[str] = None                 # substring match on name OR description


def build_task_query(
    f: Optional[TaskFilter] = None,
    sort: Optional[SortSpec] = None,
) -> tuple[str, list]:
    """Return (sql, params) for a SELECT on the tasks table."""
    clauses: list[str] = []
    params: list = []

    if f is not None:
        if f.status is not None:
            clauses.append("status = ?")
            params.append(f.status)

        if f.project_id is not None:
            if f.project_id == -1:
                clauses.append("project_id IS NULL")
            else:
                clauses.append("project_id = ?")
                params.append(f.project_id)

        # Each requested tag must appear somewhere in the comma-separated tags column.
        for tag in f.tags:
            # Match as full token: start, end, or surrounded by commas.
            clauses.append(
                "(tags = ? OR tags LIKE ? OR tags LIKE ? OR tags LIKE ?)"
            )
            params.extend([tag, f"{tag},%", f"%,{tag}", f"%,{tag},%"])

        if f.deadline_on is not None:
            clauses.append("deadline = ?")
            params.append(f.deadline_on)

        if f.deadline_before is not None:
            clauses.append("deadline <= ?")
            params.append(f.deadline_before)

        if f.deadline_after is not None:
            clauses.append("deadline >= ?")
            params.append(f.deadline_after)

        if f.overdue:
            # Overdue = has a deadline, deadline is before today, and not yet done.
            clauses.append("deadline IS NOT NULL AND deadline < date('now') AND status != 'done'")

        if f.search is not None:
            clauses.append("(name LIKE ? OR description LIKE ?)")
            needle = f"%{f.search}%"
            params.extend([needle, needle])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = ""
    if sort is not None:
        order = f"ORDER BY {sort.sql_fragment(_TASK_SORT_FIELDS)}"

    sql = f"SELECT * FROM tasks {where} {order}".strip()
    return sql, params


# ------------------------------------------------------------------ project filter

@dataclass
class ProjectFilter:
    search: Optional[str] = None            # substring match on name OR description
    has_tasks: Optional[bool] = None        # True = only projects with tasks; False = only empty projects
    start_before: Optional[str] = None      # ISO-8601 date, inclusive
    start_after: Optional[str] = None       # ISO-8601 date, inclusive
    deployment_before: Optional[str] = None # ISO-8601 date, inclusive
    deployment_after: Optional[str] = None  # ISO-8601 date, inclusive


def build_project_query(
    f: Optional[ProjectFilter] = None,
    sort: Optional[SortSpec] = None,
) -> tuple[str, list]:
    """Return (sql, params) for a SELECT on the projects table."""
    clauses: list[str] = []
    params: list = []

    if f is not None:
        if f.search is not None:
            clauses.append("(name LIKE ? OR description LIKE ?)")
            needle = f"%{f.search}%"
            params.extend([needle, needle])

        if f.has_tasks is True:
            clauses.append("EXISTS (SELECT 1 FROM tasks WHERE tasks.project_id = projects.id)")
        elif f.has_tasks is False:
            clauses.append("NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.project_id = projects.id)")

        if f.start_before is not None:
            clauses.append("start_date <= ?")
            params.append(f.start_before)

        if f.start_after is not None:
            clauses.append("start_date >= ?")
            params.append(f.start_after)

        if f.deployment_before is not None:
            clauses.append("deployment_date <= ?")
            params.append(f.deployment_before)

        if f.deployment_after is not None:
            clauses.append("deployment_date >= ?")
            params.append(f.deployment_after)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = ""
    if sort is not None:
        order = f"ORDER BY {sort.sql_fragment(_PROJECT_SORT_FIELDS)}"

    sql = f"SELECT * FROM projects {where} {order}".strip()
    return sql, params
