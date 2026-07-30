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
from rich.tree import Tree
from rich import box

from .models import Project, Task, TaskEvent
from .service import ActivityEntry, BlockedDigestEntry, DependencyTree, ProjectStatusReport

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
    console.print(f"[bold]Priority:[/bold]    {task.priority or '-'}")
    console.print(f"[bold]Tags:[/bold]        {', '.join(task.tags) if task.tags else '-'}")
    console.print(f"[bold]Deadline:[/bold]    {task.deadline or '-'}")
    console.print(f"[bold]Project ID:[/bold]  {task.project_id if task.project_id is not None else '-'}")
    console.print(f"[bold]Goal ID:[/bold]     {task.goal_id if task.goal_id is not None else '-'}")
    depends_str = ", ".join(f"#{d}" for d in task.depends) if task.depends else "-"
    console.print(f"[bold]Depends on:[/bold]  {depends_str}")
    console.print(f"[bold]Created:[/bold]     {task.created_at or '-'}")
    console.print(f"[bold]Updated:[/bold]     {task.updated_at or '-'}")
    if task.blocked_reason:
        console.print(f"[bold]Blocked:[/bold]     {task.blocked_reason}")
    if task.blocked_note:
        console.print(f"[bold]Note:[/bold]        {task.blocked_note}")
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


_STATUS_ORDER = [
    "scheduled", "in_progress",
    "blocked_dep", "blocked_owner", "blocked_external",
    "done", "failed", "suspended",
    "needs_plan", "desire", "triage",
]


def format_project_status(report: ProjectStatusReport, out: TextIO = sys.stdout) -> None:
    """Rich status-rollup panel for a project."""
    console = Console(file=out, highlight=False, legacy_windows=False)
    p = report.project
    status_val = p.status.value if hasattr(p.status, "value") else str(p.status)

    _hr(console, f"Project #{p.id} — {p.name}")
    console.print(f"[bold]Status:[/bold]   {status_val}   [dim]({report.total} tasks total)[/dim]")

    # Count summary — skip statuses with 0 unless they're the active groups
    active_statuses = {"scheduled", "in_progress", "blocked_dep", "blocked_owner", "blocked_external"}
    console.print()
    console.print("[bold]Task counts:[/bold]")
    for sv in _STATUS_ORDER:
        n = report.counts.get(sv, 0)
        if n == 0 and sv not in active_statuses:
            continue
        style = _STATUS_STYLES.get(sv, "dim")
        marker = f"[{style}]{sv}[/{style}]" if style != "dim" else f"[dim]{sv}[/dim]"
        console.print(f"  {marker:<35}  {n}")

    # Next actionable
    _hr(console, f"Next actionable ({len(report.next_actionable)})")
    if report.next_actionable:
        _task_table(report.next_actionable, out)
    else:
        console.print("[dim]No scheduled or in-progress tasks.[/dim]")

    # Blocked
    _hr(console, f"Blocked ({len(report.blocked)})")
    if report.blocked:
        for t in report.blocked:
            sv = t.status.value if hasattr(t.status, "value") else str(t.status)
            console.print(f"  [yellow]#{t.id}[/yellow] {t.name}  [dim]({sv})[/dim]")
            if t.blocked_reason:
                console.print(f"       [dim]reason: {t.blocked_reason}[/dim]")
            if t.blocked_note:
                console.print(f"       [dim]note:   {t.blocked_note}[/dim]")
    else:
        console.print("[dim]No blocked tasks.[/dim]")

    # Recently completed
    _hr(console, f"Recently completed ({report.counts.get('done', 0)})")
    if report.recently_completed:
        _task_table(report.recently_completed, out)
        if report.counts.get("done", 0) > len(report.recently_completed):
            console.print(f"[dim]  … {report.counts['done'] - len(report.recently_completed)} more done task(s) not shown[/dim]")
    else:
        console.print("[dim]No completed tasks yet.[/dim]")

    _hr(console)


def _task_label(task: Task) -> str:
    sv = task.status.value if hasattr(task.status, "value") else str(task.status)
    style = _STATUS_STYLES.get(sv, "dim")
    status_str = f"[{style}]{sv}[/{style}]" if style != "dim" else f"[dim]{sv}[/dim]"
    return f"#{task.id} {task.name}  {status_str}"


def format_dependency_tree(tree: DependencyTree, out: TextIO = sys.stdout) -> None:
    """Rich dependency tree showing upstream and downstream chains for a task."""
    console = Console(file=out, highlight=False, legacy_windows=False)
    task = tree.task

    root = Tree(_task_label(task))

    up_node = root.add("[bold]Depends on (upstream)[/bold]")
    if tree.upstream:
        for t in tree.upstream:
            up_node.add(_task_label(t))
    else:
        up_node.add("[dim]none[/dim]")

    down_node = root.add("[bold]Depended on by (downstream)[/bold]")
    if tree.downstream:
        for t in tree.downstream:
            down_node.add(_task_label(t))
    else:
        down_node.add("[dim]none[/dim]")

    console.print(root)


# ══════════════════════════════════════════════════════════════════ blocked digest

