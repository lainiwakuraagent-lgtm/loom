# JAR — Project & Task Management System

A Python CLI and library for managing projects and tasks with flexible filtering, sorting, and multiple output formats.

---

## Features

- **Projects and tasks are independent** — a project can have no tasks, and a task can exist without a project.
- **Rich filtering and sorting** on every list command.
- **Four output formats**: `table` (default, colour-coded), `json`, `csv`, `plain`.
- **Task lifecycle tracking** — every creation, field change, and deletion is recorded as an immutable event. History is preserved even after a task is deleted.
- **Usable as a library** — the service layer is fully decoupled from the CLI.
- **Structured logging** — DB-level logs and service-level logs written to separate rotating files.
- **Project deletion is API-only** — protects against accidental cascade-deletes from the command line.

---

## Installation

```bash
pip install -e .
```

Dependencies installed automatically: `click`, `rich`, `platformdirs`, `mcp`.

---

## Data model

### Task fields
| Field | Type | Notes |
|---|---|---|
| `id` | int | auto-assigned |
| `name` | str | required |
| `description` | str | optional, free-form |
| `tags` | list[str] | comma-separated; suggested values: `bug`, `feature`, `chore`, `docs`, `research`, `design` |
| `deadline` | str (YYYY-MM-DD) | optional |
| `project_id` | int | optional — `null` for standalone tasks |
| `status` | enum | `todo` / `in_progress` / `done` |

### Project fields
| Field | Type | Notes |
|---|---|---|
| `id` | int | auto-assigned |
| `name` | str | required |
| `description` | str | optional, free-form. Convention: include an **MVP:** section (minimum viable scope) and an **EVALUATION:** section (why the project is useful / why it is being developed) |
| `start_date` | str (YYYY-MM-DD) | optional |
| `deployment_date` | str (YYYY-MM-DD) | optional — target go-live date |

---

## CLI Usage

Global options (must appear before the subcommand):

```
--db PATH          Override database file location (or set JAR_DB env var)
--format FORMAT    Output format: table | json | csv | plain  (default: table)
```

### Projects

```bash
# Create
jar project add --name "API Gateway" --start-date 2025-01-01 --deployment-date 2025-06-01
jar project add                         # prompts for name only
jar project add --name "Auth" -D        # opens $EDITOR for a multi-line description

# List
jar project list
jar project list --search "API"
jar project list --has-tasks yes
jar project list --has-tasks no
jar project list --start-after 2025-01-01
jar project list --deployment-before 2025-12-31
jar project list --sort name
jar project list --sort deployment_date:desc
jar --format json project list

# Show detail (including task list)
jar project show 3

# Edit
jar project edit 3 --name "New Name"
jar project edit 3 --start-date 2025-03-01
jar project edit 3 --deployment-date ""   # clears the field
jar project edit 3 -D                     # opens $EDITOR to rewrite description

# Delete — NOT available via CLI (see below)
```

### Tasks

```bash
# Create
jar task add --name "Fix login bug"
jar task add --name "Write docs" --tags "docs" --project 3 --status todo
jar task add --name "Deploy" --tags "chore,feature" --deadline 2025-06-01 \
             --project 3 --status in_progress

# List
jar task list
jar task list --status todo
jar task list --status in_progress
jar task list --project 3
jar task list --project none             # standalone tasks only
jar task list --tag bug
jar task list --tag bug --tag feature    # tasks that have BOTH tags
jar task list --deadline-before 2025-06-01
jar task list --deadline-after 2025-01-01
jar task list --search "login"
jar task list --sort deadline
jar task list --sort name:desc

# Combined filters
jar task list --status todo --project 3 --tag bug --sort deadline:asc

# Output formats
jar --format json task list --status done
jar --format csv  task list --project 3 > tasks.csv
jar --format plain task list

# Show detail
jar task show 12

# Edit
jar task edit 12 --status done
jar task edit 12 --name "Renamed task"
jar task edit 12 --tags "bug,feature"
jar task edit 12 --tags ""              # clears all tags
jar task edit 12 --deadline ""          # clears deadline
jar task edit 12 --project 5            # reassign to another project
jar task edit 12 --project ""           # detach from project (make standalone)

# Delete (with confirmation)
jar task delete 12
jar task delete 12 --yes                # skip confirmation

# Show full audit history for a task
jar task history 12
jar task history 12 --format json
jar task history 42                     # works even if task #42 was deleted
```

### --sort syntax

```
--sort FIELD           ascending (default)
--sort FIELD:asc
--sort FIELD:desc
```

**Task sort fields:** `id`, `name`, `deadline`, `status`, `created_at`, `updated_at`

**Project sort fields:** `id`, `name`, `start_date`, `deployment_date`, `created_at`, `updated_at`

NULL values are sorted **last** in ascending order, **first** in descending order.

---

## Project deletion — API only

Deleting a project performs a **hard cascade delete**: the project and all its tasks are removed in a single transaction. This operation is intentionally not exposed via the CLI to prevent accidental data loss.

Use the Python API directly (e.g., from a script or admin tool running on the local system):

```python
from jar.db import get_connection, init_db
from jar.service import ProjectService

conn = get_connection()          # uses the default DB path
init_db(conn)
ProjectService(conn).delete(3)  # deletes project #3 and all its tasks
conn.close()
```

---

## Library / service layer usage

