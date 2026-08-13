"""The replay account: the equity walk, the trailing floor, and the death.

There is no stored balance — every number here is derived by walking the attempt
files — so what is worth testing is the walk itself, and specifically the two
things about it that are easy to get subtly wrong and impossible to notice:

  - the floor follows **day closes**, not new highs, so an intraday spike must
    not move it (and once a day closes at or above 52,100 it must lock at
    50,100 and stay there whatever happens next);
  - the day the daily loss is counted against is the **New York** day, so two
    sittings on either side of midnight UTC are one trading day.

Plus the boundary the whole feature depends on for not being retroactive:
attempts that pre-date the first epoch are never counted.

Run directly:  ``.venv/bin/python tests/test_replay_account.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal import replay_account as acct  # noqa: E402
from journal import replays  # noqa: E402

TAPE = {"n": 900_000, "t0": 1_770_000_000_000, "end": 1_770_050_000_000, "rth_open_ms": 1_770_010_000_000}
PREFS = {"size": 1, "stopTicks": 50, "targetTicks": 120, "orderType": "market"}
LOG = {"orders": [], "closes": [], "brackets": []}


def _tmp(fn):
    """Point the store at a scratch directory for one test — test_replays.py's
    pattern, and it covers account.json too because that lives in the same dir."""

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


def _t(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _trade(pnl: float, tid: int = 1) -> dict:
    return {
        "id": tid, "side": "long", "size": 1, "entryPrice": 21_000.0,
        "entryMs": TAPE["rth_open_ms"], "openType": "market",
        "exitMs": TAPE["rth_open_ms"] + 60_000, "exitPrice": 21_000.0 + pnl / 20,
        "reason": "target" if pnl > 0 else "stop", "pts": pnl / 20, "pnl": pnl,
        "r": 1.0, "rCash": 1.0,
    }


def _sitting(
    created_at: str,
    net: float | None,
    *,
    status: str = "finished",
    trades: list | None = None,
    date: str = "2026-02-03",
) -> dict:
    """One settled sitting, stamped when we say rather than when it ran.

    ``created_at`` is written back over the store's own stamp because every gate
    and every grouping in the account reads it, and a test that could only make
    attempts "now" could not exercise a single one of them.
    """
    a = replays.create(
        symbol="NQH5", root="NQ", date=date, tz="New York", engine_version=1,
        tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
    )
    replays.save(
        a["id"],
        log=LOG,
        trades=trades or [],
        summary={} if net is None else {"net_usd": net},
        status=status,
    )
    d = replays.attempt_dir(a["id"])
    rec = replays._read_json(d / "attempt.json", {})
    rec["created_at"] = created_at
    rec["updated_at"] = created_at
    if status != "active":
        rec["finished_at"] = created_at
    replays._write_json(d / "attempt.json", rec)
    return rec


def _epoch(started_at: str, cause: str | None = None) -> None:
    e: dict = {"started_at": started_at}
    if cause:
        e["cause_of_death"] = cause
    acct.save_state({"epochs": [e]})


# --- the walk ---------------------------------------------------------------


@_tmp
def test_equity_is_the_sum_of_settled_attempts_and_nothing_else():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 400.0)
    _sitting("2026-02-03T15:00:00Z", -150.0)
    # Still running: not settled, so not counted. It is the sweep's job to make
    # sure this state cannot be used to park a loss.
    _sitting("2026-02-03T16:00:00Z", -900.0, status="active")

    v = acct.derive(now=_t("2026-02-03T17:00:00Z"))
    assert v["equity"] == 50_250.0
    assert v["epoch"]["sittings"] == 2
    assert v["epoch"]["net"] == 250.0
    assert v["status"] == "live"


@_tmp
def test_net_falls_back_to_the_trades_when_there_is_no_summary():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", None, trades=[_trade(-250), _trade(80, 2)])
    v = acct.derive(now=_t("2026-02-03T17:00:00Z"))
    assert v["equity"] == 49_830.0


@_tmp
def test_the_floor_follows_day_closes_and_never_an_intraday_high():
    _epoch("2026-02-03T00:00:00Z")
    # One day: up 3,000 and then back down 1,000. The high was 53,000, the close
    # was 52,000, and the floor is only allowed to have seen the close.
    _sitting("2026-02-03T14:00:00Z", 3_000.0)
    _sitting("2026-02-03T18:00:00Z", -1_000.0)

    # Still that day: nothing has closed, so the floor is still the opening one.
    same_day = acct.derive(now=_t("2026-02-03T20:00:00Z"))
    assert same_day["equity"] == 52_000.0
    assert same_day["floor"] == 48_000.0

    # The day is over. 52,000 − 2,000, not 53,000 − 2,000.
    next_day = acct.derive(now=_t("2026-02-04T15:00:00Z"))
    assert next_day["peak_close"] == 52_000.0
    assert next_day["floor"] == 50_000.0


@_tmp
def test_the_floor_locks_at_50100_once_a_day_closes_over_the_trail_cap():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 2_500.0, date="2026-02-03")   # closes 52,500
    _sitting("2026-02-04T14:00:00Z", -1_000.0, date="2026-02-04")  # closes 51,500
    _sitting("2026-02-05T14:00:00Z", -800.0, date="2026-02-05")    # closes 50,700

    v = acct.derive(now=_t("2026-02-06T15:00:00Z"))
    assert v["peak_close"] == 52_500.0
    # Capped at the Initial Trail Balance, so it stops following upward...
    assert v["floor"] == 50_100.0
    # ...and it does not come back down with the equity either.
    assert v["equity"] == 50_700.0


# --- the death --------------------------------------------------------------


@_tmp
def test_closing_at_or_under_the_floor_blows_the_account():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -2_100.0)

    v = acct.derive(now=_t("2026-02-03T15:00:00Z"))
    assert v["equity"] == 47_900.0
    assert v["status"] == "blown"
    assert v["last_death"]["floor"] == 48_000.0
    assert v["last_death"]["cause_of_death"] is None
    # The timeout runs from the death, not from the write-up.
    assert v["cooldown_until"] == "2026-02-04T14:00:00Z"
    assert v["can_reset"] is False


@_tmp
def test_blown_becomes_cooldown_when_the_cause_is_written_then_resettable():
    _epoch("2026-02-03T00:00:00Z", cause="held a loser through the number")
    _sitting("2026-02-03T14:00:00Z", -2_100.0)

    written = acct.derive(now=_t("2026-02-03T15:00:00Z"))
    assert written["status"] == "cooldown"
    assert written["can_reset"] is False

    # 24h after the death, and not before.
    still = acct.derive(now=_t("2026-02-04T13:59:00Z"))
    assert still["status"] == "cooldown"
    out = acct.derive(now=_t("2026-02-04T14:01:00Z"))
    assert out["status"] == "can_reset"
    assert out["last_death"]["cause_of_death"] == "held a loser through the number"


@_tmp
def test_a_reset_epoch_starts_clean_and_still_carries_the_last_cause():
    _epoch("2026-02-03T00:00:00Z", cause="revenge traded the recovery")
    _sitting("2026-02-03T14:00:00Z", -2_100.0)

    minted = acct.ensure_epoch(now=_t("2026-02-04T15:00:00Z"))
    assert minted["started_at"] == "2026-02-04T15:00:00Z"
    assert len(acct.load_state()["epochs"]) == 2

    v = acct.derive(now=_t("2026-02-04T15:00:00Z"))
    assert v["status"] == "live"
    assert v["equity"] == 50_000.0
    assert v["epoch"]["index"] == 1 and v["epoch"]["sittings"] == 0
    # The dead account is still readable from inside the live one — that is what
    # "pinned into the next epoch" means.
    assert v["last_death"]["cause_of_death"] == "revenge traded the recovery"
    assert v["last_death"]["epoch"] == 0


@_tmp
def test_ensure_epoch_does_not_mint_over_a_living_account():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -100.0)
    acct.ensure_epoch(now=_t("2026-02-03T18:00:00Z"))
    assert len(acct.load_state()["epochs"]) == 1


# --- the day ----------------------------------------------------------------


@_tmp
def test_the_daily_loss_is_counted_against_the_new_york_day():
    _epoch("2026-02-03T00:00:00Z")
    # 18:00 and 21:00 New York on the 3rd — the second one is already the 4th in
    # UTC, and grouping by UTC would hand it a fresh $1,200.
    _sitting("2026-02-03T23:00:00Z", -500.0)
    _sitting("2026-02-04T02:00:00Z", -300.0)

    v = acct.derive(now=_t("2026-02-04T03:00:00Z"))
    assert v["day_net"] == -800.0
    assert v["day_loss_remaining"] == 400.0

    # And the next New York day starts over.
    tomorrow = acct.derive(now=_t("2026-02-04T16:00:00Z"))
    assert tomorrow["day_net"] == 0.0
    assert tomorrow["day_loss_remaining"] == 1_200.0


@_tmp
def test_a_green_day_does_not_bank_extra_room():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 900.0)
    v = acct.derive(now=_t("2026-02-03T17:00:00Z"))
    assert v["day_net"] == 900.0
    assert v["day_loss_remaining"] == 1_200.0
    assert v["target_remaining"] == 2_100.0


# --- the gates' inputs ------------------------------------------------------


@_tmp
def test_next_sitting_is_an_hour_after_the_last_one_opened():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -50.0)

    early = acct.derive(now=_t("2026-02-03T14:30:00Z"))
    assert early["next_sitting_at"] == "2026-02-03T15:00:00Z"
    late = acct.derive(now=_t("2026-02-03T15:30:00Z"))
    assert late["next_sitting_at"] is None


@_tmp
def test_the_sweep_settles_an_abandoned_sitting_so_it_cannot_hide_a_loss():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -700.0, status="active")

    now = _t("2026-02-03T14:30:00Z")
    assert acct.sweep_stale_actives(now=now) == []
    assert acct.derive(now=now)["equity"] == 50_000.0

    later = _t("2026-02-03T16:00:00Z")
    assert len(acct.sweep_stale_actives(now=later)) == 1
    assert acct.derive(now=later)["equity"] == 49_300.0
    assert acct.derive(now=later)["epoch"]["sittings"] == 1


@_tmp
def test_attempts_from_before_the_first_epoch_are_never_counted():
    # Months of practice already on disk, and no account.json. Minting epoch 0
    # must not open it $30,000 in the hole.
    for i in range(4):
        _sitting(f"2026-01-0{i + 1}T14:00:00Z", -900.0, date=f"2026-01-0{i + 1}")

    now = _t("2026-02-03T15:00:00Z")
    fresh = acct.derive(now=now)
    assert fresh["equity"] == 50_000.0
    assert fresh["epoch"]["sittings"] == 0
    assert fresh["status"] == "live"

    acct.ensure_epoch(now=now)
    assert acct.derive(now=now)["equity"] == 50_000.0


# --- the flags --------------------------------------------------------------
# Levels are passed in rather than read: `routing.settings()` opens journal.db,
# and a flag that depended on this instance's configured numbers would pass or
# fail on whoever ran it.

GUARDS = None  # filled in below, once routing is importable


def _guards():
    from journal.live.routing import Guards

    return Guards()


def _paced(pnl: float, tid: int, *, at_s: int, held_s: int = 300) -> dict:
    """A trade that is fine — slow enough, small enough, on time."""
    t = _trade(pnl, tid)
    t["entryMs"] = TAPE["rth_open_ms"] + at_s * 1000
    t["exitMs"] = t["entryMs"] + held_s * 1000
    return t


@_tmp
def test_a_fast_trade_is_flagged_and_a_paced_one_is_not():
    flags = acct.flags_for(
        {}, [_paced(-100, 1, at_s=0, held_s=12), _paced(-100, 2, at_s=3600)], guards=_guards()
    )
    assert len(flags) == 1
    assert flags[0]["trade_id"] == 1
    assert flags[0]["reasons"] == ["resolved in 12s"]
    assert flags[0]["ms"] == TAPE["rth_open_ms"]


@_tmp
def test_trading_in_the_hole_is_flagged_at_the_trade_that_did_it():
    g = _guards()  # slow_down_at 300
    # Two ordinary losses that dig the hole, then one taken while standing in it.
    trades = [_paced(-160, 1, at_s=0), _paced(-160, 2, at_s=600), _paced(-40, 3, at_s=3600)]
    flags = acct.flags_for({}, trades, guards=g)
    # Neither of the first two is flagged: what is asked is what was known when
    # the decision was made, and the day was inside the level both times.
    assert [f["trade_id"] for f in flags] == [3]
    assert "already $320 down" in flags[0]["reasons"][0]


@_tmp
def test_a_loss_over_the_risk_ceiling_is_flagged():
    g = _guards()  # max_risk_usd 250
    flags = acct.flags_for({}, [_paced(-400, 1, at_s=0)], guards=g)
    assert flags[0]["reasons"] == ["lost $400 against a $250 ceiling"]
    # A win of the same size is not a flag: the ceiling is on what a stop is
    # allowed to cost, and a target that ran is not a rule being broken.
    assert acct.flags_for({}, [_paced(400, 1, at_s=0)], guards=g) == []


@_tmp
def test_one_trade_earns_one_flag_however_many_reasons_it_has():
    g = _guards()
    trades = [
        _paced(-160, 1, at_s=0),
        _paced(-160, 2, at_s=600),
        # Fast, in the hole, and over the ceiling — one thing to answer for.
        _paced(-400, 3, at_s=3600, held_s=8),
    ]
    flags = acct.flags_for({}, trades, guards=g)
    assert [f["trade_id"] for f in flags] == [3]
    assert len(flags[0]["reasons"]) == 3


@_tmp
def test_rewinds_are_flagged_from_the_attempt_record():
    flags = acct.flags_for(
        {"rewinds": [{"from_ms": 1_770_020_000_000, "to_ms": 1_770_015_000_000, "dropped": 2}]},
        [],
        guards=_guards(),
    )
    assert len(flags) == 1
    assert flags[0]["kind"] == "rewind"
    assert flags[0]["ms"] == 1_770_020_000_000
    assert "2 trades un-happened" in flags[0]["label"]


@_tmp
def test_flags_are_worst_first():
    g = _guards()
    trades = [_paced(-300, 1, at_s=0, held_s=5), _paced(-900, 2, at_s=3600, held_s=5)]
    assert [f["trade_id"] for f in acct.flags_for({}, trades, guards=g)] == [2, 1]


@_tmp
def test_a_review_is_only_complete_when_every_flag_has_a_verdict():
    flags = [{"kind": "trade"}, {"kind": "trade"}]
    assert acct.review_is_complete([], None) is True
    assert acct.review_is_complete(flags, None) is False
    assert acct.review_is_complete(flags, {"items": [{"flag_idx": 0, "verdict": "leak"}]}) is False
    assert acct.review_is_complete(
        flags,
        {"items": [{"flag_idx": 0, "verdict": "leak"}, {"flag_idx": 1, "verdict": "justified"}]},
    ) is True
    # An item with no verdict is not an answer.
    assert acct.review_is_complete(
        flags,
        {"items": [{"flag_idx": 0, "verdict": "leak"}, {"flag_idx": 1, "note": "hmm"}]},
    ) is False


# --- the lifecycle, through the router --------------------------------------


def _finish(trades: list, rewinds: list | None = None) -> dict:
    """Open a sitting through the router and run it out of tape."""
    from api.routers import replays as router

    created = router.create_replay(
        router.CreateIn(
            symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
            engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
        )
    )
    return router.save_replay(
        created["id"],
        router.SaveIn(
            log=LOG,
            trades=trades,
            summary={"net_usd": sum(t["pnl"] for t in trades)},
            rewinds=rewinds,
            status="finished",
        ),
    )


@_tmp
def test_a_clean_sitting_passes_straight_through_to_reviewed():
    done = _finish([_paced(120, 1, at_s=0)])
    assert done["flags"] == []
    # No ceremony over a session with nothing wrong with it — the next sitting
    # should not wait on a formality.
    assert done["status"] == "reviewed"


@_tmp
def test_a_flagged_sitting_stops_at_finished_and_blocks_until_answered():
    from fastapi import HTTPException

    from api.routers import replays as router

    _epoch("2026-01-01T00:00:00Z")
    done = _finish([_paced(-400, 1, at_s=0, held_s=6)])
    assert done["status"] == "finished"
    assert len(done["flags"]) == 1

    blocked = acct.derive()
    assert blocked["review_block"]["attempt_id"] == done["id"]
    assert len(blocked["review_block"]["flags"]) == 1

    # A verdict short is not a review.
    try:
        router.patch_replay(done["id"], router.PatchIn(status="reviewed"))
    except HTTPException as e:
        assert e.status_code == 409
    else:
        raise AssertionError("an unanswered sitting was accepted as reviewed")

    filed = router.patch_replay(
        done["id"],
        router.PatchIn(
            status="reviewed",
            review=router.ReviewIn(
                items=[router.ReviewItem(flag_idx=0, verdict="leak", note="chased it back")]
            ),
        ),
    )
    assert filed["status"] == "reviewed"
    assert filed["review"]["items"][0]["note"] == "chased it back"
    # Stamped for us if the client did not.
    assert filed["review"]["reviewed_at"].endswith("Z")
    assert acct.derive()["review_block"] is None


@_tmp
def test_trading_on_after_a_review_withdraws_it():
    from api.routers import replays as router

    done = _finish([_paced(-400, 1, at_s=0, held_s=6)])
    router.patch_replay(
        done["id"],
        router.PatchIn(
            status="reviewed",
            review=router.ReviewIn(items=[router.ReviewItem(flag_idx=0, verdict="justified")]),
        ),
    )
    # Rewound and traded on: the verdicts were given about trades that are no
    # longer the trades in the file.
    router.save_replay(done["id"], router.SaveIn(log=LOG, trades=[], summary={}, status="active"))
    reopened = replays.read(done["id"])
    assert "review" not in reopened and "flags" not in reopened

    again = router.save_replay(
        done["id"],
        router.SaveIn(
            log=LOG, trades=[_paced(-400, 1, at_s=0, held_s=6)], summary={}, status="finished"
        ),
    )
    assert again["status"] == "finished" and len(again["flags"]) == 1


# --- the gate ---------------------------------------------------------------


@_tmp
def test_each_refusal_has_its_own_code_and_they_are_ordered_by_severity():
    _epoch("2026-02-03T00:00:00Z")
    assert acct.refusal(now=_t("2026-02-03T12:00:00Z")) is None

    # An hour between sittings.
    _sitting("2026-02-03T14:00:00Z", -50.0)
    hour = acct.refusal(now=_t("2026-02-03T14:20:00Z"))
    assert hour["code"] == "hour" and hour["until"] == "2026-02-03T15:00:00Z"
    assert acct.refusal(now=_t("2026-02-03T15:20:00Z")) is None


@_tmp
def test_an_unreviewed_sitting_outranks_the_hour_gate():
    from api.routers import replays as router

    _epoch("2026-01-01T00:00:00Z")
    _finish([_paced(-400, 1, at_s=0, held_s=6)])
    no = acct.refusal()
    # Not "wait an hour" — the hour has nothing to do with why this is refused.
    assert no["code"] == "review"

    done = acct.derive()["review_block"]["attempt_id"]
    router.patch_replay(
        done,
        router.PatchIn(
            status="reviewed",
            review=router.ReviewIn(items=[router.ReviewItem(flag_idx=0, verdict="leak")]),
        ),
    )
    assert acct.refusal()["code"] == "hour"


@_tmp
def test_a_dead_account_is_never_told_to_wait_an_hour():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -2_100.0)

    blown = acct.refusal(now=_t("2026-02-03T14:10:00Z"))
    assert blown["code"] == "blown" and blown["until"] is None

    acct.write_cause("held a loser through the number", now=_t("2026-02-03T14:20:00Z"))
    cool = acct.refusal(now=_t("2026-02-03T14:30:00Z"))
    assert cool["code"] == "cooldown" and cool["until"] == "2026-02-04T14:00:00Z"

    # Timeout served: not a refusal at all. The create mints the next account.
    assert acct.refusal(now=_t("2026-02-04T15:00:00Z")) is None


@_tmp
def test_the_hour_gate_counts_a_sitting_that_is_still_open():
    _epoch("2026-02-03T00:00:00Z")
    # A second tab must not be told the gate is clear because the sitting in the
    # first one has not been written off yet.
    _sitting("2026-02-03T14:00:00Z", None, status="active")
    no = acct.refusal(now=_t("2026-02-03T14:10:00Z"))
    assert no["code"] == "hour" and no["until"] == "2026-02-03T15:00:00Z"


@_tmp
def test_the_create_route_refuses_with_the_code_and_the_deadline():
    from fastapi import HTTPException

    from api.routers import replays as router

    _epoch(acct._iso(datetime.now(timezone.utc) - timedelta(days=1)))
    _sitting(acct._iso(datetime.now(timezone.utc) - timedelta(minutes=5)), -50.0)

    try:
        router.create_replay(
            router.CreateIn(
                symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
                engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
            )
        )
    except HTTPException as e:
        assert e.status_code == 409
        assert e.detail["code"] == "hour"
        assert e.detail["until"].endswith("Z")
    else:
        raise AssertionError("a sitting opened inside the hour gate")


@_tmp
def test_a_create_after_the_cooldown_opens_a_fresh_account():
    from api.routers import replays as router

    died = datetime.now(timezone.utc) - timedelta(days=2)
    _epoch(acct._iso(died - timedelta(hours=1)), cause="revenge traded the recovery")
    _sitting(acct._iso(died), -2_100.0)
    assert acct.derive()["status"] == "can_reset"

    router.create_replay(
        router.CreateIn(
            symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
            engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
        )
    )
    v = acct.derive()
    assert v["status"] == "live"
    assert v["epoch"]["index"] == 1
    assert v["equity"] == 50_000.0
    # And the account that died is still readable from inside the new one.
    assert v["last_death"]["cause_of_death"] == "revenge traded the recovery"


@_tmp
def test_a_cause_cannot_be_written_for_a_living_account():
    from fastapi import HTTPException

    from api.routers import replays as router

    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -100.0)
    for body in (router.CauseIn(cause_of_death="nothing happened"), router.CauseIn(cause_of_death="  ")):
        try:
            router.write_cause(body)
        except HTTPException as e:
            assert e.status_code == 409
        else:
            raise AssertionError("a cause was accepted with no death behind it")


@_tmp
def test_pre_feature_attempts_do_not_gate_the_first_sitting():
    from api.routers import replays as router

    # Practice from before any of this existed, including one an hour ago.
    for i in range(3):
        _sitting(f"2026-01-0{i + 1}T14:00:00Z", -900.0, date=f"2026-01-0{i + 1}")
    _sitting(acct._iso(datetime.now(timezone.utc) - timedelta(minutes=5)), -900.0)

    assert acct.refusal() is None
    created = router.create_replay(
        router.CreateIn(
            symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
            engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
        )
    )
    assert created["status"] == "active"
    assert acct.derive()["equity"] == 50_000.0


# --- the route --------------------------------------------------------------


@_tmp
def test_router_sweeps_before_it_answers():
    from api.routers import replays as router

    _epoch(acct._iso(datetime.now(timezone.utc) - timedelta(days=1)))
    stale = _sitting(
        acct._iso(datetime.now(timezone.utc) - timedelta(hours=3)), -400.0, status="active"
    )

    v = router.get_account()
    assert v["equity"] == 49_600.0
    assert replays.read(stale["id"])["status"] == "abandoned"
    assert v["caps"] == {"minis": 4, "micros": 40}
    assert v["now"].endswith("Z")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
