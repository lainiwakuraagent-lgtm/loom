# Project JAR — Project & Task Management System

## Architecture Overview

A Python-based CLI + service system for managing projects and tasks with flexible filtering, sorting, and multiple output formats.

### Technology Stack
- **Language:** Python 3.11+
- **Storage:** SQLite via `sqlite3` (stdlib) — single-file database, portable, no external server needed
- **CLI:** `click` — ergonomic command parsing, subcommands, options
- **Output formatting:** `rich` — tables, colored output, JSON, plain text
- **Data models:** `dataclasses` + manual ORM layer (no heavy ORM dependency)
- **Config:** `platformdirs` for locating the DB file and log directories (e.g. `~/.local/share/jar/`)
- **Logging:** stdlib `logging` — two rotating file handlers writing to separate directories
- **MCP transport:** `mcp` (stdio) + `starlette` + `uvicorn` (HTTP/SSE web transport)

### Design Principles
- Projects and tasks are independent — neither requires the other
- All entities have a stable integer primary key (UUID alternative kept simple)
- The system is usable both as a CLI tool and as an importable Python library (service layer is decoupled from CLI)
- All timestamps stored as ISO-8601 UTC strings in SQLite

---

## Data Models

### Task
| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `name` | TEXT NOT NULL | short label |
| `description` | TEXT | multi-line detail |
| `tags` | TEXT | comma-separated list; each tag is a free-text string OR one of enum values: `bug`, `feature`, `chore`, `docs`, `research`, `design` |
| `deadline` | TEXT (ISO-8601) | nullable |
| `project_id` | INTEGER FK → Project | nullable (standalone task allowed) |
| `status` | TEXT ENUM | `todo` \| `in_progress` \| `done` |
| `created_at` | TEXT (ISO-8601) | set on insert |
| `updated_at` | TEXT (ISO-8601) | updated on every write |

### Project
| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `name` | TEXT NOT NULL | short label |
| `description` | TEXT | **MUST include**: (1) MVP definition, (2) final evaluation / why it is being developed or its usefulness |
| `start_date` | TEXT (ISO-8601) | nullable |
| `deployment_date` | TEXT (ISO-8601) | nullable (target release / go-live) |
| `created_at` | TEXT (ISO-8601) | set on insert |
| `updated_at` | TEXT (ISO-8601) | updated on every write |

> **Constraint note:** Project `description` is validated at write time to contain at least two sections — one labeled/prefixed with `MVP:` and one with `EVALUATION:` (case-insensitive). The CLI will guide the user; the service layer raises `ValueError` on violation.

### task_events (schema v2)

Immutable per-field audit log. Every task creation, field change, and deletion is recorded here. History survives task deletion (no FK constraint on `task_id`).

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `task_id` | INTEGER | references the task — NO FK, history survives deletion |
| `event_type` | TEXT | `created` \| `updated` \| `deleted` |
| `field_name` | TEXT | which field changed; `null` for `created`/`deleted` events |
| `old_value` | TEXT | previous value as string; `null` for `created` events |
| `new_value` | TEXT | new value as string; `null` for `deleted` events |
| `changed_at` | TEXT (ISO-8601 UTC) | when the event occurred |
| `task_snapshot` | TEXT (JSON) | full task state at time of event |

Tracked fields: `name`, `description`, `tags`, `deadline`, `status`, `project_id`.

---

## File Listing

```
project JAR/
├── CLAUDE.md                   # this file
├── pyproject.toml              # project metadata, deps, entry point
├── README.md                   # usage docs
├── jar/
│   ├── __init__.py
│   ├── db.py                   # DB connection, schema migrations (v2), init
│   ├── models.py               # dataclasses: Task, Project, TaskEvent; enums: Status, EventType
│   ├── repository.py           # data access layer (CRUD + queries + TaskEventRepository)
│   ├── service.py              # business logic, validation, event recording
│   ├── filters.py              # filter/sort spec dataclasses + SQL builder
│   ├── formatters.py           # output: table, json, csv, plain (tasks, projects, events)
│   ├── analytics.py            # AnalyticsService — read-only computed metrics from task_events
│   ├── logging_config.py       # configures both loggers, creates log dirs
│   ├── mcp_server.py           # MCP server (18 tools); --transport stdio|sse, --host, --port
│   └── cli/
│       ├── __init__.py
│       ├── main.py             # click group entry point
│       ├── project_cmds.py     # `jar project add|edit|delete|list|show`
│       ├── task_cmds.py        # `jar task add|edit|delete|list|show|history`
│       └── analytics_cmds.py   # `jar analytics summary|deadline|velocity|capacity|behavior|realism`
└── tests/
    ├── test_models.py
    ├── test_filters.py
    ├── test_repository.py      # includes TaskEventRepository tests
    ├── test_service.py         # includes TaskServiceHistory tests
    └── test_analytics.py       # AnalyticsService metric computation tests

# Runtime log directories (created automatically, not committed to VCS)
~/.local/share/jar/logs/db/         # DB-level operation logs
~/.local/share/jar/logs/service/    # Service-level call logs
```

