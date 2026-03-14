"""Logging configuration for JAR.

Two separate loggers are provided:
- DB logger   → logs/db/db.log       (DEBUG — every SQL statement + params)
- Service logger → logs/service/service.log  (INFO — service method calls + errors)

Both use rotating file handlers capped at 5 MB × 3 backup files.
Log directories are created automatically under the platform user-data directory.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import platformdirs

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3

_db_logger: logging.Logger | None = None
_service_logger: logging.Logger | None = None


def _log_base_dir() -> Path:
    return Path(platformdirs.user_data_dir("jar", appauthor=False)) / "logs"


def _make_rotating_handler(log_file: Path) -> logging.handlers.RotatingFileHandler:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        filename=str(log_file),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    return handler


def get_db_logger() -> logging.Logger:
    """Return the singleton DB-level logger (DEBUG, writes to logs/db/db.log)."""
    global _db_logger
    if _db_logger is not None:
        return _db_logger

    logger = logging.getLogger("jar.db")
    if not logger.handlers:
        log_file = _log_base_dir() / "db" / "db.log"
        logger.addHandler(_make_rotating_handler(log_file))
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

    _db_logger = logger
    return _db_logger


def get_service_logger() -> logging.Logger:
    """Return the singleton service-level logger (INFO, writes to logs/service/service.log)."""
    global _service_logger
    if _service_logger is not None:
        return _service_logger

    logger = logging.getLogger("jar.service")
    if not logger.handlers:
        log_file = _log_base_dir() / "service" / "service.log"
        logger.addHandler(_make_rotating_handler(log_file))
        logger.setLevel(logging.INFO)
        logger.propagate = False

    _service_logger = logger
    return _service_logger
