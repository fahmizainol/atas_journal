"""Initial Balance / ORB study: session compute and the day-type classifier.

All synthetic and hand-computable — a session is built as one minute-bar frame
whose path is chosen to land in a known day type, then ``session_row`` must
read back the IB, breaks, extensions and classification that path encodes.

Run directly:  ``.venv/bin/python tests/test_ib.py``
"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from journal.sim import ib as ibmod  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = date(2025, 10, 13)
CFG = ibmod.IbConfig.build("NQ", DAY, DAY)


def _bars(segments) -> pd.DataFrame:
    """Minute bars from (minutes, open, high, low, close) segments starting at
    09:30 ET. Each segment repeats its OHLC for `minutes` bars."""
    t0 = datetime(DAY.year, DAY.month, DAY.day, 9, 30, tzinfo=ET)
    rows = []
    i = 0
    for minutes, o, h, l, c in segments:
        for _ in range(minutes):
            ts = (t0 + timedelta(minutes=i)).astimezone(ZoneInfo("UTC"))
            rows.append({"ts_utc": pd.Timestamp(ts), "open": float(o),
                         "high": float(h), "low": float(l), "close": float(c),
                         "volume": 100.0, "end_idx": i})
            i += 1
    return pd.DataFrame(rows)


def test_normal_day_no_break():
    # IB 100..110, afternoon stays inside — no break, IB is 100% of the range.
    b = _bars([(60, 105, 110, 100, 105), (330, 105, 109, 101, 104)])
    r = ibmod.session_row(b, CFG)
    assert r["ib_high"] == 110 and r["ib_low"] == 100 and r["ib_range"] == 10
    assert not r["broke_up"] and not r["broke_down"]
    assert r["first_break"] is None and r["second_break"] is None
    assert r["day_type"] == "normal"
    assert r["ib_pct_of_day"] == 1.0
    print("normal day ok")


def test_normal_variation_up():
    # Breaks up to 115 (ext 0.5x, range 1.5x IB), closes mid — normal variation.
    b = _bars([(60, 105, 110, 100, 105), (60, 105, 115, 104, 112), (270, 112, 113, 106, 108)])
    r = ibmod.session_row(b, CFG)
    assert r["broke_up"] and not r["broke_down"]
    assert r["first_break"]["side"] == "up"
    assert r["first_break"]["min_after_open"] == 60  # first post-IB minute
    assert r["ext_up_x"] == 0.5 and r["max_ext_x"] == 0.5
    assert r["day_type"] == "normal_variation"
    assert r["close_beyond_break"] is False  # 108 back under 110
    print("normal variation ok")


def test_trend_day_up():
    # Runs to 135 (range 3.5x IB) and closes at the high — trend.
    b = _bars([(60, 105, 110, 100, 105), (300, 105, 130, 104, 128), (30, 128, 135, 127, 135)])
    r = ibmod.session_row(b, CFG)
    assert r["range_x"] == 3.5
    assert r["day_type"] == "trend"
    assert r["close_beyond_break"] is True
    print("trend day ok")


def test_big_range_mid_close_stays_normal_variation():
    # Same 3.5x range but closes mid-range — not a trend day.
    b = _bars([(60, 105, 110, 100, 105), (300, 105, 135, 104, 130), (30, 130, 131, 114, 115)])
    r = ibmod.session_row(b, CFG)
    assert r["range_x"] == 3.5
    assert r["day_type"] == "normal_variation"
    print("mid-close big range ok")


def test_neutral_day_second_break_and_extreme():
    # Breaks up first, then down, closes at the low — neutral extreme; the
    # second break (down) is on the close's side.
    b = _bars([(60, 105, 110, 100, 105),
               (60, 105, 114, 104, 112),   # first break up @min 60
               (60, 110, 111, 95, 96),     # second break down @min 120
               (210, 96, 97, 94, 94.5)])   # close near the low
    r = ibmod.session_row(b, CFG)
    assert r["broke_both"]
    assert r["first_break"]["side"] == "up"
    assert r["second_break"]["side"] == "down"
    assert r["second_break"]["min_after_open"] == 120
    assert r["day_type"] == "neutral_extreme"
    assert r["close_pos"] < 0.25
    print("neutral extreme ok")


def test_neutral_center():
    # Both sides broken, close mid-range.
    b = _bars([(60, 105, 110, 100, 105), (60, 105, 113, 104, 111),
               (60, 110, 111, 97, 99), (210, 99, 106, 98, 105)])
    r = ibmod.session_row(b, CFG)
    assert r["day_type"] == "neutral_center"
    print("neutral center ok")


def test_orb_windows_and_r():
    # First 5m candle: open 105 close 107, low 104 -> long, stop dist 3.
    # Session closes 116 -> move 9, r = 3.0.
    b = _bars([(5, 105, 108, 104, 107), (55, 107, 110, 100, 105), (330, 105, 117, 104, 116)])
    r = ibmod.session_row(b, CFG)
    o5 = r["orb"]["5"]
    assert o5["dir"] == 1 and o5["follow"] is True
    assert o5["move_pts"] == 9.0 and o5["r_mult"] == 3.0
    # 30m window spans both opening segments: open 105, close 105 @min29 (same
    # segment) -> the window candle is the segment's OHLC repeated; dir 0 is
    # impossible here since close(107 segment) ... just assert it exists.
    assert r["orb"]["30"]["window"] == 30
    print("orb ok")


def test_gap_and_globex_context():
    b = _bars([(60, 105, 110, 100, 105), (330, 105, 112, 101, 108)])
    r = ibmod.session_row(b, CFG, on_high=111.0, on_low=99.0,
                          prior_close=95.0, adr14=20.0)
    assert r["gap_pts"] == 10.0 and r["gap_x"] == 0.5
    assert r["ib_vs_adr"] == 0.5
    assert r["open_vs_on"] == "inside"
    assert r["ib_vs_on"] == "inside"
    # IB above the ON high -> broke_high
    r2 = ibmod.session_row(b, CFG, on_high=108.0, on_low=99.0)
    assert r2["ib_vs_on"] == "broke_high"
    print("context ok")


def test_aggregates_shapes():
    days = []
    for i, seg in enumerate([
        [(60, 105, 110, 100, 105), (330, 105, 109, 101, 104)],           # normal
        [(60, 105, 110, 100, 105), (330, 105, 130, 104, 129)],           # trend-ish
        [(60, 105, 110, 100, 105), (60, 105, 113, 104, 111),
         (270, 110, 111, 96, 97)],                                       # neutral
    ]):
        r = ibmod.session_row(_bars(seg), CFG)
        r["day"] = (date(2025, 10, 13) + timedelta(days=i)).isoformat()
        days.append(r)
    br = ibmod._break_rates(days)
    assert any(row["label"] == "broke both sides" and row["n"] == 1 for row in br)
    dt = ibmod._day_types(days)
    assert sum(row["n"] for row in dt) == 3
    ext = ibmod._ext_distribution(days)
    assert any(row["label"].startswith("reached ≥2.0") for row in ext)
    ep = ibmod._break_epilogue(days)
    assert any("double break" in row["label"] for row in ep)
    orb = ibmod._orb_follow(days, CFG.orb_windows)
    assert len(orb) == 3
    wd = ibmod._weekday_cuts(days)
    assert sum(row["n"] for row in wd) == 3
    print("aggregates ok")


# --- the chart overlay: the study's window, drawn ---------------------------


def _tick_frame(prices) -> pd.DataFrame:
    """One tick per minute from 09:30 ET, priced from `prices`."""
    t0 = pd.Timestamp(datetime(DAY.year, DAY.month, DAY.day, 9, 30, tzinfo=ET)).tz_convert("UTC")
    ts = pd.date_range(t0, periods=len(prices), freq="1min", tz="UTC")
    return pd.DataFrame({
        "ts_utc": ts, "price": [float(p) for p in prices], "size": 1.0, "side": "N",
    })


def test_chart_overlay_measures_only_the_ib_window():
    # 60 min oscillating 100/110, then 10 min at 130: the day's high is NOT the
    # IB's high, and the drawn levels must be the study's, not the session's.
    f = _tick_frame([100 if i % 2 else 110 for i in range(60)] + [130] * 10)
    bar_ts = f["ts_utc"].iloc[::5]  # 5-minute "bars"
    times = bar_ts.astype("int64").to_numpy() // 10**9  # display axis, any convention
    ib = ibmod.chart_overlay(f, DAY, bar_ts, times)
    assert ib["high"] == 110 and ib["low"] == 100
    # Endpoints snapped onto the drawn bar grid: the bell, the bar at 10:30
    # (where the extension guides start), the last bar.
    assert ib["start"] == times[0]
    assert ib["formed"] == times[12]
    assert ib["end"] == times[-1]
    print("chart overlay window ok")


def test_chart_overlay_absent_when_the_window_never_completes():
    # 45 minutes of data: the IB hasn't formed, so nothing is drawn — honest
    # absence, same rule as the weekly anchor.
    f = _tick_frame([100] * 45)
    bar_ts = f["ts_utc"].iloc[::5]
    times = bar_ts.astype("int64").to_numpy() // 10**9
    assert ibmod.chart_overlay(f, DAY, bar_ts, times) is None
    assert ibmod.chart_overlay(f.iloc[:0], DAY, bar_ts, times) is None
    print("chart overlay honest absence ok")


def test_width_bucket_edges():
    lo, hi = ibmod.WIDTH_TERCILE_EDGES
    assert ibmod.width_bucket(None) is None  # no adr14 denominator yet
    assert ibmod.width_bucket(lo - 0.01) == "narrow"
    assert ibmod.width_bucket(lo) == "mid"  # edges are inclusive into mid
    assert ibmod.width_bucket(hi) == "mid"
    assert ibmod.width_bucket(hi + 0.01) == "wide"
    print("width buckets ok")


@contextmanager
def _snapshot_dir(snapshots):
    """Point ``IB_DIR`` at a temp dir holding the given snapshots.

    Each snapshot is (symbol, start, end, ib_minutes, days) and carries only the
    fields ``session_widths`` reads. A context manager rather than a pytest
    fixture so this file still runs as a plain script (see the module docstring).
    """
    real = ibmod.IB_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ibmod.IB_DIR = Path(tmp)
        for symbol, start, end, ib_minutes, days in snapshots:
            blob = {"ib_version": ibmod.IB_VERSION, "symbol": symbol,
                    "start": start, "end": end, "ib_minutes": ib_minutes,
                    "days": days}
            (Path(tmp) / f"{symbol}_{start}-{end}_{ib_minutes}.json").write_text(
                json.dumps(blob))
        try:
            yield Path(tmp)
        finally:
            ibmod.IB_DIR = real


def test_session_widths_reads_the_widest_snapshot():
    # A one-month snapshot and a full-window one covering the same day with a
    # *different* ib_vs_adr — the wide one must win, because its adr14 chain is
    # the longer one and the chip has to mean the same thing in every window.
    with _snapshot_dir([
        ("NQ", "2025-06-01", "2025-06-30", 60,
         [{"day": "2025-06-10", "ib_range": 100.0, "ib_vs_adr": 0.30, "adr14": 333.0}]),
        ("NQ", "2025-02-01", "2026-06-30", 60,
         [{"day": "2025-06-10", "ib_range": 100.0, "ib_vs_adr": 0.80, "adr14": 125.0},
          {"day": "2025-06-11", "ib_range": 90.0, "ib_vs_adr": None, "adr14": None},
          {"day": "2026-01-05", "ib_range": 400.0, "ib_vs_adr": 0.55, "adr14": 727.0}]),
    ]):
        out = ibmod.session_widths("NQ", date(2025, 6, 1), date(2025, 6, 30))
    assert out["source"]["start"] == "2025-02-01"
    assert out["tercile_edges"] == list(ibmod.WIDTH_TERCILE_EDGES)
    # Sliced to the requested range: the January day is in the snapshot, not the ask.
    assert set(out["days"]) == {"2025-06-10", "2025-06-11"}
    assert out["days"]["2025-06-10"]["width"] == "wide"
    # Inside the ADR warm-up: present, but with no bucket rather than a made-up one.
    assert out["days"]["2025-06-11"]["width"] is None
    print("session widths snapshot pick ok")


def test_session_widths_honest_absence():
    # Wrong symbol, and a right-symbol snapshot at a non-default IB window: the
    # terciles were cut on 60-minute widths, so a 30-minute study can't feed them.
    with _snapshot_dir([
        ("ES", "2025-02-01", "2026-06-30", 60,
         [{"day": "2025-06-10", "ib_range": 50.0, "ib_vs_adr": 0.9, "adr14": 55.0}]),
        ("NQ", "2025-02-01", "2026-06-30", 30,
         [{"day": "2025-06-10", "ib_range": 100.0, "ib_vs_adr": 0.9, "adr14": 111.0}]),
    ]):
        out = ibmod.session_widths("NQ", date(2025, 6, 1), date(2025, 6, 30))
        # No snapshots to read at all, but the pinned edges still come back so
        # the UI can render its caption over an empty column.
        gone = ibmod.session_widths("XX", date(2030, 1, 1), date(2030, 1, 2))
    assert out["run_id"] is None and out["source"] is None and out["days"] == {}
    assert gone["days"] == {} and gone["tercile_edges"] == list(ibmod.WIDTH_TERCILE_EDGES)
    print("session widths honest absence ok")


def test_day_context_reads_the_widest_snapshot():
    # Same rule as session_widths and for the same reason: the adr14 chain makes
    # a day's denominator a property of the window it was computed in, so the
    # simulator has to be handed the full run's number, not the short run's.
    with _snapshot_dir([
        ("NQ", "2025-06-01", "2025-06-30", 60,
         [{"day": "2025-06-10", "ib_range": 100.0, "ib_vs_adr": 0.30, "adr14": 333.0}]),
        ("NQ", "2025-02-01", "2026-06-30", 60,
         [{"day": "2025-06-10", "ib_range": 100.0, "ib_vs_adr": 0.80, "adr14": 125.0},
          {"day": "2025-06-11", "ib_range": 90.0, "ib_vs_adr": None, "adr14": None}]),
    ]):
        got = ibmod.day_context("NQ", date(2025, 6, 10))
        warmup = ibmod.day_context("NQ", date(2025, 6, 11))
        uncovered = ibmod.day_context("NQ", date(2030, 1, 1))
    assert got["adr14"] == 125.0
    assert got["source"]["start"] == "2025-02-01"
    # Honest absence twice: inside the ADR warm-up, and outside the study.
    assert warmup["adr14"] is None
    assert uncovered["adr14"] is None and uncovered["source"] is not None
    print("day context snapshot pick ok")


def test_day_context_cache_follows_the_files():
    # The lookup is cached (a per-session endpoint calls it), so the cache must
    # not outlive the snapshots it read. Two directories in a row, same symbol
    # and same day, different answers.
    with _snapshot_dir([
        ("NQ", "2025-02-01", "2026-06-30", 60,
         [{"day": "2025-06-10", "ib_range": 100.0, "ib_vs_adr": 0.8, "adr14": 125.0}]),
    ]):
        first = ibmod.day_context("NQ", date(2025, 6, 10))
    with _snapshot_dir([
        ("NQ", "2025-02-01", "2026-06-30", 60,
         [{"day": "2025-06-10", "ib_range": 100.0, "ib_vs_adr": 0.4, "adr14": 250.0}]),
    ]):
        second = ibmod.day_context("NQ", date(2025, 6, 10))
        # And with no snapshots at all there is nothing to hand back.
        empty = ibmod.day_context("ES", date(2025, 6, 10))
    assert first["adr14"] == 125.0 and second["adr14"] == 250.0
    assert empty["adr14"] is None and empty["source"] is None
    print("day context cache invalidation ok")


if __name__ == "__main__":
    test_normal_day_no_break()
    test_normal_variation_up()
    test_trend_day_up()
    test_big_range_mid_close_stays_normal_variation()
    test_chart_overlay_measures_only_the_ib_window()
    test_chart_overlay_absent_when_the_window_never_completes()
    test_neutral_day_second_break_and_extreme()
    test_neutral_center()
    test_orb_windows_and_r()
    test_gap_and_globex_context()
    test_aggregates_shapes()
    test_width_bucket_edges()
    test_session_widths_reads_the_widest_snapshot()
    test_session_widths_honest_absence()
    test_day_context_reads_the_widest_snapshot()
    test_day_context_cache_follows_the_files()
    print("all ib tests passed")
