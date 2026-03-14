"""CLI commands for tasks: add, edit, delete, list, show."""

from __future__ import annotations

import sys
from datetime import date
from typing import Optional

import click
from rich.console import Console

from ..db import get_connection, init_db
from ..filters import TaskFilter, SortSpec
from ..formatters import format_task_detail, format_tasks
from ..models import SUGGESTED_TAGS, Status
from ..service import TaskService

from .main import JarContext, parse_sort_option, pass_jar

_err = Console(stderr=True, legacy_windows=False)
_STATUSES = [s.value for s in Status]


def _get_service(jar: JarContext) -> tuple[TaskService, object]:
    conn = get_connection(jar.db_path)
    init_db(conn)
    return TaskService(conn), conn


def _warn_past_deadline(deadline: Optional[str]) -> None:
    if deadline and deadline < date.today().isoformat():
        _err.print(f"[yellow]Warning:[/yellow] Deadline {deadline!r} is in the past.")


# ══════════════════════════════════════════════════════════════════ group

@click.group("task")
def task() -> None:
    """Manage tasks."""


# ══════════════════════════════════════════════════════════════════ add

@task.command("add")
@click.option("--name",        "-n", default=None, help="Task name.")
@click.option("--description", "-D", default=None, help="Task description.")
@click.option(
    "--tags", "-t", default=None,
    metavar="TAG[,TAG...]",
    help=f"Comma-separated tags. Suggested: {', '.join(SUGGESTED_TAGS)}",
)
@click.option("--deadline",    "-d", default=None, metavar="YYYY-MM-DD", help="Deadline date.")
@click.option(
    "--project",   "-p", default=None, type=int, metavar="ID",
    help="Assign to project by ID (omit for standalone task).",
)
@click.option(
    "--status",    "-s",
    default="todo",
    type=click.Choice(_STATUSES, case_sensitive=False),
    show_default=True,
    help="Initial status.",
)
@pass_jar
def task_add(
    jar: JarContext,
    name: Optional[str],
    description: Optional[str],
    tags: Optional[str],
    deadline: Optional[str],
    project: Optional[int],
    status: str,
) -> None:
    """Create a new task.

    Only --name is required; all other fields are optional and may be set
    later with `jar task edit`. Pass --name to skip the interactive prompt.
    """
    if not name:
        name = click.prompt("Task name")

    _warn_past_deadline(deadline)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    svc, conn = _get_service(jar)
    try:
        t = svc.create(
            name=name,
            description=description,
            tags=tag_list,
            deadline=deadline,
            project_id=project,
            status=status,
        )
        _err.print(f"[green]Created task #{t.id}:[/green] {t.name}")
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ edit

@task.command("edit")
@click.argument("task_id", type=int)
@click.option("--name",        "-n", default=None, help="New name.")
@click.option("--description", "-D", default=None, help="New description (use '' to clear).")
@click.option(
    "--tags", "-t", default=None,
    metavar="TAG[,TAG...]",
    help="Replace tags (comma-separated; use '' to clear all).",
)
@click.option("--deadline",    "-d", default=None, metavar="YYYY-MM-DD", help="New deadline (use '' to clear).")
@click.option("--project",     "-p", default=None, metavar="ID|''",      help="Reassign project ID (use '' to detach).")
@click.option(
    "--status",    "-s",
    default=None,
    type=click.Choice(_STATUSES, case_sensitive=False),
    help="New status.",
)
@pass_jar
def task_edit(
    jar: JarContext,
    task_id: int,
    name: Optional[str],
    description: Optional[str],
    tags: Optional[str],
    deadline: Optional[str],
    project: Optional[str],
    status: Optional[str],
) -> None:
    """Edit an existing task by ID."""
    svc, conn = _get_service(jar)
    try:
        t = svc.get(task_id)
        if t is None:
            _err.print(f"[red]Task #{task_id} not found.[/red]")
            sys.exit(1)

        kwargs: dict = {}
        if name is not None:
            kwargs["name"] = name
        if description is not None:
            kwargs["description"] = description or None
        if tags is not None:
            kwargs["tags"] = [x.strip() for x in tags.split(",") if x.strip()] if tags else []
        if deadline is not None:
            dl = deadline or None
            _warn_past_deadline(dl)
            kwargs["deadline"] = dl
        if project is not None:
            kwargs["project_id"] = int(project) if project.strip() else None
        if status is not None:
            kwargs["status"] = status

        if not kwargs:
            _err.print("[yellow]Nothing to update. Pass at least one option.[/yellow]")
            sys.exit(0)

        svc.update(task_id, **kwargs)
        _err.print(f"[green]Task #{task_id} updated.[/green]")
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ delete

