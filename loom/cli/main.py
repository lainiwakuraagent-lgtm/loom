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
from .session_cmds import session       # noqa: E402

cli.add_command(project)
cli.add_command(task)
cli.add_command(analytics)
cli.add_command(goal)
cli.add_command(session)


# ------------------------------------------------------------------ loom commands

@cli.command("blockers")
@click.option(
    "--status",
    default=None,
    type=click.Choice(
        ["blocked_owner", "blocked_dep", "blocked_external", "all"],
        case_sensitive=False,
    ),
    help="Filter to a specific blocked status (default: all three).",
)
@click.option("--project", "project_id", type=int, default=None, help="Restrict to one project.")
@pass_jar
def cmd_blockers(jar: LoomContext, status: Optional[str], project_id: Optional[int]) -> None:
    """Show all blocked tasks, excluding those from abandoned/suspended goals."""
    from ..service import TaskService
    from ..formatters import format_blocked_digest
    conn = get_connection(jar.db_path)
    init_db(conn)
    try:
        ts = TaskService(conn)
        # "all" on the CLI means None in the service (no status filter)
        svc_status = None if (status is None or status == "all") else status
        entries = ts.get_blocked_digest(status=svc_status, project_id=project_id)
        format_blocked_digest(entries)
    finally:
        conn.close()


@cli.command("activity")
@click.option("--since", default=None, help="ISO-8601 UTC timestamp (e.g. 2026-07-30T00:00:00Z).")
@click.option(
    "--since-last-session",
    "since_last_session",
    is_flag=True,
    default=False,
    help="Resolve --since from the most recent Loom session's started_at.",
)
@click.option("--project", "project_id", type=int, default=None, help="Restrict to one project.")
@pass_jar
def cmd_activity(
    jar: LoomContext,
    since: Optional[str],
    since_last_session: bool,
    project_id: Optional[int],
) -> None:
    """Show recent task activity grouped by outcome (done / blocked / other).

    Examples:
      loom activity --since 2026-07-30T00:00:00Z
      loom activity --since-last-session
      loom activity --since-last-session --project 18
    """
    from ..service import TaskService
    from ..formatters import format_activity_digest
    conn = get_connection(jar.db_path)
    init_db(conn)
    try:
        ts = TaskService(conn)

        if since_last_session:
            resolved = ts.get_last_session_start()
            if resolved is None:
                click.echo("No Loom session recorded yet — cannot resolve --since-last-session.")
                return
            since = resolved
            since_label = f"last session ({since[:16]})"
        elif since:
            since_label = since[:16]
        else:
            click.echo(
                "Provide --since <ISO> or --since-last-session.\n"
                "Example: loom activity --since 2026-07-30T00:00:00Z",
                err=True,
            )
            raise SystemExit(1)

        entries = ts.get_activity(since=since, project_id=project_id)
        format_activity_digest(entries, since_label=since_label)
    finally:
        conn.close()


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
