"""MCP server for JAR — exposes ProjectService and TaskService as MCP tools.

Run directly:
    jar-mcp          (after pip install -e .)
    python -m jar.mcp_server

Transport: stdio (JSON-RPC 2.0 over stdin/stdout).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
import click
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from loom.analytics import AnalyticsService
from loom.context import generate_context_snapshot
from loom.db import get_connection, init_db
from loom.filters import ProjectFilter, SortSpec, TaskFilter
from loom.service import GoalService, LoomSessionService, ProjectService, TaskService

# ── startup ────────────────────────────────────────────────────────────────
# Connection and services are created once at import time and reused for the
# lifetime of the process.  init_db() is idempotent — safe to call on every
# start.

_conn = get_connection()
init_db(_conn)
_ps = ProjectService(_conn)
_ts = TaskService(_conn)
_as = AnalyticsService(_conn)
_gs = GoalService(_conn)
_ss = LoomSessionService(_conn)

app = Server("loom")

# ── helpers ────────────────────────────────────────────────────────────────


def _ok(data: Any) -> list[TextContent]:
    """Serialise any Python value to a single MCP TextContent block."""
    return [TextContent(type="text", text=json.dumps(data, default=str))]


def _parse_tags(value: Any) -> list[str]:
    """Normalise tags input to list[str].

    Accepts:
    - None / missing          → []
    - list[str]               → cleaned list
    - JSON array string       → parsed then cleaned
    - comma-separated string  → split and cleaned
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(t).strip() for t in value if str(t).strip()]
    raw = str(value).strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except json.JSONDecodeError:
            pass
    return [t.strip() for t in raw.split(",") if t.strip()]


def _sort_spec(args: dict) -> SortSpec | None:
    """Build a SortSpec from optional sort_field / sort_direction args."""
    field = args.get("sort_field")
    if not field:
        return None
    return SortSpec(field=field, direction=args.get("sort_direction", "asc"))


# ── tool catalogue ─────────────────────────────────────────────────────────

