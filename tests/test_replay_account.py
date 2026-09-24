"""The replay account: the equity walk, the trailing floor, and the death.

There is no stored balance — every number here is derived by walking the attempt
files — so what is worth testing is the walk itself, and specifically the two
things about it that are easy to get subtly wrong and impossible to notice:

  - the floor follows **day closes**, not new highs, so an intraday spike must
    not move it (and once a day closes at or above 52,100 it must lock at
    50,100 and stay there whatever happens next);
  - a day is the **tape** day — the session replayed, not the evening spent
    replaying it. Four tapes in one evening are four days and spend four daily
    allowances; two sittings on one tape are one day however far apart they ran.
    Getting this wrong is not a rounding error: taken from the wall clock, the
    floor never saw a day close at all and could not move.
  - a life ends **two** ways, and the two are read off different things. The
    floor is read off the booked path, because a breach is instant; the profit
    target is read off the settled balance, because that is the close a real
    eval clears on. Both stop the walk, and what they add up to is the record of
    passes and blow-ups the account carries.

Plus the boundary the whole feature depends on for not being retroactive:
attempts that pre-date the first epoch are never counted.

Run directly:  ``.venv/bin/python tests/test_replay_account.py``
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks

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
    mode: str = "replay",
    account_id: str | None = None,
    excursion: dict | None = None,
) -> dict:
    """One settled sitting, stamped when we say rather than when it ran.

    ``created_at`` is written back over the store's own stamp because every gate
    and every grouping in the account reads it, and a test that could only make
    attempts "now" could not exercise a single one of them.
    """
    a = replays.create(
        symbol="NQH5", root="NQ", date=date, tz="New York", engine_version=1,
        tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"], mode=mode,
        account_id=account_id,
    )
    summary = {} if net is None else {"net_usd": net}
    # The sitting's own reading of its equity path, which is the only place the
    # unrealised half of it exists — see `replay_account.excursion`.
    summary.update(excursion or {})
    replays.save(
        a["id"],
        log=LOG,
        trades=trades or [],
        summary=summary,
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


def _epoch(started_at: str, cause: str | None = None, *, account: acct.Account = acct.FUNDED) -> None:
    """Mint one epoch on one account, leaving the other's alone.

    Merged into whatever is on disk rather than written over it: the two
    accounts share `account.json`, and a test that wants both open would
    otherwise silently end up with only the second.
    """
    e: dict = {"started_at": started_at}
    if cause:
        e["cause_of_death"] = cause
    state = acct.load_state()
    acct.put_account(state, account, epochs=[e])
    acct.save_state(state)


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
    # One tape day: up 2,000 and then back down 1,000. The high was 52,000, the
    # close was 51,000, and the floor is only allowed to have seen the close.
    #
    # The up-leg stops short of the 3,000 profit target on purpose. A sitting
    # that settles at or over it *passes*, which ends the epoch and stops the
    # walk — so a bigger first leg would leave this testing the pass rule with
    # the floor's name on it.
    _sitting("2026-02-03T14:00:00Z", 2_000.0, date="2026-02-03")
    _sitting("2026-02-03T18:00:00Z", -1_000.0, date="2026-02-03")

    # Still on that tape: nothing has closed, so the floor is the opening one.
    same_day = acct.derive(now=_t("2026-02-03T20:00:00Z"), day="2026-02-03")
    assert same_day["equity"] == 51_000.0
    assert same_day["floor"] == 48_000.0

    # Moved on to the next tape — five minutes later by the wall clock, which is
    # the point: the day that closed is a day of the market, not of the evening.
    # 51,000 − 2,000, not 52,000 − 2,000.
    next_day = acct.derive(now=_t("2026-02-03T20:05:00Z"), day="2026-02-04")
    assert next_day["peak_close"] == 51_000.0
    assert next_day["floor"] == 49_000.0


@_tmp
def test_several_tape_days_in_one_evening_close_several_days():
    """The bug this rule was written for, in the shape it actually appeared.

    Four sittings opened on 2026-08-24 replayed the tapes of 2025-07-02,
    2026-02-17, 2024-10-08 and 2026-08-11. Grouped by ``created_at`` that was
    one day: no boundary was ever crossed, ``peak_close`` never moved, and the
    floor sat at its opening 48,000 while equity ran to 53,395 — for the whole
    session, unsticking only the next calendar day when the end-of-walk check
    finally saw a different date.
    """
    _epoch("2026-08-24T00:00:00Z")
    for i, (tape, net) in enumerate([
        ("2025-07-02", 486.0), ("2026-02-17", 1_201.0),
        ("2024-10-08", 332.5), ("2026-08-11", 1_376.0),
    ]):
        _sitting(f"2026-08-24T0{4 + i}:00:00Z", net, date=tape)

    # Mid-session, still on the last tape. Three days have closed underneath it.
    v = acct.derive(now=_t("2026-08-24T20:00:00Z"), day="2026-08-11")
    assert v["equity"] == 53_395.5
    assert v["peak_close"] == 52_019.5
    assert v["floor"] == 50_019.5

    # Tape order is not the order they were replayed in — 2024 came third — and
    # the walk stays in the order they were *lived*, so a day out of sequence
    # cannot rewrite history into a death that never happened.
    assert v["last_death"] is None
    # It ended the *other* way: 53,395.50 clears the 3,000 target, so this
    # evening passed the account on its fourth tape. That is what the numbers
    # above always said and it is worth stating — the floor readings are the
    # point of this test and every one of them holds either way.
    assert v["status"] == "passed"


@_tmp
def test_the_day_being_replayed_does_not_close_across_a_resume():
    """A day you are still trading has not ended, however many sittings it took.

    This is the property ``guardRules.accountStop`` rests on: the floor is a
    constant the browser can be handed. A resume is the case that would break it
    if the last day closed unconditionally.
    """
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 1_500.0, date="2026-02-03")
    first = acct.derive(now=_t("2026-02-03T15:00:00Z"), day="2026-02-03")
    _sitting("2026-02-03T19:00:00Z", 400.0, date="2026-02-03")
    second = acct.derive(now=_t("2026-02-03T20:00:00Z"), day="2026-02-03")
    assert first["floor"] == second["floor"] == 48_000.0

    # With no day open — the history page, an autopsy — every day closes.
    assert acct.derive(now=_t("2026-02-03T20:00:00Z"))["floor"] == 49_900.0


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
    # Not resettable until the death has been written up — the write-up is the
    # whole of what a funded death costs now, so it is the whole of the gate.
    assert v["can_reset"] is False


@_tmp
def test_a_breach_traded_back_over_is_still_a_death():
    """The death reads the sitting's booked path, not just its settled sum.

    A sitting that dives through the floor and trades its way back would settle
    above it — and a walk that only sums nets would call the account alive. It
    died at the dip; everything after it belongs to no account, which is the
    same rule the walk already applies to whole sittings after a death.
    """
    _epoch("2026-02-03T00:00:00Z")
    # The path: -1,500, then -700 (equity 47,800 — under the 48,000 floor),
    # then +1,000 back to a settled net of -1,200.
    _sitting(
        "2026-02-03T14:00:00Z",
        -1_200.0,
        trades=[_trade(-1_500), _trade(-700, 2), _trade(1_000, 3)],
    )

    v = acct.derive(now=_t("2026-02-03T15:00:00Z"))
    assert v["status"] == "blown"
    # Equity freezes at the dip — the account was liquidated there, and the
    # recovery was traded on money the account no longer had.
    assert v["equity"] == 47_800.0
    assert v["last_death"]["equity"] == 47_800.0
    assert v["last_death"]["floor"] == 48_000.0


@_tmp
def test_blown_becomes_resettable_the_moment_the_cause_is_written():
    """The write-up is the only thing a funded death waits on.

    There used to be a 24-hour timeout behind it (``COOLDOWN_S``, removed
    2026-08-24 with the move to mandatory reviews), so this same fixture used to
    read ``cooldown`` for a day first. Nothing is bought back with time now: a
    minute after the death, with the sentence written, the account is
    replaceable — and without it, it is not, however long you wait.
    """
    _epoch("2026-02-03T00:00:00Z", cause="held a loser through the number")
    _sitting("2026-02-03T14:00:00Z", -2_100.0)

    written = acct.derive(now=_t("2026-02-03T14:01:00Z"))
    assert written["status"] == "can_reset"
    assert written["can_reset"] is True
    assert written["last_death"]["cause_of_death"] == "held a loser through the number"


@_tmp
def test_a_reset_epoch_starts_clean_and_still_carries_the_last_cause():
    _epoch("2026-02-03T00:00:00Z", cause="revenge traded the recovery")
    _sitting("2026-02-03T14:00:00Z", -2_100.0)

    minted = acct.ensure_epoch(now=_t("2026-02-04T15:00:00Z"))
    assert minted["started_at"] == "2026-02-04T15:00:00Z"
    assert len(acct.epochs_of(acct.load_state(), acct.FUNDED)) == 2

    v = acct.derive(now=_t("2026-02-04T15:00:00Z"))
    assert v["status"] == "live"
    assert v["equity"] == 50_000.0
    assert v["epoch"]["index"] == 1 and v["epoch"]["sittings"] == 0
    # The dead account is still readable from inside the live one — that is what
    # "pinned into the next epoch" means.
    assert v["last_death"]["cause_of_death"] == "revenge traded the recovery"
    assert v["last_death"]["epoch"] == 0


# --- the other way out ------------------------------------------------------
# A life ends twice over: the floor catches it, or it clears the target. The
# tests below are the mirror of the four above, and the last of them is the
# tracker those two outcomes add up to.


@_tmp
def test_clearing_the_profit_target_passes_the_account():
    """Settled at or over start + 3,000 — the eval is over and it was won."""
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 3_100.0)

    v = acct.derive(now=_t("2026-02-03T15:00:00Z"))
    assert v["status"] == "passed"
    assert v["equity"] == 53_100.0
    assert v["passed"]["target"] == 53_000.0
    assert v["target_remaining"] == 0.0
    # Nothing is owed. A pass has no write-up to wait on — nothing died and there
    # is nothing to explain — so the next sitting opens the next eval at once.
    assert v["can_reset"] is True
    assert v["last_death"] is None
    assert v["record"] == {"passed": 1, "blown": 0}


@_tmp
def test_a_sitting_that_ran_through_the_target_and_gave_it_back_has_not_passed():
    """The mirror image of ``a_breach_traded_back_over_is_still_a_death``.

    The two outcomes are read off *different* things, and deliberately. A floor
    is read off the booked path, because a breach is instant and unrecoverable —
    touched is breached, whatever the sitting does afterwards. A target is read
    off the settled balance, because that is the day's close a real eval clears
    on: money you were up and handed back was never cleared, and passing an
    account on a print is neither the rule nor a habit worth practising.
    """
    _epoch("2026-02-03T00:00:00Z")
    # The path: +3,500 (equity 53,500, over the 53,000 target), then −600 back
    # to a settled net of 2,900.
    _sitting(
        "2026-02-03T14:00:00Z",
        2_900.0,
        trades=[_trade(3_500), _trade(-600, 2)],
    )

    v = acct.derive(now=_t("2026-02-03T15:00:00Z"))
    assert v["status"] == "live"
    assert v["equity"] == 52_900.0
    assert v["record"] == {"passed": 0, "blown": 0}


@_tmp
def test_a_sitting_that_died_on_the_way_to_the_target_is_blown_not_passed():
    """Both endings in one sitting, and the death is the one that counts.

    The account was liquidated at the dip; the run to the target after it was
    traded on money it no longer had, which is the same reason the recovery in
    ``a_breach_traded_back_over_is_still_a_death`` buys nothing.
    """
    _epoch("2026-02-03T00:00:00Z")
    # −2,100 takes equity to 47,900, under the 48,000 floor. The +5,200 after it
    # settles the sitting at +3,100, which would clear the target twice over.
    _sitting(
        "2026-02-03T14:00:00Z",
        3_100.0,
        trades=[_trade(-2_100), _trade(5_200, 2)],
    )

    v = acct.derive(now=_t("2026-02-03T15:00:00Z"))
    assert v["status"] == "blown"
    assert v["equity"] == 47_900.0
    assert v["passed"] is None
    assert v["record"] == {"passed": 0, "blown": 1}


@_tmp
def test_the_sitting_after_a_pass_opens_the_next_eval():
    """A pass ends the epoch as surely as a death, and owes nothing on the way.

    This is what makes the tracker a count of *completed evals* rather than of
    high-water marks: the account cannot keep quietly adding sittings to a life
    it already won, so passing twice takes two accounts.
    """
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 3_100.0)

    minted = acct.ensure_epoch(now=_t("2026-02-03T16:00:00Z"))
    assert minted["started_at"] == "2026-02-03T16:00:00Z"
    assert len(acct.epochs_of(acct.load_state(), acct.FUNDED)) == 2

    v = acct.derive(now=_t("2026-02-03T16:00:00Z"))
    assert v["status"] == "live"
    assert v["equity"] == 50_000.0
    assert v["epoch"]["index"] == 1 and v["epoch"]["sittings"] == 0
    # The life it replaced is still counted — the record outlives the epoch, and
    # it is the only thing that carries a pass forward.
    assert v["record"] == {"passed": 1, "blown": 0}


@_tmp
def test_a_passed_account_has_nothing_to_write_up():
    """``write_cause`` answers a death, so a pass refuses it like a live one."""
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 3_100.0)

    try:
        acct.write_cause("nothing killed it", now=_t("2026-02-03T15:00:00Z"))
    except ValueError as e:
        assert "still alive" in str(e)
    else:
        raise AssertionError("a passed account has no death to explain")

    # And it refuses no sitting: the gate is the write-up a *death* owes.
    assert acct.refusal(now=_t("2026-02-03T15:00:00Z")) is None


@_tmp
def test_the_record_counts_every_life_both_ways():
    """The tracker, over a history with one of each and one still running.

    Nothing here is stored: each life is the walk over its own window, so the
    count re-answers from the trades every time. A tally kept on disk would
    still be claiming this pass after the sitting behind it was deleted.
    """
    state = acct.load_state()
    acct.put_account(state, acct.FUNDED, epochs=[
        {"started_at": "2026-02-03T00:00:00Z", "cause_of_death": "sized up to get flat"},
        {"started_at": "2026-02-05T00:00:00Z"},
        {"started_at": "2026-02-07T00:00:00Z"},
    ])
    acct.save_state(state)

    _sitting("2026-02-03T14:00:00Z", -2_100.0, date="2026-02-03")   # life 0: blown
    _sitting("2026-02-05T14:00:00Z", 3_400.0, date="2026-02-05")    # life 1: passed
    _sitting("2026-02-07T14:00:00Z", 250.0, date="2026-02-07")      # life 2: live

    history = acct.lives(now=_t("2026-02-08T15:00:00Z"))
    assert [life.outcome for life in history] == ["blown", "passed", "live"]

    v = acct.derive(now=_t("2026-02-08T15:00:00Z"))
    assert v["record"] == {"passed": 1, "blown": 1}
    assert v["status"] == "live" and v["equity"] == 50_250.0
    # The death is two lives back and still readable — a pass does not bury it.
    assert v["last_death"]["epoch"] == 0
    assert v["last_death"]["cause_of_death"] == "sized up to get flat"


@_tmp
def test_ensure_epoch_does_not_mint_over_a_living_account():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -100.0)
    acct.ensure_epoch(now=_t("2026-02-03T18:00:00Z"))
    assert len(acct.epochs_of(acct.load_state(), acct.FUNDED)) == 1


# --- the day ----------------------------------------------------------------


@_tmp
def test_the_daily_loss_is_counted_against_the_tape_day():
    """One allowance per day *replayed*, not per evening spent replaying.

    This used to group by the New York date of ``created_at``, so that a late
    session did not get a fresh $1,200 halfway through itself. That subtlety is
    gone with the wall clock: a tape day is a date, and two sittings on the same
    tape share its allowance whenever they were run.
    """
    _epoch("2026-02-03T00:00:00Z")
    # Both replay the 3rd, days apart by the wall clock. One day, one allowance.
    _sitting("2026-02-09T23:00:00Z", -500.0, date="2026-02-03")
    _sitting("2026-02-10T02:00:00Z", -300.0, date="2026-02-03")

    v = acct.derive(now=_t("2026-02-10T03:00:00Z"), day="2026-02-03")
    assert v["day"] == "2026-02-03"
    assert v["day_net"] == -800.0
    assert v["day_loss_remaining"] == 400.0

    # Move to the next tape, five minutes later, and the allowance is whole
    # again — four tapes in one evening is four days and spends four of them.
    tomorrow = acct.derive(now=_t("2026-02-10T03:05:00Z"), day="2026-02-04")
    assert tomorrow["day_net"] == 0.0
    assert tomorrow["day_loss_remaining"] == 1_200.0


@_tmp
def test_a_green_day_does_not_bank_extra_room():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 900.0, date="2026-02-03")
    v = acct.derive(now=_t("2026-02-03T17:00:00Z"), day="2026-02-03")
    assert v["day_net"] == 900.0
    assert v["day_loss_remaining"] == 1_200.0
    assert v["target_remaining"] == 2_100.0


@_tmp
def test_with_no_day_open_nothing_is_spent_and_every_day_has_closed():
    """The history page's reading, and why it is not the last day's.

    A view with no tape day on the chart is not a view of *some* day — it is a
    view of the account between days. Inheriting the last day replayed would
    draw a half-spent day meter against a day nobody is trading, and the answer
    the page actually wants is "the allowance is whole and the peak is banked".
    """
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -700.0, date="2026-02-03")

    v = acct.derive(now=_t("2026-02-03T17:00:00Z"))
    assert v["day"] is None
    assert v["day_net"] == 0.0
    assert v["day_loss_remaining"] == 1_200.0
    # ...and the day itself closed, so the peak is banked even though it is
    # under the start: a red day never lowers the floor.
    assert v["peak_close"] == 50_000.0
    assert v["floor"] == 48_000.0


# --- the gates' inputs ------------------------------------------------------


@_tmp
def test_a_settled_sitting_does_not_time_gate_the_next_one():
    """The hour gate is gone (2026-08-17): the forced review is the pause
    between sittings now, and a sitting with nothing to answer for opens the
    next one immediately — one minute later as much as one hour later."""
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -50.0)

    early = acct.derive(now=_t("2026-02-03T14:01:00Z"))
    assert "next_sitting_at" not in early
    assert acct.refusal(now=_t("2026-02-03T14:01:00Z"), view=early) is None


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
def test_counted_ids_names_every_sitting_the_equity_already_includes():
    """The browser adds the sitting on screen to ``equity`` to get a live figure,
    so it has to be able to tell when that sitting is already in there. It cannot
    work this out for itself — the sweep above settles an ``active`` attempt
    without the page hearing about it — so the view says so outright.

    Without this the page double-counts the sitting the moment it settles, which
    reads as a floor breach exactly one sitting's P&L deep.
    """
    _epoch("2026-02-03T00:00:00Z")
    done = _sitting("2026-02-03T14:00:00Z", -700.0)
    live = _sitting("2026-02-03T15:00:00Z", -300.0, status="active")

    v = acct.derive(now=_t("2026-02-03T15:10:00Z"))
    assert v["counted_ids"] == [done["id"]]
    assert v["equity"] == 49_300.0
    # The live one is the page's to add: not counted, so 49,300 − 300 is right.
    assert live["id"] not in v["counted_ids"]

    # An hour on, the sweep has settled it and the account owns it. The page's
    # own arithmetic must stop, and this is the only thing that tells it to.
    later = _t("2026-02-03T16:10:00Z")
    acct.sweep_stale_actives(now=later)
    after = acct.derive(now=later)
    assert after["counted_ids"] == [done["id"], live["id"]]
    assert after["equity"] == 49_000.0


@_tmp
def test_counted_ids_stops_at_the_death_like_the_walk_does():
    """An epoch is over when the floor catches it, and the sittings after that
    belong to no account — so they are not in `equity` and must not be listed as
    though they were."""
    _epoch("2026-02-03T00:00:00Z")
    first = _sitting("2026-02-03T14:00:00Z", -2_100.0)
    _sitting("2026-02-03T15:00:00Z", -100.0)

    v = acct.derive(now=_t("2026-02-03T16:00:00Z"))
    assert v["counted_ids"] == [first["id"]]
    assert v["status"] == "blown"


def _drill(
    created_at: str,
    net: float | None,
    *,
    status: str = "finished",
    trades: list | None = None,
    date: str = "2026-02-03",
) -> dict:
    """One backtest-mode rep, stamped like ``_sitting`` and unpriced."""
    a = replays.create(
        symbol="NQH5", root="NQ", date=date, tz="New York", engine_version=1,
        tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"], model_id=3,
        mode="drill", drop_ms=TAPE["rth_open_ms"] + 3_600_000,
        window={"from_ms": TAPE["rth_open_ms"], "to_ms": TAPE["rth_open_ms"] + 5 * 3_600_000},
    )
    # A sat-out rep never autosaves — the recorder writes on a fill and there
    # wasn't one — so `trades=None` means no save at all, not an empty save.
    if trades is not None or net is not None:
        replays.save(
            a["id"], log=LOG, trades=trades or [],
            summary={} if net is None else {"net_usd": net}, status=status,
        )
    d = replays.attempt_dir(a["id"])
    rec = replays._read_json(d / "attempt.json", {})
    rec["created_at"] = created_at
    rec["updated_at"] = created_at
    rec["status"] = status
    replays._write_json(d / "attempt.json", rec)
    return rec


@_tmp
def test_a_drill_moves_neither_the_equity_nor_the_gate():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -300.0)
    now = _t("2026-02-03T14:30:00Z")
    before = acct.derive(now=now)
    before_refusal = acct.refusal(now=now)

    # Six losing reps that would have blown the account twice over.
    for i in range(6):
        _drill(f"2026-02-03T14:0{i}:30Z", -900.0)
    _drill("2026-02-03T14:29:00Z", -900.0)
    after = acct.derive(now=now)

    for key in ("equity", "floor", "peak_close", "status", "day_net",
                "day_loss_remaining", "target_remaining",
                "can_reset", "review_flagged", "record", "passed"):
        assert before[key] == after[key], f"a drill moved {key}"
    assert after["equity"] == 49_700.0
    assert after["epoch"]["sittings"] == 1
    # The account's gates read `epoch_attempts` directly, and the drill filter
    # sits above it in `attempts_by_account` — one attribution, made once for
    # every account, so a drill cannot move a real account's refusal in either
    # direction or add a life to anybody's record.
    assert acct.refusal(now=now) == before_refusal


@_tmp
def test_an_unreviewed_drill_does_not_block_the_next_real_sitting():
    # Nothing blocks on a review any more (2026-08-25), but the reason this test
    # exists is upstream of that: a drill is unpriced, so it must not reach the
    # account's state at all — in either direction, whatever the state is for.
    _epoch("2026-02-03T00:00:00Z")
    _drill("2026-02-03T14:00:00Z", -900.0, trades=[_trade(-900.0)])
    now = _t("2026-02-03T18:00:00Z")
    assert acct.derive(now=now)["review_flagged"] is None
    assert acct.refusal(now=now) is None


@_tmp
def test_a_sat_out_rep_is_deleted_when_it_goes_stale_and_a_traded_one_is_not():
    _epoch("2026-02-03T00:00:00Z")
    passed = _drill("2026-02-03T14:00:00Z", None, status="active")
    traded = _drill("2026-02-03T14:05:00Z", -200.0, status="active",
                    trades=[_trade(-200.0)], date="2026-02-04")

    later = _t("2026-02-03T16:00:00Z")
    assert set(acct.sweep_stale_actives(now=later)) == {passed["id"], traded["id"]}
    # Abandoned draws leave nothing: "28 of 40 reps had no setup" has to be a
    # fact about the market rather than about how many draws you re-rolled.
    assert not replays.attempt_dir(passed["id"]).exists()
    # A rep that traded is real practice whatever the account thinks of it.
    assert replays.read(traded["id"])["status"] == "abandoned"
    # And neither one touched the account on the way through.
    assert acct.derive(now=later)["equity"] == 50_000.0


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
def test_fast_and_hole_are_no_longer_flags():
    """Retired 2026-08-17 (review revamp): every trade owes a review now, so the
    style findings stopped earning a verdict of their own. A 12-second trade
    and a trade opened deep in the hole raise nothing."""
    g = _guards()  # slow_down_at 300
    trades = [
        _paced(-160, 1, at_s=0, held_s=12),               # fast
        _paced(-160, 2, at_s=600),                        # digs the hole
        _paced(-40, 3, at_s=3600),                        # opened $320 down
    ]
    assert acct.flags_for({}, trades, guards=g) == []


@_tmp
def test_a_loss_over_the_risk_ceiling_is_flagged():
    g = _guards()  # max_risk_usd 250
    flags = acct.flags_for({}, [_paced(-400, 1, at_s=0)], guards=g)
    assert flags[0]["reasons"] == ["lost $400 against a $250 ceiling"]
    # A win of the same size is not a flag: the ceiling is on what a stop is
    # allowed to cost, and a target that ran is not a rule being broken.
    assert acct.flags_for({}, [_paced(400, 1, at_s=0)], guards=g) == []


@_tmp
def test_a_trade_that_is_also_fast_and_in_the_hole_answers_only_for_the_ceiling():
    g = _guards()
    trades = [
        _paced(-160, 1, at_s=0),
        _paced(-160, 2, at_s=600),
        # Fast, in the hole, and over the ceiling — but only the ceiling is a
        # flag now, so it is one thing to answer for with one reason.
        _paced(-400, 3, at_s=3600, held_s=8),
    ]
    flags = acct.flags_for({}, trades, guards=g)
    assert [f["trade_id"] for f in flags] == [3]
    assert flags[0]["reasons"] == ["lost $400 against a $250 ceiling"]


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




# --- the lifecycle, through the router --------------------------------------


def _finish(trades: list, rewinds: list | None = None, *, mode: str = "replay") -> dict:
    """Open a sitting through the router and run it out of tape."""
    from api.routers import replays as router

    created = router.create_replay(
        router.CreateIn(
            symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
            engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
            mode=mode,
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
        # A finished sitting schedules its measurements here. BackgroundTasks is a
        # plain collector until a response runs it, so they are recorded and never
        # fire — which is what this file wants: it is about the review gate, and
        # the measurements need cached ticks it does not have.
        BackgroundTasks(),
    )


@_tmp
def test_a_zero_trade_sitting_passes_straight_through_to_reviewed():
    done = _finish([])
    assert done["flags"] == []
    # No ceremony over a rep where you looked and passed — the next sitting
    # should not wait on a formality.
    assert done["status"] == "reviewed"


@_tmp
def test_a_traded_sitting_owes_nothing_and_refuses_nothing():
    """`finished` is a terminal state again (2026-08-25).

    Between 2026-08-17 and then, a traded sitting parked at `finished` *was* a
    debt: the account refused the next sitting until every trade in it carried
    its three review answers. That gate is gone at the user's request.
    An unreviewed sitting is now simply an unreviewed sitting.
    """
    _epoch("2026-01-01T00:00:00Z")
    done = _finish([_paced(120, 1, at_s=0)])
    assert done["flags"] == []
    assert done["status"] == "finished"
    assert not done.get("review_later")
    assert acct.derive()["review_flagged"] is None
    assert acct.refusal() is None


@_tmp
def test_a_flagged_sitting_is_listed_and_still_refuses_nothing():
    """Flagging is the whole of what replaced the gate: it says "come back to
    this one" and it says nothing else. The oldest is named because the queue
    reads in the order it happened, and the count is what the chip renders."""
    from api.routers import replays as router

    _epoch("2026-01-01T00:00:00Z")
    # Stamped rather than run, so "oldest" below is a fact and not a race — two
    # sittings created in the same second tie on `created_at`.
    first = _sitting("2026-01-02T14:00:00Z", 120.0, trades=[_trade(120.0)])
    second = _sitting("2026-01-03T14:00:00Z", 80.0, trades=[_trade(80.0)])

    router.patch_replay(second["id"], router.PatchIn(review_later=True))
    marked = acct.derive()["review_flagged"]
    assert marked["attempt_id"] == second["id"] and marked["count"] == 1

    router.patch_replay(first["id"], router.PatchIn(review_later=True))
    marked = acct.derive()["review_flagged"]
    # Oldest first, whichever order they were marked in.
    assert marked["attempt_id"] == first["id"] and marked["count"] == 2
    # And it is a reminder, not a gate.
    assert acct.refusal() is None

    # Taking the mark off is the same door.
    router.patch_replay(first["id"], router.PatchIn(review_later=False))
    assert acct.derive()["review_flagged"]["attempt_id"] == second["id"]


@_tmp
def test_a_flag_is_recorded_and_never_asked_about():
    """A flag is a fact about the sitting, not a debt.

    The leak/justified verdict went with the grading revamp
    (``docs/trade-grading-plan.md`` G6) and the panel stopped offering the
    buttons. The server kept its half of the gate until 2026-08-23, which made
    every flagged sitting permanently unfileable — and, while a block was what
    the next create read, locked the account behind it. This is that gate
    staying gone: the flag is stored, the trade's own answers are the whole
    ask, and filing needs nothing said about the flag.
    """
    from api.routers import replays as router

    _epoch("2026-01-01T00:00:00Z")
    done = _finish([_paced(-400, 1, at_s=0, held_s=6)])
    assert done["status"] == "finished"
    assert len(done["flags"]) == 1
    # A flag is not a mark to review either — that one is yours to make.
    assert acct.derive()["review_flagged"] is None

    # The trade's three answers (level, setup, discipline) are what
    # `reviewed` costs, and nothing else is.
    # Pinned because a bare test process has no DB and `_review_rows` is
    # documented to degrade to "nothing to owe" — which would pass this whatever
    # the check did.
    original = router._review_rows
    router._review_rows = lambda _id: [{"trade_key": "k1",
                                       "watched_levels": ["value_low"],
                                       "setup": "faded_rally",
                                       "discipline": "clean"}]
    try:
        filed = router.patch_replay(done["id"], router.PatchIn(status="reviewed"))
    finally:
        router._review_rows = original
    assert filed["status"] == "reviewed"
    assert len(filed["flags"]) == 1


@_tmp
def test_filing_a_review_takes_the_flag_off():
    """A sitting leaves the queue by being answered, never by being waited out.
    The clearing is in ``journal.replays.patch`` rather than in the router so
    that no caller has to remember it."""
    from api.routers import replays as router

    _epoch("2026-01-01T00:00:00Z")
    done = _finish([_paced(120, 1, at_s=0)])
    router.patch_replay(done["id"], router.PatchIn(review_later=True))
    assert acct.derive()["review_flagged"]["attempt_id"] == done["id"]

    original = router._review_rows
    router._review_rows = lambda _id: [{"trade_key": "k1",
                                       "watched_levels": ["value_low"],
                                       "setup": "faded_rally",
                                       "discipline": "clean"}]
    try:
        filed = router.patch_replay(done["id"], router.PatchIn(status="reviewed"))
    finally:
        router._review_rows = original
    assert filed["review_later"] is False
    assert acct.derive()["review_flagged"] is None


@_tmp
def test_a_rewind_that_erased_everything_owes_nothing():
    """The zero-trade pass-through does not depend on the sitting being clean.

    A rewind over your own fills raises a flag with no trade under it. Nothing
    is left to grade, so there is nothing to review — and parking it at
    `finished` would leave a sitting whose review panel has no cards.
    """
    _epoch("2026-01-01T00:00:00Z")
    done = _finish([], rewinds=[{"from_ms": TAPE["rth_open_ms"] + 60_000,
                                 "to_ms": TAPE["rth_open_ms"], "dropped": 2}])
    assert len(done["flags"]) == 1 and done["flags"][0]["kind"] == "rewind"
    assert done["status"] == "reviewed"
    assert acct.derive()["review_flagged"] is None


@_tmp
def test_reviewed_is_refused_while_any_trade_is_unreviewed():
    """Every journaled trade owes its three answers before `reviewed` is
    accepted. The journal is stubbed at the router's seam — a bare test process
    has no DB, and `_review_rows` is documented to degrade to "nothing to owe"
    there, which is exactly the case this test must not silently fall into."""
    from fastapi import HTTPException

    from api.routers import replays as router

    done = _finish([_paced(-400, 1, at_s=0, held_s=6)])  # oversized → one flag
    file_it = router.PatchIn(status="reviewed")
    original = router._review_rows
    router._review_rows = lambda _id: [{"trade_key": "k1",
                                       "watched_levels": [], "setup": None,
                                       "discipline": None}]
    try:
        try:
            router.patch_replay(done["id"], file_it)
        except HTTPException as e:
            assert e.status_code == 409 and "unreviewed" in str(e.detail)
        else:
            raise AssertionError("an unreviewed sitting was accepted as reviewed")
        router._review_rows = lambda _id: [{"trade_key": "k1",
                                       "watched_levels": ["value_low"],
                                       "setup": "faded_rally",
                                       "discipline": "clean"}]
        assert router.patch_replay(done["id"], file_it)["status"] == "reviewed"
    finally:
        router._review_rows = original


@_tmp
def test_the_next_drill_waits_for_the_last_reps_answers():
    """Decision V6: an unreviewed rep blocks the next drill — and only the next
    drill; the account's world is not consulted. Same stubbed seam as above."""
    from fastapi import HTTPException

    from api.routers import replays as router

    _drill("2026-02-03T14:00:00Z", -130.0, trades=[_trade(-130.0)])
    kw = dict(
        symbol="NQH5", root="NQ", date="2026-02-04", tz="New York",
        engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
        mode="drill", model_id=3, drop_ms=TAPE["rth_open_ms"],
        window={"from_ms": TAPE["rth_open_ms"], "to_ms": TAPE["rth_open_ms"] + 1},
    )
    original = router._review_rows
    router._review_rows = lambda _id: [{"trade_key": "k1",
                                       "watched_levels": [], "setup": None,
                                       "discipline": None}]
    # The gate itself is switched off in the app right now (see
    # `DRILL_REVIEW_REQUIRED`), so it is pinned on here: the switch is meant to
    # be temporary, and a suite that went quiet with it would stop guarding the
    # mechanics it is supposed to come back to.
    gate = router.DRILL_REVIEW_REQUIRED
    router.DRILL_REVIEW_REQUIRED = True
    try:
        try:
            router.create_replay(router.CreateIn(**kw))
        except HTTPException as e:
            assert e.status_code == 409 and e.detail["code"] == "drill_review"
            assert e.detail["attempt_id"]
        else:
            raise AssertionError("a drill opened over an unreviewed rep")
        router._review_rows = lambda _id: [{"trade_key": "k1",
                                       "watched_levels": ["value_low"],
                                       "setup": "faded_rally",
                                       "discipline": "clean"}]
        made = router.create_replay(router.CreateIn(**kw))
        assert replays.is_drill(made)
    finally:
        router._review_rows = original
        router.DRILL_REVIEW_REQUIRED = gate


