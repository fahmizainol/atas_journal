"""Replay attempts — the Simulator's practice record.

Thin by design. The fill engine lives in the browser (frontend/src/lib/
replaySim.ts) and so does every number derived from it, so this router
validates the shape of what it is handed and writes it to disk; it never
recomputes a trade or a statistic. One engine, so a stored attempt can't
disagree with the replay that produced it.

The attempt itself — log, trades, summary, rewinds — lives in data/replays/ and
still does. What changed (2026-08-08) is that its **trades are also mirrored
into journal.db**, under the `replay` account with the sitting tagged
``mode='replay'``, exactly as `/charts/live`'s paper trades are. The reason is
the one journal.live.booking gives: there is no `trades` table, so a row in
`atas_journal` reaches the Trades page, the Calendar, statistics, notes, setups
and video bookmarks with nothing else to build, and the mode tag is what keeps
practice out of the real-money numbers.

The mirror is a *projection of the stored attempt*, not a second record: every
autosave replaces the source file's rows, so a rewind that erased a fill erases
its journal row too, and deleting an attempt withdraws both. Nothing here is
recomputed — the row builder is handed the same trades that go to trades.json.

A booking failure never fails the save. The attempt on disk is the thing worth
keeping; the journal copy can be rebuilt from it, and losing a sitting because a
mirror write went wrong would be the wrong trade to make.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from journal import db, replays
from journal.live import booking as bookmod

from .. import deps

router = APIRouter()


def _mirror(attempt: dict, trades: list) -> int | None:
    """Mirror an attempt's trades into the journal. None if it could not be.

    Best-effort by contract — see the module docstring. The count is returned so
    the client can see that the two records agree, and a None says the write
    failed rather than that there was nothing to write.
    """
    try:
        conn = deps.get_conn()
        with deps.db_lock():
            return bookmod.book_attempt(conn, attempt=attempt, trades=trades)
    except Exception as e:  # noqa: BLE001 — never fail the save over the mirror
        print(f"[replays] journal mirror failed for {attempt.get('id')}: {e}")
        return None


class CreateIn(BaseModel):
    symbol: str
    root: str = ""
    date: str
    tz: str
    engine_version: int
    # Enough of the tape to know later whether it is still the same tape:
    # {n, t0, end, rth_open_ms}. Stored opaquely — the rebuild that reads it
    # lives in the client.
    tape: dict = Field(default_factory=dict)
    # The ticket the attempt was traded with, plus speed/start/blind/timeframe.
    prefs: dict = Field(default_factory=dict)
    # Same replay clock as SaveIn.clock_ms — integral today because an attempt
    # is armed on a fresh session, fractional the moment it isn't.
    started_ms: float
    model_id: int | None = None


class SaveIn(BaseModel):
    log: dict
    trades: list = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    discarded: list = Field(default_factory=list)
    rewinds: list | None = None
    # The replay clock advances by `elapsed × speed` inside an animation frame,
    # so it arrives fractional. Accept it as sent and let the store round it —
    # sub-millisecond precision is meaningless here, but rejecting it fails the
    # save.
    clock_ms: float | None = None
    status: str | None = None


class PatchIn(BaseModel):
    note: str | None = None
    model_id: int | None = None
    status: str | None = None


@router.post("/replays")
def create_replay(body: CreateIn) -> dict:
    """Open an attempt. The client calls this on the first fill — a session you
    only watched leaves no record."""
    try:
        return replays.create(
            symbol=body.symbol,
            root=body.root,
            date=body.date,
            tz=body.tz,
            engine_version=body.engine_version,
            tape=body.tape,
            prefs=body.prefs,
            started_ms=body.started_ms,
            model_id=body.model_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/replays/journal/backfill")
def backfill_journal() -> dict:
    """Mirror every attempt already on disk into the journal.

    The mirror rides on ``save``, so attempts recorded before it existed have no
    journal rows and would never get any — a practice history that starts the
    day the feature shipped. This walks the store once and books them.

    Safe to run repeatedly: each attempt replaces its own source file's rows, so
    a second run is a no-op rather than a doubling. Declared above the
    ``/replays/{attempt_id}`` routes to keep it that way if one ever takes a
    POST — a path parameter would otherwise swallow `journal`.
    """
    written = attempts = failed = 0
    for row in replays.list_attempts(limit=5000):
        try:
            trades = replays.read(row["id"]).get("trades") or []
        except (ValueError, FileNotFoundError):
            failed += 1
            continue
        n = _mirror(row, trades)
        if n is None:
            failed += 1
            continue
        attempts += 1
        written += n
    return {"attempts": attempts, "trades": written, "failed": failed}


@router.get("/replays")
def list_replays(
    limit: int = Query(500, ge=1, le=5000),
    status: str | None = Query(None),
    symbol: str | None = Query(None),
    date: str | None = Query(None),
) -> dict:
    return {
        "attempts": replays.list_attempts(
            limit=limit, status=status, symbol=symbol, date=date
        )
    }


@router.get("/replays/{attempt_id}")
def get_replay(attempt_id: str) -> dict:
    try:
        return replays.read(attempt_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"No attempt {attempt_id}") from e


@router.put("/replays/{attempt_id}")
def save_replay(attempt_id: str, body: SaveIn) -> dict:
    """Autosave. Debounced by the client, so this lands every few seconds while
    a replay is being traded rather than once at the end.

    The journal mirror rides on this rather than on `finish`, for the same
    reason the autosave exists at all: a sitting that ends by closing the tab
    never finishes, and it should still be in the journal.
    """
    try:
        attempt = replays.save(
            attempt_id,
            log=body.log,
            trades=body.trades,
            summary=body.summary,
            discarded=body.discarded,
            rewinds=body.rewinds,
            clock_ms=body.clock_ms,
            status=body.status,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"No attempt {attempt_id}") from e
    return {**attempt, "journaled": _mirror(attempt, body.trades)}


@router.patch("/replays/{attempt_id}")
def patch_replay(attempt_id: str, body: PatchIn) -> dict:
    try:
        attempt = replays.patch(
            attempt_id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"No attempt {attempt_id}") from e

    # The note and the model are the two fields a sitting carries in both
    # records. Pushed across so the Trades page shows what the history page
    # shows; the session row may not exist yet (an attempt can be annotated
    # before it has journalled a trade), and update_session is a no-op then.
    fields = body.model_dump(exclude_unset=True)
    if "note" in fields or "model_id" in fields:
        try:
            conn = deps.get_conn()
            with deps.db_lock():
                db.update_session(
                    conn,
                    bookmod.source_file_for_attempt(attempt_id),
                    note=attempt.get("note"),
                    model_id=attempt.get("model_id"),
                )
        except Exception as e:  # noqa: BLE001 — the attempt is already patched
            print(f"[replays] session patch failed for {attempt_id}: {e}")
    return attempt


@router.delete("/replays/{attempt_id}")
def delete_replay(attempt_id: str) -> dict:
    try:
        replays.delete(attempt_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # Withdraw the mirror too. "Delete the folder and it is gone" is the promise
    # journal.replays makes, and a journal row that outlived its attempt would
    # be a trade with no record of how it was taken.
    try:
        conn = deps.get_conn()
        with deps.db_lock():
            bookmod.unbook_attempt(conn, attempt_id)
    except Exception as e:  # noqa: BLE001
        print(f"[replays] journal withdraw failed for {attempt_id}: {e}")
    return {"ok": True}