_TOOLS: list[Tool] = [
    # ── projects ──────────────────────────────────────────────────────────
    Tool(
        name="project_create",
        description=(
            "Create a new project. "
            "The description MUST contain an 'MVP:' section and an 'EVALUATION:' section "
            "when a description is provided."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short project label (required)",
                },
                "description": {
                    "type": ["string", "null"],
                    "description": "Must include MVP: and EVALUATION: sections when provided",
                },
                "start_date": {
                    "type": ["string", "null"],
                    "description": "ISO-8601 date, e.g. 2025-01-01",
                },
                "deployment_date": {
                    "type": ["string", "null"],
                    "description": "Target go-live date (ISO-8601)",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="project_get",
        description="Retrieve a single project by its integer ID. Returns null if not found.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Project ID"},
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="project_update",
        description=(
            "Update one or more fields of an existing project. "
            "Omit a field to leave it unchanged; pass null to clear it."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Project ID"},
                "name": {"type": "string", "description": "New name"},
                "description": {
                    "type": ["string", "null"],
                    "description": "New description (must include MVP:/EVALUATION: if non-null); pass null to clear",
                },
                "start_date": {
                    "type": ["string", "null"],
                    "description": "ISO-8601 date or null to clear",
                },
                "deployment_date": {
                    "type": ["string", "null"],
                    "description": "ISO-8601 date or null to clear",
                },
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="project_delete",
        description=(
            "Hard-delete a project and ALL of its tasks in a single transaction. "
            "This operation is irreversible."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Project ID"},
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="project_list",
        description="List projects with optional filtering and sorting.",
        inputSchema={
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Substring match on name or description",
                },
                "has_tasks": {
                    "type": "boolean",
                    "description": "true = only projects with tasks; false = only empty projects",
                },
                "start_before": {
                    "type": "string",
                    "description": "ISO-8601 date — inclusive upper bound on start_date",
                },
                "start_after": {
                    "type": "string",
                    "description": "ISO-8601 date — inclusive lower bound on start_date",
                },
                "deployment_before": {
                    "type": "string",
                    "description": "ISO-8601 date — inclusive upper bound on deployment_date",
                },
                "deployment_after": {
                    "type": "string",
                    "description": "ISO-8601 date — inclusive lower bound on deployment_date",
                },
                "sort_field": {
                    "type": "string",
                    "enum": ["id", "name", "start_date", "deployment_date", "created_at", "updated_at"],
                },
                "sort_direction": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "default": "asc",
                },
            },
        },
    ),
    Tool(
        name="project_tasks",
        description="List all tasks belonging to a specific project.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Project ID"},
            },
            "required": ["id"],
        },
    ),
    # ── tasks ─────────────────────────────────────────────────────────────
    Tool(
        name="task_create",
        description="Create a new task.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short task label (required)"},
                "description": {
                    "type": ["string", "null"],
                    "description": "Optional free-form detail",
                },
                "tags": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string", "description": "Comma-separated tags"},
                        {"type": "null"},
                    ],
                    "description": "Suggested values: bug, feature, chore, docs, research, design",
                },
                "deadline": {
                    "type": ["string", "null"],
                    "description": "ISO-8601 date, e.g. 2025-06-01",
                },
                "project_id": {
                    "type": ["integer", "null"],
                    "description": "Assign to an existing project (omit for standalone task)",
                },
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "done", "failed"],
                    "default": "todo",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="task_get",
        description="Retrieve a single task by its integer ID. Returns null if not found.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Task ID"},
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="task_update",
        description=(
            "Update one or more fields of an existing task. "
            "Omit a field to leave it unchanged; pass null to clear it."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Task ID"},
                "name": {"type": "string", "description": "New name"},
                "description": {
                    "type": ["string", "null"],
                    "description": "Pass null to clear",
                },
                "tags": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string"},
                        {"type": "null"},
                    ],
                    "description": "New tags list; pass null or empty string to clear all tags",
                },
                "deadline": {
                    "type": ["string", "null"],
                    "description": "ISO-8601 date or null to clear",
                },
                "project_id": {
                    "type": ["integer", "null"],
                    "description": "Reassign to another project; pass null to detach (make standalone)",
                },
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "done", "failed"],
                },
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="task_delete",
        description="Delete a task by ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Task ID"},
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="task_history",
        description=(
            "Retrieve the full audit event log for a task. "
            "Returns all creation, field-change, and deletion events in chronological order. "
            "History is preserved even after the task is deleted."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "Task ID to retrieve history for",
                },
            },
            "required": ["task_id"],
        },
    ),
    Tool(
        name="task_list",
        description="List tasks with optional filtering and sorting.",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "done", "failed"],
                },
                "project_id": {
                    "type": "integer",
                    "description": "Filter by project ID; use -1 for standalone tasks only",
                },
                "tags": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string"},
                    ],
                    "description": "Task must contain ALL listed tags",
                },
                "deadline_before": {
                    "type": "string",
                    "description": "ISO-8601 inclusive upper bound on deadline",
                },
                "deadline_after": {
                    "type": "string",
                    "description": "ISO-8601 inclusive lower bound on deadline",
                },
                "deadline_on": {
                    "type": "string",
                    "description": "Exact ISO-8601 deadline date match",
                },
                "overdue": {
                    "type": "boolean",
                    "description": "Only tasks past their deadline that are not done",
                },
                "search": {
                    "type": "string",
                    "description": "Substring match on name or description",
                },
                "sort_field": {
                    "type": "string",
                    "enum": ["id", "name", "deadline", "status", "created_at", "updated_at"],
                },
                "sort_direction": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "default": "asc",
                },
            },
        },
    ),
    # ── analytics ─────────────────────────────────────────────────────────
    Tool(
        name="analytics_summary",
        description="Health dashboard — key numbers from all analytics metrics.",
        inputSchema={
            "type": "object",
            "properties": {
                "since": {"type": ["string", "null"], "description": "Scope to tasks created on or after this ISO-8601 date"},
            },
        },
    ),
    Tool(
        name="analytics_deadline_health",
        description="Deadline push rate and miss rate broken down by tag and project.",
        inputSchema={
            "type": "object",
            "properties": {
                "since":      {"type": ["string", "null"], "description": "ISO-8601 date — scope to tasks created on or after"},
                "project_id": {"type": ["integer", "null"], "description": "Filter to a specific project"},
                "tag":        {"type": ["string", "null"], "description": "Filter to tasks with this tag"},
            },
        },
    ),
    Tool(
        name="analytics_velocity",
        description="Time-to-done distribution and completion velocity per tag.",
        inputSchema={
            "type": "object",
            "properties": {
                "since":      {"type": ["string", "null"]},
                "project_id": {"type": ["integer", "null"]},
                "tag":        {"type": ["string", "null"]},
            },
        },
    ),
    Tool(
        name="analytics_capacity",
        description="Deadline clustering (overloaded weeks) and context switching load.",
        inputSchema={
            "type": "object",
            "properties": {
                "since": {"type": ["string", "null"]},
            },
        },
    ),
    Tool(
        name="analytics_behavior",
        description="Task rot (age in todo), recovery lag, and status reversals.",
        inputSchema={
            "type": "object",
            "properties": {
                "since":      {"type": ["string", "null"]},
                "project_id": {"type": ["integer", "null"]},
                "tag":        {"type": ["string", "null"]},
            },
        },
    ),
    Tool(
        name="analytics_realism",
        description="Deadline Realism Score, deadline horizon at creation, and abandonment rate.",
        inputSchema={
            "type": "object",
            "properties": {
                "since": {"type": ["string", "null"]},
                "tag":   {"type": ["string", "null"]},
            },
        },
    ),
    # ── loom: ready queue ─────────────────────────────────────────────────
    Tool(
        name="loom_ready_queue",
        description="Return tasks ready to work on, ordered by urgency. All deps met, wait_until passed.",
        inputSchema={
            "type": "object",
            "properties": {
                "goal_id": {"type": ["integer", "null"], "description": "Filter by goal ID"},
                "limit":   {"type": "integer", "default": 10, "description": "Max tasks"},
            },
        },
    ),
    # ── loom: context snapshot ────────────────────────────────────────────
    Tool(
        name="loom_context_snapshot",
        description="Generate a compact JSON context snapshot: current task, ready queue, counters.",
        inputSchema={
            "type": "object",
            "properties": {
                "goal_id":     {"type": ["integer", "null"]},
                "output_path": {"type": ["string", "null"], "description": "Write JSON to this path"},
            },
        },
    ),
    # ── loom: block task ─────────────────────────────────────────────────
    Tool(
        name="loom_block_task",
        description="Mark a task as blocked (blocked_owner or blocked_dep) with a reason.",
        inputSchema={
            "type": "object",
            "required": ["task_id", "reason"],
            "properties": {
                "task_id":        {"type": "integer"},
                "reason":         {"type": "string", "enum": ["blocked_owner", "blocked_dep"]},
                "blocked_note":   {"type": ["string", "null"]},
            },
        },
    ),
    # ── goal_list ─────────────────────────────────────────────────────────
    Tool(
        name="goal_list",
        description="List all goals.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── goal_create ───────────────────────────────────────────────────────
    Tool(
        name="goal_create",
        description="Create a new goal.",
        inputSchema={
            "type": "object",
            "required": ["name"],
            "properties": {
                "name":               {"type": "string"},
                "description":        {"type": ["string", "null"]},
                "status":             {"type": "string", "default": "desire"},
                "priority":           {"type": "integer", "default": 0},
                "estimated_sessions": {"type": ["integer", "null"]},
            },
        },
    ),
    # ── goal_update ───────────────────────────────────────────────────────
    Tool(
        name="goal_update",
        description="Update an existing goal.",
        inputSchema={
            "type": "object",
            "required": ["goal_id"],
            "properties": {
                "goal_id":            {"type": "integer"},
                "name":               {"type": ["string", "null"]},
                "description":        {"type": ["string", "null"]},
                "status":             {"type": ["string", "null"]},
                "priority":           {"type": ["integer", "null"]},
                "started_at":         {"type": ["string", "null"]},
                "completed_at":       {"type": ["string", "null"]},
                "estimated_sessions": {"type": ["integer", "null"]},
                "actual_sessions":    {"type": ["integer", "null"]},
            },
        },
    ),
    # ── loom_session_start ────────────────────────────────────────────────
    Tool(
        name="loom_session_start",
        description="Record the start of a LOOM agent session.",
        inputSchema={
            "type": "object",
            "required": ["date", "session_number"],
            "properties": {
                "date":           {"type": "string", "description": "YYYY-MM-DD"},
                "session_number": {"type": "integer"},
                "session_type":   {"type": ["string", "null"]},
                "active_goal_id": {"type": ["integer", "null"]},
            },
        },
    ),
    # ── loom_session_end ──────────────────────────────────────────────────
    Tool(
        name="loom_session_end",
        description="Record the end of a LOOM agent session.",
        inputSchema={
            "type": "object",
            "required": ["session_id", "exit_reason"],
            "properties": {
                "session_id":          {"type": "integer"},
                "exit_reason":         {"type": "string"},
                "context_pct_at_exit": {"type": ["number", "null"]},
                "handoff_note":        {"type": ["string", "null"]},
                "tasks_started":       {"type": ["array", "null"], "items": {"type": "integer"}},
                "tasks_completed":     {"type": ["array", "null"], "items": {"type": "integer"}},
            },
        },
    ),
]

