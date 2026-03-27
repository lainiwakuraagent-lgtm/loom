"""CLI commands for analytics: summary, deadline, velocity, capacity, behavior, realism."""

from __future__ import annotations

import json
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich import box

from ..analytics import AnalyticsService
from ..db import get_connection, init_db

from .main import JarContext, pass_jar

_out = Console(legacy_windows=False)
_err = Console(stderr=True, legacy_windows=False)

# Color thresholds for rates (miss rate, push rate, abandonment, etc.)
_RATE_STYLE = "green"
_RATE_WARN  = "yellow"
_RATE_BAD   = "red"

def _rate_style(rate: Optional[float]) -> str:
    if rate is None:
        return "dim"
    if rate >= 0.5:
        return _RATE_BAD
    if rate >= 0.25:
        return _RATE_WARN
    return _RATE_STYLE


def _pct(rate: Optional[float]) -> str:
    if rate is None:
        return "—"
    return f"{rate * 100:.1f}%"


def _fmt_rate(rate: Optional[float]) -> str:
    s = _rate_style(rate)
    return f"[{s}]{_pct(rate)}[/{s}]"


def _get_service(jar: JarContext) -> tuple[AnalyticsService, object]:
    conn = get_connection(jar.db_path)
    init_db(conn)
    return AnalyticsService(conn), conn


# ══════════════════════════════════════════════════════════════════ group

@click.group("analytics")
def analytics() -> None:
    """Computed metrics from the task lifecycle event log (read-only)."""


# ══════════════════════════════════════════════════════════════════ summary