@_tmp
def test_a_settled_drill_never_blocks_even_unreviewed():
    """The gate reads the status, not just the answers: a rep settled out-of-band
    (an admin edit, an abandon by the sweep) is done owing — without this, one
    such rep would lock 🎲 forever with no review left to file."""
    from api.routers import replays as router

    _drill("2026-02-03T14:00:00Z", -130.0, status="reviewed",
           trades=[_trade(-130.0)])
    kw = dict(
        symbol="NQH5", root="NQ", date="2026-02-04", tz="New York",
        engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
        mode="drill", model_id=3, drop_ms=TAPE["rth_open_ms"],
        window={"from_ms": TAPE["rth_open_ms"], "to_ms": TAPE["rth_open_ms"] + 1},
    )
    original = router._review_rows
    router._review_rows = lambda _id: [{"trade_key": "k1",
                                       "watched_levels": [], "setup": None,
                                       "discipline": None}]
    # Pinned on for the same reason as the test above: with the switch off every
    # drill passes, which would make this one prove nothing.
    gate = router.DRILL_REVIEW_REQUIRED
    router.DRILL_REVIEW_REQUIRED = True
    try:
        made = router.create_replay(router.CreateIn(**kw))
        assert replays.is_drill(made)
    finally:
        router._review_rows = original
        router.DRILL_REVIEW_REQUIRED = gate


