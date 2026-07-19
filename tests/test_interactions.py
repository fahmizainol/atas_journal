"""Interactions v9 additions: gap-closer attribution, static ref levels,
snap classes + same-minute confluence, and the new aggregates.

All synthetic — a session is built directly as a ``_Session`` with hand-drawn
price/level paths, so every classification is hand-checkable.

Run directly:  ``.venv/bin/python tests/test_interactions.py``
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.sim import interactions as inter  # noqa: E402
from journal.sim.interactions import (  # noqa: E402
    InteractionConfig,
    _Series,
    _Session,
    _acceptance_decay_agg,
    _detect,
    _gap_closer,
    _gap_closer_agg,
    _snap_class_label,
    _snap_conf_label,
    _vasnap_agg,
)

DAY = date(2026, 3, 2)
CFG = InteractionConfig.build("NQ", DAY, DAY)
T0 = 1_772_000_000 - (1_772_000_000 % 60)  # arbitrary epoch minute
OLD_ANCHOR = T0 - 86_400  # a level mature enough that warmup never gates


def _session(close: np.ndarray, series: list[_Series]) -> _Session:
    n = len(close)
    t0 = datetime(DAY.year, DAY.month, DAY.day, 9, 30)
    return _Session(
        day=DAY,
        minute_utc=np.array([T0 + 60 * i for i in range(n)], dtype="int64"),
        et_time=[(t0 + timedelta(minutes=i)).time() for i in range(n)],
        high=close + 1.0, low=close - 1.0, close=close.astype(float),
        volume=np.full(n, 100.0), delta=np.zeros(n),
        series=series,
        ny_mid=np.full(n, 0.0), ny_up1=np.full(n, 1.0), ny_up2=np.full(n, 2.0),
        ny_lo1=np.full(n, -1.0), ny_lo2=np.full(n, -2.0),
    )


def _static(kind: str, px: float, n: int, source: str = "ref") -> _Series:
    return _Series(source, kind, np.full(n, px), False, OLD_ANCHOR)


def test_gap_closer_price_led():
    # Price walks down onto a static level: the gap closes by price alone.
    n = 100
    close = np.full(n, 120.0)
    close[8] = 110.0
    close[9] = 105.0
    close[10] = 101.0          # touches the 100 level (tolerance 2)
    close[11:] = 115.0         # bounce -> reject
    sess = _session(close, [_static("ONH", 100.0, n)])
    touches, snaps, _ = _detect(sess, CFG)
    assert len(touches) == 1 and not snaps
    t = touches[0]
    assert t["label"] == "ONH" and t["source"] == "ref"
    assert t["closed_by"] == "price"
    assert t["price_closed_pts"] > 0 and t["level_closed_pts"] == 0.0
    assert t["outcome"] == "reject"
    print("gap closer price-led ok")


def test_gap_closer_level_led():
    # The level falls onto flat price — the touch is the level's, not a test.
    n = 100
    close = np.full(n, 99.0)
    values = np.maximum(100.0, 130.0 - 2.0 * np.arange(n))  # hits 100 at bar 15
    sess = _session(close, [_Series("ny", "VAH", values, False, OLD_ANCHOR)])
    touches, _, _ = _detect(sess, CFG)
    assert touches, "falling level should register a touch"
    t = touches[0]
    assert t["closed_by"] == "level"
    assert t["level_closed_pts"] > 0 and t["price_closed_pts"] == 0.0
    print("gap closer level-led ok")


def test_gap_closer_direct():
    # Both converge equally -> "both"; neither converges -> "drift".
    vals = np.array([110.0, 110, 110, 110, 110, 105])
    close = np.array([90.0, 90, 90, 90, 90, 95])
    assert _gap_closer(vals, close, 5)[0] == "both"
    flat_v = np.full(6, 110.0)
    flat_c = np.full(6, 109.0)
    assert _gap_closer(flat_v, flat_c, 5)[0] == "drift"
    assert _gap_closer(flat_v, flat_c, 0)[0] == "unknown"
    print("gap closer direct ok")


def test_snap_class_and_confluence():
    # Two VA boundaries snap over price in the same minute: a 35-pt node-flip
    # and an 8-pt creep. Each should see the other as a co-snap.
    n = 100
    close = np.full(n, 100.0)
    flip = np.where(np.arange(n) < 20, 90.0, 130.0)   # jump 40 >= SNAP_FLIP_PTS
    creep = np.where(np.arange(n) < 20, 97.0, 105.0)  # jump 8 < SNAP_FLIP_PTS
    sess = _session(close, [
        _Series("ny", "VAH", flip, True, OLD_ANCHOR),
        _Series("globex", "VAH", creep, True, OLD_ANCHOR),
    ])
    _, snaps, _ = _detect(sess, CFG)
    assert len(snaps) == 2
    by_class = {s["snap_class"]: s for s in snaps}
    assert set(by_class) == {"node_flip", "creep"}
    assert all(s["co_snaps"] == 1 for s in snaps)
    assert all(s["snap_dir"] == "up_over_price" for s in snaps)

    # The class/confluence labelers drive their aggregate buckets.
    for s in snaps:  # minimal reversion fields the aggregate reads
        s.update({"vwap_dist_pts": 10.0, "revert_min": 5, "revert_move": 8.0,
                  "adverse_move": 2.0})
    class_rows = {r["label"] for r in _vasnap_agg(snaps, _snap_class_label)}
    assert class_rows == {"node_flip up_over_price", "creep up_over_price"}
    conf_rows = _vasnap_agg(snaps, _snap_conf_label)
    assert [r["label"] for r in conf_rows] == ["multi-level up_over_price"]
    assert conf_rows[0]["n"] == 2
    print("snap class + confluence ok")


def test_vwap_midline_is_touchable():
    # The midline rides at 100; price comes down through it — a touch on the
    # "NY VWAP" label must fire (it was the one level you couldn't touch).
    n = 100
    close = np.full(n, 120.0)
    close[10] = 101.0
    close[11:] = 112.0
    sess = _session(close, [_static("VWAP", 100.0, n, source="ny")])
    touches, _, _ = _detect(sess, CFG)
    assert len(touches) == 1 and touches[0]["label"] == "NY VWAP"
    print("vwap midline touchable ok")


def test_new_aggregates_shape():
    # Assemble touches across nth buckets and closers; both new touch
    # aggregates must bucket them and end on the null-baseline row.
    n = 100
    close = np.full(n, 120.0)
    for i in (10, 20, 30, 40, 50, 60, 70):  # 7 spaced touches of one zone
        close[i] = 101.0
    close[75:] = 130.0
    sess = _session(close, [_static("pd POC", 100.0, n)])
    touches, _, _ = _detect(sess, CFG)
    assert len(touches) == 7
    assert [t["nth_touch"] for t in touches] == list(range(1, 8))

    decay = _acceptance_decay_agg(touches)
    assert [r["label"] for r in decay[:-1]] == ["1st", "2nd", "3rd", "4th-6th", "7th+"]
    assert decay[-1]["label"].startswith("null baseline")
    assert decay[3]["n"] == 3  # 4th-6th

    gap = _gap_closer_agg(touches)
    assert gap[-1]["label"].startswith("null baseline")
    assert all(r["n"] for r in gap[:-1])
    print("new aggregates shape ok")


def test_run_id_reflects_v9():
    assert inter.INTERACTIONS_VERSION == 9
    assert "_v9-" in CFG.run_id()
    assert "session_refs" in CFG.sources
    print("version bump ok")


if __name__ == "__main__":
    test_gap_closer_price_led()
    test_gap_closer_level_led()
    test_gap_closer_direct()
    test_snap_class_and_confluence()
    test_vwap_midline_is_touchable()
    test_new_aggregates_shape()
    test_run_id_reflects_v9()
    print("all interactions tests passed")
