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
        attempt.json     # identity, tape fingerprint, ticket, status, note,
                         # rewinds — and, once it finishes, the flags the
                         # account raised over it and the review answering them
                         # (see journal.replay_account)
        log.json         # primary — the order log the browser recorded
        trades.json      # frozen — the trades that log produced
        discarded.json   # trades a rewind erased (written only when there were any)
        summary.json     # derived — the aggregates the history page reads
        whatif.json      # derived — this sitting re-priced under the exit ladder
                         #   (written when it finishes; see ``read_whatif``)

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

# ``reviewed`` is ``finished`` plus the answer: a sitting whose flags have each
# been called a leak or justified (see ``journal.replay_account``). It is a
# separate status rather than a field on ``finished`` because it is what the
# next sitting's gate reads, and a gate should turn on a status rather than on
# the shape of another record.
STATUSES = ("active", "finished", "abandoned", "reviewed")

# What kind of sitting this is. ``replay`` is the page you pick a day on;
# ``drill`` is backtest mode — one model, a random RTH clock on a day you are
# not told the date of (docs/backtest-mode-plan.md); ``paper`` is that same
# pick-a-day page run on the practice account, which has the funded account's
# rules but not its consequences (see ``journal.replay_account``).
#
# The mode is also **which account prices the sitting**: ``replay`` and ``paper``
# each have one and never share it, ``drill`` has none. That mapping lives in
# ``replay_account.BY_MODE`` rather than here, so this stays the list of what a
# sitting can be.
#
# It is written once, at create, and `patch` refuses to change it. Otherwise
# "relabel the losing sitting" moves a loss off the account that took it — onto
# the paper one, or off every account at all — which is the same hole the
# stale-active sweep exists to close, re-opened through a new door.
MODES = ("replay", "drill", "paper")

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
    mode: str = "replay",
    account_id: str | None = None,
    drop_ms: int | None = None,
    window: dict | None = None,
) -> dict:
    """Open an attempt. Called on the first fill, never before — a session you
    watched without trading leaves nothing behind.

    **Except in ``mode='drill'``**, where it is called at the drop instead. That
    reverses the rule above on purpose and only there: a drill rep where you
    looked and correctly found nothing is the row the whole mode exists to
    produce, and under the first-fill contract it leaves no trace at all. The
    cost is that abandoning a drill leaves an empty attempt, which
    ``replay_account.sweep_stale_actives`` deletes rather than settles.

    ``drop_ms`` is the replay clock the rep was thrown in at and ``window`` the
    bounds it was drawn from (``{"from_ms", "to_ms"}``, display-zone wall clocks
    like every other time here). Neither is derivable afterwards — ``started_ms``
    is the same number today but stops being it the moment a drill can be
    resumed — and the drop histogram is the mode's most interesting output.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
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
        # Every attempt written before backtest mode existed has no `mode` at
        # all, so everything downstream reads it as `attempt.get("mode") or
        # "replay"` — see `is_drill`. Writing it unconditionally here keeps that
        # fallback needed in exactly one generation of files.
        "mode": mode,
        # **Which account pays for this sitting**, and like the mode it is fixed
        # the moment the sitting opens (`patch` refuses to move it). The mode
        # used to carry this and could name two accounts; there can now be any
        # number, so it gets a field. `None` on a drill, which no account
        # prices — see `account_id_of` for the fallback that makes every sitting
        # already on disk answer correctly without being touched.
        "account_id": None if mode == "drill" else (account_id or None),
        "drop_ms": None if drop_ms is None else int(drop_ms),
        "window": dict(window) if window else None,
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

    attempt = _read_json(d / "attempt.json", {})
    # Does this save claim to *reopen* a sitting that had ended? If so, hold on
    # to what the sitting held before the write, so the claim can be checked
    # below against something other than the writer's word for it.
    reopening = status == "active" and attempt.get("status") in ("finished", "reviewed")
    was = (
        (_read_json(d / "log.json", {}), _read_json(d / "trades.json", []))
        if reopening
        else None
    )

    _write_json(d / "log.json", log)
    _write_json(d / "trades.json", trades)
    _write_json(d / "summary.json", summary)
    # Absent rather than empty: an attempt with no do-overs shouldn't carry a
    # file that says so, the way store.py leaves out vetoed/missed.
    if discarded:
        _write_json(d / "discarded.json", discarded)
    else:
        (d / "discarded.json").unlink(missing_ok=True)

    now = _utc_now()
    attempt["updated_at"] = _iso(now)
    if clock_ms is not None:
        attempt["clock_ms"] = int(clock_ms)
    if rewinds is not None:
        attempt["rewinds"] = rewinds
    attempt["discarded_trades"] = len(discarded or [])
    # A sitting reopens only when it is **traded on**, and a save carrying the
    # very orders and trades it already held is not trading on. It is a resume
    # re-marking the excursion, a scrub, a bracket leg cancelling a step after
    # the flatten — all of which write, and all of which used to un-end the
    # sitting. An hour later the stale sweep found it `active` and filed it
    # `abandoned`, which is where 23 ended sittings went before 2026-09-06.
    #
    # The recorder knows the difference now too (`useReplayAttempt`'s
    # `settledRef`, which is where the rule belongs — only the browser knows
    # what it is about to do). This is not that rule a second time: it is the
    # store declining to record a change when nothing it stores has changed,
    # which needs no notion of trading and can only ever refuse a no-op.
    if was is not None and was == (log, trades):
        status = attempt.get("status")
    if status is not None:
        attempt["status"] = status
        # Stamped once: an attempt that finishes, is reopened and finishes again
        # keeps the moment it first ran out of tape.
        if status == "finished" and not attempt.get("finished_at"):
            attempt["finished_at"] = _iso(now)
        # Trading on drops what was said about the old sitting: its flags no
        # longer describe the trades in the file, and a `reviewed` carried
        # forward would let a sitting be traded further under the protection of
        # an answer given about a different one. Flags are recomputed the next
        # time it finishes. `review` is the retired verdict record (see `patch`)
        # — cleared here so an attempt from before 2026-08-20 does not carry a
        # stale one through a rewind.
        if status == "active":
            attempt.pop("review", None)
            attempt.pop("flags", None)
    _write_json(d / "attempt.json", attempt)
    return attempt


def mode_of(attempt: dict) -> str:
    """Which of ``MODES`` this sitting is — the one reader of the field.

    The fallback is the whole reason it exists: attempts written before the mode
    did carry no ``mode`` key and are all replays, so anything reading
    ``attempt["mode"]`` directly would KeyError on the sittings already on disk.
    The account walks by mode (``replay_account.epoch_attempts``), which makes a
    wrong answer here a sitting priced on the wrong account rather than a crash.
    """
    return str(attempt.get("mode") or "replay")


def is_drill(attempt: dict) -> bool:
    """Is this sitting a backtest-mode rep? The one mode no account prices."""
    return mode_of(attempt) == "drill"


def account_id_of(attempt: dict) -> str | None:
    """Which account prices this sitting — ``None`` for a drill.

    This used to be the ``mode``, which could name exactly two accounts. It now
    names any number of them, so the account is stamped in its own field and the
    mode is left to say what *kind* of sitting this is (a drill or not).

    The fallback is the whole reason the field is optional, and it is exact
    rather than a guess: every sitting written before accounts were a list was
    priced by mode, ``paper`` was the paper account and everything else was the
    funded one. So nothing on disk needs migrating and nothing is reattributed.

    A drill is priced by nobody and says so with ``None`` — the same answer
    ``replay_account.for_mode`` gave it, and for the same reason (backtest reps
    are unpriced by design, docs/backtest-mode-plan.md D2).
    """
    if is_drill(attempt):
        return None
    stamped = str(attempt.get("account_id") or "").strip()
    if stamped:
        return stamped
    return "paper" if mode_of(attempt) == "paper" else "funded"


def patch(attempt_id: str, **fields: Any) -> dict:
    """Change the things that are yours to change after the fact — the note, the
    model it was practising, the status, the flags raised over it and whether it
    is marked to review later. Never the trades, and never the mode.

    A sitting used to carry a ``review`` here as well: one leak/justified verdict
    per flag. Retired 2026-08-20 (``docs/trade-grading-plan.md`` G6) — the review
    is the per-trade answers, which live on the journal rows. Attempts written
    before then keep the field on disk; nothing reads it, and ``save`` still
    clears it when a reviewed sitting is traded on."""
    d = _require(attempt_id)
    attempt = _read_json(d / "attempt.json", {})
    now = _utc_now()
    # The mode decides *which account* counts this sitting — the funded one, the
    # paper one, or none at all. Letting it move after the fact would make
    # "relabel it" a way to shift a loss onto an account that did not take it;
    # refuse loudly rather than silently dropping the field, since an "ok" that
    # ignored you is worse.
    if "mode" in fields and fields["mode"] is not None:
        if fields["mode"] != mode_of(attempt):
            raise ValueError("an attempt's mode is fixed when it opens")
    # And for the same reason, one door along: the account id is *the* answer to
    # "which account counts this loss", so letting it move after the fact would
    # reopen exactly the hole the mode's immutability closes.
    if "account_id" in fields and fields["account_id"] is not None:
        if fields["account_id"] != account_id_of(attempt):
            raise ValueError("an attempt's account is fixed when it opens")
    if "note" in fields and fields["note"] is not None:
        attempt["note"] = str(fields["note"])
    if "model_id" in fields:
        attempt["model_id"] = fields["model_id"]
    if fields.get("flags") is not None:
        attempt["flags"] = list(fields["flags"])
    if fields.get("review_later") is not None:
        attempt["review_later"] = bool(fields["review_later"])
    status = fields.get("status")
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        attempt["status"] = status
        if status == "finished" and not attempt.get("finished_at"):
            attempt["finished_at"] = _iso(now)
        # Filing the review answers the flag, so the flag comes off with it —
        # here rather than in the router because it is an invariant of the
        # record ("a reviewed sitting is not waiting to be reviewed"), and a
        # caller that had to remember to clear it is a caller that will forget.
        if status == "reviewed":
            attempt["review_later"] = False
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
    """One attempt, whole: the record, the log, the trades, the aggregates.

    ``whatif.json`` is deliberately *not* here. It is a counterfactual measured
    off this sitting rather than a part of it, it is the largest file in the
    folder, and every caller of ``read`` today wants the sitting — see
    ``read_whatif``.
    """
    d = _require(attempt_id)
    return {
        **_read_json(d / "attempt.json", {}),
        "log": _read_json(d / "log.json", {"orders": [], "closes": [], "brackets": []}),
        "trades": _read_json(d / "trades.json", []),
        "discarded": _read_json(d / "discarded.json", []),
        "summary": _read_json(d / "summary.json", {}),
    }


# --- the cached what-if grid -------------------------------------------------
#
# Re-pricing a sitting under the exit ladder costs about a second and a whole
# tick tape, which is affordable when a day view asks for one and is not when an
# account view asks for thirty. So the grid is measured once, when the sitting
# finishes, and read back from here afterwards.


def read_whatif(attempt_id: str, *, engine_version: int, grid_version: int) -> dict | None:
    """This sitting's cached what-if grid, or None if there is not a usable one.

    **A grid from another engine or another ladder is missing, not usable.** Both
    fingerprints are checked here rather than by the callers, because the failure
    they prevent is silent: a row still keyed ``t25`` whose arithmetic has moved
    would pool into an account's answer looking exactly like a fresh one. Re-price
    it instead — that is what ``demo/whatif_backfill.py`` is for.
    """
    try:
        d = attempt_dir(attempt_id)
    except ValueError:
        return None
    got = _read_json(d / "whatif.json")
    if not isinstance(got, dict):
        return None
    if got.get("engine_version") != engine_version or got.get("grid_version") != grid_version:
        return None
    return got


def write_whatif(attempt_id: str, payload: dict) -> dict:
    """Store a priced grid beside the sitting it was measured off.

    An invalid grid is stored too, and on purpose: ``pick_cfg`` refusing to
    reproduce a sitting is a stable fact about it under this engine, and storing
    the refusal is what stops every account view re-paying a second per rep to be
    told the same thing.
    """
    _write_json(_require(attempt_id) / "whatif.json", payload)
    return payload
