"""LOOM CLI — root entry point."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import click

from ..db import get_connection, init_db
from ..formatters import FORMATS


# ------------------------------------------------------------------ context obj

@dataclass
class LoomContext:
    db_path: Optional[str]
    fmt: str


pass_jar = click.make_pass_decorator(LoomContext)
JarContext = LoomContext  # backward compat alias for sub-modules


# ------------------------------------------------------------------ sort helper

def parse_sort_option(value: Optional[str], entity: str):
    """Parse 'field' or 'field:asc/desc' into a SortSpec, or return None."""
    if not value:
        return None
    from ..filters import SortSpec
    parts = value.split(":", 1)
    field = parts[0].strip()
    direction = parts[1].strip().lower() if len(parts) == 2 else "asc"
    try:
        return SortSpec(field=field, direction=direction)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint=f"--sort")


# ------------------------------------------------------------------ root group

@click.group()
@click.option(
    "--db",
    default=None,
    metavar="PATH",
    envvar="LOOM_DB",
    help="Path to the SQLite database file. Overrides default location.",
)
@click.option(
    "--format", "fmt",
    default="table",
    type=click.Choice(FORMATS, case_sensitive=False),
    envvar="LOOM_FORMAT",
    show_default=True,
    help="Output format for list and show commands.",
)
@click.version_option(package_name="project-loom")
@click.pass_context
def cli(ctx: click.Context, db: Optional[str], fmt: str) -> None:
    """LOOM — project and task management."""
    ctx.ensure_object(dict)
    jar_ctx = LoomContext(db_path=db, fmt=fmt)
    ctx.obj = jar_ctx

    # Initialise DB eagerly so subcommands have a ready connection.
    conn = get_connection(db)
    init_db(conn)
    conn.close()


# ------------------------------------------------------------------ sub-groups

from .project_cmds import project        # noqa: E402 — imported after cli is defined
from .task_cmds import task              # noqa: E402
from .analytics_cmds import analytics   # noqa: E402
from .goal_cmds import goal             # noqa: E402

cli.add_command(project)
cli.add_command(task)
cli.add_command(analytics)
cli.add_command(goal)


# ------------------------------------------------------------------ loom commands

@cli.command("queue")
@click.option("--goal", "goal_id", type=int, default=None, help="Filter by goal ID.")
@click.option("--limit", default=10, show_default=True, help="Max tasks to show.")
@pass_jar
def cmd_queue(jar: LoomContext, goal_id: Optional[int], limit: int) -> None:
    """Show the ready queue — tasks ordered by urgency with all deps met."""
    from ..service import TaskService
    conn = get_connection(jar.db_path)
    init_db(conn)
    try:
        ts = TaskService(conn)
        tasks = ts.get_ready_queue(goal_id=goal_id, limit=limit)
        if not tasks:
            click.echo("Ready queue is empty.")
            return
        click.echo(f"{'#':>3}  {'ID':>4}  {'Pri':>4}  {'Urgency':>7}  {'Name'}")
        click.echo("-" * 60)
        for i, t in enumerate(tasks, 1):
            click.echo(
                f"{i:>3}  {t.id:>4}  {(t.priority or 'none'):>4}  "
                f"{t.urgency_score:>7.1f}  {t.name}"
            )
    finally:
        conn.close()


@cli.command("context")
@click.option("--goal", "goal_id", type=int, default=None, help="Active goal ID.")
@click.option("--output", "output_path", default=None, help="Write JSON to this path.")
@pass_jar
def cmd_context(jar: LoomContext, goal_id: Optional[int], output_path: Optional[str]) -> None:
    """Generate and print a context snapshot (JSON) for session injection."""
    import json as _json
    from ..context import generate_context_snapshot
    conn = get_connection(jar.db_path)
    init_db(conn)
    try:
        snap = generate_context_snapshot(conn, goal_id=goal_id, output_path=output_path)
        click.echo(_json.dumps(snap, indent=2, default=str))
        if output_path:
            click.echo(f"\n[written to {output_path}]", err=True)
    finally:
        conn.close()
