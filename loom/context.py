"""Context snapshot generator — produces a compact JSON block for session injection."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_context_snapshot(
    conn: sqlite3.Connection,
    goal_id: Optional[int] = None,
    output_path: Optional[str] = None,
) -> dict:
    """Generate a compact context block for session injection.

    Returns a dict and optionally writes it as JSON to output_path.

    If goal_id is not given, resolves the active goal (the single
    highest-priority scheduled/in_progress goal — see
    GoalService.resolve_active()). Surfaces the active goal's and active
    project's full detail (not just bare ids), plus the current task's full
    lineage (task -> project -> goal), so a session doesn't lose the
    strategic thread behind whatever it's actually working on.
    """
    from .service import GoalService, ProjectService, TaskService

    gs = GoalService(conn)
    ps = ProjectService(conn)
    ts = TaskService(conn)

    active_goal = gs.get(goal_id) if goal_id is not None else gs.resolve_active()
    resolved_goal_id = active_goal.id if active_goal else goal_id

    active_project = ps.resolve_active(resolved_goal_id) if resolved_goal_id is not None else None

    ready = ts.get_ready_queue(goal_id=resolved_goal_id, limit=10)

    current = ready[0] if ready else None
    next_tasks = ready[1:] if ready else []

    current_lineage = None
    if current is not None:
        task_project = ps.get(current.project_id) if current.project_id is not None else None
        # Use the task's own goal_id, not active_goal — get_ready_queue's goal_id
        # filter already guarantees they match when a goal is resolved, and
        # falling back to active_goal when the task genuinely has none would
        # fabricate a lineage link that was never actually recorded.
        task_goal = gs.get(current.goal_id) if current.goal_id is not None else None
        current_lineage = {
            "project": task_project.to_dict() if task_project else None,
            "goal": task_goal.to_dict() if task_goal else None,
        }

    blocked_owner = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status='blocked_owner'"
        + (" AND goal_id = ?" if resolved_goal_id is not None else ""),
        (resolved_goal_id,) if resolved_goal_id is not None else (),
    ).fetchone()[0]

    done_count = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status='done'"
        + (" AND goal_id = ?" if resolved_goal_id is not None else ""),
        (resolved_goal_id,) if resolved_goal_id is not None else (),
    ).fetchone()[0]

    total_count = conn.execute(
        "SELECT COUNT(*) FROM tasks"
        + (" WHERE goal_id = ?" if resolved_goal_id is not None else ""),
        (resolved_goal_id,) if resolved_goal_id is not None else (),
    ).fetchone()[0]

    snapshot = {
        "generated_at": _now_utc(),
        "active_goal_id": resolved_goal_id,
        "active_goal": active_goal.to_dict() if active_goal else None,
        "active_project": active_project.to_dict() if active_project else None,
        "current_task": current.to_dict() if current else None,
        "current_task_lineage": current_lineage,
        "ready_queue": [t.to_dict() for t in next_tasks],
        "blocked_owner_count": blocked_owner,
        "done_count": done_count,
        "total_count": total_count,
    }

    if output_path:
        with open(output_path, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)

    return snapshot
