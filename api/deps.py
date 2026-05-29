"""Shared SQLite connection + lock for the FastAPI app.

FastAPI runs our synchronous handlers in a threadpool, but a single sqlite3
connection is not safe under truly concurrent statement execution. Since this
is a single-user local app, we serialize every DB touch through one process-wide
lock over one shared connection (``check_same_thread=False`` is already set in
``db.connect``). ``init_db`` runs once on startup (applies the AI-schema
migration).
"""

from __future__ import annotations

import sqlite3
import threading

from journal import db

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def init() -> None:
    """Open the shared connection and run schema init/migrations. Idempotent."""
    global _conn
    if _conn is None:
        _conn = db.connect()
        db.init_db(_conn)


def get_conn() -> sqlite3.Connection:
    if _conn is None:
        init()
    assert _conn is not None
    return _conn


def db_lock() -> threading.Lock:
    return _lock
