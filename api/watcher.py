"""Background auto-import loop + the feed state the UI polls.

One module-level ``WatcherState`` for the process (single-user app, one watcher).
The scan itself is synchronous and holds the shared DB lock — same discipline as
every request handler — so it runs via ``asyncio.to_thread`` to keep the event
loop free.
"""

from __future__ import annotations

import asyncio
import logging

from journal import db, watcher
from journal.config import BACKTEST_DIR, WATCH_INTERVAL_S

from . import deps

log = logging.getLogger(__name__)

state = watcher.WatcherState()
_task: asyncio.Task | None = None


def ensure_model_folders() -> None:
    """Create the drop-box folder for every live model (idempotent).

    Archived models keep whatever folder they have, but we stop (re)creating it.
    """
    conn = deps.get_conn()
    with deps.db_lock():
        folders = db.model_folder_map(conn)
    for folder, model in folders.items():
        if not model["archived"]:
            (BACKTEST_DIR / folder).mkdir(parents=True, exist_ok=True)


def scan_now() -> int:
    conn = deps.get_conn()
    with deps.db_lock():
        return watcher.scan_once(conn, state)


async def _loop() -> None:
    while True:
        try:
            await asyncio.to_thread(scan_now)
        except Exception:
            # The loop must survive anything — a dead watcher looks exactly
            # like "no new files", which is the worst failure mode.
            log.exception("watcher scan failed")
        await asyncio.sleep(WATCH_INTERVAL_S)


def start() -> None:
    global _task
    if _task is None:
        ensure_model_folders()
        _task = asyncio.get_running_loop().create_task(_loop())