@_tmp
def test_trading_on_after_a_review_withdraws_it():
    from api.routers import replays as router

    done = _finish([_paced(-400, 1, at_s=0, held_s=6)])
    # Tag half pinned satisfied — same reason as the flagged-sitting test: with
    # a live journal the unreviewed trade would refuse this `reviewed` first.
    original = router._review_rows
    router._review_rows = lambda _id: [{"trade_key": "k1",
                                       "watched_levels": ["value_low"],
                                       "setup": "faded_rally",
                                       "discipline": "clean"}]
    try:
        router.patch_replay(done["id"], router.PatchIn(status="reviewed"))
    finally:
        router._review_rows = original
    # Rewound and traded on: `reviewed` and the flags were about trades that are
    # no longer the trades in the file.
    router.save_replay(done["id"], router.SaveIn(log=LOG, trades=[], summary={}, status="active"),
                       BackgroundTasks())
    reopened = replays.read(done["id"])
    assert "review" not in reopened and "flags" not in reopened

    again = router.save_replay(
        done["id"],
        router.SaveIn(
            log=LOG, trades=[_paced(-400, 1, at_s=0, held_s=6)], summary={}, status="finished"
        ),
        BackgroundTasks(),
    )
    assert again["status"] == "finished" and len(again["flags"]) == 1


