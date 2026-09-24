"""How ATAS lots group into flat->flat logical trades.

``build_logical_trades`` is whole-column: it walks every lot's open/close events
as one sorted array and finds the trade boundaries with a cumulative sum rather
than a loop. That rewrite rests on an argument worth stating, because the code
no longer says it out loud:

    at a session gap the running position is zero either way — either the
    previous instant left it flat, or the gap rule just force-closed it — so the
    points where the position restarts are fixed by the timestamps alone, and
    the position is a cumsum restarted at each of them.

The rules that argument has to keep are the ones pinned here. Two of them barely
appear in real exports — the session-gap force-close fires **once** across the
whole journal — so they are exactly the rules a later change could break with
every visible number still looking right.

Run directly:  ``PYTHONPATH=src .venv/bin/python -m pytest tests/test_logical_grouping.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from journal import trades  # noqa: E402

BASE = pd.Timestamp("2026-06-15 09:00:00", tz="UTC")
STAMPS = ("open_ts_utc", "close_ts_utc", "open_ts_local", "close_ts_local")


def _journal(*lots, account="Replay", source="take-1.xlsx", instrument="NQ") -> pd.DataFrame:
    """A journal frame from ``(open_min, close_min, volume)`` triples.

    Minutes are offsets from 09:00 UTC. ``volume`` is signed the way ATAS ships
    it — positive opens long — and the close mirrors it.
    """
    rows = []
    for i, lot in enumerate(lots):
        open_min, close_min, vol = lot[0], lot[1], lot[2]
        acct = lot[3] if len(lot) > 3 else account
        src = lot[4] if len(lot) > 4 else source
        opened, closed = BASE + pd.Timedelta(minutes=open_min), BASE + pd.Timedelta(minutes=close_min)
        rows.append({
            "dedupe_key": f"lot_{i}", "account": acct, "instrument": instrument,
            "source_file": src,
            "open_ts_utc": opened, "close_ts_utc": closed,
            "open_ts_local": opened, "close_ts_local": closed,
            "open_price": 100.0 + i, "open_volume": float(vol),
            "close_price": 105.0 + i, "close_volume": -float(vol),
            "price_pnl": 5.0, "profit_ticks": 20.0, "pnl": 10.0 * (i + 1),
            "comment": "",
        })
    df = pd.DataFrame(rows)
    for col in STAMPS:
        df[col] = df[col].astype("datetime64[us, UTC]")
    return df


# --- the session-gap force-close --------------------------------------------

def test_a_session_gap_force_closes_an_open_position():
    """Two lots that never net to flat are one trade — unless a session sits
    between them. Position drift must not merge yesterday into today."""
    # Open at 09:00 and again at 11:00: two hours apart, still holding.
    jr = _journal((0, 300, 1), (120, 301, 1))
    out = trades.build_logical_trades(jr)
    assert len(out) == 2, "the gap should have closed the first trade"
    assert [k for ks in out["lot_keys"] for k in ks] == ["lot_0", "lot_1"]


def test_the_same_lots_inside_the_gap_stay_one_trade():
    """The contrast that makes the test above mean something: identical shape,
    the second open merely closer, and it is a scale-in again."""
    jr = _journal((0, 300, 1), (30, 301, 1))
    out = trades.build_logical_trades(jr)
    assert len(out) == 1
    assert out.iloc[0]["lot_keys"] == ["lot_0", "lot_1"]
    assert out.iloc[0]["leg_count"] == 2
    assert out.iloc[0]["max_contracts"] == 2.0


def test_a_gap_over_a_flat_position_is_not_a_force_close():
    """Nothing to force-close when the position is already flat, so the gap adds
    no boundary of its own — the two trades are two because each went flat."""
    jr = _journal((0, 5, 1), (180, 185, 1))
    out = trades.build_logical_trades(jr)
    assert len(out) == 2
    assert [t[0] for t in out["lot_keys"]] == ["lot_0", "lot_1"]


# --- one instant is one batch -----------------------------------------------

def test_a_position_rolled_at_one_instant_does_not_split():
    """The flat check happens once per instant, after the whole instant.

    Closing one lot and opening the next on the same stamp momentarily nets the
    position to zero. Reading that as flat would cut one trade in half.
    """
    jr = _journal((0, 30, 1), (30, 40, 1))
    out = trades.build_logical_trades(jr)
    assert len(out) == 1, "the roll was read as a return to flat"
    assert out.iloc[0]["lot_keys"] == ["lot_0", "lot_1"]


def test_going_flat_ends_the_trade():
    """The other half of the same rule — flat really does close a trade."""
    jr = _journal((0, 30, 1), (40, 50, 1))
    assert len(trades.build_logical_trades(jr)) == 2


# --- what may never be merged -----------------------------------------------

def test_two_attempts_at_the_same_day_never_merge():
    """Each export is one replay attempt. Two takes at the same account,
    instrument and day interleave by timestamp; grouping on ``source_file`` is
    what lets the day view show take-1 and take-2 instead of one blend."""
    jr = _journal((0, 300, 1, "Replay", "take-1.xlsx"),
                  (1, 301, 1, "Replay", "take-2.xlsx"))
    out = trades.build_logical_trades(jr)
    assert len(out) == 2
    assert set(out["source_file"]) == {"take-1.xlsx", "take-2.xlsx"}


def test_accounts_never_merge():
    jr = _journal((0, 300, 1, "Live"), (1, 301, 1, "Replay"))
    out = trades.build_logical_trades(jr)
    assert len(out) == 2
    assert set(out["account"]) == {"Live", "Replay"}


# --- the numbers a trade carries --------------------------------------------

def test_a_scale_in_averages_on_size_and_sums_its_lots():
    jr = _journal((0, 300, 1), (30, 301, 3))
    row = trades.build_logical_trades(jr).iloc[0]
    assert row["max_contracts"] == 4.0
    assert row["direction"] == "Long"
    # open_price is 100 + i, weighted 1 and 3.
    assert row["avg_entry"] == pytest.approx((100.0 * 1 + 101.0 * 3) / 4)
    assert row["gross_pnl"] == 30.0 and row["net_pnl"] == 30.0
    assert row["duration_s"] == pytest.approx(301 * 60)


def test_a_short_is_named_off_the_first_open():
    assert trades.build_logical_trades(_journal((0, 30, -2))).iloc[0]["direction"] == "Short"


def test_trades_are_numbered_by_entry_time():
    jr = _journal((60, 65, 1), (0, 5, 1), (30, 35, 1))
    out = trades.build_logical_trades(jr)
    assert list(out["trade_no"]) == [1, 2, 3]
    assert list(out["entry_ts_utc"]) == sorted(out["entry_ts_utc"])
    assert [t[0] for t in out["lot_keys"]] == ["lot_1", "lot_2", "lot_0"]


# --- the key everything is filed under --------------------------------------

def test_lots_sharing_an_open_stamp_give_a_stable_key():
    """``trade_key`` is a hash of the trade's *first* lot, and notes, reviews,
    recall cards and rule checks are all filed under it. Lots opened on the same
    stamp must therefore have a defined order — the per-span sort this replaced
    left it to whatever quicksort did above 16 elements."""
    # 20 lots on one stamp: past the 16 below which numpy sorts by insertion and
    # is stable by accident, so this is the range where the old sort was arbitrary.
    jr = _journal(*[(0, 300, 1) for _ in range(20)], (30, 301, -20))
    first = trades.build_logical_trades(jr)
    assert len(first) == 1, "the lots never net to flat until the last one"
    # Journal order, not an arbitrary permutation of the tie. lot_20 is the
    # closing lot and opens later, so it sorts last.
    assert first.iloc[0]["lot_keys"] == [f"lot_{i}" for i in range(21)]
    # And it is the same answer every time it is asked.
    for _ in range(3):
        again = trades.build_logical_trades(jr)
        assert again.iloc[0]["trade_key"] == first.iloc[0]["trade_key"]
        assert again.iloc[0]["lot_keys"] == first.iloc[0]["lot_keys"]


def test_an_empty_journal_builds_nothing():
    assert trades.build_logical_trades(pd.DataFrame()).empty
    assert trades.build_logical_trades(None).empty


# --- fill markers -----------------------------------------------------------

def _executions(*stamps, account="Replay", instrument="NQ") -> pd.DataFrame:
    rows = [{
        "exchange_id": f"x{i}", "account": account, "instrument": instrument,
        "ts_utc": BASE + pd.Timedelta(minutes=m),
        "ts_local": BASE + pd.Timedelta(minutes=m),
        "direction": "Buy", "price": 100.0, "volume": 1.0,
    } for i, m in enumerate(stamps)]
    df = pd.DataFrame(rows)
    for col in ("ts_utc", "ts_local"):
        df[col] = df[col].astype("datetime64[us, UTC]")
    return df


def test_fills_are_the_ones_inside_the_trade_s_window():
    jr = _journal((10, 20, 1))
    out = trades.build_logical_trades(jr, _executions(5, 10, 15, 20, 25))
    # Inclusive at both ends: the entry and exit fills belong to the trade.
    assert [f["exchange_id"] for f in out.iloc[0]["fills"]] == ["x1", "x2", "x3"]


def test_fills_sharing_a_stamp_keep_their_export_order():
    """One order filling in pieces stamps every piece identically; their export
    order is the only order they have."""
    jr = _journal((10, 20, 1))
    out = trades.build_logical_trades(jr, _executions(15, 15, 15))
    assert [f["exchange_id"] for f in out.iloc[0]["fills"]] == ["x0", "x1", "x2"]


def test_a_trade_with_no_executions_has_no_markers():
    """A truncated Replay export means fewer markers, never wrong PnL."""
    jr = _journal((10, 20, 1))
    assert trades.build_logical_trades(jr, _executions(100, 200)).iloc[0]["fills"] is None
    assert trades.build_logical_trades(jr, None).iloc[0]["fills"] is None
    assert trades.build_logical_trades(jr).iloc[0]["fills"] is None


def test_fills_do_not_cross_accounts():
    jr = _journal((10, 20, 1), account="Replay")
    out = trades.build_logical_trades(jr, _executions(15, account="Live"))
    assert out.iloc[0]["fills"] is None