---

## Implementation Steps (in order)

### Phase 1 — Foundation
1. Create `pyproject.toml` with dependencies: `click`, `rich`, `platformdirs`, `mcp`, `uvicorn[standard]`
2. Implement `jar/models.py` — `Status` enum, `TagEnum` enum, `Task` and `Project` dataclasses
3. Implement `jar/db.py` — `get_connection()`, `init_db()` (creates tables if not exist, enables `PRAGMA foreign_keys = ON` and WAL mode), schema version table for future migrations
4. Implement `jar/logging_config.py`:
   - `get_db_logger()` — returns a `logging.Logger` writing to `logs/db/db.log` (rotating, max 5 MB × 3 backups)
   - `get_service_logger()` — returns a `logging.Logger` writing to `logs/service/service.log` (rotating, max 5 MB × 3 backups)
   - Log directories created automatically via `platformdirs.user_data_dir`
   - Log format: `%(asctime)s | %(levelname)s | %(name)s | %(message)s`
   - DB logger logs at DEBUG level (every SQL statement + params); service logger logs at INFO level (every public method entry/exit + errors)

### Phase 2 — Data Layer
5. Implement `jar/repository.py`:
   - `ProjectRepository`: `insert`, `update`, `delete`, `get_by_id`, `list_all`, `list_filtered`
     - `delete` issues `DELETE FROM tasks WHERE project_id = ?` first, then `DELETE FROM projects WHERE id = ?` — both inside a single transaction, enforced by SQLite `PRAGMA foreign_keys = ON` cascade as well
     - Every SQL execution is logged via `get_db_logger()` at DEBUG level (operation name, table, id/params)
   - `TaskRepository`: `insert`, `update`, `delete`, `get_by_id`, `list_all`, `list_filtered`
     - Every SQL execution logged the same way
6. Implement `jar/filters.py`:
   - `TaskFilter` dataclass: `status`, `project_id`, `tags`, `deadline_before`, `deadline_after`, `search` (name/desc substring)
   - `ProjectFilter` dataclass: `search`, `has_tasks`, `start_before`, `start_after`, `deployment_before`, `deployment_after`
   - `SortSpec` dataclass: `field`, `direction` (`asc`/`desc`)
   - `build_task_query()` and `build_project_query()` — generate parameterized SQL WHERE + ORDER BY

### Phase 3 — Business Logic
8. Implement `jar/service.py`:
   - `ProjectService`: wraps repository, enforces `MVP:` + `EVALUATION:` description constraint
     - Every public method (`create`, `update`, `delete`, `get`, `list_filtered`) is wrapped with `get_service_logger()` — logs method name + arguments on entry (INFO), result summary on success (INFO), full exception on failure (ERROR)
   - `TaskService`: wraps repository, validates status transitions, resolves project FK
     - Same service-level logging pattern
9. Implement `jar/formatters.py`:
   - `format_tasks(tasks, fmt)` and `format_projects(projects, fmt)` where `fmt ∈ {table, json, csv, plain}`
   - Table format uses `rich.table.Table` with color-coded status column
   - JSON format outputs a JSON array (pretty-printed)
   - CSV format outputs RFC 4180 CSV to stdout
   - Plain format outputs one item per line with key=value pairs

### Phase 4 — CLI
10. Implement `jar/cli/main.py` — root `click.group()`, `--db` path override option, `--format` global option
11. Implement `jar/cli/project_cmds.py`:
    - `jar project add` — interactive prompts for name, description (with MVP/EVALUATION hint), start_date, deployment_date
    - `jar project edit <id>` — edit any field
    - `jar project delete <id>` — **no CLI delete command exposed**; deletion is intentionally absent from the CLI and only callable via the service/library API directly (enforces "local system only" restriction); the CLI shows an informative error if attempted
    - `jar project list` — with `--search`, `--sort`, `--format` options
    - `jar project show <id>` — full detail including task list
