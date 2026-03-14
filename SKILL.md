# JAR MCP — Claude Usage Guide

You have access to the **JAR** MCP server, which manages projects and tasks stored in a local SQLite database. Use the tools below to create, read, update, delete, and filter projects and tasks on the user's behalf.

---

## Data model quick-reference

### Project
| Field | Type | Notes |
|---|---|---|
| `id` | int | auto-assigned, stable |
| `name` | str | required |
| `description` | str \| null | **must contain `MVP:` and `EVALUATION:` sections** when non-null |
| `start_date` | `YYYY-MM-DD` \| null | |
| `deployment_date` | `YYYY-MM-DD` \| null | target go-live |
| `created_at` / `updated_at` | ISO-8601 UTC | set automatically |

### Task
| Field | Type | Notes |
|---|---|---|
| `id` | int | auto-assigned, stable |
| `name` | str | required |
| `description` | str \| null | free-form |
| `tags` | list[str] | suggested: `bug` `feature` `chore` `docs` `research` `design` |
| `deadline` | `YYYY-MM-DD` \| null | |
| `project_id` | int \| null | null = standalone task |
| `status` | `todo` \| `in_progress` \| `done` | default `todo` |
| `created_at` / `updated_at` | ISO-8601 UTC | set automatically |

---

## Tools

### `project_create`
Create a new project.

```json
{
  "name": "API Gateway",
  "description": "MVP: Build a basic reverse-proxy with auth.\nEVALUATION: Reduces coupling between frontend and backend services.",
  "start_date": "2025-01-01",
  "deployment_date": "2025-06-01"
}
```

- `name` is required; all other fields are optional.
- If you include `description`, it **must** contain both an `MVP:` line/section and an `EVALUATION:` line/section (case-insensitive). The server will reject it otherwise.
- Omit `description` entirely if the user has not provided the MVP/evaluation content yet.

---

### `project_get`
Fetch one project by ID. Returns `null` if not found.

```json
{ "id": 3 }
```

---

### `project_update`
Update any subset of a project's fields.

```json
{ "id": 3, "deployment_date": "2025-09-01" }
```

**Omit** a field → it is left unchanged.
**Pass `null`** → the field is cleared (set to NULL in the database).

```json
{ "id": 3, "deployment_date": null }
```

- The same `MVP:` + `EVALUATION:` constraint applies if you supply a non-null `description`.

---

### `project_delete`
Hard-delete a project **and every task that belongs to it**. This is irreversible — confirm with the user before calling.

```json
{ "id": 3 }
```

Returns `{ "deleted": true }` on success.

---

### `project_list`
List projects with optional filters and sorting. All fields are optional.

```json
{
  "search": "API",
  "has_tasks": true,
  "deployment_before": "2025-12-31",
  "sort_field": "deployment_date",
  "sort_direction": "asc"
}
```

| Parameter | Type | Meaning |
|---|---|---|
| `search` | string | substring match on `name` or `description` |
| `has_tasks` | boolean | `true` = only projects that have at least one task |
| `start_before` / `start_after` | `YYYY-MM-DD` | inclusive bounds on `start_date` |
| `deployment_before` / `deployment_after` | `YYYY-MM-DD` | inclusive bounds on `deployment_date` |
| `sort_field` | `id` \| `name` \| `start_date` \| `deployment_date` \| `created_at` \| `updated_at` | |
| `sort_direction` | `asc` (default) \| `desc` | NULLs sort last in asc, first in desc |

---

### `project_tasks`
List all tasks that belong to a specific project.

```json
{ "id": 3 }
```

---

### `task_create`
Create a new task.

```json
{
  "name": "Fix login redirect bug",
  "tags": ["bug"],
  "deadline": "2025-06-01",
  "project_id": 3,
  "status": "todo"
}
```

- `name` is required.
- `tags` can be a JSON array `["bug", "feature"]` or a comma-separated string `"bug,feature"`.
- `project_id` is optional — omit for a standalone task.
- `status` defaults to `"todo"` if omitted.

---

### `task_get`
Fetch one task by ID. Returns `null` if not found.

```json
{ "id": 12 }
```

---

### `task_update`
Update any subset of a task's fields.

```json
{ "id": 12, "status": "in_progress" }
```

**Omit** a field → unchanged. **Pass `null`** → cleared.

```json
{ "id": 12, "deadline": null, "project_id": null }
```

- `project_id: null` detaches the task from its project (makes it standalone).
- `tags: null` or `tags: []` clears all tags.

---

### `task_delete`
Delete a task by ID.

```json
{ "id": 12 }
```

Returns `{ "deleted": true }` on success.

---

### `task_list`
List tasks with optional filters and sorting. All fields are optional.

```json
{
  "status": "todo",
  "project_id": 3,
  "tags": ["bug"],
  "deadline_before": "2025-06-01",
  "sort_field": "deadline",
  "sort_direction": "asc"
}
```

| Parameter | Type | Meaning |
|---|---|---|
| `status` | `todo` \| `in_progress` \| `done` | exact match |
| `project_id` | int | exact match; use `-1` for standalone tasks only |
| `tags` | array or CSV string | task must contain **all** listed tags |
| `deadline_before` / `deadline_after` | `YYYY-MM-DD` | inclusive bounds |
| `deadline_on` | `YYYY-MM-DD` | exact deadline date |
| `overdue` | boolean | `true` = deadline in the past and status ≠ done |
| `search` | string | substring match on `name` or `description` |
| `sort_field` | `id` \| `name` \| `deadline` \| `status` \| `created_at` \| `updated_at` | |
| `sort_direction` | `asc` (default) \| `desc` | |

---

## Behavioural guidelines

### Project descriptions
Always include both sections when writing a project description:
```
MVP: <minimum viable scope — what the smallest useful version does>
EVALUATION: <why this project is being built / what success looks like>
```
If the user asks you to create a project but has not given you enough information for both sections, ask for it rather than submitting an incomplete description. Alternatively, omit `description` and remind the user to add it later with `project_update`.

### Destructive operations
- **`project_delete`** cascades to all child tasks. Always tell the user what will be deleted and ask for confirmation before calling it.
- **`task_delete`** affects only that one task. A simple confirmation is sufficient.

### Update vs replace
Use `project_update` / `task_update` with only the changed fields. Never re-send unchanged fields with their current values — that would be noisy and could accidentally overwrite concurrent edits.

### Returning results to the user
- For single items (`project_get`, `task_get`), present the key fields in a readable summary.
- For lists, present a concise table or bullet list; mention the total count.
- For mutations, confirm what was created/changed/deleted with the item's ID and name.
- For errors returned by the server (e.g. missing MVP/EVALUATION, invalid project ID), explain the constraint to the user and offer to fix the input.
