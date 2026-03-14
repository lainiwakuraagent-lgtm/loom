"""JAR CLI — root entry point."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import click

from ..db import get_connection, init_db
from ..formatters import FORMATS


# ------------------------------------------------------------------ context obj

@dataclass
class JarContext:
    db_path: Optional[str]
    fmt: str


pass_jar = click.make_pass_decorator(JarContext)


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
    envvar="JAR_DB",
    help="Path to the SQLite database file. Overrides default location.",
)
@click.option(
    "--format", "fmt",
    default="table",
    type=click.Choice(FORMATS, case_sensitive=False),
    envvar="JAR_FORMAT",
    show_default=True,
    help="Output format for list and show commands.",
)
@click.version_option(package_name="project-jar")
@click.pass_context
def cli(ctx: click.Context, db: Optional[str], fmt: str) -> None:
    """JAR — project and task management."""
    ctx.ensure_object(dict)
    jar_ctx = JarContext(db_path=db, fmt=fmt)
    ctx.obj = jar_ctx

    # Initialise DB eagerly so subcommands have a ready connection.
    conn = get_connection(db)
    init_db(conn)
    conn.close()


# ------------------------------------------------------------------ sub-groups

from .project_cmds import project  # noqa: E402 — imported after cli is defined
from .task_cmds import task        # noqa: E402

cli.add_command(project)
cli.add_command(task)
