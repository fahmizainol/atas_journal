"""Replay attempts: what you did in the Simulator, kept.

**This module is still the attempt's home.** The log, the trades it produced,
the rewinds that erased some of them and the aggregates over the rest live in
files, and nothing below reads or writes journal.db.

What changed (2026-08-08): the trades are *also* mirrored into journal.db by
``api.routers.replays``, under the `replay` account with each sitting tagged
``sessions.mode='replay'``. The earlier position here — that a fill against a
re-runnable tape is not a trade and stays on disk — drew the line in the wrong
place. Practice is the majority of the trading that actually gets done, and
keeping it out of the journal meant keeping it out of the Trades page, the
calendar, notes, setups and every review tool built on them; the harm that line
was drawn against (practice contaminating real-money statistics) is already
prevented by the mode tag, which is exactly how ATAS's own `Replay` account and
`/charts/live`'s paper account are handled.

The two records cannot drift, because the journal side is a projection of this
one and never an independent event log: each ``save`` replaces the sitting's
journal rows wholesale, so a rewind withdraws what it erased, and ``delete``
withdraws the lot. "Delete the folder and it is gone" still holds — the folder
is simply no longer the only place it goes.

    data/replays/<session_date>/<attempt_id>/
        attempt.json     # identity, tape fingerprint, ticket, status, note, rewinds
        log.json         # primary — the order log the browser recorded
        trades.json      # frozen — the trades that log produced
        discarded.json   # trades a rewind erased (written only when there were any)
        summary.json     # derived — the aggregates the history page reads

One *attempt* is one sitting, not one day: replay the same session twice and
that is two attempts, the second carrying ``repeat_index: 1`` so a track record
can tell a cold read from a re-run.

The browser computes everything here. The fill engine lives only in
``frontend/src/lib/replaySim.ts``, and a second implementation on this side
could disagree with it about a fill — so this module stores what it is handed
and never re-derives it. That is why ``summary.json`` is marked derived but
still written: a stat nobody can read from outside the UI is one no script and
no LLM can read at all.

Two fingerprints make a stored log honest about what it can still reproduce:

  - ``engine_version`` — bumped by hand in replaySim.ts when fill semantics
    change, so a rebuild under new rules is never mistaken for the old numbers;
  - ``tape`` — symbol, date, tz, tick count and span. The tick cache is not
    immutable (the 16:00-17:00 gap fix re-fetched 352 sessions and moved every
    index in them), so a rebuild checks this before trusting ``OrderRec.idx``
    and falls back to replaying by timestamp when it drifts.

Times throughout are the display-zone wall clock as epoch-ms — the projection
``api.routers.simulator`` ships the tape in, not UTC. The zone is stored
alongside them so they stay invertible; the one irreducible ambiguity is the
hour of a DST fold.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

REPLAYS_DIR = DATA_DIR / "replays"

# <session date>_<contract>_<UTC stamp>[_n]. The leading date is also the folder
# the attempt lives in, so an id names its own path (a lookup never scans) and
# matching this doubles as the path-traversal guard.
ATTEMPT_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_([A-Z0-9]+)_(\d{8}T\d{6}Z)(?:_(\d+))?$")

STATUSES = ("active", "finished", "abandoned")

# A sitting is a few hundred orders at the very most. The bound is here so a
# runaway client can't write an unbounded file, not because anyone is expected
# to approach it.
MAX_ORDERS = 20_000
MAX_TRADES = 20_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(ts: datetime) -> str:
    return ts.strftime("%Y%m%dT%H%M%SZ")


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    """Write via a temp file in the same directory, then replace.

    An attempt autosaves while a replay is running; a half-written trades.json
    left by a crash mid-write would take the whole attempt with it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(path)


def attempt_dir(attempt_id: str) -> Path:
    m = ATTEMPT_ID_RE.match(attempt_id)
    if not m:
        raise ValueError(f"not an attempt id: {attempt_id!r}")
    return REPLAYS_DIR / m.group(1) / attempt_id


def _require(attempt_id: str) -> Path:
    d = attempt_dir(attempt_id)
    if not (d / "attempt.json").exists():
        raise FileNotFoundError(attempt_id)
    return d


# --- create -----------------------------------------------------------------


