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
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from jar.db import get_connection, init_db
from jar.filters import ProjectFilter, SortSpec, TaskFilter
from jar.service import ProjectService, TaskService

# ── startup ────────────────────────────────────────────────────────────────
# Connection and services are created once at import time and reused for the
# lifetime of the process.  init_db() is idempotent — safe to call on every
# start.

_conn = get_connection()
init_db(_conn)
_ps = ProjectService(_conn)
_ts = TaskService(_conn)

app = Server("jar")

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
                    "enum": ["todo", "in_progress", "done"],
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
                    "enum": ["todo", "in_progress", "done"],
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
        name="task_list",
        description="List tasks with optional filtering and sorting.",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "done"],
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

    raise ValueError(f"Unknown tool: {name!r}")


# ── entry point ────────────────────────────────────────────────────────────


def main() -> None:
    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
