"""Database connection, initialisation, and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import platformdirs

# Current schema version — bump when adding migrations.
SCHEMA_VERSION = 4

_DEFAULT_DB_PATH: Optional[Path] = None


def _default_db_path() -> Path:
    global _DEFAULT_DB_PATH
    if _DEFAULT_DB_PATH is None:
        data_dir = Path(platformdirs.user_data_dir("loom", appauthor=False))
        data_dir.mkdir(parents=True, exist_ok=True)
        _DEFAULT_DB_PATH = data_dir / "loom.db"
    return _DEFAULT_DB_PATH


def get_connection(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    """Open and return a sqlite3 connection.

    Enables WAL mode and foreign key enforcement on every connection.
    The caller is responsible for closing the connection.
    """
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


# ------------------------------------------------------------------ schema DDL

_DDL_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version  INTEGER NOT NULL
);
"""

_DDL_PROJECTS = """
CREATE TABLE IF NOT EXISTS projects (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    description      TEXT,
    start_date       TEXT,
    deployment_date  TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);
"""

_DDL_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT,
    tags        TEXT,
    deadline    TEXT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL DEFAULT 'triage',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
"""

_DDL_TASK_EVENTS = """
CREATE TABLE IF NOT EXISTS task_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL,
    event_type    TEXT    NOT NULL,
    field_name    TEXT,
    old_value     TEXT,
    new_value     TEXT,
    changed_at    TEXT    NOT NULL,
    task_snapshot TEXT
);
"""

_DDL_TASK_EVENTS_IDX_TASK = """
CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
"""

_DDL_TASK_EVENTS_IDX_TIME = """
CREATE INDEX IF NOT EXISTS idx_task_events_changed_at ON task_events(changed_at);
"""

# v4 new tables

_DDL_GOALS = """
CREATE TABLE IF NOT EXISTS goals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    description         TEXT,
    status              TEXT    NOT NULL DEFAULT 'desire',
    priority            INTEGER DEFAULT 0,
    started_at          TEXT,
    completed_at        TEXT,
    estimated_sessions  INTEGER,
    actual_sessions     INTEGER DEFAULT 0,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);
"""

_DDL_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS loom_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT    NOT NULL,
    session_number      INTEGER NOT NULL,
    type                TEXT,
    active_goal_id      INTEGER REFERENCES goals(id),
    started_at          TEXT,
    ended_at            TEXT,
    duration_minutes    INTEGER,
    context_pct_at_exit REAL,
    exit_reason         TEXT,
    handoff_note        TEXT,
    tasks_started       TEXT,
    tasks_completed     TEXT
);
"""

# v4 status trigger (9 statuses)

_VALID_STATUS_LITERAL = "('triage','desire','scheduled','in_progress','blocked_dep','blocked_owner','suspended','done','failed')"

_DDL_TASK_STATUS_CHECK_V4 = f"""
CREATE TRIGGER IF NOT EXISTS tasks_status_check
BEFORE INSERT ON tasks
BEGIN
    SELECT RAISE(ABORT, 'Invalid status value')
    WHERE NEW.status NOT IN {_VALID_STATUS_LITERAL};
END;
"""

_DDL_TASK_STATUS_CHECK_UPDATE_V4 = f"""
CREATE TRIGGER IF NOT EXISTS tasks_status_check_update
BEFORE UPDATE OF status ON tasks
BEGIN
    SELECT RAISE(ABORT, 'Invalid status value')
    WHERE NEW.status NOT IN {_VALID_STATUS_LITERAL};
END;
"""

# v3 triggers (kept for reference, used during fresh init path before v4 migration runs)
_DDL_TASK_STATUS_CHECK = _DDL_TASK_STATUS_CHECK_V4
_DDL_TASK_STATUS_CHECK_UPDATE = _DDL_TASK_STATUS_CHECK_UPDATE_V4

# v4 ALTER TABLE migrations

_MIGRATION_V4_TASKS = [
    "ALTER TABLE tasks ADD COLUMN goal_id INTEGER REFERENCES goals(id)",
    "ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'none'",
    "ALTER TABLE tasks ADD COLUMN wait_until TEXT",
    "ALTER TABLE tasks ADD COLUMN depends TEXT",
    "ALTER TABLE tasks ADD COLUMN blocked_reason TEXT",
    "ALTER TABLE tasks ADD COLUMN blocked_note TEXT",
    "ALTER TABLE tasks ADD COLUMN urgency_score REAL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN context_tag TEXT",
    "ALTER TABLE tasks ADD COLUMN estimated_sessions INTEGER",
    "ALTER TABLE tasks ADD COLUMN actual_sessions INTEGER DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN handoff_note TEXT",
]

_MIGRATION_V4_PROJECTS = [
    "ALTER TABLE projects ADD COLUMN goal_id INTEGER REFERENCES goals(id)",
    "ALTER TABLE projects ADD COLUMN status TEXT DEFAULT 'planned'",
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and run any pending migrations.

    Safe to call on an already-initialised DB (idempotent).
    """
    with conn:
        conn.execute(_DDL_SCHEMA_VERSION)
        conn.execute(_DDL_PROJECTS)
        conn.execute(_DDL_TASKS)
        conn.execute(_DDL_TASK_STATUS_CHECK)
        conn.execute(_DDL_TASK_STATUS_CHECK_UPDATE)

        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row["version"] if row else 0

        if current < SCHEMA_VERSION:
            _run_migrations(conn, from_version=current)

            if current == 0:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))


def _run_migrations(conn: sqlite3.Connection, from_version: int) -> None:
    """Apply incremental migrations from `from_version` up to SCHEMA_VERSION."""
    if from_version < 2:
        conn.execute(_DDL_TASK_EVENTS)
        conn.execute(_DDL_TASK_EVENTS_IDX_TASK)
        conn.execute(_DDL_TASK_EVENTS_IDX_TIME)
    if from_version < 3:
        conn.execute("DROP TRIGGER IF EXISTS tasks_status_check")
        conn.execute("DROP TRIGGER IF EXISTS tasks_status_check_update")
        conn.execute(_DDL_TASK_STATUS_CHECK)
        conn.execute(_DDL_TASK_STATUS_CHECK_UPDATE)
    if from_version < 4:
        conn.execute(_DDL_GOALS)
        conn.execute(_DDL_SESSIONS_TABLE)
        for sql in _MIGRATION_V4_TASKS:
            conn.execute(sql)
        for sql in _MIGRATION_V4_PROJECTS:
            conn.execute(sql)
        # Replace old 4-status triggers with new 9-status triggers
        conn.execute("DROP TRIGGER IF EXISTS tasks_status_check")
        conn.execute("DROP TRIGGER IF EXISTS tasks_status_check_update")
        conn.execute(_DDL_TASK_STATUS_CHECK_V4)
        conn.execute(_DDL_TASK_STATUS_CHECK_UPDATE_V4)