def create(
    *,
    symbol: str,
    root: str,
    date: str,
    tz: str,
    engine_version: int,
    tape: dict,
    prefs: dict,
    started_ms: int,
    model_id: int | None = None,
) -> dict:
    """Open an attempt. Called on the first fill, never before — a session you
    watched without trading leaves nothing behind."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise ValueError(f"not a session date: {date!r}")
    if not re.fullmatch(r"[A-Z0-9]+", symbol or ""):
        raise ValueError(f"not a contract symbol: {symbol!r}")

    now = _utc_now()
    day_dir = REPLAYS_DIR / date
    # How many times this session has been traded before. A day you have already
    # seen the end of is not a cold read, and the track record should be able to
    # say so even when it counts those attempts anyway.
    repeat_index = sum(
        1
        for a in _day_attempts(day_dir)
        if a.get("symbol") == symbol
    )

    base = f"{date}_{symbol}_{_stamp(now)}"
    attempt_id, n = base, 0
    # Two sittings can open inside the same second — a fill, a rewind past it,
    # a fill again.
    while (day_dir / attempt_id).exists():
        n += 1
        attempt_id = f"{base}_{n}"

    attempt = {
        "id": attempt_id,
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "finished_at": None,
        "symbol": symbol,
        "root": root,
        "date": date,
        "tz": tz,
        "engine_version": int(engine_version),
        "tape": tape,
        "prefs": prefs,
        "status": "active",
        "started_ms": int(started_ms),
        "clock_ms": int(started_ms),
        "repeat_index": repeat_index,
        "note": "",
        "model_id": model_id,
        # Every seek that erased a fill, and how many trades it took with it.
        # An attempt with any of these is a do-over: still a real attempt, but
        # one whose win rate was written with the answer in hand.
        "rewinds": [],
        "discarded_trades": 0,
    }
    _write_json(day_dir / attempt_id / "attempt.json", attempt)
    return attempt


# --- write ------------------------------------------------------------------


def save(
    attempt_id: str,
    *,
    log: dict,
    trades: list,
    summary: dict,
    discarded: list | None = None,
    rewinds: list | None = None,
    clock_ms: int | None = None,
    status: str | None = None,
) -> dict:
    """Autosave: the log, what it produced, and the aggregates over it."""
    d = _require(attempt_id)
    if len(log.get("orders") or []) > MAX_ORDERS:
        raise ValueError(f"more than {MAX_ORDERS} orders in one attempt")
    if len(trades) > MAX_TRADES:
        raise ValueError(f"more than {MAX_TRADES} trades in one attempt")
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")

    _write_json(d / "log.json", log)
    _write_json(d / "trades.json", trades)
    _write_json(d / "summary.json", summary)
    # Absent rather than empty: an attempt with no do-overs shouldn't carry a
    # file that says so, the way store.py leaves out vetoed/missed.
    if discarded:
        _write_json(d / "discarded.json", discarded)
    else:
        (d / "discarded.json").unlink(missing_ok=True)

    attempt = _read_json(d / "attempt.json", {})
    now = _utc_now()
    attempt["updated_at"] = _iso(now)
    if clock_ms is not None:
        attempt["clock_ms"] = int(clock_ms)
    if rewinds is not None:
        attempt["rewinds"] = rewinds
    attempt["discarded_trades"] = len(discarded or [])
    if status is not None:
        attempt["status"] = status
        # Stamped once: an attempt that finishes, is reopened and finishes again
        # keeps the moment it first ran out of tape.
        if status == "finished" and not attempt.get("finished_at"):
            attempt["finished_at"] = _iso(now)
    _write_json(d / "attempt.json", attempt)
    return attempt


def patch(attempt_id: str, **fields: Any) -> dict:
    """Change the things that are yours to change after the fact — the note, the
    model it was practising, the status. Never the trades."""
    d = _require(attempt_id)
    attempt = _read_json(d / "attempt.json", {})
    now = _utc_now()
    if "note" in fields and fields["note"] is not None:
        attempt["note"] = str(fields["note"])
    if "model_id" in fields:
        attempt["model_id"] = fields["model_id"]
    status = fields.get("status")
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        attempt["status"] = status
        if status == "finished" and not attempt.get("finished_at"):
            attempt["finished_at"] = _iso(now)
    attempt["updated_at"] = _iso(now)
    _write_json(d / "attempt.json", attempt)
    return attempt


def delete(attempt_id: str) -> None:
    d = attempt_dir(attempt_id)
    if d.exists():
        shutil.rmtree(d)
    # Leave no empty date folders behind — the listing walks them.
    parent = d.parent
    if parent.exists() and parent != REPLAYS_DIR and not any(parent.iterdir()):
        parent.rmdir()


# --- read -------------------------------------------------------------------


def _day_attempts(day_dir: Path) -> list[dict]:
    if not day_dir.is_dir():
        return []
    out = []
    for p in sorted(day_dir.iterdir()):
        a = _read_json(p / "attempt.json")
        if isinstance(a, dict):
            out.append(a)
    return out


def list_attempts(
    *,
    limit: int = 500,
    status: str | None = None,
    symbol: str | None = None,
    date: str | None = None,
) -> list[dict]:
    """Every attempt, newest sitting first, each with its summary inlined.

    The row is what the history table draws and what the KPI tiles pool over, so
    it carries the whole attempt record (rewind events included) rather than a
    curated subset — the honest-sample filters are the client's to apply, and it
    can only apply them to what it was told.
    """
    if not REPLAYS_DIR.is_dir():
        return []
    days = sorted(
        (p for p in REPLAYS_DIR.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if date:
        days = [p for p in days if p.name == date]

    rows: list[dict] = []
    for day in days:
        for attempt in _day_attempts(day):
            if status and attempt.get("status") != status:
                continue
            if symbol and attempt.get("symbol") != symbol:
                continue
            summary = _read_json(day / attempt["id"] / "summary.json", {})
            rows.append({**attempt, "summary": summary or {}})
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows[: max(1, limit)]


def read(attempt_id: str) -> dict:
    """One attempt, whole: the record, the log, the trades, the aggregates."""
    d = _require(attempt_id)
    return {
        **_read_json(d / "attempt.json", {}),
        "log": _read_json(d / "log.json", {"orders": [], "closes": [], "brackets": []}),
        "trades": _read_json(d / "trades.json", []),
        "discarded": _read_json(d / "discarded.json", []),
        "summary": _read_json(d / "summary.json", {}),
    }
