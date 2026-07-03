"""CLI commands for projects: add, edit, list, show.

Project deletion is intentionally NOT available via the CLI.
It is only accessible through the Python API (ProjectService.delete())
to restrict destructive cascade-deletes to local system / trusted code.
"""

from __future__ import annotations

import sys
from typing import Optional

import click
from rich.console import Console

from ..db import get_connection, init_db
from ..filters import ProjectFilter, SortSpec
from ..formatters import format_project_detail, format_projects
from ..service import ProjectService

from .main import JarContext, parse_sort_option, pass_jar

_err = Console(stderr=True, legacy_windows=False)


def _get_service(jar: JarContext) -> tuple[ProjectService, object]:
    conn = get_connection(jar.db_path)
    init_db(conn)
    return ProjectService(conn), conn


# ══════════════════════════════════════════════════════════════════ group

@click.group("project")
def project() -> None:
    """Manage projects."""


# ══════════════════════════════════════════════════════════════════ add

@project.command("add")
@click.option("--name",              "-n", default=None, help="Project name.")
@click.option("--start-date",        "-s", default=None, metavar="YYYY-MM-DD", help="Start date.")
@click.option("--deployment-date",   "-d", default=None, metavar="YYYY-MM-DD", help="Target deployment date.")
@click.option("--edit-description",  "-D", is_flag=True, default=False, help="Open $EDITOR to write project description.")
@pass_jar
def project_add(jar: JarContext, name: Optional[str], start_date: Optional[str], deployment_date: Optional[str], edit_description: bool) -> None:
    """Create a new project.

    Only --name is required. Use -D to open $EDITOR for a multi-line description.
    All other fields are optional and editable later with `jar project edit`.
    """
    if not name:
        name = click.prompt("Project name")

    description = None
    if edit_description:
        raw = click.edit("")
        description = raw.strip() if raw else None

    svc, conn = _get_service(jar)
    try:
        p = svc.create(name=name, description=description, start_date=start_date, deployment_date=deployment_date)
        _err.print(f"[green]Created project #{p.id}:[/green] {p.name}")
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ edit

@project.command("edit")
@click.argument("project_id", type=int)
@click.option("--name",            "-n", default=None, help="New name.")
@click.option("--start-date",      "-s", default=None, metavar="YYYY-MM-DD", help="New start date (use '' to clear).")
@click.option("--deployment-date", "-d", default=None, metavar="YYYY-MM-DD", help="New deployment date (use '' to clear).")
@click.option("--edit-description", "-D", is_flag=True, default=False, help="Open editor to rewrite description.")
@pass_jar
def project_edit(
    jar: JarContext,
    project_id: int,
    name: Optional[str],
    start_date: Optional[str],
    deployment_date: Optional[str],
    edit_description: bool,
) -> None:
    """Edit an existing project by ID."""
    svc, conn = _get_service(jar)
    try:
        p = svc.get(project_id)
        if p is None:
            _err.print(f"[red]Project #{project_id} not found.[/red]")
            sys.exit(1)

        kwargs: dict = {}
        if name is not None:
            kwargs["name"] = name
        # Map empty string → None for nullable date fields
        if start_date is not None:
            kwargs["start_date"] = start_date or None
        if deployment_date is not None:
            kwargs["deployment_date"] = deployment_date or None

        if edit_description:
            new_desc = click.edit(p.description or "")
            if new_desc is not None:
                kwargs["description"] = new_desc.strip() or None

        if not kwargs:
            _err.print("[yellow]Nothing to update. Pass at least one option.[/yellow]")
            sys.exit(0)

        svc.update(project_id, **kwargs)
        _err.print(f"[green]Project #{project_id} updated.[/green]")
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ delete (blocked)

@project.command("delete")
@click.argument("project_id", type=int)
def project_delete(project_id: int) -> None:  # noqa: ARG001
    """[UNAVAILABLE] Project deletion is restricted to the Python API."""
    Console(stderr=True, legacy_windows=False).print(
        "[red bold]Project deletion is not available via the CLI.[/red bold]\n\n"
        "This operation performs a hard cascade-delete (removes the project and ALL\n"
        "its tasks) and is intentionally restricted to trusted local system code.\n\n"
        "Use the Python API instead:\n\n"
        "  [cyan]from loom.db import get_connection, init_db[/cyan]\n"
        "  [cyan]from loom.service import ProjectService[/cyan]\n"
        f"  [cyan]conn = get_connection()[/cyan]\n"
        f"  [cyan]init_db(conn)[/cyan]\n"
        f"  [cyan]ProjectService(conn).delete({project_id})[/cyan]\n"
    )
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════ list

@project.command("list")
@click.option("--search",            "-q", default=None, help="Substring search in name/description.")
@click.option("--has-tasks",               default=None, type=click.Choice(["yes", "no"]), help="Filter by whether project has tasks.")
@click.option("--start-before",            default=None, metavar="YYYY-MM-DD")
@click.option("--start-after",             default=None, metavar="YYYY-MM-DD")
@click.option("--deployment-before",       default=None, metavar="YYYY-MM-DD")
@click.option("--deployment-after",        default=None, metavar="YYYY-MM-DD")
@click.option("--sort",                    default=None, metavar="FIELD[:asc|desc]",
              help="Sort by field. E.g. --sort name or --sort deployment_date:desc")
@click.option("--format", "fmt",           default=None, type=click.Choice(["table","json","csv","plain"]),
              help="Override global output format.")
@pass_jar
def project_list(
    jar: JarContext,
    search: Optional[str],
    has_tasks: Optional[str],
    start_before: Optional[str],
    start_after: Optional[str],
    deployment_before: Optional[str],
    deployment_after: Optional[str],
    sort: Optional[str],
    fmt: Optional[str],
) -> None:
    """List projects with optional filters."""
    has_tasks_bool = {"yes": True, "no": False}.get(has_tasks)  # type: ignore[arg-type]
    f = ProjectFilter(
        search=search,
        has_tasks=has_tasks_bool,
        start_before=start_before,
        start_after=start_after,
        deployment_before=deployment_before,
        deployment_after=deployment_after,
    )
    sort_spec = parse_sort_option(sort, "project")
    svc, conn = _get_service(jar)
    try:
        projects = svc.list_filtered(f, sort_spec)
        format_projects(projects, fmt=fmt or jar.fmt)
    except ValueError as exc:
        _err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ show

@project.command("show")
@click.argument("project_id", type=int)
@pass_jar
def project_show(jar: JarContext, project_id: int) -> None:
    """Show full detail for a project, including its tasks."""
    svc, conn = _get_service(jar)
    try:
        p = svc.get(project_id)
        if p is None:
            _err.print(f"[red]Project #{project_id} not found.[/red]")
            sys.exit(1)
        tasks = svc.tasks_for_project(project_id)
        format_project_detail(p, tasks)
    finally:
        conn.close()