# --- the gate ---------------------------------------------------------------


@_tmp
def test_a_clean_settled_sitting_refuses_nothing_and_at_once():
    """The hour gate is gone (2026-08-17) and so is the review that replaced it
    (2026-08-25), so a settled sitting opens the next one immediately."""
    _epoch("2026-02-03T00:00:00Z")
    assert acct.refusal(now=_t("2026-02-03T12:00:00Z")) is None
    _sitting("2026-02-03T14:00:00Z", -50.0)
    assert acct.refusal(now=_t("2026-02-03T14:01:00Z")) is None


@_tmp
def test_an_unreviewed_sitting_refuses_nothing_flagged_or_not():
    """The one that used to be `test_filing_the_review_opens_the_account_
    immediately`, inverted: there is no longer anything to open.

    A blow-up still costs its sentence — that is the last gate in the module,
    and the point of checking it here is that removing the review did not take
    it with it.
    """
    from api.routers import replays as router

    _epoch("2026-01-01T00:00:00Z")
    done = _finish([_paced(-400, 1, at_s=0, held_s=6)])
    assert acct.refusal() is None

    router.patch_replay(done["id"], router.PatchIn(review_later=True))
    assert acct.derive()["review_flagged"]["attempt_id"] == done["id"]
    assert acct.refusal() is None, "a flag must never become a gate"


