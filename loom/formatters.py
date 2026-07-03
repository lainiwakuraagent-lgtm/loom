"""Output formatters — table, json, csv, plain.

All functions write to stdout (or a provided file object).
User-facing messages (warnings, prompts) should go to stderr, not here.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from typing import TextIO

from rich.console import Console
from rich.table import Table
from rich import box

from .models import Project, Task, TaskEvent

# Supported format names
FORMATS = ("table", "json", "csv", "plain")

_STATUS_STYLES = {
    "todo":        "yellow",
    "in_progress": "cyan",
    "done":        "green",
    "failed":      "red",
}

_EVENT_TYPE_STYLES = {
    "created": "green",
    "updated": "cyan",
    "deleted": "red",
}

_MAX_VALUE_LEN = 30


# ══════════════════════════════════════════════════════════════════ tasks


def format_tasks(
    tasks: list[Task],
    fmt: str = "table",
    out: TextIO = sys.stdout,
) -> None:
    if fmt == "table":
        _task_table(tasks, out)
    elif fmt == "json":
        _task_json(tasks, out)
    elif fmt == "csv":
        _task_csv(tasks, out)
    elif fmt == "plain":
        _task_plain(tasks, out)
    else:
        raise ValueError(f"Unknown format {fmt!r}. Choose from: {FORMATS}")


def _task_table(tasks: list[Task], out: TextIO) -> None:
    console = Console(file=out, highlight=False, legacy_windows=False)
    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, pad_edge=False)
    table.add_column("ID",          style="dim",   no_wrap=True, min_width=4)
    table.add_column("Name",                       no_wrap=False, min_width=20)
    table.add_column("Status",                     no_wrap=True)
    table.add_column("Tags",        style="dim",   no_wrap=True)
    table.add_column("Deadline",    style="dim",   no_wrap=True)
    table.add_column("Project",     style="dim",   no_wrap=True)

    for t in tasks:
        status_val = t.status.value if hasattr(t.status, "value") else t.status
        style = _STATUS_STYLES.get(status_val, "")
        table.add_row(
            str(t.id),
            t.name,
            f"[{style}]{status_val}[/{style}]" if style else status_val,
            ", ".join(t.tags) if t.tags else "",
            t.deadline or "",
            str(t.project_id) if t.project_id is not None else "",
        )

    console.print(table)


def _task_json(tasks: list[Task], out: TextIO) -> None:
    out.write(json.dumps([t.to_dict() for t in tasks], indent=2, default=str))
    out.write("\n")


def _task_csv(tasks: list[Task], out: TextIO) -> None:
    fields = ["id", "name", "description", "tags", "deadline", "project_id", "status", "created_at", "updated_at"]
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for t in tasks:
        d = t.to_dict()
        d["tags"] = ",".join(t.tags)
        writer.writerow(d)


def _task_plain(tasks: list[Task], out: TextIO) -> None:
    for t in tasks:
        d = t.to_dict()
        d["tags"] = ",".join(t.tags)
        line = "  ".join(f"{k}={v}" for k, v in d.items() if v is not None)
        out.write(line + "\n")


# ══════════════════════════════════════════════════════════════════ projects


def format_projects(
    projects: list[Project],
    fmt: str = "table",
    out: TextIO = sys.stdout,
) -> None:
    if fmt == "table":
        _project_table(projects, out)
    elif fmt == "json":
        _project_json(projects, out)
    elif fmt == "csv":
        _project_csv(projects, out)
    elif fmt == "plain":
        _project_plain(projects, out)
    else:
        raise ValueError(f"Unknown format {fmt!r}. Choose from: {FORMATS}")


def _project_table(projects: list[Project], out: TextIO) -> None:
    console = Console(file=out, highlight=False, legacy_windows=False)
    if not projects:
        console.print("[dim]No projects found.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, pad_edge=False)
    table.add_column("ID",               style="dim",  no_wrap=True, min_width=4)
    table.add_column("Name",                           no_wrap=False, min_width=20)
    table.add_column("Start",            style="dim",  no_wrap=True)
    table.add_column("Deploy",           style="dim",  no_wrap=True)
    table.add_column("Description",      style="dim",  no_wrap=False, max_width=40)

    for p in projects:
        desc_preview = ""
        if p.description:
            first_line = p.description.strip().splitlines()[0]
            desc_preview = first_line[:40] + ("…" if len(first_line) > 40 else "")

        table.add_row(
            str(p.id),
            p.name,
            p.start_date or "",
            p.deployment_date or "",
            desc_preview,
        )

    console.print(table)


def _project_json(projects: list[Project], out: TextIO) -> None:
    out.write(json.dumps([p.to_dict() for p in projects], indent=2, default=str))
    out.write("\n")


def _project_csv(projects: list[Project], out: TextIO) -> None:
    fields = ["id", "name", "description", "start_date", "deployment_date", "created_at", "updated_at"]
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for p in projects:
        writer.writerow(p.to_dict())


def _project_plain(projects: list[Project], out: TextIO) -> None:
    for p in projects:
        d = p.to_dict()
        line = "  ".join(f"{k}={v}" for k, v in d.items() if v is not None)
        out.write(line + "\n")


# ══════════════════════════════════════════════════════════════════ task events


def format_task_events(
    events: list[TaskEvent],
    fmt: str = "table",
    out: TextIO = sys.stdout,
) -> None:
    if fmt == "table":
        _event_table(events, out)
    elif fmt == "json":
        _event_json(events, out)
    elif fmt == "csv":
        _event_csv(events, out)
    elif fmt == "plain":
        _event_plain(events, out)
    else:
        raise ValueError(f"Unknown format {fmt!r}. Choose from: {FORMATS}")


def _event_table(events: list[TaskEvent], out: TextIO) -> None:
    console = Console(file=out, highlight=False, legacy_windows=False)
    if not events:
        console.print("[dim]No history found.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, pad_edge=False)
    table.add_column("ID",        style="dim",  no_wrap=True, min_width=4)
    table.add_column("Task ID",   style="dim",  no_wrap=True)
    table.add_column("Timestamp",               no_wrap=True)
    table.add_column("Event",                   no_wrap=True)
    table.add_column("Field",     style="dim",  no_wrap=True)
    table.add_column("Old Value", style="dim",  no_wrap=False, max_width=_MAX_VALUE_LEN)
    table.add_column("New Value",               no_wrap=False, max_width=_MAX_VALUE_LEN)

    for e in events:
        et = e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type)
        style = _EVENT_TYPE_STYLES.get(et, "")
        event_str = f"[{style}]{et}[/{style}]" if style else et

        def _trunc(v: Optional[str]) -> str:
            if v is None:
                return ""
            return v if len(v) <= _MAX_VALUE_LEN else v[:_MAX_VALUE_LEN - 1] + "…"

        table.add_row(
            str(e.id),
            str(e.task_id),
            e.changed_at,
            event_str,
            e.field_name or "",
            _trunc(e.old_value),
            _trunc(e.new_value),
        )

    console.print(table)


def _event_json(events: list[TaskEvent], out: TextIO) -> None:
    def _to_dict_inlined(e: TaskEvent) -> dict:
        d = e.to_dict()
        if d.get("task_snapshot"):
            try:
                d["task_snapshot"] = json.loads(d["task_snapshot"])
            except (ValueError, TypeError):
                pass
        return d

    out.write(json.dumps([_to_dict_inlined(e) for e in events], indent=2, default=str))
    out.write("\n")


def _event_csv(events: list[TaskEvent], out: TextIO) -> None:
    fields = ["id", "task_id", "event_type", "field_name", "old_value", "new_value", "changed_at", "task_snapshot"]
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for e in events:
        writer.writerow(e.to_dict())


def _event_plain(events: list[TaskEvent], out: TextIO) -> None:
    for e in events:
        d = e.to_dict()
        line = "  ".join(f"{k}={v}" for k, v in d.items() if v is not None)
        out.write(line + "\n")


# ══════════════════════════════════════════════════════════════════ detail views

_WIDTH = 80


def _hr(console: "Console", title: str = "") -> None:
    """Print an ASCII horizontal rule (avoids cp1252 encoding issues on Windows)."""
    if title:
        pad = max(2, (_WIDTH - len(title) - 2) // 2)
        console.print(f"[dim]{'-' * pad}[/dim] [bold]{title}[/bold] [dim]{'-' * pad}[/dim]")
    else:
        console.print(f"[dim]{'-' * _WIDTH}[/dim]")


def format_task_detail(task: Task, out: TextIO = sys.stdout) -> None:
    """Rich single-task detail panel."""
    console = Console(file=out, highlight=False, legacy_windows=False)
    status_val = task.status.value if hasattr(task.status, "value") else task.status
    style = _STATUS_STYLES.get(status_val, "")

    _hr(console, f"Task #{task.id}")
    console.print(f"[bold]Name:[/bold]        {task.name}")
    console.print(f"[bold]Status:[/bold]      [{style}]{status_val}[/{style}]" if style else f"[bold]Status:[/bold]      {status_val}")
    console.print(f"[bold]Tags:[/bold]        {', '.join(task.tags) if task.tags else '-'}")
    console.print(f"[bold]Deadline:[/bold]    {task.deadline or '-'}")
    console.print(f"[bold]Project ID:[/bold]  {task.project_id if task.project_id is not None else '-'}")
    console.print(f"[bold]Created:[/bold]     {task.created_at or '-'}")
    console.print(f"[bold]Updated:[/bold]     {task.updated_at or '-'}")
    if task.description:
        _hr(console, "Description")
        console.print(task.description)
    _hr(console)


def format_project_detail(project: Project, tasks: list[Task], out: TextIO = sys.stdout) -> None:
    """Rich single-project detail panel including its task list."""
    console = Console(file=out, highlight=False, legacy_windows=False)

    _hr(console, f"Project #{project.id}")
    console.print(f"[bold]Name:[/bold]        {project.name}")
    console.print(f"[bold]Start:[/bold]       {project.start_date or '-'}")
    console.print(f"[bold]Deployment:[/bold]  {project.deployment_date or '-'}")
    console.print(f"[bold]Created:[/bold]     {project.created_at or '-'}")
    console.print(f"[bold]Updated:[/bold]     {project.updated_at or '-'}")

    if project.description:
        _hr(console, "Description")
        console.print(project.description)

    _hr(console, f"Tasks ({len(tasks)})")
    if tasks:
        _task_table(tasks, out)
    else:
        console.print("[dim]No tasks assigned.[/dim]")
    _hr(console)