```python
from jar.db import get_connection, init_db
from jar.service import ProjectService, TaskService
from jar.filters import TaskFilter, ProjectFilter, SortSpec

conn = get_connection()   # default path, or pass a custom path string
init_db(conn)

ps = ProjectService(conn)
ts = TaskService(conn)

# Create
project = ps.create("My Project", description="MVP: ...\nEVALUATION: ...",
                     start_date="2025-01-01", deployment_date="2025-12-31")
task = ts.create("First task", tags=["feature"], project_id=project.id,
                  deadline="2025-06-01", status="todo")

# Query with filters
tasks = ts.list_filtered(
    TaskFilter(status="todo", tags=["bug"], deadline_before="2025-06-01"),
    sort=SortSpec(field="deadline", direction="asc"),
)

projects = ps.list_filtered(
    ProjectFilter(has_tasks=True, search="API"),
    sort=SortSpec(field="deployment_date", direction="asc"),
)

# Update
ts.update(task.id, status="in_progress", tags=["feature", "urgent"])
ps.update(project.id, deployment_date="2025-09-01")

# Delete (task — safe, also available via CLI)
ts.delete(task.id)

# Delete project — cascade, use with care
ps.delete(project.id)

conn.close()
```

---

## Task lifecycle tracking

Every write to a task is recorded in the `task_events` table (schema v2). The event log is immutable — events are never deleted, even when the task itself is.

### Event types

| Event | When recorded |
|---|---|
| `created` | Task is first created — full snapshot of initial state |
| `updated` | Any tracked field changes — one row per changed field |
| `deleted` | Task is deleted — full snapshot of final state |

### Tracked fields

`name`, `description`, `tags`, `deadline`, `status`, `project_id`

### Querying history

**CLI:**
```bash
jar task history <id>                  # table view (default)
jar task history <id> --format json    # JSON array of events
jar task history <id> --format csv     # CSV export
```

**Python API:**
```python
from jar.service import TaskService
from jar.db import get_connection, init_db

conn = get_connection()
init_db(conn)
ts = TaskService(conn)

events = ts.get_history(task_id=5)
for e in events:
    print(e.event_type, e.field_name, e.old_value, "→", e.new_value, "@", e.changed_at)
```

Each `TaskEvent` also carries a `task_snapshot` field — a JSON string of the complete task state at the time the event was recorded, enabling point-in-time reconstruction without replaying the entire log.

### TaskEvent model

| Field | Type | Notes |
|---|---|---|
| `id` | int | auto-assigned |
| `task_id` | int | references the task (no FK — survives deletion) |
| `event_type` | `EventType` | `created` / `updated` / `deleted` |
| `field_name` | str | which field changed; `null` for `created`/`deleted` events |
| `old_value` | str | previous value as string; `null` for `created` events |
| `new_value` | str | new value as string; `null` for `deleted` events |
| `changed_at` | str (ISO-8601 UTC) | when the change occurred |
| `task_snapshot` | str (JSON) | full task state at time of event |

---

## Logging

Two rotating log files are written automatically:

| File | Logger | Level | Content |
|---|---|---|---|
| `~/.local/share/jar/logs/db/db.log` | `jar.db` | DEBUG | Every SQL statement and parameters |
| `~/.local/share/jar/logs/service/service.log` | `jar.service` | INFO | Every service method call, result summary, and errors |

Both rotate at **5 MB** with **3 backups**. Directories are created on first run.

> SQL parameters are logged at DEBUG level. Treat log files as potentially sensitive.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `JAR_DB` | Override database file path |
| `JAR_FORMAT` | Override default output format (`table`/`json`/`csv`/`plain`) |

---

## MCP server

JAR ships an [MCP](https://modelcontextprotocol.io) server that exposes every service-layer operation as a tool, letting Claude (or any MCP-compatible client) manage your projects and tasks directly.

### Start the server

The server communicates over **stdio** — the MCP client launches it as a subprocess automatically.

```bash
jar-mcp          # installed entry point
# or
python -m jar.mcp_server
```

### Connect Claude Desktop

Add the following block to your `claude_desktop_config.json` (usually at `%APPDATA%\Claude\claude_desktop_config.json` on Windows or `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "jar": {
      "command": "jar-mcp"
    }
  }
}
```

With a custom database path:

```json
{
  "mcpServers": {
    "jar": {
      "command": "jar-mcp",
      "env": { "JAR_DB": "C:/path/to/my.db" }
    }
  }
}
```

Restart Claude Desktop after saving the config. The 12 JAR tools will appear in Claude's tool list.

### Available tools

| Tool | Description |
|---|---|
| `project_create` | Create a project (description must include `MVP:` and `EVALUATION:` sections) |
| `project_get` | Fetch a project by ID |
| `project_update` | Update any project field; omit to keep, pass `null` to clear |
| `project_delete` | Hard-delete a project and all its tasks (irreversible) |
| `project_list` | List/filter/sort projects |
| `project_tasks` | List all tasks for a project |
| `task_create` | Create a task |
| `task_get` | Fetch a task by ID |
| `task_update` | Update any task field; omit to keep, pass `null` to clear |
| `task_delete` | Delete a task |
| `task_list` | List/filter/sort tasks |
| `task_history` | Retrieve the full audit event log for a task by `task_id` |

---

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

147 tests covering models, filters (SQL builder), repository (in-memory SQLite), service layer, and task event lifecycle — including cascade-delete verification and event-survival-after-deletion checks.