# ── handlers ───────────────────────────────────────────────────────────────


@app.list_tools()
async def handle_list_tools() -> list[Tool]:
    return _TOOLS


@app.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args: dict[str, Any] = arguments or {}

    # ── project_create ────────────────────────────────────────────────────
    if name == "project_create":
        result = _ps.create(
            name=args["name"],
            description=args.get("description"),
            start_date=args.get("start_date"),
            deployment_date=args.get("deployment_date"),
        )
        return _ok(result.to_dict())

    # ── project_get ───────────────────────────────────────────────────────
    if name == "project_get":
        result = _ps.get(args["id"])
        return _ok(result.to_dict() if result else None)

    # ── project_update ────────────────────────────────────────────────────
    if name == "project_update":
        # Only forward keys that were explicitly supplied by the caller.
        # Absent key  → omitted from kwargs → service default (_MISSING) → no change.
        # Present null → kwargs[field] = None → service clears the field.
        kwargs: dict[str, Any] = {}
        if "name" in args and args["name"] is not None:
            kwargs["name"] = args["name"]
        for f in ("description", "start_date", "deployment_date"):
            if f in args:
                kwargs[f] = args[f]
        result = _ps.update(args["id"], **kwargs)
        return _ok(result.to_dict())

    # ── project_delete ────────────────────────────────────────────────────
    if name == "project_delete":
        deleted = _ps.delete(args["id"])
        return _ok({"deleted": deleted})

    # ── project_list ──────────────────────────────────────────────────────
    if name == "project_list":
        pf = ProjectFilter(
            search=args.get("search"),
            has_tasks=args.get("has_tasks"),
            start_before=args.get("start_before"),
            start_after=args.get("start_after"),
            deployment_before=args.get("deployment_before"),
            deployment_after=args.get("deployment_after"),
        )
        result = _ps.list_filtered(pf, sort=_sort_spec(args))
        return _ok([p.to_dict() for p in result])

    # ── project_tasks ─────────────────────────────────────────────────────
    if name == "project_tasks":
        result = _ps.tasks_for_project(args["id"])
        return _ok([t.to_dict() for t in result])

    # ── task_create ───────────────────────────────────────────────────────
    if name == "task_create":
        result = _ts.create(
            name=args["name"],
            description=args.get("description"),
            tags=_parse_tags(args.get("tags")),
            deadline=args.get("deadline"),
            project_id=args.get("project_id"),
            status=args.get("status", "todo"),
        )
        return _ok(result.to_dict())

    # ── task_get ──────────────────────────────────────────────────────────
    if name == "task_get":
        result = _ts.get(args["id"])
        return _ok(result.to_dict() if result else None)

    # ── task_update ───────────────────────────────────────────────────────
    if name == "task_update":
        kwargs = {}
        if "name" in args and args["name"] is not None:
            kwargs["name"] = args["name"]
        if "status" in args and args["status"] is not None:
            kwargs["status"] = args["status"]
        for f in ("description", "deadline"):
            if f in args:
                kwargs[f] = args[f]
        if "tags" in args:
            kwargs["tags"] = _parse_tags(args["tags"])
        if "project_id" in args:
            kwargs["project_id"] = args["project_id"]  # None → detach
        result = _ts.update(args["id"], **kwargs)
        return _ok(result.to_dict())

    # ── task_delete ───────────────────────────────────────────────────────
    if name == "task_delete":
        deleted = _ts.delete(args["id"])
        return _ok({"deleted": deleted})

    # ── task_history ──────────────────────────────────────────────────────
    if name == "task_history":
        events = _ts.get_history(args["task_id"])
        return _ok([e.to_dict() for e in events])

    # ── task_list ─────────────────────────────────────────────────────────
    if name == "task_list":
        tf = TaskFilter(
            status=args.get("status"),
            project_id=args.get("project_id"),
            tags=_parse_tags(args.get("tags")),
            deadline_before=args.get("deadline_before"),
            deadline_after=args.get("deadline_after"),
            deadline_on=args.get("deadline_on"),
            overdue=args.get("overdue", False),
            search=args.get("search"),
        )
        result = _ts.list_filtered(tf, sort=_sort_spec(args))
        return _ok([t.to_dict() for t in result])

    # ── analytics_summary ─────────────────────────────────────────────────
    if name == "analytics_summary":
        return _ok(_as.summary(since=args.get("since")))

    # ── analytics_deadline_health ─────────────────────────────────────────
    if name == "analytics_deadline_health":
        return _ok(_as.deadline_health(
            since=args.get("since"),
            project_id=args.get("project_id"),
            tag=args.get("tag"),
        ))

    # ── analytics_velocity ────────────────────────────────────────────────
    if name == "analytics_velocity":
        return _ok(_as.velocity(
            since=args.get("since"),
            project_id=args.get("project_id"),
            tag=args.get("tag"),
        ))

    # ── analytics_capacity ────────────────────────────────────────────────
    if name == "analytics_capacity":
        return _ok(_as.capacity(since=args.get("since")))

    # ── analytics_behavior ────────────────────────────────────────────────
    if name == "analytics_behavior":
        return _ok(_as.behavior(
            since=args.get("since"),
            project_id=args.get("project_id"),
            tag=args.get("tag"),
        ))

    # ── analytics_realism ─────────────────────────────────────────────────
    if name == "analytics_realism":
        return _ok(_as.realism(
            since=args.get("since"),
            tag=args.get("tag"),
        ))

    # ── loom_ready_queue ──────────────────────────────────────────────────
    if name == "loom_ready_queue":
        tasks = _ts.get_ready_queue(
            goal_id=args.get("goal_id"),
            limit=int(args.get("limit", 10)),
        )
        return _ok([t.to_dict() for t in tasks])

    # ── loom_context_snapshot ─────────────────────────────────────────────
    if name == "loom_context_snapshot":
        snap = generate_context_snapshot(
            _conn,
            goal_id=args.get("goal_id"),
            output_path=args.get("output_path"),
        )
        return _ok(snap)

    # ── loom_block_task ───────────────────────────────────────────────────
    if name == "loom_block_task":
        result = _ts.update(
            args["task_id"],
            status=args["reason"],
            blocked_note=args.get("blocked_note"),
        )
        return _ok(result.to_dict())

    # ── goal_list ─────────────────────────────────────────────────────────
    if name == "goal_list":
        goals = _gs.list_all()
        return _ok([g.to_dict() for g in goals])

    # ── goal_create ───────────────────────────────────────────────────────
    if name == "goal_create":
        result = _gs.create(
            name=args["name"],
            description=args.get("description"),
            status=args.get("status", "desire"),
            priority=int(args.get("priority", 0)),
            estimated_sessions=args.get("estimated_sessions"),
        )
        return _ok(result.to_dict())

    # ── goal_update ───────────────────────────────────────────────────────
    if name == "goal_update":
        from loom.service import _MISSING
        result = _gs.update(
            goal_id=args["goal_id"],
            name=args.get("name"),
            description=args.get("description", _MISSING),
            status=args.get("status"),
            priority=args.get("priority"),
            started_at=args.get("started_at", _MISSING),
            completed_at=args.get("completed_at", _MISSING),
            estimated_sessions=args.get("estimated_sessions", _MISSING),
            actual_sessions=args.get("actual_sessions"),
        )
        return _ok(result.to_dict())

    # ── loom_session_start ────────────────────────────────────────────────
    if name == "loom_session_start":
        result = _ss.start_session(
            date=args["date"],
            session_number=int(args["session_number"]),
            session_type=args.get("session_type"),
            active_goal_id=args.get("active_goal_id"),
        )
        return _ok({"id": result.id, "started_at": result.started_at})

    # ── loom_session_end ──────────────────────────────────────────────────
    if name == "loom_session_end":
        result = _ss.end_session(
            session_id=int(args["session_id"]),
            exit_reason=args["exit_reason"],
            context_pct_at_exit=args.get("context_pct_at_exit"),
            handoff_note=args.get("handoff_note"),
            tasks_started=args.get("tasks_started"),
            tasks_completed=args.get("tasks_completed"),
        )
        return _ok({"id": result.id, "duration_minutes": result.duration_minutes})

    raise ValueError(f"Unknown tool: {name!r}")


