"""Auto-import watcher over ``data/imports/``.

One scan pass per tick. Files in the root classify themselves (live/replay is
inferred from their accounts, as always); files under ``live/`` or ``replay/``
import as that mode; files under ``backtest/<folder>/`` import as
``mode='backtest'`` bound to the model whose ``models.folder`` matches the
subfolder — the folder placement *is* the declaration, so nothing is ever
guessed. A folder matching no model is skipped loudly rather than imported
wrong, and a file whose accounts contradict its folder is imported but
flagged in the feed.

ATAS may still be writing an export when a tick fires, so a file is only
eligible once it has settled: either its size+mtime survived a full tick
unchanged, or it is older than ``WATCH_SETTLED_AGE_S`` (covers files that
accumulated while the app was closed — no reason to make those wait a tick).

Everything the watcher does lands in ``WatcherState.events`` (a bounded feed
the UI polls), so a mis-filed export is visible within a minute, not at
month-end review.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import db, ingest
from .config import BACKTEST_DIR, IMPORTS_DIR, UTC_TZ, WATCH_SETTLED_AGE_S

FEED_LIMIT = 100


@dataclass
class WatcherState:
    """Carried across ticks: settle tracking, failure suppression, event feed."""

    # path -> (size, mtime) seen last tick; unchanged next tick = settled.
    pending: dict[str, tuple[int, float]] = field(default_factory=dict)
    # path -> mtime that raised on import; skipped until the file changes,
    # so one corrupt export doesn't error the feed every 60 seconds.
    failed: dict[str, float] = field(default_factory=dict)
    # unknown-folder paths already reported (same rationale as ``failed``).
    warned: set[str] = field(default_factory=set)
    events: list[dict] = field(default_factory=list)
    seq: int = 0
    last_scan_at: str | None = None

    def push(self, kind: str, file: str, **extra) -> None:
        self.seq += 1
        self.events.append({
            "seq": self.seq,
            "ts": datetime.now(tz=UTC_TZ).isoformat(),
            "kind": kind,
            "file": file,
            **extra,
        })
        del self.events[: -FEED_LIMIT]


def _xlsx_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [p for p in sorted(directory.glob("*.xlsx")) if not p.name.startswith("~$")]


def scan_once(
    conn: sqlite3.Connection,
    state: WatcherState,
    imports_dir: Path = IMPORTS_DIR,
    backtest_dir: Path = BACKTEST_DIR,
    importer: Callable = ingest.import_file,
    settled_age_s: float = WATCH_SETTLED_AGE_S,
    now: float | None = None,
    lock: AbstractContextManager | None = None,
) -> int:
    """One watcher pass; returns how many files were imported.

    ``importer``/``now`` are injectable for tests; production uses the real
    ingest path and the wall clock. ``lock`` is held only for the short DB
    windows (and passed to the importer for its writes) — never across file
    parsing, so API requests stay responsive during an import pass.
    """
    now = time.time() if now is None else now
    _lk = lock if lock is not None else nullcontext()
    with _lk:
        folders = db.model_folder_map(conn)

    # (path, mode override, model row or None). Unknown backtest folders are
    # reported once per file and never imported — a wrong guess would silently
    # pollute the stats, a skipped file is visible in the feed.
    targets: list[tuple[Path, str | None, dict | None]] = []
    seen: set[str] = set()
    for path in _xlsx_in(imports_dir):
        targets.append((path, None, None))
    # live/ and replay/ declare the mode by placement, same as backtest
    # folders — but they bind no model.
    for folder_mode in ("live", "replay"):
        for path in _xlsx_in(imports_dir / folder_mode):
            targets.append((path, folder_mode, None))
    for sub in sorted(backtest_dir.iterdir()) if backtest_dir.is_dir() else []:
        if not sub.is_dir():
            continue
        for path in _xlsx_in(sub):
            model = folders.get(sub.name)
            if model is None:
                seen.add(str(path))
                if str(path) not in state.warned:
                    state.warned.add(str(path))
                    state.push(
                        "unknown_folder", path.name, folder=sub.name,
                        message=f"backtest/{sub.name}/ matches no model — file skipped",
                    )
                continue
            targets.append((path, "backtest", model))

    with _lk:
        imported_mtimes = db.file_mtime_map(conn)
    imported = 0
    for path, mode, model in targets:
        key = str(path)
        # DB identity: path relative to the imports dir (bare name for root
        # files), so a backtest export never collides with a same-named root
        # file. ``key`` above is the on-disk path for the in-memory state maps.
        source = ingest.source_key(path, imports_dir)
        seen.add(key)
        try:
            stat = path.stat()
        except OSError:
            continue  # deleted between glob and stat
        mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC_TZ).isoformat()
        if imported_mtimes.get(source) == mtime_iso:
            continue  # this exact version is already in
        if state.failed.get(key) == stat.st_mtime:
            continue
        sig = (stat.st_size, stat.st_mtime)
        if now - stat.st_mtime < settled_age_s and state.pending.get(key) != sig:
            state.pending[key] = sig  # young and not yet seen stable — next tick
            continue

        try:
            counts = importer(
                conn, path,
                source_tz=ingest.auto_source_tz_for_date(
                    datetime.fromtimestamp(stat.st_mtime).date()
                ),
                file_mtime=mtime_iso,
                mode=mode,
                model_id=model["id"] if model else None,
                lock=lock,
            )
        except Exception as exc:
            state.failed[key] = stat.st_mtime
            state.push("error", source, message=str(exc))
            continue

        state.pending.pop(key, None)
        state.failed.pop(key, None)
        with _lk:
            session = db.sessions_map(conn).get(source, {})
        message = None
        if mode and session.get("mode") != mode:
            # The session predates this import (upsert never overwrites), e.g.
            # a file first imported from the root, later moved into a model
            # folder. Say so instead of implying the folder won.
            message = (
                f"session already existed as {session.get('mode')} — left unchanged; "
                "re-tag it in Session controls if the folder is right"
            )
        elif mode in ("backtest", "replay") and session.get("account") not in (None, "Replay"):
            # Backtests and replays run on the Replay account; a real account
            # in one of those folders is almost certainly a mis-filed live
            # export.
            message = (
                f"heads up: this file touched account {session['account']} — "
                f"a {mode} is expected to be all-Replay"
            )
        elif mode == "live" and session.get("account") == "Replay":
            message = (
                "heads up: this file is all-Replay — "
                "is it really a live session?"
            )
        elif model and model.get("archived"):
            message = f"model “{model['name']}” is archived"
        state.push(
            "imported", source,
            mode=session.get("mode"),
            model_id=session.get("model_id"),
            model_name=model["name"] if model else None,
            counts=counts,
            **({"message": message} if message else {}),
        )
        imported += 1

    # Forget files that left the tree, so a re-appearing file re-warns/settles.
    state.pending = {k: v for k, v in state.pending.items() if k in seen}
    state.failed = {k: v for k, v in state.failed.items() if k in seen}
    state.warned = {k for k in state.warned if k in seen}
    state.last_scan_at = datetime.now(tz=UTC_TZ).isoformat()
    return imported