@task.command("delete")
@click.argument("task_id", type=int)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
@pass_jar
def task_delete(jar: JarContext, task_id: int, yes: bool) -> None:
    """Permanently delete a task."""
    svc, conn = _get_service(jar)
    try:
        t = svc.get(task_id)
        if t is None:
            _err.print(f"[red]Task #{task_id} not found.[/red]")
            sys.exit(1)

        if not yes:
            click.confirm(f"Delete task #{task_id} '{t.name}'?", abort=True)

        svc.delete(task_id)
        _err.print(f"[green]Task #{task_id} deleted.[/green]")
    except click.Abort:
        _err.print("[yellow]Aborted.[/yellow]")
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ list

@task.command("list")
@click.option(
    "--status",  "-s",
    default=None,
    type=click.Choice(_STATUSES, case_sensitive=False),
    help="Filter by status.",
)
@click.option("--project",          "-p", default=None, metavar="ID|none",
              help="Filter by project ID, or 'none' for standalone tasks.")
@click.option("--tag",              "-t", default=None, multiple=True,
              help="Filter by tag (repeatable: --tag bug --tag feature).")
@click.option("--deadline-on",            default=None, metavar="YYYY-MM-DD",
              help="Tasks whose deadline is exactly this date.")
@click.option("--deadline-before",        default=None, metavar="YYYY-MM-DD",
              help="Tasks with deadline on or before this date.")
@click.option("--deadline-after",         default=None, metavar="YYYY-MM-DD",
              help="Tasks with deadline on or after this date.")
@click.option("--overdue",          "-o", is_flag=True, default=False,
              help="Show only overdue tasks (deadline past today, status not done).")
@click.option("--search",           "-q", default=None, help="Substring search in name/description.")
@click.option("--sort",                   default=None, metavar="FIELD[:asc|desc]",
              help="E.g. --sort deadline or --sort name:desc")
@click.option("--format", "fmt",          default=None,
              type=click.Choice(["table","json","csv","plain"]),
              help="Override global output format.")
@pass_jar
def task_list(
    jar: JarContext,
    status: Optional[str],
    project: Optional[str],
    tag: tuple,
    deadline_on: Optional[str],
    deadline_before: Optional[str],
    deadline_after: Optional[str],
    overdue: bool,
    search: Optional[str],
    sort: Optional[str],
    fmt: Optional[str],
) -> None:
    """List tasks with optional filters.

    Day range example:  jar task list --deadline-after 2025-06-01 --deadline-before 2025-06-30
    Specific day:       jar task list --deadline-on 2025-06-15
    Overdue:            jar task list --overdue
    """
    # Resolve --project: integer ID, 'none' → -1 (standalone), or None (no filter)
    project_id: Optional[int] = None
    if project is not None:
        project_id = -1 if project.lower() == "none" else int(project)

    f = TaskFilter(
        status=status,
        project_id=project_id,
        tags=list(tag),
        deadline_on=deadline_on,
        deadline_before=deadline_before,
        deadline_after=deadline_after,
        overdue=overdue,
        search=search,
    )
    sort_spec = parse_sort_option(sort, "task")
    svc, conn = _get_service(jar)
    try:
        tasks = svc.list_filtered(f, sort_spec)
        format_tasks(tasks, fmt=fmt or jar.fmt)
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ show

@task.command("show")
@click.argument("task_id", type=int)
@pass_jar
def task_show(jar: JarContext, task_id: int) -> None:
    """Show full detail for a task."""
    svc, conn = _get_service(jar)
    try:
        t = svc.get(task_id)
        if t is None:
            _err.print(f"[red]Task #{task_id} not found.[/red]")
            sys.exit(1)
        format_task_detail(t)
    finally:
        conn.close()
