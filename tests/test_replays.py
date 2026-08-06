"""Replay attempts store + router: the Simulator's practice record.

The store is deliberately dumb — the browser computes every number and this
side writes it — so what's worth testing is the part that isn't: ids that name
their own folder (and refuse to name anything else), the repeat count that says
a session was replayed before, do-over bookkeeping, and the lifecycle stamps.

Run directly:  ``.venv/bin/python tests/test_replays.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import replays  # noqa: E402

TAPE = {"n": 900_000, "t0": 1_770_000_000_000, "end": 1_770_050_000_000, "rth_open_ms": 1_770_010_000_000}
PREFS = {"size": 1, "stopTicks": 40, "targetTicks": 80, "orderType": "market"}


def _tmp(fn):
    """Point the store at a scratch directory for one test."""

    def run():
        with tempfile.TemporaryDirectory() as td:
            original = replays.REPLAYS_DIR
            replays.REPLAYS_DIR = Path(td) / "replays"
            try:
                fn()
            finally:
                replays.REPLAYS_DIR = original

    run.__name__ = fn.__name__
    return run


def _open(symbol="NQH5", date="2026-02-03") -> dict:
    return replays.create(
        symbol=symbol,
        root="NQ",
        date=date,
        tz="New York",
        engine_version=1,
        tape=TAPE,
        prefs=PREFS,
        started_ms=TAPE["rth_open_ms"],
    )


def _trade(pnl: float, tid: int = 1) -> dict:
    return {
        "id": tid,
        "side": "long",
        "size": 1,
        "entryPrice": 21000.0,
        "entryMs": TAPE["rth_open_ms"],
        "openType": "market",
        "exitMs": TAPE["rth_open_ms"] + 60_000,
        "exitPrice": 21000.0 + pnl / 20,
        "reason": "target" if pnl > 0 else "stop",
        "pts": pnl / 20,
        "pnl": pnl,
        "r": 1.0 if pnl > 0 else -1.0,
        "rCash": 1.0 if pnl > 0 else -1.0,
    }


LOG = {"orders": [{"id": 1, "type": "market", "side": "long", "ms": 1, "idx": 5}], "closes": [], "brackets": []}


@_tmp
def test_attempt_id_names_its_own_folder():
    a = _open()
    d = replays.attempt_dir(a["id"])
    # The id starts with the session date, and that date is the folder. A lookup
    # is a path join, never a scan.
    assert d.parent.name == "2026-02-03"
    assert d.name == a["id"]
    assert (d / "attempt.json").exists()


@_tmp
def test_bad_ids_cannot_escape_the_replays_dir():
    for bad in ("../../etc/passwd", "2026-02-03_NQH5", "nope", "", "2026-02-03/../../x"):
        try:
            replays.attempt_dir(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted as an attempt id")


@_tmp
def test_repeat_index_counts_prior_sittings_on_that_session():
    first = _open()
    second = _open()
    other_day = _open(date="2026-02-04")
    other_sym = _open(symbol="NQM5")
    assert first["repeat_index"] == 0
    # A day you have already seen the end of is not a cold read.
    assert second["repeat_index"] == 1
    assert other_day["repeat_index"] == 0
    assert other_sym["repeat_index"] == 0


@_tmp
def test_save_writes_the_three_tiers_and_summary_is_stored_verbatim():
    a = _open()
    summary = {"trades": 2, "net_usd": 130.0, "win_rate": 50.0, "made_up": "kept anyway"}
    replays.save(a["id"], log=LOG, trades=[_trade(250), _trade(-120, 2)], summary=summary)

    got = replays.read(a["id"])
    assert got["log"] == LOG
    assert len(got["trades"]) == 2
    # Never recomputed here: one fill engine, and it lives in the browser.
    assert got["summary"] == summary
    assert got["status"] == "active"


@_tmp
def test_discarded_file_only_exists_when_a_rewind_erased_something():
    a = _open()
    d = replays.attempt_dir(a["id"])
    replays.save(a["id"], log=LOG, trades=[_trade(50)], summary={})
    assert not (d / "discarded.json").exists()

    replays.save(
        a["id"],
        log=LOG,
        trades=[_trade(50)],
        summary={},
        discarded=[_trade(-300, 9)],
        rewinds=[{"from_ms": 100, "to_ms": 50, "dropped": 1}],
    )
    rec = replays.read(a["id"])
    assert (d / "discarded.json").exists()
    assert rec["discarded_trades"] == 1
    assert len(rec["rewinds"]) == 1
    assert rec["discarded"][0]["pnl"] == -300

    # And it goes away again if the attempt is re-saved without one.
    replays.save(a["id"], log=LOG, trades=[_trade(50)], summary={}, discarded=[])
    assert not (d / "discarded.json").exists()
    assert replays.read(a["id"])["discarded_trades"] == 0


@_tmp
def test_finishing_stamps_once_and_reopening_keeps_the_first_stamp():
    a = _open()
    replays.save(a["id"], log=LOG, trades=[_trade(10)], summary={}, status="finished")
    first = replays.read(a["id"])["finished_at"]
    assert first is not None

    # Rewound and traded on: back to active, but the moment it first ran out of
    # tape is not rewritten.
    replays.save(a["id"], log=LOG, trades=[_trade(10), _trade(20, 2)], summary={}, status="active")
    reopened = replays.read(a["id"])
    assert reopened["status"] == "active"
    assert reopened["finished_at"] == first


@_tmp
def test_patch_touches_the_annotation_and_never_the_trades():
    a = _open()
    replays.save(a["id"], log=LOG, trades=[_trade(75)], summary={"trades": 1})
    replays.patch(a["id"], note="chased the open", model_id=3, status="finished")
    got = replays.read(a["id"])
    assert got["note"] == "chased the open"
    assert got["model_id"] == 3
    assert got["status"] == "finished"
    assert len(got["trades"]) == 1 and got["trades"][0]["pnl"] == 75


@_tmp
def test_listing_is_newest_first_and_inlines_the_summary():
    a = _open(date="2026-02-03")
    b = _open(date="2026-02-05")
    replays.save(a["id"], log=LOG, trades=[], summary={"net_usd": 10.0})
    replays.save(b["id"], log=LOG, trades=[], summary={"net_usd": -5.0})

    rows = replays.list_attempts()
    assert len(rows) == 2
    # Newest sitting first — the order the history table draws in.
    assert rows[0]["created_at"] >= rows[1]["created_at"]
    assert {r["summary"]["net_usd"] for r in rows} == {10.0, -5.0}

    only = replays.list_attempts(date="2026-02-05")
    assert [r["id"] for r in only] == [b["id"]]
    assert replays.list_attempts(status="finished") == []


@_tmp
def test_delete_removes_the_attempt_and_prunes_the_empty_day():
    a = _open()
    day = replays.attempt_dir(a["id"]).parent
    replays.save(a["id"], log=LOG, trades=[_trade(1)], summary={})
    replays.delete(a["id"])
    assert not replays.attempt_dir(a["id"]).exists()
    assert not day.exists()


@_tmp
def test_writes_to_a_missing_attempt_raise_rather_than_creating_one():
    ghost = "2026-02-03_NQH5_20260101T000000Z"
    for call in (
        lambda: replays.save(ghost, log=LOG, trades=[], summary={}),
        lambda: replays.patch(ghost, note="x"),
        lambda: replays.read(ghost),
    ):
        try:
            call()
        except FileNotFoundError:
            continue
        raise AssertionError("a missing attempt was written to")


@_tmp
def test_router_round_trip():
    from api.routers import replays as router

    created = router.create_replay(
        router.CreateIn(
            symbol="NQH5",
            root="NQ",
            date="2026-02-03",
            tz="New York",
            engine_version=1,
            tape=TAPE,
            prefs=PREFS,
            started_ms=TAPE["rth_open_ms"],
        )
    )
    router.save_replay(
        created["id"],
        router.SaveIn(log=LOG, trades=[_trade(200)], summary={"trades": 1, "net_usd": 200.0}, status="finished"),
    )
    # Called directly, so the Query(...) defaults have to be passed by hand —
    # the same wrinkle tests/helpers.py works around for resolve_scope.
    listing = dict(limit=500, status=None, symbol=None, date=None)
    listed = router.list_replays(**listing)["attempts"]
    assert len(listed) == 1 and listed[0]["status"] == "finished"
    assert router.get_replay(created["id"])["summary"]["net_usd"] == 200.0
    router.delete_replay(created["id"])
    assert router.list_replays(**listing)["attempts"] == []


@_tmp
def test_router_rejects_a_bad_date_and_a_bad_id():
    from fastapi import HTTPException

    from api.routers import replays as router

    try:
        router.create_replay(
            router.CreateIn(
                symbol="NQH5", root="NQ", date="03/02/2026", tz="New York",
                engine_version=1, tape=TAPE, prefs=PREFS, started_ms=0,
            )
        )
    except HTTPException as e:
        assert e.status_code == 400
    else:
        raise AssertionError("a malformed session date was accepted")

    try:
        router.get_replay("../../secrets")
    except HTTPException as e:
        assert e.status_code == 400
    else:
        raise AssertionError("a traversal id was accepted")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