@_tmp
def test_a_dead_account_refuses_as_blown_until_it_is_written_up():
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -2_100.0)

    blown = acct.refusal(now=_t("2026-02-03T14:10:00Z"))
    assert blown["code"] == "blown"
    # No refusal here carries a deadline any more: every one of them is answered
    # by doing something rather than by waiting.
    assert "until" not in blown

    acct.write_cause("held a loser through the number", now=_t("2026-02-03T14:20:00Z"))
    # Written up: not a refusal at all, and immediately. The create mints the
    # next account.
    assert acct.refusal(now=_t("2026-02-03T14:21:00Z")) is None


@_tmp
def test_an_open_sitting_does_not_gate_a_second_tab():
    """The hour gate used to hold here, then the review gate did. Neither is
    left: an open sitting in one tab has never been a reason to refuse a second,
    and now nothing is until the account actually dies."""
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", None, status="active")
    assert acct.refusal(now=_t("2026-02-03T14:10:00Z")) is None


@_tmp
def test_the_create_route_refuses_with_the_code():
    from fastapi import HTTPException

    from api.routers import replays as router

    # A death nobody has written up: the gate that outranks every other one, and
    # the one that proves the refusal travels through the route as a 409 rather
    # than being enforced in the browser alone.
    died = datetime.now(timezone.utc) - timedelta(hours=1)
    _epoch(acct._iso(died - timedelta(hours=1)))
    _sitting(acct._iso(died), -2_100.0)

    try:
        router.create_replay(
            router.CreateIn(
                symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
                engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
            )
        )
    except HTTPException as e:
        assert e.status_code == 409
        assert e.detail["code"] == "blown"
    else:
        raise AssertionError("a sitting opened on a blown account")


