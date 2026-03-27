"""Database connection, initialisation, and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import platformdirs

# Current schema version — bump when adding migrations.
SCHEMA_VERSION = 3

_DEFAULT_DB_PATH: Optional[Path] = None


def _default_db_path() -> Path:
    global _DEFAULT_DB_PATH
    if _DEFAULT_DB_PATH is None:
        data_dir = Path(platformdirs.user_data_dir("jar", appauthor=False))
        data_dir.mkdir(parents=True, exist_ok=True)
        _DEFAULT_DB_PATH = data_dir / "jar.db"
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
    status      TEXT    NOT NULL DEFAULT 'todo',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
"""

_DDL_TASK_STATUS_CHECK = """
CREATE TRIGGER IF NOT EXISTS tasks_status_check
BEFORE INSERT ON tasks
BEGIN
    SELECT RAISE(ABORT, 'Invalid status value')
    WHERE NEW.status NOT IN ('todo', 'in_progress', 'done', 'failed');
END;
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

_DDL_TASK_STATUS_CHECK_UPDATE = """
CREATE TRIGGER IF NOT EXISTS tasks_status_check_update
BEFORE UPDATE OF status ON tasks
BEGIN
    SELECT RAISE(ABORT, 'Invalid status value')
    WHERE NEW.status NOT IN ('todo', 'in_progress', 'done', 'failed');
END;
"""


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