12. Implement `jar/cli/task_cmds.py`:
    - `jar task add` — prompts for all fields, `--project` option
    - `jar task edit <id>` — edit any field, including reassign project
    - `jar task delete <id>` — with confirmation
    - `jar task list` — with `--status`, `--project`, `--tag`, `--deadline-before`, `--deadline-after`, `--search`, `--sort`, `--format`
    - `jar task show <id>` — full detail

### Phase 5 — Tests & Polish
13. Write unit tests for models, repository (using in-memory SQLite `:memory:`), service (validation logic), and filter SQL builder; include a test that verifies cascade-delete removes all tasks when a project is deleted
14. Write `README.md` with install instructions and usage examples

---

## CLI Usage Examples (target UX)

```bash
# Projects
jar project add
jar project list --format table
jar project list --search "API" --sort deployment_date:asc
jar project show 3
jar project edit 3
jar project delete 3

# Tasks
jar task add --project 3
jar task list --status todo --format json
jar task list --tag feature --deadline-before 2025-06-01 --sort deadline:asc
jar task list --project 3 --status in_progress
jar task show 12
jar task edit 12
jar task delete 12

# Global options
jar --format csv task list --status done > done_tasks.csv
jar --db /custom/path/mydb.db task list
```

---

## Service Layer (library usage)

```python
from jar.service import ProjectService, TaskService
from jar.analytics import AnalyticsService
from jar.filters import TaskFilter, SortSpec
from jar.db import get_connection, init_db

conn = get_connection()
init_db(conn)
ts = TaskService(conn)

tasks = ts.list_filtered(
    TaskFilter(status="todo", tags=["bug"], deadline_before="2025-06-01"),
    sort=SortSpec(field="deadline", direction="asc")
)

# Analytics — read-only, no writes
svc = AnalyticsService(conn)
summary = svc.summary()
deadline_health = svc.deadline_health(since="2026-01-01", tag="feature")
velocity = svc.velocity()
capacity = svc.capacity()
behavior = svc.behavior(project_id=3)
realism = svc.realism(tag="bug")
```

### AnalyticsService methods

All methods are read-only and return plain Python dicts. They accept optional `since` (ISO date string), `project_id` (int), and `tag` (str) filters to scope the analysis.

| Method | Filters | What it computes |
|---|---|---|
| `summary(since)` | `since` | Compact health dashboard combining key numbers from all metrics |
| `deadline_health(since, project_id, tag)` | all | Push rate + miss rate by tag/project |
| `velocity(since, project_id, tag)` | all | Time-to-done distribution + completions per week |
| `capacity(since)` | `since` | Deadline clustering + context switching load |
| `behavior(since, project_id, tag)` | all | Task rot + recovery lag + status reversals |
| `realism(since, tag)` | `since`, `tag` | Deadline Realism Score + horizon + abandonment rate |

---

## Potential Edge Cases

| Case | Handling |
|---|---|
| Deleting a project that has tasks | **Hard cascade delete** — all tasks with `project_id = <id>` are deleted first (in the same transaction), then the project row is removed. SQLite `PRAGMA foreign_keys = ON` enforces referential integrity. This operation is NOT exposed via CLI; it is only callable programmatically through `ProjectService.delete()` to restrict access to local system consumers only. |
| Task assigned to non-existent project_id | Service raises `ValueError` before insert/update |
| Invalid status value in CLI | `click.Choice` restricts input; service layer also validates |
| Tag input — mixed free-text and enum values | Tags stored as raw comma-separated strings; enum values listed as suggestions in CLI help |
| Empty `description` on project | Allowed in DB (nullable), but `MVP:` + `EVALUATION:` constraint only applies when description is non-empty |
| Deadline in the past | Allowed (no hard block); CLI shows a warning via `rich` |
| Concurrent writes | SQLite WAL mode enabled; single-user assumption, no connection pooling needed |
| `--format json` piped to another process | Output is clean JSON to stdout; all user-facing messages go to stderr |
| DB file does not exist yet | `init_db()` creates it on first run automatically |
| Sorting by a nullable field (e.g. `deadline`) | NULLs sorted last in ASC, first in DESC (SQLite default behavior, documented) |
| Log directory does not exist yet | `logging_config.py` creates both `logs/db/` and `logs/service/` on import via `os.makedirs(..., exist_ok=True)` |
| Sensitive data in logs | SQL params are logged at DEBUG — treat log files as sensitive; log directories default to user data dir (not world-readable on Linux/macOS) |
| Log file growth | Both handlers are `RotatingFileHandler` (max 5 MB, 3 backups each) to cap disk usage |