@_tmp
def test_a_drill_opens_through_a_gate_that_is_refusing_replays():
    from fastapi import HTTPException

    from api.routers import replays as router

    # A dead account — the only gate left since the review stopped being one
    # (2026-08-25), and the one a drill has to pass through untouched.
    _epoch(acct._iso(datetime.now(timezone.utc) - timedelta(days=1)))
    _sitting(acct._iso(datetime.now(timezone.utc) - timedelta(minutes=5)), -2_100.0,
             trades=[_trade(-2_100.0)])

    kw = dict(symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
              engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"])
    made = router.create_replay(router.CreateIn(
        **kw, mode="drill", model_id=3, drop_ms=TAPE["rth_open_ms"],
        window={"from_ms": TAPE["rth_open_ms"], "to_ms": TAPE["rth_open_ms"] + 1},
    ))
    assert replays.is_drill(made)
    # And it did not spend the account's gate on the way through: the next real
    # sitting is still refused for the same reason. A drill must not be able to
    # mint the replacement epoch either — see the `ensure_epoch` warning on the
    # create route.
    try:
        router.create_replay(router.CreateIn(**kw))
    except HTTPException as e:
        assert e.status_code == 409 and e.detail["code"] == "blown"
    else:
        raise AssertionError("a drill cleared the account's gate for a replay")

    # A drill with nothing bound measures nothing, and the binding cannot be
    # added later — `upsert_session` is INSERT OR IGNORE.
    try:
        router.create_replay(router.CreateIn(**kw, mode="drill"))
    except HTTPException as e:
        assert e.status_code == 400
    else:
        raise AssertionError("an unbound drill was opened")


@_tmp
def test_the_campaign_counts_the_reps_that_had_no_trades():
    from api.routers import replays as router

    _epoch("2026-02-03T00:00:00Z")
    # Four reps: two traded, one sat out, one still open. Plus a replay sitting
    # and a drill on another model, neither of which is this campaign.
    _drill("2026-02-03T14:00:00Z", 220.0, trades=[_trade(220.0)])
    _drill("2026-02-03T15:00:00Z", -130.0, trades=[_trade(-130.0)], date="2026-02-04")
    _drill("2026-02-03T16:00:00Z", None, trades=[], date="2026-02-05")
    _drill("2026-02-03T17:00:00Z", None, status="active", date="2026-02-06")
    _sitting("2026-02-03T18:00:00Z", 900.0)
    other = _drill("2026-02-03T19:00:00Z", -400.0, trades=[_trade(-400.0)], date="2026-02-09")
    replays.patch(other["id"], model_id=99)

    got = router.drill_campaign(model_id=3)
    # Three settled reps, not two: the sat-out one is the row the mode exists
    # to write, and a trades-based aggregate would never have seen it.
    assert got["reps"] == 3
    assert got["traded_reps"] == 2 and got["sat_out"] == 1
    assert abs(got["base_rate"] - 2 / 3) < 1e-9
    # The rep still being sat is not counted, or every open campaign would
    # claim a rep it has not finished.
    assert got["trades"] == 2
    assert got["net_usd"] == 90.0
    # Another model's reps are another campaign, and a replay is not one at all.
    assert router.drill_campaign(model_id=99)["reps"] == 1
    # The drop histogram covers every settled rep, traded or not — which is what
    # makes the base rate readable rather than just small.
    assert sum(h["reps"] for h in got["drawn_by_hour"]) == 3
    assert sum(h["reps"] for h in got["traded_by_hour"]) == 2


@_tmp
def test_the_campaign_names_the_reps_that_were_rewound():
    """A drill can be seeked backwards (plan D8, superseded), so the campaign
    has to say when one was: a rewound rep had a second look at tape it then
    traded through, which is not the cold read the base rate is measuring.

    It is still counted — dropping it would shrink a campaign for a reason the
    card never showed — so the flag rides *beside* the rate, not inside it."""
    from api.routers import replays as router

    _epoch("2026-02-03T00:00:00Z")
    cold = _drill("2026-02-03T14:00:00Z", 220.0, trades=[_trade(220.0)])
    warm = _drill("2026-02-03T15:00:00Z", -130.0, trades=[_trade(-130.0)], date="2026-02-04")
    replays.save(
        warm["id"], log=LOG, trades=[_trade(-130.0)], summary={"net_usd": -130.0},
        status="finished",
        rewinds=[{"from_ms": 1_770_020_000_000, "to_ms": 1_770_015_000_000, "dropped": 1}],
    )

    got = router.drill_campaign(model_id=3)
    assert got["rewound_reps"] == 1
    # In the campaign, not removed from it: two reps, both traded, base rate 1.0.
    assert got["reps"] == 2 and got["traded_reps"] == 2
    assert got["base_rate"] == 1.0
    # And a campaign with no rewind in it reports zero rather than nothing, so
    # the column reads as "none" instead of "not measured".
    replays.patch(cold["id"], model_id=99)
    assert router.drill_campaign(model_id=99)["rewound_reps"] == 0


@_tmp
def test_a_drill_does_not_mint_the_first_epoch():
    from api.routers import replays as router

    # No account.json at all, and months of practice on disk. If a drill minted
    # epoch 0 the real account's life would begin at a moment nothing was at
    # stake, and every sitting either side of it would be mis-filed for good.
    router.create_replay(router.CreateIn(
        symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
        engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
        mode="drill", model_id=3,
    ))
    assert acct.epochs_of(acct.load_state(), acct.FUNDED) == []


@_tmp
def test_a_create_after_the_write_up_opens_a_fresh_account():
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


# --- the registry -----------------------------------------------------------


def _v1_file(payload: dict) -> None:
    replays._write_json(replays.REPLAYS_DIR / acct.ACCOUNT_FILE, payload)


@_tmp
def test_a_v1_file_migrates_to_both_accounts_with_their_history():
    """The shape every existing install has on disk, read by the new code.

    v1 was two hardcoded top-level keys. Losing either list would lose where an
    account began, which is the one fact in this module that cannot be re-derived
    from the trades — so this is the migration test that matters.
    """
    _v1_file({
        "epochs": [{"started_at": "2026-02-01T00:00:00Z", "cause_of_death": "over-traded"},
                   {"started_at": "2026-02-03T00:00:00Z"}],
        "paper_epochs": [{"started_at": "2026-02-02T00:00:00Z"}],
    })

    state = acct.load_state()
    assert state["version"] == acct.STATE_VERSION
    funded = acct.account_by_id("funded", state)
    paper = acct.account_by_id("paper", state)
    assert funded is not None and paper is not None
    # The lives, in order, with the write-up still attached to the one it belongs to.
    assert [e["started_at"] for e in acct.epochs_of(state, funded)] == [
        "2026-02-01T00:00:00Z", "2026-02-03T00:00:00Z",
    ]
    assert acct.epochs_of(state, funded)[0]["cause_of_death"] == "over-traded"
    assert [e["started_at"] for e in acct.epochs_of(state, paper)] == ["2026-02-02T00:00:00Z"]
    # The shapes they had before templates existed.
    assert funded.template.key == "lucid_pro" and funded.needs_cause is True
    assert paper.template.key == "paper" and paper.needs_cause is False


@_tmp
def test_the_migration_is_idempotent_and_survives_a_save():
    _v1_file({"epochs": [{"started_at": "2026-02-01T00:00:00Z"}], "paper_epochs": []})
    once = acct.load_state()
    acct.save_state(once)
    twice = acct.load_state()
    acct.save_state(twice)
    assert acct.load_state() == once


@_tmp
def test_the_clock_lock_is_a_property_of_the_shape_not_of_the_id():
    """Which accounts must be traded at the speed the day ran at.

    The page used to answer this with ``acctId === "funded"``, which the
    registry had already made the wrong question: every LucidDaily account made
    from a template kept the speed ladder, the scrubber and the held-Ctrl
    turbo, so the one shape whose floor moves *under an open position* was the
    one you could fast-forward the day on. It is a rule shape, like
    ``trailing``, and the view says so out loud.
    """
    assert acct.TEMPLATES["lucid_pro"].real_time
    assert acct.TEMPLATES["lucid_daily"].real_time
    # Paper is the practice surface and keeps the full transport.
    assert not acct.TEMPLATES["paper"].real_time

    daily = acct.Account("d1", "A daily", acct.TEMPLATES["lucid_daily"],
                         acct.Numbers())
    state = acct.load_state()
    acct.put_account(state, daily, epochs=[{"started_at": "2026-02-03T00:00:00Z"}])
    acct.save_state(state)
    v = acct.derive(account=acct.account_by_id("d1"),
                    now=_t("2026-02-03T18:00:00Z"))
    assert v["rules"]["real_time"] is True
    assert acct.derive(account=acct.PAPER,
                       now=_t("2026-02-03T18:00:00Z"))["rules"]["real_time"] is False


@_tmp
def test_an_account_walks_on_its_own_numbers():
    """The point of the registry: two accounts, two rulebooks, one walk."""
    tight = acct.Account(
        "tight", "LucidDaily 25K", acct.TEMPLATES["lucid_daily"],
        acct.Numbers(start=25_000.0, max_loss=1_000.0, trail_cap=26_500.0,
                     day_loss=500.0, profit_target=1_500.0),
    )
    state = acct.load_state()
    acct.put_account(state, tight, epochs=[{"started_at": "2026-02-03T00:00:00Z"}])
    acct.save_state(state)

    v = acct.derive(account=acct.account_by_id("tight"), now=_t("2026-02-03T18:00:00Z"))
    assert v["equity"] == 25_000.0
    assert v["floor"] == 24_000.0           # start − its own max_loss, not 2,000
    assert v["target_remaining"] == 1_500.0
    assert v["rules"]["trailing"] == "intraday"
    assert v["rules"]["day_loss"] == 500.0
    # A stored account's edited numbers are what answers, never the seed's.
    assert acct.account_by_id("tight").numbers.max_loss == 1_000.0


@_tmp
def test_an_unknown_template_falls_back_rather_than_dropping_the_account():
    """A file from a newer version, or a hand edit. The safe reading of "I do
    not know this shape" is the strictest shape there is — never no account,
    because an account that vanished takes its sittings' pricing with it."""
    state = acct.load_state()
    state["accounts"].append({"id": "odd", "label": "Odd", "template": "from_the_future",
                              "numbers": {}, "epochs": []})
    acct.save_state(state)
    odd = acct.account_by_id("odd")
    assert odd is not None and odd.template.key == acct.DEFAULT_TEMPLATE


@_tmp
def test_a_sitting_is_priced_by_its_account_id_and_it_cannot_be_moved():
    _epoch("2026-02-03T00:00:00Z")
    a = _sitting("2026-02-03T14:00:00Z", -500.0)
    assert replays.account_id_of(a) == "funded"
    # The same refusal `mode` gets, and for the same reason: "relabel the losing
    # sitting" must not be a way to move a loss onto an account that never took it.
    try:
        replays.patch(a["id"], account_id="paper")
        raise AssertionError("an attempt's account should be fixed when it opens")
    except ValueError as e:
        assert "fixed when it opens" in str(e)


@_tmp
def test_sittings_written_before_account_ids_are_attributed_by_mode():
    """Nothing on disk had to be migrated, so the fallback has to be exact."""
    _epoch("2026-02-03T00:00:00Z")
    _epoch("2026-02-03T00:00:00Z", account=acct.PAPER)
    old_funded = _sitting("2026-02-03T14:00:00Z", -500.0)
    old_paper = _sitting("2026-02-03T15:00:00Z", 700.0, mode="paper")
    for rec in (old_funded, old_paper):
        d = replays.attempt_dir(rec["id"])
        raw = replays._read_json(d / "attempt.json", {})
        raw.pop("account_id", None)          # as written before the field existed
        replays._write_json(d / "attempt.json", raw)

    assert replays.account_id_of(replays.read(old_funded["id"])) == "funded"
    assert replays.account_id_of(replays.read(old_paper["id"])) == "paper"
    funded = acct.derive(now=_t("2026-02-03T18:00:00Z"))
    paper = acct.derive(account=acct.PAPER, now=_t("2026-02-03T18:00:00Z"))
    assert (funded["equity"], paper["equity"]) == (49_500.0, 50_700.0)


@_tmp
def test_an_archived_account_is_hidden_from_the_list_but_still_walked():
    """Archive rather than delete: a deleted account leaves its sittings priced
    by nobody, which is the hole `account_id` immutability exists to close."""
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", -500.0)
    state = acct.load_state()
    funded = acct.account_by_id("funded", state)
    acct.put_account(state, acct.Account(
        funded.id, funded.label, funded.template, funded.numbers, archived=True))
    acct.save_state(state)

    ids = [a.id for a in acct.accounts(include_archived=False)]
    assert "funded" not in ids and "paper" in ids
    assert "funded" in [a.id for a in acct.accounts()]
    # Its history is untouched, and it still prices what it priced.
    assert acct.derive(account=acct.account_by_id("funded"),
                       now=_t("2026-02-03T18:00:00Z"))["equity"] == 49_500.0


# --- the intraday trail -----------------------------------------------------


def _daily(**numbers) -> acct.Account:
    """A LucidDaily account, opened and ready to be walked."""
    a = acct.Account("daily", "LucidDaily", acct.TEMPLATES["lucid_daily"],
                     acct.Numbers(**numbers))
    state = acct.load_state()
    acct.put_account(state, a, epochs=[{"started_at": "2026-02-03T00:00:00Z"}])
    acct.save_state(state)
    return acct.account_by_id("daily")


@_tmp
def test_an_intraday_floor_follows_the_booked_peak_inside_one_sitting():
    a = _daily()
    # Up 900 on the first trade, then giving 400 back. The peak was 50,900, so
    # the floor is 48,900 — under `eod` it would still be 48,000 all sitting.
    _sitting("2026-02-03T14:00:00Z", 500.0, account_id="daily",
             trades=[_trade(900), _trade(-400, 2)])
    v = acct.derive(account=a, now=_t("2026-02-03T18:00:00Z"), day="2026-02-03")
    assert v["equity"] == 50_500.0
    assert v["peak_close"] == 50_900.0
    assert v["floor"] == 48_900.0
    assert v["rules"]["trailing"] == "intraday"


@_tmp
def test_an_unrealised_high_the_sitting_reported_raises_the_floor():
    """The case that has no trace in `trades.json` at all.

    A runner that goes +800 and closes flat books one trade for nothing. Under a
    rule that only reads booked P&L the room it consumed comes back, which is
    exactly the rule LucidDaily does not have.
    """
    a = _daily()
    _sitting("2026-02-03T14:00:00Z", 0.0, account_id="daily",
             trades=[_trade(0)], excursion={"peak_usd": 800.0, "trough_usd": 0.0,
                                            "min_room_usd": 2_000.0})
    v = acct.derive(account=a, now=_t("2026-02-03T18:00:00Z"), day="2026-02-03")
    assert v["equity"] == 50_000.0
    assert v["floor"] == 48_800.0


@_tmp
def test_peak_then_dip_is_a_death_and_a_bare_peak_could_not_say_so():
    """Why the sitting reports three figures rather than one.

    Peak-then-dip and dip-then-peak have identical `peak_usd` and identical
    booked paths, and only one of them is a death: the floor the dip fell
    through is the raised one. `min_room_usd` is the browser's verdict at the
    time, and the only reading that can tell them apart.
    """
    a = _daily()
    # Ran +900 (floor → 48,900), then down to 48,850 — 50 under the floor it had
    # just raised — and closed at 50,100. Settled well above the opening floor.
    _sitting("2026-02-03T14:00:00Z", 100.0, account_id="daily",
             trades=[_trade(100)],
             excursion={"peak_usd": 900.0, "trough_usd": -1_150.0,
                        "min_room_usd": -50.0})
    v = acct.derive(account=a, now=_t("2026-02-03T18:00:00Z"), day="2026-02-03")
    assert v["status"] == "blown"
    assert v["last_death"] is not None

    # The same sitting with the room it actually had left: alive.
    a2 = _daily()
    state = acct.load_state()
    acct.put_account(state, acct.Account("safe", "Safe", acct.TEMPLATES["lucid_daily"],
                                         acct.Numbers()),
                     epochs=[{"started_at": "2026-02-03T00:00:00Z"}])
    acct.save_state(state)
    _sitting("2026-02-03T15:00:00Z", 100.0, account_id="safe",
             trades=[_trade(100)],
             excursion={"peak_usd": 900.0, "trough_usd": -100.0,
                        "min_room_usd": 1_900.0})
    assert acct.derive(account=acct.account_by_id("safe"),
                       now=_t("2026-02-03T18:00:00Z"), day="2026-02-03")["status"] == "live"
    assert a2 is not None


@_tmp
def test_a_sitting_with_no_reading_degrades_to_the_booked_path():
    """Every attempt written before this shipped, and every one the sweep
    settled on an old autosave. The fallback must cost the account a *rule*,
    never a life: a missing figure lowers the floor, it never invents a death."""
    a = _daily()
    _sitting("2026-02-03T14:00:00Z", -300.0, account_id="daily", trades=[_trade(-300)])
    v = acct.derive(account=a, now=_t("2026-02-03T18:00:00Z"), day="2026-02-03")
    assert v["status"] == "live"
    assert v["equity"] == 49_700.0
    # The booked path never went above the start, so the floor is the opening one.
    assert v["floor"] == 48_000.0


@_tmp
def test_the_eod_walk_ignores_excursion_figures_entirely():
    """`eod` must be byte-identical whether or not a sitting reports a path.
    LucidPro is a shipped account with real epochs on disk; this is the
    regression that protects them."""
    _epoch("2026-02-03T00:00:00Z")
    _sitting("2026-02-03T14:00:00Z", 100.0,
             excursion={"peak_usd": 5_000.0, "trough_usd": -4_000.0,
                        "min_room_usd": -3_000.0})
    v = acct.derive(now=_t("2026-02-03T18:00:00Z"), day="2026-02-03")
    assert v["status"] == "live"
    assert v["floor"] == 48_000.0
    assert v["peak_close"] == 50_000.0


@_tmp
def test_the_intraday_floor_never_falls():
    """The invariant every defect in this area violates."""
    a = _daily()
    for i, (net, peak) in enumerate([(600.0, 700.0), (-400.0, 100.0), (250.0, 300.0)]):
        _sitting(f"2026-02-0{3 + i}T14:00:00Z", net, account_id="daily",
                 date=f"2026-02-0{3 + i}", trades=[_trade(net)],
                 excursion={"peak_usd": peak, "trough_usd": min(0.0, net),
                            "min_room_usd": 1_000.0})
    floors = [
        acct.derive(account=a, now=_t("2026-02-06T18:00:00Z"), day=d)["floor"]
        for d in ("2026-02-03", "2026-02-04", "2026-02-05", None)
    ]
    assert floors == sorted(floors), floors


# --- the paper account ------------------------------------------------------


@_tmp
def test_the_two_accounts_cannot_reach_each_other():
    _epoch("2026-02-03T00:00:00Z")
    _epoch("2026-02-03T00:00:00Z", account=acct.PAPER)
    _sitting("2026-02-03T14:00:00Z", -500.0)
    _sitting("2026-02-03T15:00:00Z", 700.0, mode="paper")
    # And a drill, which is neither's.
    _drill("2026-02-03T16:00:00Z", -900.0)

    funded = acct.derive(now=_t("2026-02-03T18:00:00Z"))
    paper = acct.derive(account=acct.PAPER, now=_t("2026-02-03T18:00:00Z"))
    assert (funded["equity"], funded["epoch"]["sittings"]) == (49_500.0, 1)
    assert (paper["equity"], paper["epoch"]["sittings"]) == (50_700.0, 1)
    assert (funded["account"], paper["account"]) == ("funded", "paper")


@_tmp
def test_a_blown_paper_account_resets_in_the_same_second():
    """The one difference the whole mode exists for. Same floor, same death —
    but nothing is owed, so the next sitting opens on a fresh $50,000. (On the
    funded account the difference is now one sentence rather than one sentence
    and a day: ``COOLDOWN_S`` went on 2026-08-24.)"""
    _epoch("2026-02-03T00:00:00Z", account=acct.PAPER)
    _sitting("2026-02-03T14:00:00Z", -2_100.0, mode="paper")

    now = _t("2026-02-03T14:30:00Z")
    dead = acct.derive(account=acct.PAPER, now=now)
    assert dead["equity"] == 47_900.0
    assert dead["last_death"]["floor"] == 48_000.0
    # Never `blown`: no write-up is owed, so a paper death lands straight here.
    assert dead["status"] == "can_reset"
    assert dead["can_reset"] is True
    assert acct.refusal(account=acct.PAPER, now=now) is None

    # Which the create route is what acts on — the epoch is minted there.
    acct.ensure_epoch(account=acct.PAPER, now=now)
    fresh = acct.derive(account=acct.PAPER, now=now)
    assert fresh["equity"] == 50_000.0
    assert fresh["status"] == "live"
    assert fresh["epoch"]["index"] == 1


@_tmp
def test_a_funded_death_leaves_paper_open_and_the_other_way_round():
    _epoch("2026-02-03T00:00:00Z")
    _epoch("2026-02-03T00:00:00Z", account=acct.PAPER)
    _sitting("2026-02-03T14:00:00Z", -2_100.0)

    now = _t("2026-02-03T15:00:00Z")
    assert acct.derive(now=now)["status"] == "blown"
    assert acct.refusal(now=now)["code"] == "blown"
    # Paper never saw it. This is the one thing about two accounts on one file
    # that would be invisible if it broke — the paper page would simply refuse.
    assert acct.derive(account=acct.PAPER, now=now)["status"] == "live"
    assert acct.refusal(account=acct.PAPER, now=now) is None


@_tmp
def test_a_paper_flag_is_papers_own_and_refuses_nothing():
    """Paper used to owe the review exactly as the funded account did — it was
    the one thing a paper death still cost. Since 2026-08-25 neither owes it, so
    what is pinned here is the part that still matters: a mark made on one
    account's sitting is invisible to the other, the same way its equity is."""
    from api.routers import replays as router

    _epoch("2026-01-01T00:00:00Z", account=acct.PAPER)
    done = _finish([_paced(120, 1, at_s=0)], mode="paper")
    assert done["mode"] == "paper"
    assert done["status"] == "finished"
    assert acct.refusal(account=acct.PAPER) is None

    router.patch_replay(done["id"], router.PatchIn(review_later=True))
    assert acct.derive(account=acct.PAPER)["review_flagged"]["attempt_id"] == done["id"]
    assert acct.refusal(account=acct.PAPER) is None
    # The funded account never sees it.
    assert acct.derive()["review_flagged"] is None


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


@_tmp
def test_the_route_makes_an_account_from_a_template_and_prices_a_sitting_on_it():
    from api.routers import replays as router

    made = router.create_account(router.AccountIn(
        label="LucidDaily 25K", template="lucid_daily",
        numbers={"start": 25_000, "max_loss": 1_000, "day_loss": 500, "day_goal": 400},
    ))
    assert made["id"] == "luciddaily-25k"
    assert made["trailing"] == "intraday"
    assert made["numbers"]["day_goal"] == 400.0

    opened = router.create_replay(router.CreateIn(
        symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
        engine_version=1, tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"],
        account_id="luciddaily-25k",
    ))
    # Stamped, and the account it names is the one that walks it.
    assert opened["account_id"] == "luciddaily-25k"
    router.save_replay(
        opened["id"],
        router.SaveIn(log=LOG, trades=[_trade(-200)], summary={"net_usd": -200.0},
                      status="finished"),
        BackgroundTasks(),
    )
    v = router.get_account(account="luciddaily-25k", date="2026-02-03")
    assert v["equity"] == 24_800.0
    assert v["day_net"] == -200.0
    assert v["day_loss_remaining"] == 300.0
    # And the funded account never saw it.
    assert router.get_account()["equity"] == 50_000.0


@_tmp
def test_the_route_refuses_to_reshape_or_orphan_an_account():
    from api.routers import replays as router
    from fastapi import HTTPException

    router.create_account(router.AccountIn(label="Scratch", template="lucid_pro"))

    # The rule shape is fixed: its sittings were traded under it.
    try:
        router.patch_account("scratch", router.AccountIn(label="Scratch", template="lucid_daily"))
        raise AssertionError("a template swap should be refused")
    except HTTPException as e:
        assert e.status_code == 409 and "fixed when it opens" in str(e.detail)

    # Numbers and the name are editable, and the epochs survive the edit.
    state = acct.load_state()
    acct.put_account(state, acct.account_by_id("scratch", state),
                     epochs=[{"started_at": "2026-02-03T00:00:00Z"}])
    acct.save_state(state)
    router.patch_account("scratch", router.AccountIn(
        label="Scratch 25K", template="lucid_pro", numbers={"start": 25_000}))
    edited = acct.account_by_id("scratch")
    assert (edited.label, edited.numbers.start) == ("Scratch 25K", 25_000.0)
    assert len(acct.epochs_of(acct.load_state(), edited)) == 1

    # Untraded: deletable.
    router.create_account(router.AccountIn(label="Spare", template="lucid_pro"))
    assert router.delete_account("spare") == {"deleted": "spare"}

    # Traded: archived instead, never deleted — its sittings would be priced by
    # nobody.
    opened = replays.create(
        symbol="NQH5", root="NQ", date="2026-02-03", tz="New York", engine_version=1,
        tape=TAPE, prefs=PREFS, started_ms=TAPE["rth_open_ms"], account_id="scratch",
    )
    assert opened["account_id"] == "scratch"
    try:
        router.delete_account("scratch")
        raise AssertionError("an account with history should not be deletable")
    except HTTPException as e:
        assert e.status_code == 409 and e.detail["code"] == "has_history"

    # The built-ins are never deletable at all.
    for built_in in ("funded", "paper"):
        try:
            router.delete_account(built_in)
            raise AssertionError(f"{built_in} should not be deletable")
        except HTTPException as e:
            assert e.status_code == 409 and "built in" in str(e.detail)


@_tmp
def test_an_archived_account_refuses_to_open_a_new_sitting():
    from api.routers import replays as router
    from fastapi import HTTPException

    router.create_account(router.AccountIn(label="Retired", template="lucid_pro"))
    router.patch_account("retired", router.AccountIn(label="Retired", archived=True))
    try:
        router.create_replay(router.CreateIn(
            symbol="NQH5", root="NQ", date="2026-02-03", tz="New York",
            engine_version=1, tape=TAPE, prefs=PREFS,
            started_ms=TAPE["rth_open_ms"], account_id="retired",
        ))
        raise AssertionError("an archived account should refuse")
    except HTTPException as e:
        assert e.status_code == 409 and e.detail["code"] == "archived"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
