# JAR MCP Server — Implementation Plan

## Goal

Expose every `ProjectService` and `TaskService` method from `jar/service.py` as MCP tools so that a Claude instance (or any MCP-compatible client) can manage projects and tasks by invoking them directly.

---

## Architecture Overview

```
Claude (MCP client)
        │  stdio (JSON-RPC 2.0)
        ▼
jar/mcp_server.py          ← new file: MCP server entry point
        │
        ├── ProjectService (jar/service.py)
        └── TaskService    (jar/service.py)
                │
                ├── ProjectRepository / TaskRepository (jar/repository.py)
                └── SQLite DB  (same default path as CLI: ~/.local/share/jar/jar.db)
```

- Transport: **stdio** — Claude Desktop (and the MCP SDK default) communicates over stdin/stdout; no HTTP server needed.
- The server creates a single SQLite connection on startup, initialises the DB schema via `init_db()`, and keeps it open for the lifetime of the process.
- All existing service-layer validation (MVP/EVALUATION constraint, status enum, project FK check) is preserved automatically — MCP tools just call the same service methods.
- Errors from the service layer are caught and returned as MCP error responses with a descriptive message.

---

## New Dependency

```
mcp>=1.0        # Anthropic's Model Context Protocol Python SDK
```

Added to `pyproject.toml` under `[project] dependencies`.

---

## Files to Create / Modify

| File | Action | Purpose |
|---|---|---|
| `jar/mcp_server.py` | **Create** | MCP server: tool registration, argument parsing, service dispatch |
| `pyproject.toml` | **Modify** | Add `mcp>=1.0` dependency; add `jar-mcp` script entry point |

No other existing files need to change.

---

## Tools Exposed (11 total)

### Project tools

| Tool name | Maps to | Key arguments |
|---|---|---|
| `project_create` | `ProjectService.create` | `name` (required), `description`, `start_date`, `deployment_date` |
| `project_get` | `ProjectService.get` | `id` (required) |
| `project_update` | `ProjectService.update` | `id` (required), any subset of `name`, `description`, `start_date`, `deployment_date` |
| `project_delete` | `ProjectService.delete` | `id` (required) |
| `project_list` | `ProjectService.list_filtered` | `search`, `has_tasks`, `start_before`, `start_after`, `deployment_before`, `deployment_after`, `sort_field`, `sort_direction` |
| `project_tasks` | `ProjectService.tasks_for_project` | `id` (required) |

### Task tools

| Tool name | Maps to | Key arguments |
|---|---|---|
| `task_create` | `TaskService.create` | `name` (required), `description`, `tags`, `deadline`, `project_id`, `status` |
| `task_get` | `TaskService.get` | `id` (required) |
| `task_update` | `TaskService.update` | `id` (required), any subset of `name`, `description`, `tags`, `deadline`, `project_id`, `status` |
| `task_delete` | `TaskService.delete` | `id` (required) |
| `task_list` | `TaskService.list_filtered` | `status`, `project_id`, `tags`, `deadline_before`, `deadline_after`, `deadline_on`, `overdue`, `search`, `sort_field`, `sort_direction` |

---

## Implementation Steps (in order)

### Step 1 — Add dependency

In `pyproject.toml`:
- Add `"mcp>=1.0"` to `[project] dependencies`.
- Add a new entry-point script: `jar-mcp = "jar.mcp_server:main"` so the server can be launched with a single command.

### Step 2 — Create `jar/mcp_server.py`

**2a. Boilerplate / startup**

```python
import json, sqlite3
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from jar.db import get_connection, init_db
from jar.service import ProjectService, TaskService
from jar.filters import TaskFilter, ProjectFilter, SortSpec

app = Server("jar")
```

On process start: call `get_connection()` → `init_db(conn)` → construct `ProjectService(conn)` and `TaskService(conn)`.

**2b. Tool definitions**

Each tool is registered with:
- A snake_case name (table above).
- A short human-readable `description` string.
- A JSON Schema `inputSchema` defining every accepted argument with its type, description, and which are required. Optional arguments that accept `null` (to clear a field) are typed as `["string", "null"]`.

**2c. Tool dispatch (`@app.call_tool`)**

A single dispatcher function switches on `name`, deserialises the validated arguments, constructs the appropriate filter/sort objects where needed, calls the service method, and serialises the result:

- Dataclass instances (`Task`, `Project`) → `.to_dict()` → `json.dumps` → `TextContent`
- Lists → `json.dumps([item.to_dict() for item in result])` → `TextContent`
- Booleans (delete) → `json.dumps({"deleted": result})` → `TextContent`
- `None` (not found) → `json.dumps(null)` → `TextContent`
- `ValueError` / any exception from the service → re-raised so MCP SDK converts it to an error response

**2d. Argument handling rules**

- `tags` argument: accepted as a JSON array string OR a comma-separated string; normalised to `list[str]` before calling the service.
- `sort_field` + `sort_direction`: combined into a `SortSpec` when `sort_field` is provided; omitted otherwise.
- `has_tasks` for `project_list`: accepted as a boolean (`true`/`false`) or the strings `"yes"`/`"no"`/`"true"`/`"false"`.
- Fields that can be explicitly cleared (`description`, `deadline`, `start_date`, `deployment_date`, `project_id`, `tags`): the caller passes `null` (JSON) to clear; this is forwarded as the sentinel `_MISSING` vs `None` distinction that the service layer already handles.

**2e. Entry point**

```python
def main():
    import asyncio
    asyncio.run(stdio_server(app))
```

### Step 3 — Verify locally

```bash
pip install -e ".[dev]"
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | jar-mcp
```

Expected: JSON response listing all 11 tools.

---

## Claude Desktop / MCP Client Registration

After installation, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "jar": {
      "command": "jar-mcp"
    }
  }
}
```

Or with a custom DB path:

```json
{
  "mcpServers": {
    "jar": {
      "command": "jar-mcp",
      "env": { "JAR_DB": "/path/to/custom.db" }
    }
  }
}
```

---

## Potential Edge Cases

| Case | Handling |
|---|---|
| `project_create` called without MVP/EVALUATION in description | Service raises `ValueError`; MCP SDK converts to error response with the message text |
| `task_update` / `project_update` — field omitted vs explicitly set to null | Tool schema distinguishes via JSON null; server maps null → `None` (clear) and absent key → `_MISSING` (leave unchanged) |
| `tags` passed as JSON array string `'["bug","feature"]'` vs plain string `"bug,feature"` | Server normalises both forms to `list[str]` before calling service |
| `project_delete` cascades to tasks | Existing service/repository behaviour; the tool description warns Claude that this is destructive and irreversible |
| DB not initialised yet | `init_db(conn)` runs on server startup; tables created automatically |
| `task_list` with `project_id = -1` (standalone tasks) | `TaskFilter(project_id=-1)` already handled in `build_task_query` |
| `has_tasks` filter as string vs boolean | Normalised in dispatcher before constructing `ProjectFilter` |
| Large result sets | All results serialised as a single JSON string in one `TextContent` block; no streaming (acceptable for a local task manager) |
| Concurrent tool calls | SQLite WAL mode (set by `init_db`) handles concurrent reads; single-threaded async server serialises writes naturally |
| `sort_direction` without `sort_field` | Ignored; no `SortSpec` constructed |
| Invalid `sort_field` value | `SortSpec.sql_fragment` raises `ValueError` → MCP error response |
