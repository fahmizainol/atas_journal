"""Replay attempts — the Simulator's practice record.

Thin by design. The fill engine lives in the browser (frontend/src/lib/
replaySim.ts) and so does every number derived from it, so this router
validates the shape of what it is handed and writes it to disk; it never
recomputes a trade or a statistic. One engine, so a stored attempt can't
disagree with the replay that produced it.

Writes go to data/replays/ and never to journal.db — see journal.replays for
why a synthetic fill stays out of the real trading record.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from journal import replays

router = APIRouter()


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
    a replay is being traded rather than once at the end."""
    try:
        return replays.save(
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


@router.patch("/replays/{attempt_id}")
def patch_replay(attempt_id: str, body: PatchIn) -> dict:
    try:
        return replays.patch(
            attempt_id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"No attempt {attempt_id}") from e


@router.delete("/replays/{attempt_id}")
def delete_replay(attempt_id: str) -> dict:
    try:
        replays.delete(attempt_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
