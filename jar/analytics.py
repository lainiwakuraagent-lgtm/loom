"""Analytics module — read-only computed metrics from the task lifecycle event log.

All methods return plain dicts containing calculated evaluations only.
No data is written; no CRUD operations are exposed.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import date
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────── helpers


def _parse_snapshot(snapshot_json: Optional[str]) -> dict:
    """Parse a task_snapshot JSON string; return empty dict on failure."""
    if not snapshot_json:
        return {}
    try:
        return json.loads(snapshot_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def _date_diff_days(d1: str, d2: str) -> float:
    """Return (d2 − d1) in days. Accepts ISO date or ISO datetime strings."""
    a = date.fromisoformat(d1[:10])
    b = date.fromisoformat(d2[:10])
    return (b - a).days


def _iso_week(dt_str: str) -> str:
    """Convert an ISO-8601 datetime/date string to a 'YYYY-WNN' week label."""
    return date.fromisoformat(dt_str[:10]).strftime("%Y-W%W")


def _safe_mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def _safe_median(values: list) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0


def _safe_percentile(values: list, p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    idx = p / 100.0 * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _extract_tags(snap: dict) -> list[str]:
    """Extract the tags list from a parsed task snapshot."""
    tags = snap.get("tags") or []
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    return [str(t) for t in tags if t]


# ──────────────────────────────────────────────────────────────── service


class AnalyticsService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ─────────────────────────────────────────── private helpers

    def _all_created_snapshots(self) -> dict[int, dict]:
        """Return {task_id: parsed_snapshot} for every CREATED event."""
        rows = self._conn.execute(
            "SELECT task_id, task_snapshot FROM task_events WHERE event_type = 'created'"
        ).fetchall()
        return {r["task_id"]: _parse_snapshot(r["task_snapshot"]) for r in rows}

    def _created_at_map(self) -> dict[int, str]:
        """Return {task_id: changed_at} for every CREATED event."""
        rows = self._conn.execute(
            "SELECT task_id, changed_at FROM task_events WHERE event_type = 'created'"
        ).fetchall()
        return {r["task_id"]: r["changed_at"] for r in rows}

    def _scoped_task_ids(
        self,
        since: Optional[str],
        project_id: Optional[int],
        tag: Optional[str],
    ) -> Optional[frozenset]:
        """
        Return a frozenset of task_ids matching the given filters, or None
        if no filters are applied (meaning all tasks are in scope).
        """
        if not (since or project_id is not None or tag):
            return None

        rows = self._conn.execute(
            "SELECT task_id, changed_at, task_snapshot FROM task_events WHERE event_type = 'created'"
        ).fetchall()

        result: set[int] = set()
        for row in rows:
            snap = _parse_snapshot(row["task_snapshot"])

            if since and row["changed_at"] < since:
                continue
            if project_id is not None and snap.get("project_id") != project_id:
                continue
            if tag:
                if tag not in _extract_tags(snap):
                    continue
            result.add(row["task_id"])

        return frozenset(result)

    def _done_events(self, scope: Optional[frozenset]) -> list:
        """Return the last 'done' event per task (max changed_at)."""
        rows = self._conn.execute(
            """
            SELECT task_id, MAX(changed_at) AS done_at, task_snapshot
            FROM task_events
            WHERE event_type = 'updated'
              AND field_name = 'status'
              AND new_value = 'done'
            GROUP BY task_id
            """
        ).fetchall()
        if scope is not None:
            rows = [r for r in rows if r["task_id"] in scope]
        return rows

    def _final_deadlines(self) -> dict[int, str]:
        """
        Return {task_id: final_deadline_string} using the latest deadline-change
        event per task; falls back to the CREATED snapshot value if no change
        event exists.
        """
        # Latest deadline update per task
        override: dict[int, str] = {}
        rows = self._conn.execute(
            """
            SELECT task_id, new_value
            FROM task_events
            WHERE field_name = 'deadline'
              AND new_value IS NOT NULL
            GROUP BY task_id
            HAVING changed_at = MAX(changed_at)
            """
        ).fetchall()
        for r in rows:
            if r["new_value"]:
                override[r["task_id"]] = r["new_value"]
        return override

    # ─────────────────────────────────────────── public metrics

    def deadline_health(
        self,
        since: Optional[str] = None,
        project_id: Optional[int] = None,
        tag: Optional[str] = None,
    ) -> dict:
        """Deadline push rate and miss rate by tag/project."""
        scope = self._scoped_task_ids(since, project_id, tag)
        created_snaps = self._all_created_snapshots()

        # ── deadline pushes ──────────────────────────────────────
        dl_rows = self._conn.execute(
            """
            SELECT task_id, old_value, new_value
            FROM task_events
            WHERE field_name = 'deadline'
              AND old_value IS NOT NULL
              AND new_value IS NOT NULL
            """
        ).fetchall()
        if scope is not None:
            dl_rows = [r for r in dl_rows if r["task_id"] in scope]

        push_days_per_task: dict[int, list[float]] = defaultdict(list)
        pull_days: list[float] = []
        # accumulators keyed by tag / project
        tag_acc: dict[str, dict] = defaultdict(lambda: {"changes": 0, "push_days": []})
        proj_acc: dict[str, dict] = defaultdict(lambda: {"changes": 0, "push_days": []})

        for r in dl_rows:
            tid = r["task_id"]
            old_d, new_d = r["old_value"], r["new_value"]
            if len(old_d) < 10 or len(new_d) < 10:
                continue

            days = _date_diff_days(old_d, new_d)
            snap = created_snaps.get(tid, {})
            task_tags = _extract_tags(snap)
            pid_str = str(snap.get("project_id") or "")

            for t in task_tags:
                tag_acc[t]["changes"] += 1
            if pid_str:
                proj_acc[pid_str]["changes"] += 1

            if days > 0:
                push_days_per_task[tid].append(days)
                for t in task_tags:
                    tag_acc[t]["push_days"].append(days)
                if pid_str:
                    proj_acc[pid_str]["push_days"].append(days)
            elif days < 0:
                pull_days.append(-days)

        all_push_days = [d for v in push_days_per_task.values() for d in v]
        push_count = len(all_push_days)
        total_changes = len(dl_rows)

        dist: dict[str, int] = {"1": 0, "2": 0, "3+": 0}
        for cnt in (len(v) for v in push_days_per_task.values()):
            if cnt == 1:
                dist["1"] += 1
            elif cnt == 2:
                dist["2"] += 1
            else:
                dist["3+"] += 1

        # ── miss rate ────────────────────────────────────────────
        done_rows = self._done_events(scope)
        today = date.today().isoformat()
        total_with_dl = missed = 0
        miss_tag: dict[str, dict] = defaultdict(lambda: {"total": 0, "missed": 0})
        miss_proj: dict[str, dict] = defaultdict(lambda: {"total": 0, "missed": 0})

        for r in done_rows:
            snap = _parse_snapshot(r["task_snapshot"])
            deadline = snap.get("deadline")
            if not deadline:
                continue
            total_with_dl += 1
            is_miss = r["done_at"][:10] > deadline
            if is_miss:
                missed += 1

            csn = created_snaps.get(r["task_id"], snap)
            for t in _extract_tags(csn):
                miss_tag[t]["total"] += 1
                if is_miss:
                    miss_tag[t]["missed"] += 1
            pid_str = str(csn.get("project_id") or "")
            if pid_str:
                miss_proj[pid_str]["total"] += 1
                if is_miss:
                    miss_proj[pid_str]["missed"] += 1

        overdue_count: int = self._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status != 'done' AND deadline IS NOT NULL AND deadline < ?",
            (today,),
        ).fetchone()[0]

        return {
            "deadline_pushes": {
                "total_deadline_changes": total_changes,
                "push_count": push_count,
                "pull_count": len(pull_days),
                "push_rate": round(push_count / total_changes, 3) if total_changes else 0.0,
                "avg_push_days": round(_safe_mean(all_push_days), 1),
                "push_count_distribution": dist,
                "by_tag": {
                    t: {
                        "changes": d["changes"],
                        "push_rate": round(len(d["push_days"]) / d["changes"], 3)
                        if d["changes"] else 0.0,
                        "avg_push_days": round(_safe_mean(d["push_days"]), 1),
                    }
                    for t, d in tag_acc.items()
                },
                "by_project": {
                    p: {
                        "changes": d["changes"],
                        "push_rate": round(len(d["push_days"]) / d["changes"], 3)
                        if d["changes"] else 0.0,
                        "avg_push_days": round(_safe_mean(d["push_days"]), 1),
                    }
                    for p, d in proj_acc.items()
                },
            },
            "miss_rate": {
                "overall_miss_rate": round(missed / total_with_dl, 3) if total_with_dl else 0.0,
                "active_overdue_count": overdue_count,
                "by_tag": {
                    t: {**d, "miss_rate": round(d["missed"] / d["total"], 3)}
                    for t, d in miss_tag.items()
                },
                "by_project": {
                    p: {**d, "miss_rate": round(d["missed"] / d["total"], 3)}
                    for p, d in miss_proj.items()
                },
            },
        }

    def velocity(
        self,
        since: Optional[str] = None,
        project_id: Optional[int] = None,
        tag: Optional[str] = None,
    ) -> dict:
        """Time-to-done distribution and completion velocity per tag."""
        scope = self._scoped_task_ids(since, project_id, tag)
        created_snaps = self._all_created_snapshots()
        cat_map = self._created_at_map()
        done_rows = self._done_events(scope)

        durations: list[float] = []
        before_dl = on_dl = after_dl = 0
        by_tag_dur: dict[str, list[float]] = defaultdict(list)
        vel_weeks: dict[str, list[str]] = defaultdict(list)
        all_done_weeks: list[str] = []

        for r in done_rows:
            tid = r["task_id"]
            done_at = r["done_at"]
            created_at = cat_map.get(tid)
            if not created_at:
                continue

            dur = _date_diff_days(created_at, done_at)
            durations.append(dur)

            snap = _parse_snapshot(r["task_snapshot"])
            deadline = snap.get("deadline")
            if deadline:
                done_date = done_at[:10]
                if done_date < deadline:
                    before_dl += 1
                elif done_date == deadline:
                    on_dl += 1
                else:
                    after_dl += 1

            csn = created_snaps.get(tid, snap)
            task_tags = _extract_tags(csn)
            week = _iso_week(done_at)
            all_done_weeks.append(week)
            for t in task_tags:
                by_tag_dur[t].append(dur)
                vel_weeks[t].append(week)

        def _vel_stats(weeks: list[str], total: int) -> dict:
            if not weeks:
                return {"total_completed": total, "avg_per_week": 0.0, "recent_4w_avg": 0.0}
            from collections import Counter
            wc = Counter(weeks)
            avg = round(total / len(wc), 2)
            recent = sorted(wc)[-4:]
            r_avg = round(sum(wc[w] for w in recent) / len(recent), 2) if recent else 0.0
            return {"total_completed": total, "avg_per_week": avg, "recent_4w_avg": r_avg}

        ttd: dict[str, Any] = {
            "count": len(durations),
            "before_deadline_count": before_dl,
            "on_deadline_count": on_dl,
            "after_deadline_count": after_dl,
            "by_tag": {
                t: {
                    "count": len(d),
                    "mean_days": round(_safe_mean(d), 1),
                    "median_days": round(_safe_median(d), 1),
                }
                for t, d in by_tag_dur.items()
            },
        }
        if durations:
            ttd["stats"] = {
                "min_days": round(min(durations), 1),
                "max_days": round(max(durations), 1),
                "mean_days": round(_safe_mean(durations), 1),
                "median_days": round(_safe_median(durations), 1),
                "p25_days": round(_safe_percentile(durations, 25), 1),
                "p75_days": round(_safe_percentile(durations, 75), 1),
            }
        else:
            ttd["insufficient_data"] = True
            ttd["reason"] = "No completed tasks found"

        return {
            "time_to_done": ttd,
            "completion_velocity": {
                "overall": _vel_stats(all_done_weeks, len(durations)),
                "by_tag": {t: _vel_stats(w, len(w)) for t, w in vel_weeks.items()},
            },
        }

    def capacity(self, since: Optional[str] = None) -> dict:
        """Deadline clustering and context switching load."""
        scope = self._scoped_task_ids(since, None, None)
        created_snaps = self._all_created_snapshots()
        final_deadlines = self._final_deadlines()
        done_rows = self._done_events(scope)
        done_map = {r["task_id"]: r["done_at"][:10] for r in done_rows}

        # ── deadline clustering ──────────────────────────────────
        week_data: dict[str, dict] = defaultdict(lambda: {"count": 0, "done": 0, "missed": 0})

        for tid, snap in created_snaps.items():
            if scope is not None and tid not in scope:
                continue
            deadline = final_deadlines.get(tid) or snap.get("deadline")
            if not deadline or len(deadline) < 10:
                continue

            week = _iso_week(deadline)
            week_data[week]["count"] += 1

            if tid in done_map:
                done_date = done_map[tid]
                if done_date > deadline:
                    week_data[week]["missed"] += 1
                else:
                    week_data[week]["done"] += 1

        weeks_sorted = sorted(
            [{"week": w, **d} for w, d in week_data.items()],
            key=lambda x: x["week"],
        )
        counts = [w["count"] for w in weeks_sorted]
        avg_per_week = round(_safe_mean(counts), 1)
        variance = _safe_mean([(c - avg_per_week) ** 2 for c in counts])
        std_dev = round(math.sqrt(variance), 1) if counts else 0.0
        threshold = int(avg_per_week + std_dev) + 1

        overloaded = [w for w in weeks_sorted if w["count"] >= threshold]
        normal = [w for w in weeks_sorted if w["count"] < threshold]

        def _miss_stats(wlist: list[dict]) -> float:
            d = sum(w["done"] for w in wlist)
            m = sum(w["missed"] for w in wlist)
            return round(m / (d + m), 3) if (d + m) else 0.0

        # ── context switching ────────────────────────────────────
        status_rows = self._conn.execute(
            """
            SELECT task_id, new_value AS status, changed_at
            FROM task_events
            WHERE event_type = 'updated' AND field_name = 'status'
            """
        ).fetchall()
        if scope is not None:
            status_rows = [r for r in status_rows if r["task_id"] in scope]

        week_tags: dict[str, set] = defaultdict(set)
        week_projs: dict[str, set] = defaultdict(set)

        for r in status_rows:
            if r["status"] != "in_progress":
                continue
            week = _iso_week(r["changed_at"])
            snap = created_snaps.get(r["task_id"], {})
            for t in _extract_tags(snap):
                week_tags[week].add(t)
            pid = snap.get("project_id")
            if pid:
                week_projs[week].add(pid)

        tc_per_week = [len(t) for t in week_tags.values()]
        pc_per_week = [len(p) for p in week_projs.values()]
        avg_tags = round(_safe_mean(tc_per_week), 1)
        avg_projs = round(_safe_mean(pc_per_week), 1)
        hs_thresh = avg_tags + 1 if avg_tags >= 1 else 2

        high_switch = sorted(
            [{"week": w, "tag_count": len(t)} for w, t in week_tags.items() if len(t) >= hs_thresh],
            key=lambda x: x["week"],
        )
        hs_week_set = {w["week"] for w in high_switch}

        hs_miss = hs_total = ls_miss = ls_total = 0
        for tid, snap in created_snaps.items():
            if scope is not None and tid not in scope:
                continue
            deadline = final_deadlines.get(tid) or snap.get("deadline")
            if not deadline or tid not in done_map:
                continue
            is_miss = done_map[tid] > deadline
            dl_week = _iso_week(deadline)
            if dl_week in hs_week_set:
                hs_total += 1
                if is_miss:
                    hs_miss += 1
            else:
                ls_total += 1
                if is_miss:
                    ls_miss += 1

        return {
            "deadline_clustering": {
                "tasks_per_week": weeks_sorted,
                "avg_per_week": avg_per_week,
                "overload_threshold": threshold,
                "overloaded_weeks": [{"week": w["week"], "count": w["count"]} for w in overloaded],
                "high_load_miss_rate": _miss_stats(overloaded),
                "normal_miss_rate": _miss_stats(normal),
            },
            "context_switching": {
                "avg_concurrent_tags_per_week": avg_tags,
                "avg_concurrent_projects_per_week": avg_projs,
                "high_switch_weeks": high_switch,
                "miss_rate_high_switch": round(hs_miss / hs_total, 3) if hs_total else 0.0,
                "miss_rate_low_switch": round(ls_miss / ls_total, 3) if ls_total else 0.0,
            },
        }

    def behavior(
        self,
        since: Optional[str] = None,
        project_id: Optional[int] = None,
        tag: Optional[str] = None,
    ) -> dict:
        """Task rot (age in todo), recovery lag, and status reversals."""
        scope = self._scoped_task_ids(since, project_id, tag)
        created_snaps = self._all_created_snapshots()
        cat_map = self._created_at_map()

        # ── status events per task ───────────────────────────────
        status_rows = self._conn.execute(
            """
            SELECT task_id, old_value AS from_status, new_value AS to_status, changed_at
            FROM task_events
            WHERE event_type = 'updated' AND field_name = 'status'
            ORDER BY task_id, changed_at
            """
        ).fetchall()
        if scope is not None:
            status_rows = [r for r in status_rows if r["task_id"] in scope]

        status_by_task: dict[int, list] = defaultdict(list)
        for r in status_rows:
            status_by_task[r["task_id"]].append(r)

        # ── task rot ─────────────────────────────────────────────
        first_start: dict[int, str] = {}
        for tid, events in status_by_task.items():
            for e in events:
                if e["to_status"] != "todo":
                    first_start[tid] = e["changed_at"]
                    break

        today = date.today().isoformat()
        days_to_start: list[float] = []
        by_tag_rot: dict[str, list[float]] = defaultdict(list)
        rotting: list[dict] = []
        rot_threshold = 14

        for tid, created_at in cat_map.items():
            if scope is not None and tid not in scope:
                continue
            snap = created_snaps.get(tid, {})

            if tid in first_start:
                d = _date_diff_days(created_at, first_start[tid])
                if d >= 0:
                    days_to_start.append(d)
                    for t in _extract_tags(snap):
                        by_tag_rot[t].append(d)
            else:
                age = _date_diff_days(created_at, today + "T00:00:00Z")
                if age >= rot_threshold:
                    is_deleted = self._conn.execute(
                        "SELECT 1 FROM task_events WHERE event_type='deleted' AND task_id=?", (tid,)
                    ).fetchone()
                    if not is_deleted:
                        rotting.append({
                            "task_id": tid,
                            "name": snap.get("name", ""),
                            "age_days": int(age),
                            "tags": _extract_tags(snap),
                        })

        rotting.sort(key=lambda x: x["age_days"], reverse=True)

        # ── recovery lag ─────────────────────────────────────────
        done_rows = self._done_events(scope)
        lag_days: list[float] = []
        worst: Optional[tuple] = None

        for r in done_rows:
            snap = _parse_snapshot(r["task_snapshot"])
            deadline = snap.get("deadline")
            if not deadline:
                continue
            done_date = r["done_at"][:10]
            if done_date <= deadline:
                continue
            lag = _date_diff_days(deadline, done_date)
            lag_days.append(lag)
            name = created_snaps.get(r["task_id"], snap).get("name", "")
            if worst is None or lag > worst[1]:
                worst = (r["task_id"], lag, name)

        dist: dict[str, int] = {"1-7d": 0, "8-14d": 0, "15-30d": 0, "31+d": 0}
        for d in lag_days:
            if d <= 7:
                dist["1-7d"] += 1
            elif d <= 14:
                dist["8-14d"] += 1
            elif d <= 30:
                dist["15-30d"] += 1
            else:
                dist["31+d"] += 1

        # ── status reversals ─────────────────────────────────────
        _backwards = {
            ("done", "in_progress"): "done_to_in_progress",
            ("done", "todo"): "done_to_todo",
            ("in_progress", "todo"): "in_progress_to_todo",
        }
        rev_by_type: dict[str, int] = {v: 0 for v in _backwards.values()}
        tasks_with_rev: set[int] = set()
        total_rev = 0

        for r in status_rows:
            key = (r["from_status"], r["to_status"])
            if key in _backwards:
                rev_by_type[_backwards[key]] += 1
                total_rev += 1
                tasks_with_rev.add(r["task_id"])

        n_tasks = (
            len(scope & set(cat_map)) if scope is not None else len(cat_map)
        )

        return {
            "task_rot": {
                "avg_days_to_start": round(_safe_mean(days_to_start), 1),
                "median_days_to_start": round(_safe_median(days_to_start), 1),
                "rot_threshold_days": rot_threshold,
                "current_rotting_count": len(rotting),
                "rotting_tasks": rotting[:10],
                "by_tag": {
                    t: {"avg_days_to_start": round(_safe_mean(d), 1)}
                    for t, d in by_tag_rot.items()
                },
            },
            "recovery_lag": {
                "missed_and_recovered_count": len(lag_days),
                "avg_lag_days": round(_safe_mean(lag_days), 1),
                "median_lag_days": round(_safe_median(lag_days), 1),
                "distribution": dist,
                "worst": {
                    "task_id": worst[0],
                    "name": worst[2],
                    "lag_days": int(worst[1]),
                } if worst else None,
            },
            "status_reversals": {
                "total_reversals": total_rev,
                "tasks_with_reversals": len(tasks_with_rev),
                "reversal_rate_per_task": round(total_rev / n_tasks, 3) if n_tasks else 0.0,
                "by_type": rev_by_type,
            },
        }

    def realism(
        self,
        since: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> dict:
        """Deadline Realism Score, deadline horizon at creation, and abandonment rate."""
        scope = self._scoped_task_ids(since, None, tag)
        created_snaps = self._all_created_snapshots()

        # Tasks that ever had a deadline change
        tasks_with_dl_changes: set[int] = {
            r["task_id"]
            for r in self._conn.execute(
                "SELECT DISTINCT task_id FROM task_events WHERE field_name='deadline'"
            ).fetchall()
        }

        done_rows = self._done_events(scope)
        done_map = {r["task_id"]: r["done_at"][:10] for r in done_rows}
        done_snaps = {r["task_id"]: _parse_snapshot(r["task_snapshot"]) for r in done_rows}

        # ── deadline realism score (DRS) ─────────────────────────
        drs_total = drs_on_time = 0
        drs_by_tag: dict[str, dict] = defaultdict(lambda: {"total": 0, "on_time": 0})

        for tid, done_date in done_map.items():
            if scope is not None and tid not in scope:
                continue
            if tid in tasks_with_dl_changes:
                continue  # exclude tasks where deadline was modified

            snap = done_snaps.get(tid, {})
            deadline = snap.get("deadline")
            if not deadline:
                continue

            drs_total += 1
            on_time = done_date <= deadline
            if on_time:
                drs_on_time += 1

            csn = created_snaps.get(tid, snap)
            for t in _extract_tags(csn):
                drs_by_tag[t]["total"] += 1
                if on_time:
                    drs_by_tag[t]["on_time"] += 1

        drs_output: dict[str, Any] = {
            "overall_drs": round(drs_on_time / drs_total, 3) if drs_total else 0.0,
            "by_tag": {
                t: {
                    "total": d["total"],
                    "on_time": d["on_time"],
                    "drs": round(d["on_time"] / d["total"], 3) if d["total"] else 0.0,
                }
                for t, d in drs_by_tag.items()
            },
        }
        if not drs_total:
            drs_output["insufficient_data"] = True
            drs_output["reason"] = "No completed tasks with un-pushed deadlines found"

        # ── deadline horizon ─────────────────────────────────────
        horizons: list[float] = []
        reactive = planned = 0
        horizon_by_tag: dict[str, list[float]] = defaultdict(list)

        for tid, snap in created_snaps.items():
            if scope is not None and tid not in scope:
                continue
            deadline = snap.get("deadline")
            created_at = snap.get("created_at")
            if not deadline or not created_at:
                continue

            horizon = _date_diff_days(created_at, deadline + "T00:00:00Z")
            horizons.append(horizon)
            if horizon < 3:
                reactive += 1
            if horizon > 14:
                planned += 1
            for t in _extract_tags(snap):
                horizon_by_tag[t].append(horizon)

        # ── abandonment rate ─────────────────────────────────────
        deleted_ids: set[int] = {
            r["task_id"]
            for r in self._conn.execute(
                "SELECT DISTINCT task_id FROM task_events WHERE event_type='deleted'"
            ).fetchall()
        }
        done_ids: set[int] = {
            r["task_id"]
            for r in self._conn.execute(
                "SELECT DISTINCT task_id FROM task_events WHERE field_name='status' AND new_value='done'"
            ).fetchall()
        }
        abandoned_ids = deleted_ids - done_ids
        if scope is not None:
            abandoned_ids = abandoned_ids & scope

        total_created = len(scope) if scope is not None else len(created_snaps)
        abandon_by_tag: dict[str, dict] = defaultdict(lambda: {"created": 0, "abandoned": 0})
        for tid, snap in created_snaps.items():
            if scope is not None and tid not in scope:
                continue
            for t in _extract_tags(snap):
                abandon_by_tag[t]["created"] += 1
                if tid in abandoned_ids:
                    abandon_by_tag[t]["abandoned"] += 1

        return {
            "deadline_realism": drs_output,
            "deadline_horizon": {
                "avg_days_ahead": round(_safe_mean(horizons), 1),
                "median_days_ahead": round(_safe_median(horizons), 1),
                "by_tag": {
                    t: {"avg_days_ahead": round(_safe_mean(h), 1)}
                    for t, h in horizon_by_tag.items()
                },
                "reactive_count": reactive,
                "planned_count": planned,
            },
            "abandonment": {
                "total_created": total_created,
                "abandoned_count": len(abandoned_ids),
                "abandonment_rate": round(len(abandoned_ids) / total_created, 3)
                if total_created else 0.0,
                "by_tag": {
                    t: {
                        "created": d["created"],
                        "abandoned": d["abandoned"],
                        "rate": round(d["abandoned"] / d["created"], 3) if d["created"] else 0.0,
                    }
                    for t, d in abandon_by_tag.items()
                },
            },
        }

    def summary(self, since: Optional[str] = None) -> dict:
        """Compact health dashboard combining key numbers from all metrics."""
        dh = self.deadline_health(since=since)
        vel = self.velocity(since=since)
        beh = self.behavior(since=since)
        rea = self.realism(since=since)

        miss_by_tag = dh["miss_rate"]["by_tag"]
        worst_tag = (
            max(miss_by_tag, key=lambda t: miss_by_tag[t]["miss_rate"])
            if miss_by_tag else None
        )
        rot_by_tag = beh["task_rot"]["by_tag"]
        rot_tag = (
            max(rot_by_tag, key=lambda t: rot_by_tag[t]["avg_days_to_start"])
            if rot_by_tag else None
        )

        ttd = vel["time_to_done"]
        stats = ttd.get("stats", {})

        return {
            "miss_rate_overall": dh["miss_rate"]["overall_miss_rate"],
            "active_overdue_count": dh["miss_rate"]["active_overdue_count"],
            "deadline_push_rate": dh["deadline_pushes"]["push_rate"],
            "avg_time_to_done_days": stats.get("mean_days"),
            "median_time_to_done_days": stats.get("median_days"),
            "tasks_completed": ttd["count"],
            "completion_velocity_4w_avg": vel["completion_velocity"]["overall"]["recent_4w_avg"],
            "rotting_tasks_count": beh["task_rot"]["current_rotting_count"],
            "abandonment_rate": rea["abandonment"]["abandonment_rate"],
            "deadline_realism_score": rea["deadline_realism"]["overall_drs"],
            "status_reversals": beh["status_reversals"]["total_reversals"],
            "avg_recovery_lag_days": beh["recovery_lag"]["avg_lag_days"],
            "most_missed_tag": worst_tag,
            "most_missed_tag_rate": miss_by_tag[worst_tag]["miss_rate"] if worst_tag else None,
            "most_procrastinated_tag": rot_tag,
            "most_procrastinated_avg_days": rot_by_tag[rot_tag]["avg_days_to_start"]
            if rot_tag else None,
        }
