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
    """
    from .service import TaskService

    ts = TaskService(conn)
    ready = ts.get_ready_queue(goal_id=goal_id, limit=10)

    current = ready[0] if ready else None
    next_tasks = ready[1:] if ready else []

    blocked_owner = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status='blocked_owner'"
        + (" AND goal_id = ?" if goal_id is not None else ""),
        (goal_id,) if goal_id is not None else (),
    ).fetchone()[0]

    done_count = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status='done'"
        + (" AND goal_id = ?" if goal_id is not None else ""),
        (goal_id,) if goal_id is not None else (),
    ).fetchone()[0]

    total_count = conn.execute(
        "SELECT COUNT(*) FROM tasks"
        + (" WHERE goal_id = ?" if goal_id is not None else ""),
        (goal_id,) if goal_id is not None else (),
    ).fetchone()[0]

    snapshot = {
        "generated_at": _now_utc(),
        "active_goal_id": goal_id,
        "current_task": current.to_dict() if current else None,
        "ready_queue": [t.to_dict() for t in next_tasks],
        "blocked_owner_count": blocked_owner,
        "done_count": done_count,
        "total_count": total_count,
    }

    if output_path:
        with open(output_path, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)

    return snapshot
