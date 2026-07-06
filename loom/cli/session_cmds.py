"""CLI commands for session tracking: start, end, list."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import click

from ..db import get_connection, init_db
from .main import JarContext, pass_jar


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ══════════════════════════════════════════════════════════════════ group

@click.group("session")
def session() -> None:
    """Track agent session start and end times."""


# ══════════════════════════════════════════════════════════════════ start

@session.command("start")
@click.option("--date",           required=True, help="Date string (YYYY-MM-DD).")
@click.option("--number",         required=True, type=int, help="Session number for this date.")
@click.option("--type",   "stype", default=None, help="Session type (planning/execution/emergency).")
@click.option("--goal",   "goal_id", default=None, type=int, help="Active goal ID.")
@pass_jar
def session_start(
    jar: JarContext,
    date: str,
    number: int,
    stype: Optional[str],
    goal_id: Optional[int],
) -> None:
    """Record a new session starting now. Prints the row ID."""
    conn = get_connection(jar.db_path)
    init_db(conn)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO loom_sessions (date, session_number, type, active_goal_id, started_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (date, number, stype, goal_id, _now_iso()),
            )
        click.echo(cur.lastrowid)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ end

@session.command("end")
@click.option("--id",     "row_id",    required=True, type=int, help="Session row ID (from 'session start').")
@click.option("--context-pct",         default=None, type=float, help="Context window usage % at exit.")
@click.option("--exit-reason",         default=None, help="Why the session ended.")
@click.option("--handoff",             default=None, help="One-line handoff note.")
@pass_jar
def session_end(
    jar: JarContext,
    row_id: int,
    context_pct: Optional[float],
    exit_reason: Optional[str],
    handoff: Optional[str],
) -> None:
    """Close a session: record end time and duration."""
    conn = get_connection(jar.db_path)
    init_db(conn)
    try:
        row = conn.execute(
            "SELECT started_at FROM loom_sessions WHERE id = ?", (row_id,)
        ).fetchone()
        if not row:
            click.echo(f"ERROR: no session with id={row_id}", err=True)
            raise SystemExit(1)

        ended_at = _now_iso()
        started_at = row["started_at"]
        duration_minutes: Optional[int] = None
        if started_at:
            try:
                start_dt = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                end_dt = datetime.strptime(ended_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
            except ValueError:
                pass

        with conn:
            conn.execute(
                """
                UPDATE loom_sessions
                SET ended_at = ?, duration_minutes = ?,
                    context_pct_at_exit = ?, exit_reason = ?, handoff_note = ?
                WHERE id = ?
                """,
                (ended_at, duration_minutes, context_pct, exit_reason, handoff, row_id),
            )
        click.echo(f"Session {row_id} closed. Duration: {duration_minutes} min.")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════ list

@session.command("list")
@click.option("--limit", default=10, show_default=True, help="Max sessions to show.")
@pass_jar
def session_list(jar: JarContext, limit: int) -> None:
    """List recent sessions."""
    conn = get_connection(jar.db_path)
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT s.id, s.date, s.session_number, s.type,
                   s.active_goal_id, g.name AS goal_name,
                   s.started_at, s.duration_minutes, s.exit_reason
            FROM loom_sessions s
            LEFT JOIN goals g ON g.id = s.active_goal_id
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        if not rows:
            click.echo("No sessions recorded yet.")
            return
        click.echo(f"{'ID':>4}  {'Date':>10}  {'#':>3}  {'Type':>10}  {'Goal':>4}  {'Min':>4}  {'Exit reason'}")
        click.echo("-" * 72)
        for r in rows:
            click.echo(
                f"{r['id']:>4}  {r['date']:>10}  {r['session_number']:>3}  "
                f"{(r['type'] or ''):>10}  {(str(r['active_goal_id']) if r['active_goal_id'] else ''):>4}  "
                f"{(str(r['duration_minutes']) if r['duration_minutes'] is not None else '?'):>4}  "
                f"{(r['exit_reason'] or '')}"
            )
    finally:
        conn.close()
