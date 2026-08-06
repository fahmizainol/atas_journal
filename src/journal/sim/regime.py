"""Per-session regime KPIs from the dual anchored VWAPs.

The question this answers is not "did the run make money" but "what kind of day
was it" — and that is a property of the *market*, not of any run. So a regime
artifact is keyed by (symbol, session date) alone, cached on disk, and joined to
whatever run's P&L you happen to be looking at. Two runs over the same day read
the same regime; deleting a run doesn't invalidate it.

Everything is measured from 1-minute time bars, deliberately *not* the engine's
tick-count bars: a KPI whose denominator is "bars" would otherwise mean something
different at ticks_per_bar=200 than at 500, and the whole point is to compare
days, not configs. The bands themselves are the sim's tick-derived VWAP (see
vwap.py) — the same bands the engine trades — sampled at each minute's close.

Anti-leakage: every KPI is snapshotted at fixed intraday checkpoints as well as
at the close. A metric read from the "eod" snapshot is hindsight; one read from
"09:45" is what you could have known at 09:45. Anything that trains on these has
to be able to tell the two apart, so the artifact never collapses them.

Since v8 the artifact also carries a market-structure layer: the swing-pivot →
BOS/CHoCH state machine from the structure-events study (docs/research/
market-structure-events.md), run causally per checkpoint, plus the bar-overlap
chop measure from the winners-vs-losers study. Both studies found the *events*
carry no forward edge on their own — what survives is the texture and state,
which is exactly what a regime artifact is for. The structure KPIs describe the
day; nothing here trades a break.

Charts and KPIs are GETs, and a GET must never spend money at Databento — so
this reads the tick cache only (see ticks.cached_rth / cached_overnight). A day
whose overnight was never bought still gets its RTH-anchored KPIs, marked
``partial``; a day with no ticks at all gets nothing.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time

import numpy as np
import pandas as pd

from ..config import CACHE_DIR, ET_TZ
from . import ticks as tickmod
from . import vwap as vwapmod

REGIME_DIR = CACHE_DIR / "regime"

# Bump when a KPI's definition changes. Old files are then simply ignored and
# recomputed — never migrated, never silently reinterpreted under a new meaning.
REGIME_VERSION = 8

# What "knowable at time T" means. The intraday checkpoints are where a model
# would actually have to choose; "eod" is the hindsight snapshot.
CHECKPOINTS: list[tuple[str, time]] = [
    ("09:30", time(9, 30)),
    ("09:45", time(9, 45)),
    ("10:30", time(10, 30)),
    ("12:00", time(12, 0)),
    ("eod", time(16, 0)),
]

# Quadrant states, by where a close sits relative to the two anchors.
ABOVE_BOTH = "above_both"
BELOW_BOTH = "below_both"
ABOVE_GX = "above_gx_only"
ABOVE_NY = "above_ny_only"
# Pre-RTH there is only one anchor to be above or below.
ON_ABOVE = "on_above_gx"
ON_BELOW = "on_below_gx"

# One window for every slope KPI, so "slope" always means "over the last 30
# minutes" no matter which anchor or spread it is read from.
SPREAD_SLOPE_MIN = 30

# Structure-layer knobs. The swing threshold is ATR-scaled, not fixed points:
# a 40pt reversal is 300 swings on a high-vol day and 67 on a quiet one, so a
# fixed number means something different every session (structure-events study
# §7). Two tiers, exactly the study's internal-vs-swing split: the fine tier
# reads whipsaw density, the major tier reads the day's skeleton — bias, its
# age, and whether breaks continue it or flip it. The ATR that scales both is
# the median 14-bar ATR of the RTH bars available *at the checkpoint* (overnight
# bars before enough RTH exists), so the threshold itself is causal — an eod ATR
# would leak the day's volatility into its own 09:45 snapshot.
ST_FINE_MULT = 2.0
ST_MAJOR_MULT = 4.0
ST_ATR_BARS = 14
# The chop KPIs are an *occupancy*, not a session mean: a bar is "in chop" when
# its trailing 10-bar overlap runs at or above 0.60 — the winners-vs-losers
# study's own window and threshold family (overlap_10) — and the KPI is the
# share of minutes spent in that state. The obvious alternative, averaging the
# overlap over the whole session, is degenerate: 365 cached sessions land in
# 0.53–0.63 (std 0.016), because averaging 390 bars washes the texture out.
# Occupancy keeps the local measure local and has real spread (0.21–0.65).
CHOP_STATE_BARS = 10
CHOP_STATE_OVERLAP = 0.60
CHOP_WINDOW_MIN = 30
# A day that spends at least this share of its RTH minutes in chop-state reads
# "churny". Calibrated on the 365 full cached sessions 2025-02..2026-07 (mean
# occupancy 0.434, std 0.084): 0.48 sits just above the upper tercile, so
# churny is a real minority class (~29% of days) rather than a coin flip, and
# it is class-orthogonal — every class's mean occupancy lands in 0.42–0.44, so
# the label is not a re-reading of trend-vs-balance. Like classify()'s
# thresholds, this is a description of the collected sample, not a fit to P&L.
TEXTURE_CHURN_OCC = 0.48


# --- bars -------------------------------------------------------------------

def minute_bars(t: pd.DataFrame, freq: str = "1min") -> pd.DataFrame:
    """Time OHLCV bars from a tick frame, plus ``end_idx`` — the positional index
    of each bar's last tick back into *t*, which is how a bar's close is aligned
    with the running VWAP frames (they are one row per tick). ``freq`` is any
    pandas offset (default ``"1min"``); pass e.g. ``"5min"`` for coarser candles —
    the per-tick VWAP/profile frames sampled at the wider bar boundaries stay the
    true developing values, only sparser."""
    if t.empty:
        return pd.DataFrame(columns=["ts_utc", "open", "high", "low", "close",
                                     "volume", "end_idx"])
    g = t.assign(_i=np.arange(len(t)), _m=t["ts_utc"].dt.floor(freq)).groupby("_m", sort=True)
    b = g.agg(
        open=("price", "first"), high=("price", "max"), low=("price", "min"),
        close=("price", "last"), volume=("size", "sum"), end_idx=("_i", "last"),
    ).reset_index().rename(columns={"_m": "ts_utc"})
    return b


# --- primitives -------------------------------------------------------------

def _f(x) -> float | None:
    """NaN and inf are not JSON, and a silently-dropped key is worse than a null:
    a missing KPI must read as 'not computable here', not as zero."""
    if x is None:
        return None
    v = float(x)
    return None if not np.isfinite(v) else round(v, 4)


def _per_hour(count: int, n_bars: int) -> float | None:
    """Counts are rates, so a 15-minute checkpoint is comparable with the close."""
    return None if n_bars < 2 else _f(count / (n_bars / 60.0))


def _crossings(values: np.ndarray, line: np.ndarray) -> int:
    """Times the series closed through *line* — sign flips of (value − line),
    ignoring exact touches (a close sitting on the line hasn't crossed it yet)."""
    above = values > line
    return int(np.count_nonzero(above[1:] != above[:-1]))


def _longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def _touch_hold(close: np.ndarray, pierce: np.ndarray, line: np.ndarray,
                in_channel: np.ndarray, upper: bool, within: int = 3) -> float | None:
    """Of the bars that pierced the σ line from inside the channel, the fraction
    that closed back on the channel side within *within* bars.

    This is the "does price respect the band" number — the one the user reads a
    day by. A high ratio is a day where the band is a wall; a low one is a day
    where price is slicing through it and the bounce model has nothing to lean on.
    """
    n = len(close)
    hits = holds = 0
    for i in range(1, n):
        if not in_channel[i - 1]:
            continue
        pierced = pierce[i] <= line[i] if upper else pierce[i] >= line[i]
        if not pierced:
            continue
        hits += 1
        j1 = min(i + within, n - 1)
        back = (close[i:j1 + 1] > line[i:j1 + 1]) if upper else (close[i:j1 + 1] < line[i:j1 + 1])
        if back.any():
            holds += 1
    return None if hits == 0 else _f(holds / hits)


def _net_travel(close: np.ndarray) -> float | None:
    """(close − open) over the range, all from 1-minute closes — the scale-free
    "did the day actually go anywhere". A trend day closes near an extreme
    (|ntr| → 1); a day that gapped somewhere and parked never travels (|ntr| → 0),
    however one-sided its closes were. Anchor-free, so it exists on partial days.
    """
    if len(close) < 2:
        return None
    rng = float(close.max() - close.min())
    return None if rng <= 0 else _f((close[-1] - close[0]) / rng)


def _flip_rates(state: np.ndarray, n_bars: int) -> tuple[float | None, float | None]:
    """Quadrant transitions split by whether the side actually changed.

    A deep flip is above-both → below-both (however long the single-anchor churn
    zone held in between); a shallow one dips into an "only" state and returns to
    the side it left. The distinction is the point: v5's raw transition count
    treated both alike, and shallow churn is what a trend day produces whenever
    the two anchors run close together — it says nothing about the day's side.
    """
    deep = shallow = 0
    last_side = 0
    prev = None
    for s in state:
        if prev is not None and s != prev:
            side = 1 if s == ABOVE_BOTH else (-1 if s == BELOW_BOTH else 0)
            if side and last_side and side != last_side:
                deep += 1
            elif side and side == last_side:
                shallow += 1
        if s == ABOVE_BOTH:
            last_side = 1
        elif s == BELOW_BOTH:
            last_side = -1
        prev = s
    return _per_hour(deep, n_bars), _per_hour(shallow, n_bars)


def _vwap_slope(mid: np.ndarray, high: np.ndarray, low: np.ndarray,
                close: np.ndarray) -> tuple[float | None, float | None]:
    """VWAP slope over the last SPREAD_SLOPE_MIN bars, in two units.

    ``ppm`` is the native unit — points per minute, the plain derivative.

    ``deg`` needs a convention, because an angle on a chart is an artifact of the
    aspect ratio: squash the time axis and every line steepens. The convention
    here is 1 ATR of rise per minute of run ≡ 45°, with ATR the mean 1-minute
    true range over the same window. That makes the angle comparable across days
    and instruments, which raw points per minute is not.
    """
    n = len(mid)
    k = min(SPREAD_SLOPE_MIN, n - 1)
    if k < 1:
        return None, None
    ppm = (mid[-1] - mid[-1 - k]) / k
    prev_close = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = float(tr[-k:].mean())
    # A zero ATR is a tape where nothing traded a range at all; VWAP cannot have
    # moved either, so the angle is flat rather than undefined.
    deg = 0.0 if atr <= 0 else float(np.degrees(np.arctan(ppm / atr)))
    return _f(ppm), _f(deg)


def _band_kpis(b: pd.DataFrame, w: pd.DataFrame) -> dict:
    """The four band metrics for one anchor. *b* is the bar slice, *w* the band
    frame sampled at those bars' closes (same length, index-aligned by position).
    """
    n = len(b)
    if n < 2:
        return {"band_cross_rate": None, "upper_channel_occupancy": None,
                "above_dev2_occupancy": None, "middle_band_occupancy": None,
                "lower_channel_occupancy": None, "below_dev2_occupancy": None,
                "touch_hold_ratio": None, "lower_touch_hold_ratio": None,
                "vwap_cross_rate": None, "vwap_slope_ppm": None,
                "vwap_slope_deg": None}

    close = b["close"].to_numpy(dtype="float64")
    low = b["low"].to_numpy(dtype="float64")
    high = b["high"].to_numpy(dtype="float64")
    mid = w["mid"].to_numpy(dtype="float64")
    std = w["std"].to_numpy(dtype="float64")
    up1 = w["upper1"].to_numpy(dtype="float64")
    lo1 = w["lower1"].to_numpy(dtype="float64")

    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(std > 0, (close - mid) / std, np.nan)

    in_upper = (z >= 1) & (z <= 2)
    in_lower = (z <= -1) & (z >= -2)
    # The remaining zones, so the five occupancies partition the session: a close
    # is in exactly one of them (band edges count toward the channels, matching
    # in_upper/in_lower above).
    above2 = z > 2
    below2 = z < -2
    middle = (z > -1) & (z < 1)
    slope_ppm, slope_deg = _vwap_slope(mid, high, low, close)

    return {
        "band_cross_rate": _per_hour(_crossings(close, up1), n),
        "upper_channel_occupancy": _f(np.nanmean(in_upper.astype("float64"))),
        "above_dev2_occupancy": _f(np.nanmean(above2.astype("float64"))),
        "middle_band_occupancy": _f(np.nanmean(middle.astype("float64"))),
        "lower_channel_occupancy": _f(np.nanmean(in_lower.astype("float64"))),
        "below_dev2_occupancy": _f(np.nanmean(below2.astype("float64"))),
        "touch_hold_ratio": _touch_hold(close, low, up1, in_upper, upper=True),
        "lower_touch_hold_ratio": _touch_hold(close, high, lo1, in_lower, upper=False),
        "vwap_cross_rate": _per_hour(_crossings(close, mid), n),
        "vwap_slope_ppm": slope_ppm,
        "vwap_slope_deg": slope_deg,
    }


def _gx_rescue(close: np.ndarray, pierce: np.ndarray, ny_1: np.ndarray,
               gx_1: np.ndarray, u: float = 1.0, within: int = 5) -> float | None:
    """Of the pullbacks that *broke* the session dev1 on a closing basis while the
    Globex dev1 ran beyond it, the fraction where the wicks held short of the
    Globex line and price closed back past the session line within *within* bars.

    This is the "bounced at Globex's dev1 instead of the session's" event: the
    session band failed, but the deeper band the overnight anchor put behind it
    caught the pullback anyway. Only computable when the wrap geometry is
    actually present at the break — a break with the Globex line on the *near*
    side of the session line has no second floor to be rescued by.

    Read in a signed frame so the two bands are one function: ``u`` = +1 is the
    upper side (the floor underneath a broken +1σ, *pierce* = the bar lows), −1
    the lower (the ceiling above a broken −1σ, *pierce* = the highs). Every
    comparison below is the upper-side one multiplied through by ``u``.
    """
    n = len(close)
    events = rescues = 0
    for i in range(1, n):
        broke = (u * (close[i - 1] - ny_1[i - 1]) > 0
                 and u * (close[i] - ny_1[i]) <= 0)
        if not broke or not u * (gx_1[i] - ny_1[i]) < 0:
            continue
        events += 1
        j1 = min(i + within, n - 1)
        for j in range(i, j1 + 1):
            if u * (pierce[j] - gx_1[j]) < 0:
                break  # sliced the Globex line too: both bands failed
            if u * (close[j] - ny_1[j]) > 0:
                rescues += 1
                break
    return None if events == 0 else _f(rescues / events)


def _quadrant(close: np.ndarray, mid_g: np.ndarray, mid_n: np.ndarray) -> np.ndarray:
    above_g = close > mid_g
    above_n = close > mid_n
    out = np.full(len(close), BELOW_BOTH, dtype=object)
    out[above_g & above_n] = ABOVE_BOTH
    out[above_g & ~above_n] = ABOVE_GX
    out[~above_g & above_n] = ABOVE_NY
    return out


def _dual_kpis(b: pd.DataFrame, wg: pd.DataFrame, wn: pd.DataFrame) -> dict:
    """The dual-VWAP metrics — only meaningful over RTH bars, where both anchors
    exist. This is where the user's own read lives: the model works on days that
    hold above both and fails on days that churn between them."""
    n = len(b)
    empty = {"abr": None, "bbr": None, "net_conviction": None,
             "longest_hold_min": None,
             "longest_hold_below_min": None, "quadrant_transitions_rate": None,
             "deep_flip_rate": None, "shallow_flip_rate": None,
             "norm_spread": None, "spread_slope": None,
             "upper_wrap_occupancy": None, "upper_dev1_gap_sigma": None,
             "gx_upper_rescue_ratio": None,
             "lower_wrap_occupancy": None, "lower_dev1_gap_sigma": None,
             "gx_lower_rescue_ratio": None}
    if n < 2:
        return empty

    close = b["close"].to_numpy(dtype="float64")
    low = b["low"].to_numpy(dtype="float64")
    high = b["high"].to_numpy(dtype="float64")
    mid_g = wg["mid"].to_numpy(dtype="float64")
    mid_n = wn["mid"].to_numpy(dtype="float64")
    std_g = wg["std"].to_numpy(dtype="float64")
    std_n = wn["std"].to_numpy(dtype="float64")
    gx_up1 = wg["upper1"].to_numpy(dtype="float64")
    gx_up2 = wg["upper2"].to_numpy(dtype="float64")
    ny_up1 = wn["upper1"].to_numpy(dtype="float64")
    ny_up2 = wn["upper2"].to_numpy(dtype="float64")
    gx_lo1 = wg["lower1"].to_numpy(dtype="float64")
    gx_lo2 = wg["lower2"].to_numpy(dtype="float64")
    ny_lo1 = wn["lower1"].to_numpy(dtype="float64")
    ny_lo2 = wn["lower2"].to_numpy(dtype="float64")

    above_both = (close > mid_g) & (close > mid_n)
    below_both = (close < mid_g) & (close < mid_n)
    state = _quadrant(close, mid_g, mid_n)
    transitions = int(np.count_nonzero(state[1:] != state[:-1]))
    deep_rate, shallow_rate = _flip_rates(state, n)

    with np.errstate(divide="ignore", invalid="ignore"):
        spread = np.where(std_g > 0, (mid_n - mid_g) / std_g, np.nan)
    k = min(SPREAD_SLOPE_MIN, n - 1)  # bars are minutes, so k bars back is k minutes back

    # The wrap geometry: the Globex upper channel containing the session's, so a
    # pullback through the session +1σ still has the Globex +1σ underneath it.
    wrapped = (gx_up1 <= ny_up1) & (gx_up2 >= ny_up2)
    # The same read on the lower bands, mirrored: the Globex −1σ ABOVE the
    # session's is the ceiling a rally through the session −1σ runs into. It is
    # what the fade-long would be buying into, exactly as the upper wrap is the
    # floor the fade-short sells into.
    wrapped_lo = (gx_lo1 >= ny_lo1) & (gx_lo2 <= ny_lo2)
    with np.errstate(divide="ignore", invalid="ignore"):
        dev1_gap = np.where(std_n > 0, (ny_up1 - gx_up1) / std_n, np.nan)
        dev1_gap_lo = np.where(std_n > 0, (gx_lo1 - ny_lo1) / std_n, np.nan)

    return {
        "upper_wrap_occupancy": _f(wrapped.mean()),
        "upper_dev1_gap_sigma": _f(np.nanmean(dev1_gap)),
        "gx_upper_rescue_ratio": _gx_rescue(close, low, ny_up1, gx_up1, u=1.0),
        "lower_wrap_occupancy": _f(wrapped_lo.mean()),
        "lower_dev1_gap_sigma": _f(np.nanmean(dev1_gap_lo)),
        "gx_lower_rescue_ratio": _gx_rescue(close, high, ny_lo1, gx_lo1, u=-1.0),
        "abr": _f(above_both.mean()),
        "bbr": _f(below_both.mean()),
        # One number for "whose day was it": +1 is every close above both
        # anchors, −1 every close below both. classify() reads this, so the two
        # occupancies can't disagree with the label through separate thresholds.
        "net_conviction": _f(above_both.mean() - below_both.mean()),
        "longest_hold_min": _longest_run(above_both),
        "longest_hold_below_min": _longest_run(below_both),
        "quadrant_transitions_rate": _per_hour(transitions, n),
        "deep_flip_rate": deep_rate,
        "shallow_flip_rate": shallow_rate,
        "norm_spread": _f(spread[-1]),
        "spread_slope": _f(spread[-1] - spread[-1 - k]),
    }


def _overnight_kpis(b: pd.DataFrame, w: pd.DataFrame, first_rth) -> dict:
    """What was knowable *before* the bell — the only KPIs a 09:30 decision can
    read. Globex-anchored by construction: the NY anchor doesn't exist yet."""
    empty = {"on_abr": None, "on_band_cross_rate": None, "on_range_pts": None,
             "open_z": None, "on_vwap_slope_ppm": None, "on_vwap_slope_deg": None}
    if len(b) < 2:
        return empty

    close = b["close"].to_numpy(dtype="float64")
    mid = w["mid"].to_numpy(dtype="float64")
    up1 = w["upper1"].to_numpy(dtype="float64")
    high = b["high"].to_numpy(dtype="float64")
    low = b["low"].to_numpy(dtype="float64")

    open_z = None
    if first_rth is not None:
        std0 = float(first_rth["std"])
        if std0 > 0:
            open_z = (float(first_rth["open"]) - float(first_rth["mid"])) / std0

    # The Globex VWAP has the whole night behind it by the bell, so unlike the
    # intraday anchors its slope is fully formed *before* the first entry — the
    # one slope a 09:30 decision can actually read. Same 30-min window, so this
    # is the 09:00→09:30 grade of the overnight anchor.
    slope_ppm, slope_deg = _vwap_slope(mid, high, low, close)

    return {
        "on_abr": _f((close > mid).mean()),
        "on_band_cross_rate": _per_hour(_crossings(close, up1), len(b)),
        "on_range_pts": _f(b["high"].max() - b["low"].min()),
        "open_z": _f(open_z),
        "on_vwap_slope_ppm": slope_ppm,
        "on_vwap_slope_deg": slope_deg,
    }


# --- market structure -------------------------------------------------------

def _causal_zigzag(high: np.ndarray, low: np.ndarray, thr: float) -> list[tuple]:
    """(pivot_idx, price, kind, confirm_idx) swings — the structure studies'
    primitive, verbatim, so this layer agrees with them bit-for-bit. A pivot
    exists at bar t only if the reversal confirming it completed by t: running
    this on a prefix of the bars yields exactly the pivots the full run had
    confirmed by then, which is what makes a per-checkpoint scan honest."""
    n = len(high)
    piv = []
    direction = 0  # 0 unknown, +1 up-leg (tracking max), -1 down-leg
    max_i, min_i = 0, 0
    for i in range(n):
        if high[i] >= high[max_i]:
            max_i = i
        if low[i] <= low[min_i]:
            min_i = i
        if direction >= 0 and high[max_i] - low[i] >= thr:
            piv.append((max_i, high[max_i], "H", i))
            direction = -1
            min_i = i
        elif direction <= 0 and high[i] - low[min_i] >= thr:
            piv.append((min_i, low[min_i], "L", i))
            direction = 1
            max_i = i
    return piv


def _structure_scan(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                    thr: float) -> tuple[np.ndarray, list[tuple[int, bool]]]:
    """The BOS/CHoCH bias state machine over one bar prefix.

    Returns the per-bar bias (+1 up, −1 down, 0 not yet seeded) and the break
    events as (bar, is_choch). Canonical SMC form: a close beyond the active
    swing *in* the bias is a BOS (continuation), the first close beyond the
    opposing swing is a CHoCH (the bias flips). A broken reference is consumed —
    the machine waits for the next confirmed pivot before that side can break
    again, so one swing never fires twice.
    """
    piv = _causal_zigzag(high, low, thr)
    by_confirm: dict[int, list] = {}
    for p in piv:
        by_confirm.setdefault(p[3], []).append(p)

    n = len(close)
    bias_arr = np.zeros(n, dtype="int8")
    breaks: list[tuple[int, bool]] = []
    ref_high = ref_low = None
    bias = 0
    for t in range(n):
        for p in by_confirm.get(t, []):
            if p[2] == "H":
                ref_high = p[1]
            else:
                ref_low = p[1]
        c = close[t]
        if ref_high is not None and c > ref_high:
            breaks.append((t, bias == -1))
            bias = 1
            ref_high = None
        elif ref_low is not None and c < ref_low:
            breaks.append((t, bias == 1))
            bias = -1
            ref_low = None
        bias_arr[t] = bias
    return bias_arr, breaks


def _chop_state(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """Per-bar chop flag: NaN until a full trailing window exists, else 1.0 when
    the mean bar-to-bar range overlap of the last CHOP_STATE_BARS bars is at or
    above CHOP_STATE_OVERLAP. This is the winners-vs-losers overlap_10, computed
    at every bar instead of only at entries — 1.0 is bars stacked on top of each
    other, a clean staircase runs near 0."""
    n = len(high)
    out = np.full(n, np.nan)
    if n < CHOP_STATE_BARS:
        return out
    inter = np.minimum(high[1:], high[:-1]) - np.maximum(low[1:], low[:-1])
    rng = ((high[1:] - low[1:]) + (high[:-1] - low[:-1])) / 2.0
    frac = np.where(rng > 0, np.clip(inter / np.where(rng > 0, rng, 1), 0, 1), np.nan)
    w = CHOP_STATE_BARS - 1  # pair-overlaps per 10-bar window
    ov = pd.Series(frac).rolling(w, min_periods=w - 3).mean().to_numpy()
    out[1:] = np.where(np.isfinite(ov), (ov >= CHOP_STATE_OVERLAP).astype("float64"),
                       np.nan)
    out[:CHOP_STATE_BARS - 1] = np.nan
    return out


def _occupancy(state: np.ndarray, min_bars: int = CHOP_WINDOW_MIN // 3) -> float | None:
    """Share of the bars whose chop flag is defined that have it set."""
    ok = np.isfinite(state)
    return None if ok.sum() < min_bars else _f(state[ok].mean())


def _median_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                window: int = ST_ATR_BARS) -> float | None:
    """Median rolling ATR over the bars given — the causal volatility yardstick
    the swing thresholds scale by."""
    n = len(close)
    if n < window + 1:
        return None
    prev_close = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = pd.Series(tr).rolling(window).mean().to_numpy()
    atr = atr[np.isfinite(atr)]
    return None if len(atr) == 0 else float(np.median(atr))


def _structure_kpis(b: pd.DataFrame, n_on: int) -> dict:
    """The structure + chop KPIs for one checkpoint.

    *b* is every completed 1-minute bar the checkpoint can see — overnight bars
    first (there are *n_on* of them), then the RTH bars up to the cutoff. The
    machine runs over all of them, because the structure a 09:30 decision reads
    was built overnight; but every *rate and share* is counted over RTH bars
    only, so "breaks per hour" compares the same market hours across days
    instead of diluting a 6.5-hour session with a 15.5-hour night.

    State vs rate is the split that matters at the open: st_bias and its age are
    machine *state* — knowable at 09:30 from the overnight skeleton, like the
    on_* KPIs — while the rates are None until enough RTH bars exist to count
    over.
    """
    empty = {"st_bias": None, "st_bias_age_min": None, "st_bias_share": None,
             "st_break_rate": None, "st_bos_share": None, "st_choch_rate": None,
             "chop_occ_30m": None, "chop_occ_rth": None}
    high = b["high"].to_numpy(dtype="float64")
    low = b["low"].to_numpy(dtype="float64")
    close = b["close"].to_numpy(dtype="float64")
    n = len(b)
    n_rth = n - n_on

    out = dict(empty)
    # The chop occupancy doesn't need the zigzag or its ATR yardstick, so it
    # exists even on the thin early slices where structure can't.
    chop = _chop_state(high, low)
    out["chop_occ_30m"] = _occupancy(chop[-CHOP_WINDOW_MIN:])
    if n_rth >= 2:
        out["chop_occ_rth"] = _occupancy(chop[n_on:])

    # The yardstick is RTH volatility as soon as there is enough of it to
    # measure: a session's swings should be judged at the session's scale, not
    # a median dragged down by 15 quiet overnight hours. Before that — at the
    # bell — the overnight ATR is the only causal choice, and it is also the
    # right scale for the overnight skeleton it thresholds.
    if n_rth >= ST_ATR_BARS + 1:
        atr = _median_atr(high[n_on:], low[n_on:], close[n_on:])
    else:
        atr = _median_atr(high, low, close)
    if atr is None or atr <= 0:
        return out

    bias_arr, breaks = _structure_scan(high, low, close, ST_MAJOR_MULT * atr)
    last = int(bias_arr[-1])
    if last != 0:
        out["st_bias"] = _f(last)
        # Age = the trailing run of the current bias. A BOS extends the run —
        # only a CHoCH (or the seed) starts it — so this is "minutes since the
        # day's character last changed".
        age = int(np.argmax(bias_arr[::-1] != last)) if (bias_arr != last).any() else n
        out["st_bias_age_min"] = age
    if n_rth >= 2:
        rth_bias = bias_arr[n_on:]
        out["st_bias_share"] = _f((rth_bias == 1).mean() - (rth_bias == -1).mean())
        rth_breaks = [is_ch for (t, is_ch) in breaks if t >= n_on]
        out["st_break_rate"] = _per_hour(len(rth_breaks), n_rth)
        if rth_breaks:
            out["st_bos_share"] = _f(1.0 - sum(rth_breaks) / len(rth_breaks))
        _, fine = _structure_scan(high, low, close, ST_FINE_MULT * atr)
        n_choch = sum(1 for (t, is_ch) in fine if is_ch and t >= n_on)
        out["st_choch_rate"] = _per_hour(n_choch, n_rth)
    return out


# --- classification ---------------------------------------------------------

def classify(k: dict) -> str:
    """A day's regime from one checkpoint's KPIs.

    PROVISIONAL, second draft. v5 filed 56% of the collected days as "mixed",
    nearly all of them one-sided days disqualified by the raw quadrant-transition
    count — shallow churn against a nearby anchor, not actual side changes. This
    rule reads three things instead: net conviction (whose day it was), deep
    flips (did price actually change sides), and net travel (did it go anywhere).
    A one-sided day that never travelled is its own class — "parked" — because
    a gap that spends the session flat is not a trend, however high its ABR.

    Thresholds were calibrated on the 173 cached sessions from 2025-06..2026-01
    (trend days sit at |net_travel| 0.65+, parked at ~0.2; one-sided days show
    well under 1.5 side changes an hour). Still not a fit: the KPI-vs-P&L
    scatter is what will actually tune them, and until it has, nothing here
    should be trusted to more than "this day looked like that day".
    """
    abr, bbr = k.get("abr"), k.get("bbr")
    deep = k.get("deep_flip_rate")
    ntr = k.get("net_travel")
    # The NY anchor is the intraday reference — it is the one a 09:30 decision
    # watches develop, and the one the churn shows up against first.
    vx = k.get("ny_vwap_cross_rate")
    if abr is None or bbr is None:
        return "unknown"  # no Globex anchor: there is no dual-VWAP regime to read
    nc = abr - bbr
    if abs(nc) >= 0.5 and (deep is None or deep <= 1.5):
        if ntr is not None and abs(ntr) < 0.3:
            return "parked"
        return "trend_up" if nc > 0 else "trend_down"
    if abs(nc) < 0.3 and ((vx is not None and vx >= 2)
                          or (deep is not None and deep >= 2)):
        return "balance"
    return "mixed"


def texture(k: dict) -> str:
    """The day's second axis: not *which way* it went (class) but *how it
    traded* — clean directional bars or churn. Deliberately one measure, the
    chop-state occupancy, because bar overlap is the one texture number with a
    pedigree (overlap_10 predicted stops on two independent runs, AUC .61–.64,
    and survived the gate-robustness audit as a real signal even though its
    per-trade veto failed). Orthogonal to classify() by construction: a trend
    day can be churny (grinds up through overlapping bars) and a balance day
    can be clean (wide, decisive rotations).

    Falls back to the trailing window at the 09:30 checkpoint, where no RTH
    bars exist yet and the overnight texture is all there is to read.
    """
    c = k.get("chop_occ_rth")
    if c is None:
        c = k.get("chop_occ_30m")
    if c is None:
        return "unknown"
    return "churny" if c >= TEXTURE_CHURN_OCC else "clean"


# --- the artifact -----------------------------------------------------------

def _et_utc(day: date, t: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, t), tz=ET_TZ).tz_convert("UTC")


def _sample(w: pd.DataFrame, pos: np.ndarray) -> pd.DataFrame:
    """The band frame at the ticks where those bars closed."""
    return w.iloc[pos].reset_index(drop=True)


def compute_regime(
    symbol: str, day: date,
    frames: tuple[pd.DataFrame | None, pd.DataFrame | None] | None = None,
) -> dict | None:
    """Regime artifact for one session, from the tick cache or a supplied frame.

    Reads only what is on disk. Returns None when the session has no RTH ticks
    cached at all — there is no day to describe.

    ``frames`` is ``(overnight, rth)`` handed over by a caller that already holds
    the ticks — the shadow-mode seam, mirroring ``engine._load_ticks``. A live
    session in progress has no cache file to read and must not grow one, and
    re-reading the settled vendor copy of a day that hasn't settled would answer
    a different question. Supplied as a pair so "there is no overnight" stays
    distinguishable from "look it up yourself": pass ``(None, rth)`` for a
    session whose night genuinely isn't there.

    A frame that is a *prefix* of the session yields the artifact as it stood at
    that moment, and every checkpoint is already causal in the whole-day path —
    each one slices to the bars that had closed by its own cutoff. So a
    checkpoint computed from a longer prefix is the same checkpoint, which is
    what makes the live caller's freeze a guarantee rather than a hope. The one
    value that does keep moving is the top-level ``class``/``texture``: they
    mirror the ``eod`` checkpoint, which on a partial day means "the day so
    far", not the day. Gates read a named checkpoint, never these.
    """
    if frames is None:
        rth = tickmod.cached_rth(symbol, day)
        on = tickmod.cached_overnight(symbol, day)
    else:
        on, rth = frames
    if rth is None or rth.empty:
        return None
    partial = on is None or on.empty

    if partial:
        full, rth_i0 = rth, 0
    else:
        # Same splice the charts do: the two segments meet end-exclusive at 09:30,
        # so concatenation is already ordered.
        full = pd.concat([on, rth], ignore_index=True)
        rth_i0 = len(on)

    bars = minute_bars(full)
    if bars.empty:
        return None
    pos = bars["end_idx"].to_numpy()

    # Globex-anchored bands only exist when the frame actually starts at 18:00.
    w_gx_full = None if partial else vwapmod.vwap_bands(full)
    w_ny_full = vwapmod.vwap_bands(full.iloc[rth_i0:].reset_index(drop=True))

    rth_open = _et_utc(day, tickmod.RTH_OPEN)
    is_rth = bars["ts_utc"] >= rth_open
    rth_bars = bars[is_rth].reset_index(drop=True)
    on_bars = bars[~is_rth].reset_index(drop=True)

    # Positional sampling, exactly as sim_charts does it: a bar's row in a band
    # frame is where its last tick fell. An overnight bar has no NY-anchored row
    # at all (its position is negative), which is why the NY frames are only ever
    # sampled at RTH bars.
    rth_pos = pos[is_rth.to_numpy()]
    b_ny = _sample(w_ny_full, rth_pos - rth_i0)
    b_gx_rth = None if partial else _sample(w_gx_full, rth_pos)
    b_gx_on = None if partial else _sample(w_gx_full, pos[~is_rth.to_numpy()])

    # 09:30's overnight priors are fixed for the whole day — they are what was
    # knowable at the bell — so they're computed once and stamped into every
    # checkpoint, rather than recomputed against a moving cutoff.
    first_rth = None
    if not partial and len(rth_bars) and b_gx_rth is not None and len(b_gx_rth):
        first_rth = {"open": rth_bars.loc[0, "open"], "mid": b_gx_rth.loc[0, "mid"],
                     "std": b_gx_rth.loc[0, "std"]}
    on_kpis = (_overnight_kpis(on_bars, b_gx_on, first_rth) if not partial
               else _overnight_kpis(on_bars.iloc[:0], on_bars.iloc[:0], None))

    checkpoints: dict[str, dict] = {}
    for name, t in CHECKPOINTS:
        cutoff = _et_utc(day, t)
        # A bar counts as knowable at the checkpoint only once it has closed, and
        # a 1-minute bar stamped 09:44 closes at 09:45.
        take = (rth_bars["ts_utc"] + pd.Timedelta(minutes=1) <= cutoff).to_numpy()
        rb = rth_bars[take].reset_index(drop=True)
        ny = b_ny[take].reset_index(drop=True)
        gx = None if b_gx_rth is None else b_gx_rth[take].reset_index(drop=True)

        kp: dict = dict(on_kpis)
        kp.update({f"ny_{k}": v for k, v in _band_kpis(rb, ny).items()})
        if gx is None:
            # _band_kpis on an empty slice is the all-None dict — the one key list.
            kp.update({f"gx_{k}": v for k, v in _band_kpis(rb.iloc[:0], ny.iloc[:0]).items()})
            kp.update(_dual_kpis(rb.iloc[:0], rb.iloc[:0], rb.iloc[:0]))
        else:
            kp.update({f"gx_{k}": v for k, v in _band_kpis(rb, gx).items()})
            kp.update(_dual_kpis(rb, gx, ny))
        # The structure machine reads the whole tape it could have seen — the
        # night's bars first, then the RTH bars up to this cutoff — because the
        # skeleton a 09:30 decision leans on was built overnight. Anchor-free,
        # so unlike the gx_* KPIs it exists on partial days (just unseeded).
        kp.update(_structure_kpis(pd.concat([on_bars, rb], ignore_index=True),
                                  len(on_bars)))
        kp["net_travel"] = _net_travel(rb["close"].to_numpy(dtype="float64"))
        kp["bars"] = int(len(rb))
        # Each checkpoint carries the class its own KPIs support: the label at
        # "12:00" is what the day looked like at noon, not a preview of the
        # verdict. Only the eod one is hindsight, and it is mirrored at top level.
        kp["class"] = classify(kp)
        kp["texture"] = texture(kp)
        checkpoints[name] = kp

    # Ribbon: the quadrant state per minute across the whole session. Pre-RTH bars
    # have one anchor only, so they get their own two states rather than being
    # forced into a quadrant they can't be in.
    ribbon: list[dict] = []
    if not partial and len(on_bars) and b_gx_on is not None:
        on_above = on_bars["close"].to_numpy() > b_gx_on["mid"].to_numpy()
        ribbon += [{"time": int(ts.timestamp()), "state": ON_ABOVE if a else ON_BELOW}
                   for ts, a in zip(on_bars["ts_utc"], on_above)]
    if len(rth_bars):
        if partial or b_gx_rth is None:
            # Without the night there is no Globex anchor — the ribbon degrades to
            # the NY anchor alone rather than inventing the other half.
            above = rth_bars["close"].to_numpy() > b_ny["mid"].to_numpy()
            states = np.where(above, ON_ABOVE, ON_BELOW)
        else:
            states = _quadrant(rth_bars["close"].to_numpy(dtype="float64"),
                               b_gx_rth["mid"].to_numpy(dtype="float64"),
                               b_ny["mid"].to_numpy(dtype="float64"))
        ribbon += [{"time": int(ts.timestamp()), "state": str(s)}
                   for ts, s in zip(rth_bars["ts_utc"], states)]

    return {
        "version": REGIME_VERSION,
        "symbol": symbol,
        "date": day.isoformat(),
        "partial": bool(partial),
        "class": checkpoints["eod"]["class"],
        "texture": checkpoints["eod"]["texture"],
        "checkpoints": checkpoints,
        "ribbon": ribbon,
    }


def _path(symbol: str, day: date):
    return REGIME_DIR / f"{symbol}_{day.isoformat()}_v{REGIME_VERSION}.json"


def get_regime(symbol: str, day: date) -> dict | None:
    """Cached regime for a session, computing (and writing) it on first read.

    A cache file written under an older REGIME_VERSION is simply never looked at:
    its name carries the version, so a bump orphans it rather than serving numbers
    that mean something else now.

    ``symbol`` may be a rolling root ("NQ", what a rolling run's config carries):
    it is resolved to the session's raw contract first, offline, because both the
    tick cache and the artifacts here are keyed by what actually traded. A day the
    roll map has never seen is simply not describable — never probed, this is a GET.
    """
    symbol = tickmod.contract_for_cached(symbol, day)
    if symbol is None:
        return None
    p = _path(symbol, day)
    if p.exists():
        try:
            d = json.loads(p.read_text())
            if d.get("version") == REGIME_VERSION:
                return d
        except (json.JSONDecodeError, OSError):
            pass  # a truncated write is a cache miss, not a failure

    d = compute_regime(symbol, day)
    if d is None:
        return None
    REGIME_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))
    return d