_BLOCKED_STATUS_STYLES = {
    "blocked_owner":    "yellow",
    "blocked_dep":      "cyan",
    "blocked_external": "magenta",
}


def format_blocked_digest(
    entries: list[BlockedDigestEntry],
    out: TextIO = sys.stdout,
) -> None:
    """Rich table of blocked tasks, matching surface_blockers.py entry format."""
    console = Console(file=out, highlight=False, legacy_windows=False)

    if not entries:
        console.print("[dim]No blocked tasks.[/dim]")
        return

    n = len(entries)
    console.print(
        f"\n[bold]Blocker Digest[/bold] — {n} task{'s' if n != 1 else ''} awaiting action\n"
    )

    table = Table(
        show_header=True,
        header_style="bold",
        box=box.SIMPLE,
        expand=False,
        show_lines=True,
    )
    table.add_column("Task", style="bold", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("What's needed", overflow="fold")

    for e in entries:
        style = _BLOCKED_STATUS_STYLES.get(e.status, "white")
        status_cell = f"[{style}]{e.status}[/{style}]"
        what = e.blocked_note or (e.description or "")[:200] or "(no detail)"
        table.add_row(f"T{e.task_id}  {e.name}", status_cell, what)

    console.print(table)
    console.print(
        "[dim]To unblock: reply with the answer, or mark the task as"
        " defer / skip / won't-do.[/dim]\n"
    )


# ══════════════════════════════════════════════════════════════════ activity digest

_STATUS_SECTION_ORDER = ["done", "blocked_owner", "blocked_dep", "blocked_external"]
_STATUS_STYLES_ACT = {
    "done":             "green",
    "blocked_owner":    "red",
    "blocked_dep":      "yellow",
    "blocked_external": "magenta",
    "in_progress":      "cyan",
    "scheduled":        "white",
    "needs_plan":       "dim",
    "created":          "bright_white",
}


def format_activity_digest(
    entries: list[ActivityEntry],
    since_label: str = "",
    out: TextIO = sys.stdout,
) -> None:
    """Rich grouped digest of task events, sectioned by outcome type."""
    console = Console(file=out, highlight=False, legacy_windows=False)

    if not entries:
        label = f" since {since_label}" if since_label else ""
        console.print(f"[dim]No activity{label}.[/dim]")
        return

    label = f" since {since_label}" if since_label else ""
    console.print(f"\n[bold]Activity Digest[/bold]{label} — {len(entries)} event(s)\n")

    # Group events by task_id, preserving chronological order of first occurrence
    from collections import defaultdict
    task_order: list[int] = []
    task_entries: dict[int, list[ActivityEntry]] = defaultdict(list)
    for e in entries:
        if e.task_id not in task_entries:
            task_order.append(e.task_id)
        task_entries[e.task_id].append(e)

    # Partition tasks into sections based on their final status event
    completed_ids: list[int] = []
    newly_blocked_ids: list[int] = []
    other_ids: list[int] = []

    for tid in task_order:
        evts = task_entries[tid]
        # Find last status change in this window
        status_events = [e for e in evts if e.field_name == "status"]
        final_status = status_events[-1].new_value if status_events else None
        if final_status == "done":
            completed_ids.append(tid)
        elif final_status and final_status.startswith("blocked"):
            newly_blocked_ids.append(tid)
        else:
            other_ids.append(tid)

    def _render_task(tid: int) -> None:
        evts = task_entries[tid]
        name = evts[0].task_name
        # Truncate name if too long
        name_str = name if len(name) <= 60 else name[:57] + "…"

        status_evts = [e for e in evts if e.field_name == "status"]
        final_status = status_evts[-1].new_value if status_evts else None
        ts = evts[-1].changed_at[:16].replace("T", " ")  # YYYY-MM-DD HH:MM

        style = _STATUS_STYLES_ACT.get(final_status or "", "white")
        status_badge = f"[{style}]{final_status or '—'}[/{style}]" if final_status else ""

        # Show "created", or status transitions, or field changes
        if any(e.event_type == "created" for e in evts):
            summary = "[dim]created[/dim]"
        elif status_evts:
            transitions = " → ".join(
                f"{e.old_value or '?'} [bold]→[/bold] {e.new_value or '?'}"
                for e in status_evts
            )
            summary = transitions
        else:
            changed_fields = sorted({e.field_name for e in evts if e.field_name})
            summary = f"[dim]{', '.join(changed_fields)} changed[/dim]"

        console.print(
            f"  [dim]T{tid}[/dim]  {name_str}  {status_badge}  [dim]{ts}[/dim]\n"
            f"        {summary}\n"
        )

    if completed_ids:
        console.print("[bold green]✓ Completed[/bold green]")
        for tid in completed_ids:
            _render_task(tid)

    if newly_blocked_ids:
        console.print("[bold red]▲ Newly Blocked[/bold red]")
        for tid in newly_blocked_ids:
            _render_task(tid)

    if other_ids:
        console.print("[bold]Other Changes[/bold]")
        for tid in other_ids:
            _render_task(tid)