@analytics.command("summary")
@click.option("--since", default=None, metavar="YYYY-MM-DD", help="Scope to tasks created on or after this date.")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"], case_sensitive=False), show_default=True)
@pass_jar
def summary_cmd(jar: JarContext, since: Optional[str], fmt: str) -> None:
    """Health dashboard — key numbers from all metrics."""
    svc, conn = _get_service(jar)
    data = svc.summary(since=since)
    conn.close()

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    table = Table(title="Analytics Summary", box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    def _row(label: str, val) -> None:
        table.add_row(label, str(val) if val is not None else "[dim]—[/dim]")

    def _rate_row(label: str, rate: Optional[float]) -> None:
        table.add_row(label, _fmt_rate(rate))

    _rate_row("Miss rate (overall)",       data["miss_rate_overall"])
    _row("Active overdue tasks",           data["active_overdue_count"])
    _rate_row("Deadline push rate",        data["deadline_push_rate"])
    _row("Tasks completed",                data["tasks_completed"])
    _row("Avg time to done (days)",        data.get("avg_time_to_done_days"))
    _row("Median time to done (days)",     data.get("median_time_to_done_days"))
    _row("Velocity (last 4w avg/week)",    data["completion_velocity_4w_avg"])
    _row("Rotting tasks (>14d in todo)",   data["rotting_tasks_count"])
    _row("Status reversals",               data["status_reversals"])
    _row("Avg recovery lag (days)",        data["avg_recovery_lag_days"])
    _rate_row("Abandonment rate",          data["abandonment_rate"])
    _row("Deadline realism score (DRS)",   _pct(data["deadline_realism_score"]))

    if data.get("most_missed_tag"):
        table.add_row(
            "Most missed tag",
            f"[red]{data['most_missed_tag']}[/red] ({_pct(data['most_missed_tag_rate'])})",
        )
    if data.get("most_procrastinated_tag"):
        _row(
            "Most procrastinated tag",
            f"{data['most_procrastinated_tag']} ({data['most_procrastinated_avg_days']}d avg to start)",
        )

    _out.print(table)


# ══════════════════════════════════════════════════════════════════ deadline

@analytics.command("deadline")
@click.option("--since",     default=None, metavar="YYYY-MM-DD")
@click.option("--project",   "-p", "project_id", default=None, type=int, metavar="ID")
@click.option("--tag",       "-t", default=None, metavar="TAG")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"], case_sensitive=False), show_default=True)
@pass_jar
def deadline_cmd(jar: JarContext, since: Optional[str], project_id: Optional[int], tag: Optional[str], fmt: str) -> None:
    """Deadline push rate and miss rate by tag / project."""
    svc, conn = _get_service(jar)
    data = svc.deadline_health(since=since, project_id=project_id, tag=tag)
    conn.close()

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    # ── push rate ────────────────────────────────────────────────
    pushes = data["deadline_pushes"]
    pt = Table(title="Deadline Pushes", box=box.SIMPLE_HEAD, pad_edge=False)
    pt.add_column("Metric")
    pt.add_column("Value", justify="right")
    pt.add_row("Total deadline changes", str(pushes["total_deadline_changes"]))
    pt.add_row("Push count (later)",     str(pushes["push_count"]))
    pt.add_row("Pull count (earlier)",   str(pushes["pull_count"]))
    pt.add_row("Push rate",              _fmt_rate(pushes["push_rate"]))
    pt.add_row("Avg push days",          str(pushes["avg_push_days"]))
    dist = pushes["push_count_distribution"]
    pt.add_row("Tasks pushed 1x / 2x / 3+x",
               f"{dist['1']} / {dist['2']} / {dist['3+']}")
    _out.print(pt)

    if pushes["by_tag"]:
        bt = Table(title="Push Rate by Tag", box=box.SIMPLE_HEAD, pad_edge=False)
        bt.add_column("Tag")
        bt.add_column("Changes", justify="right")
        bt.add_column("Push rate", justify="right")
        bt.add_column("Avg push days", justify="right")
        for t, d in sorted(pushes["by_tag"].items()):
            bt.add_row(t, str(d["changes"]), _fmt_rate(d["push_rate"]), str(d["avg_push_days"]))
        _out.print(bt)

    # ── miss rate ────────────────────────────────────────────────
    miss = data["miss_rate"]
    mt = Table(title="Miss Rate", box=box.SIMPLE_HEAD, pad_edge=False)
    mt.add_column("Metric")
    mt.add_column("Value", justify="right")
    mt.add_row("Overall miss rate",     _fmt_rate(miss["overall_miss_rate"]))
    mt.add_row("Active overdue tasks",  str(miss["active_overdue_count"]))
    _out.print(mt)

    if miss["by_tag"]:
        mbt = Table(title="Miss Rate by Tag", box=box.SIMPLE_HEAD, pad_edge=False)
        mbt.add_column("Tag")
        mbt.add_column("Total", justify="right")
        mbt.add_column("Missed", justify="right")
        mbt.add_column("Miss rate", justify="right")
        for t, d in sorted(miss["by_tag"].items(), key=lambda x: -x[1]["miss_rate"]):
            mbt.add_row(t, str(d["total"]), str(d["missed"]), _fmt_rate(d["miss_rate"]))
        _out.print(mbt)


# ══════════════════════════════════════════════════════════════════ velocity

@analytics.command("velocity")
@click.option("--since",     default=None, metavar="YYYY-MM-DD")
@click.option("--project",   "-p", "project_id", default=None, type=int, metavar="ID")
@click.option("--tag",       "-t", default=None, metavar="TAG")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"], case_sensitive=False), show_default=True)
@pass_jar
def velocity_cmd(jar: JarContext, since: Optional[str], project_id: Optional[int], tag: Optional[str], fmt: str) -> None:
    """Time-to-done distribution and completion velocity per tag."""
    svc, conn = _get_service(jar)
    data = svc.velocity(since=since, project_id=project_id, tag=tag)
    conn.close()

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    ttd = data["time_to_done"]
    vt = Table(title="Time to Done", box=box.SIMPLE_HEAD, pad_edge=False)
    vt.add_column("Metric")
    vt.add_column("Value", justify="right")
    vt.add_row("Tasks completed", str(ttd["count"]))

    if ttd.get("insufficient_data"):
        vt.add_row("Status", f"[dim]{ttd.get('reason', 'Insufficient data')}[/dim]")
    else:
        s = ttd["stats"]
        vt.add_row("Min days",    str(s["min_days"]))
        vt.add_row("Max days",    str(s["max_days"]))
        vt.add_row("Mean days",   str(s["mean_days"]))
        vt.add_row("Median days", str(s["median_days"]))
        vt.add_row("P25 days",    str(s["p25_days"]))
        vt.add_row("P75 days",    str(s["p75_days"]))
        vt.add_row("Finished before deadline", str(ttd["before_deadline_count"]))
        vt.add_row("Finished on deadline",     str(ttd["on_deadline_count"]))
        vt.add_row("Finished after deadline",  str(ttd["after_deadline_count"]))
    _out.print(vt)

    vel = data["completion_velocity"]
    ov = vel["overall"]
    cvt = Table(title="Completion Velocity", box=box.SIMPLE_HEAD, pad_edge=False)
    cvt.add_column("Scope")
    cvt.add_column("Total", justify="right")
    cvt.add_column("Avg/week", justify="right")
    cvt.add_column("Recent 4w avg", justify="right")
    cvt.add_row("Overall", str(ov["total_completed"]), str(ov["avg_per_week"]), str(ov["recent_4w_avg"]))
    for t, d in sorted(vel["by_tag"].items()):
        cvt.add_row(f"  {t}", str(d["total_completed"]), str(d["avg_per_week"]), str(d["recent_4w_avg"]))
    _out.print(cvt)


# ══════════════════════════════════════════════════════════════════ capacity

@analytics.command("capacity")
@click.option("--since",  default=None, metavar="YYYY-MM-DD")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"], case_sensitive=False), show_default=True)
@pass_jar
def capacity_cmd(jar: JarContext, since: Optional[str], fmt: str) -> None:
    """Deadline clustering and context switching load."""
    svc, conn = _get_service(jar)
    data = svc.capacity(since=since)
    conn.close()

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    dc = data["deadline_clustering"]
    dct = Table(title="Deadline Clustering", box=box.SIMPLE_HEAD, pad_edge=False)
    dct.add_column("Metric")
    dct.add_column("Value", justify="right")
    dct.add_row("Avg tasks/week",           str(dc["avg_per_week"]))
    dct.add_row("Overload threshold",        str(dc["overload_threshold"]))
    dct.add_row("Overloaded weeks",          str(len(dc["overloaded_weeks"])))
    dct.add_row("High-load miss rate",       _fmt_rate(dc["high_load_miss_rate"]))
    dct.add_row("Normal-load miss rate",     _fmt_rate(dc["normal_miss_rate"]))
    _out.print(dct)

    if dc["tasks_per_week"]:
        wt = Table(title="Tasks per Week", box=box.SIMPLE_HEAD, pad_edge=False)
        wt.add_column("Week")
        wt.add_column("Count", justify="right")
        wt.add_column("Done",  justify="right")
        wt.add_column("Missed", justify="right")
        overload_weeks = {w["week"] for w in dc["overloaded_weeks"]}
        for w in dc["tasks_per_week"]:
            label = f"[bold]{w['week']}[/bold]" if w["week"] in overload_weeks else w["week"]
            wt.add_row(label, str(w["count"]), str(w["done"]), str(w["missed"]))
        _out.print(wt)

    cs = data["context_switching"]
    cst = Table(title="Context Switching", box=box.SIMPLE_HEAD, pad_edge=False)
    cst.add_column("Metric")
    cst.add_column("Value", justify="right")
    cst.add_row("Avg concurrent tags/week",     str(cs["avg_concurrent_tags_per_week"]))
    cst.add_row("Avg concurrent projects/week", str(cs["avg_concurrent_projects_per_week"]))
    cst.add_row("High-switch miss rate",         _fmt_rate(cs["miss_rate_high_switch"]))
    cst.add_row("Low-switch miss rate",          _fmt_rate(cs["miss_rate_low_switch"]))
    _out.print(cst)


# ══════════════════════════════════════════════════════════════════ behavior

@analytics.command("behavior")
@click.option("--since",     default=None, metavar="YYYY-MM-DD")
@click.option("--project",   "-p", "project_id", default=None, type=int, metavar="ID")
@click.option("--tag",       "-t", default=None, metavar="TAG")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"], case_sensitive=False), show_default=True)
@pass_jar
def behavior_cmd(jar: JarContext, since: Optional[str], project_id: Optional[int], tag: Optional[str], fmt: str) -> None:
    """Task rot, recovery lag, and status reversals."""
    svc, conn = _get_service(jar)
    data = svc.behavior(since=since, project_id=project_id, tag=tag)
    conn.close()

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    rot = data["task_rot"]
    rt = Table(title="Task Rot", box=box.SIMPLE_HEAD, pad_edge=False)
    rt.add_column("Metric")
    rt.add_column("Value", justify="right")
    rt.add_row("Avg days to start",    str(rot["avg_days_to_start"]))
    rt.add_row("Median days to start", str(rot["median_days_to_start"]))
    rt.add_row(f"Rotting tasks (>{rot['rot_threshold_days']}d in todo)",
               f"[{'red' if rot['current_rotting_count'] > 0 else 'green'}]{rot['current_rotting_count']}[/{'red' if rot['current_rotting_count'] > 0 else 'green'}]")
    _out.print(rt)

    if rot["rotting_tasks"]:
        rtt = Table(title="Rotting Tasks (oldest first)", box=box.SIMPLE_HEAD, pad_edge=False)
        rtt.add_column("ID",   style="dim", no_wrap=True)
        rtt.add_column("Name")
        rtt.add_column("Age (days)", justify="right")
        rtt.add_column("Tags", style="dim")
        for item in rot["rotting_tasks"]:
            rtt.add_row(
                str(item["task_id"]),
                item["name"],
                f"[red]{item['age_days']}[/red]",
                ", ".join(item["tags"]) if item["tags"] else "",
            )
        _out.print(rtt)

    lag = data["recovery_lag"]
    lt = Table(title="Recovery Lag (overdue → done)", box=box.SIMPLE_HEAD, pad_edge=False)
    lt.add_column("Metric")
    lt.add_column("Value", justify="right")
    lt.add_row("Missed & recovered",  str(lag["missed_and_recovered_count"]))
    lt.add_row("Avg lag (days)",       str(lag["avg_lag_days"]))
    lt.add_row("Median lag (days)",    str(lag["median_lag_days"]))
    dist = lag["distribution"]
    lt.add_row("1-7d / 8-14d / 15-30d / 31+d",
               f"{dist['1-7d']} / {dist['8-14d']} / {dist['15-30d']} / {dist['31+d']}")
    if lag["worst"]:
        w = lag["worst"]
        lt.add_row("Worst case", f"#{w['task_id']} {w['name']!r} — {w['lag_days']}d")
    _out.print(lt)

    rev = data["status_reversals"]
    revt = Table(title="Status Reversals", box=box.SIMPLE_HEAD, pad_edge=False)
    revt.add_column("Metric")
    revt.add_column("Value", justify="right")
    revt.add_row("Total reversals",            str(rev["total_reversals"]))
    revt.add_row("Tasks with reversals",        str(rev["tasks_with_reversals"]))
    revt.add_row("Reversal rate per task",      str(rev["reversal_rate_per_task"]))
    by_type = rev["by_type"]
    revt.add_row("Done → in_progress",          str(by_type.get("done_to_in_progress", 0)))
    revt.add_row("Done → todo",                 str(by_type.get("done_to_todo", 0)))
    revt.add_row("In_progress → todo",          str(by_type.get("in_progress_to_todo", 0)))
    _out.print(revt)


# ══════════════════════════════════════════════════════════════════ realism

@analytics.command("realism")
@click.option("--since",  default=None, metavar="YYYY-MM-DD")
@click.option("--tag",    "-t", default=None, metavar="TAG")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"], case_sensitive=False), show_default=True)
@pass_jar
def realism_cmd(jar: JarContext, since: Optional[str], tag: Optional[str], fmt: str) -> None:
    """Deadline Realism Score, deadline horizon, and abandonment rate."""
    svc, conn = _get_service(jar)
    data = svc.realism(since=since, tag=tag)
    conn.close()

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    drs = data["deadline_realism"]
    drst = Table(title="Deadline Realism Score (DRS)", box=box.SIMPLE_HEAD, pad_edge=False)
    drst.add_column("Metric")
    drst.add_column("Value", justify="right")
    if drs.get("insufficient_data"):
        drst.add_row("Status", f"[dim]{drs.get('reason', 'Insufficient data')}[/dim]")
    else:
        overall = drs["overall_drs"]
        s = _rate_style(1.0 - overall)  # higher DRS = better; invert for color
        drst.add_row("Overall DRS", f"[{s}]{_pct(overall)}[/{s}]")

    if drs.get("by_tag"):
        drst.add_section()
        for t, d in sorted(drs["by_tag"].items(), key=lambda x: -x[1]["drs"]):
            s = _rate_style(1.0 - d["drs"])
            drst.add_row(f"  {t}", f"[{s}]{_pct(d['drs'])}[/{s}] ({d['on_time']}/{d['total']})")
    _out.print(drst)

    hor = data["deadline_horizon"]
    hort = Table(title="Deadline Horizon at Creation", box=box.SIMPLE_HEAD, pad_edge=False)
    hort.add_column("Metric")
    hort.add_column("Value", justify="right")
    hort.add_row("Avg days ahead",    str(hor["avg_days_ahead"]))
    hort.add_row("Median days ahead", str(hor["median_days_ahead"]))
    hort.add_row("Reactive (<3d)",    str(hor["reactive_count"]))
    hort.add_row("Planned (>14d)",    str(hor["planned_count"]))
    _out.print(hort)

    ab = data["abandonment"]
    abt = Table(title="Abandonment", box=box.SIMPLE_HEAD, pad_edge=False)
    abt.add_column("Metric")
    abt.add_column("Value", justify="right")
    abt.add_row("Total created",      str(ab["total_created"]))
    abt.add_row("Abandoned",          str(ab["abandoned_count"]))
    abt.add_row("Abandonment rate",   _fmt_rate(ab["abandonment_rate"]))
    if ab.get("by_tag"):
        abt.add_section()
        for t, d in sorted(ab["by_tag"].items(), key=lambda x: -x[1]["rate"]):
            abt.add_row(f"  {t}", f"{_fmt_rate(d['rate'])} ({d['abandoned']}/{d['created']})")
    _out.print(abt)
