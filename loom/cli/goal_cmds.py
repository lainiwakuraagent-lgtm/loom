"""CLI commands for goals: add, edit, list, show, archive."""

from __future__ import annotations

import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from ..db import get_connection, init_db
from ..models import GoalStatus
from ..service import GoalService

from .main import JarContext, pass_jar

_console = Console(legacy_windows=False)
_err = Console(stderr=True, legacy_windows=False)


def _get_service(jar: JarContext) -> tuple[GoalService, object]:
    conn = get_connection(jar.db_path)
    init_db(conn)
    return GoalService(conn), conn


# ══════════════════════════════════════════════════════════════════ group

@click.group("goal")
def goal() -> None:
    """Manage goals."""


# ══════════════════════════════════════════════════════════════════ add

@goal.command("add")
@click.option("--name",               "-n", required=True, help="Goal name.")
@click.option("--description",        "-d", default=None, help="Short description.")
@click.option("--status",             "-s", default="desire",
              type=click.Choice([s.value for s in GoalStatus], case_sensitive=False),
              show_default=True, help="Initial status.")
@click.option("--priority",           "-p", default=0, type=int, show_default=True, help="Priority (higher = more urgent).")
@click.option("--estimated-sessions", "-e", default=None, type=int, help="Estimated sessions to complete.")
@pass_jar
def goal_add(
    jar: JarContext,
    name: str,
    description: Optional[str],
    status: str,
    priority: int,
    estimated_sessions: Optional[int],
) -> None:
    """Create a new goal."""
    svc, conn = _get_service(jar)
    try:
        g = svc.create(
            name=name,
            description=description,
            status=status,
            priority=priority,
            estimated_sessions=estimated_sessions,
        )
        _console.print(f"[green]Created goal {g.id}:[/green] {g.name} ({g.status})")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ list

@goal.command("list")
@click.option("--all",    "-a", "show_all", is_flag=True, default=False, help="Include completed and abandoned goals.")
@click.option("--status", "-s", default=None, help="Filter by status.")
@pass_jar
def goal_list(jar: JarContext, show_all: bool, status: Optional[str]) -> None:
    """List goals."""
    svc, conn = _get_service(jar)
    try:
        goals = svc.list_all()
        if not show_all and status is None:
            goals = [g for g in goals if g.status not in (GoalStatus.COMPLETED, GoalStatus.ABANDONED)]
        if status:
            goals = [g for g in goals if g.status.value == status]
        if not goals:
            _console.print("No goals found.")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("ID",     style="dim", width=4)
        table.add_column("Status", width=12)
        table.add_column("Pri",    width=4)
        table.add_column("Est",    width=5)
        table.add_column("Name")

        status_colors = {
            "active":      "green",
            "in_progress": "cyan",
            "blocked":     "red",
            "review":      "yellow",
            "desire":      "blue",
            "completed":   "dim",
            "abandoned":   "dim",
        }

        for g in goals:
            s = g.status.value if isinstance(g.status, GoalStatus) else g.status
            color = status_colors.get(s, "white")
            table.add_row(
                str(g.id),
                f"[{color}]{s}[/{color}]",
                str(g.priority),
                str(g.estimated_sessions or ""),
                g.name,
            )
        _console.print(table)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ show

@goal.command("show")
@click.argument("goal_id", type=int)
@pass_jar
def goal_show(jar: JarContext, goal_id: int) -> None:
    """Show full detail for a goal."""
    svc, conn = _get_service(jar)
    try:
        g = svc.get(goal_id)
        if g is None:
            _err.print(f"[red]Goal {goal_id} not found.[/red]")
            sys.exit(1)
        _console.print(f"[bold]Goal {g.id}:[/bold] {g.name}")
        _console.print(f"  Status:             {g.status}")
        _console.print(f"  Priority:           {g.priority}")
        _console.print(f"  Estimated sessions: {g.estimated_sessions or '—'}")
        _console.print(f"  Actual sessions:    {g.actual_sessions}")
        _console.print(f"  Started at:         {g.started_at or '—'}")
        _console.print(f"  Completed at:       {g.completed_at or '—'}")
        _console.print(f"  Created at:         {g.created_at}")
        if g.description:
            _console.print(f"\n  Description:\n{g.description}")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ edit

@goal.command("edit")
@click.argument("goal_id", type=int)
@click.option("--name",               "-n", default=None, help="New name.")
@click.option("--description",        "-d", default=None, help="New description.")
@click.option("--status",             "-s", default=None,
              type=click.Choice([s.value for s in GoalStatus], case_sensitive=False),
              help="New status.")
@click.option("--priority",           "-p", default=None, type=int, help="New priority.")
@click.option("--estimated-sessions", "-e", default=None, type=int, help="Estimated sessions.")
@pass_jar
def goal_edit(
    jar: JarContext,
    goal_id: int,
    name: Optional[str],
    description: Optional[str],
    status: Optional[str],
    priority: Optional[int],
    estimated_sessions: Optional[int],
) -> None:
    """Update a goal's fields."""
    if not any([name, description, status, priority is not None, estimated_sessions is not None]):
        _err.print("[yellow]No changes specified.[/yellow]")
        sys.exit(0)

    svc, conn = _get_service(jar)
    try:
        # Build kwargs carefully — only pass fields that were explicitly set.
        # GoalService.update uses a _MISSING sentinel to distinguish "not set" from None.
        kwargs: dict = {"goal_id": goal_id}
        if name is not None:
            kwargs["name"] = name
        if description is not None:
            kwargs["description"] = description
        if status is not None:
            kwargs["status"] = status
        if priority is not None:
            kwargs["priority"] = priority
        if estimated_sessions is not None:
            kwargs["estimated_sessions"] = estimated_sessions

        g = svc.update(**kwargs)
        if g is None:
            _err.print(f"[red]Goal {goal_id} not found.[/red]")
            sys.exit(1)
        _console.print(f"[green]Updated goal {g.id}:[/green] {g.name} ({g.status})")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ activate

@goal.command("activate")
@click.argument("goal_id", type=int)
@click.option("--pause-others", is_flag=True, default=False,
              help="Move currently active goals to 'in_progress' (paused) status.")
@pass_jar
def goal_activate(jar: JarContext, goal_id: int, pause_others: bool) -> None:
    """Mark a goal as active.

    With --pause-others, any goal currently marked 'active' is demoted to
    'in_progress' to indicate it is paused but not abandoned.
    """
    svc, conn = _get_service(jar)
    try:
        if pause_others:
            active_goals = svc.list_active()
            for ag in active_goals:
                if ag.id != goal_id:
                    svc.update(goal_id=ag.id, status="in_progress")
                    _console.print(f"[yellow]Paused goal {ag.id}:[/yellow] {ag.name}")

        g = svc.update(goal_id=goal_id, status="active")
        if g is None:
            _err.print(f"[red]Goal {goal_id} not found.[/red]")
            sys.exit(1)
        _console.print(f"[green]Activated goal {g.id}:[/green] {g.name}")
    finally:
        conn.close()