# ── transport implementations ───────────────────────────────────────────────


def _run_stdio() -> None:
    """Run the MCP server over stdio (original behaviour)."""
    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )

    asyncio.run(_run())


def _run_sse(host: str, port: int) -> None:
    """Run the MCP server over HTTP/SSE using Starlette + uvicorn."""
    try:
        import uvicorn
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import Response
        from starlette.routing import Mount, Route
    except ImportError as exc:
        raise SystemExit(
            f"SSE transport requires uvicorn and starlette: {exc}\n"
            "Install with: pip install 'uvicorn[standard]>=0.30'"
        ) from exc

    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(
                streams[0],
                streams[1],
                app.create_initialization_options(),
            )
        return Response()  # required — prevents TypeError on client disconnect

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )

    uvicorn.run(starlette_app, host=host, port=port)


# ── entry point ────────────────────────────────────────────────────────────


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse"], case_sensitive=False),
    default="stdio",
    show_default=True,
    help="Transport to use: stdio (default) or sse (HTTP/SSE web server).",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind when using --transport=sse.",
)
@click.option(
    "--port",
    default=8000,
    show_default=True,
    type=int,
    help="Port to bind when using --transport=sse.",
)
def main(transport: str, host: str, port: int) -> None:
    """JAR MCP server — exposes ProjectService and TaskService as MCP tools."""
    if transport == "sse":
        _run_sse(host, port)
    else:
        _run_stdio()


if __name__ == "__main__":
    main()
