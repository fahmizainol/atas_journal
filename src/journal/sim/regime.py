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
REGIME_VERSION = 3

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


# --- bars -------------------------------------------------------------------

def minute_bars(t: pd.DataFrame) -> pd.DataFrame:
    """1-minute OHLCV bars from a tick frame, plus ``end_idx`` — the positional
    index of each bar's last tick back into *t*, which is how a bar's close is
    aligned with the running VWAP frames (they are one row per tick)."""
    if t.empty:
        return pd.DataFrame(columns=["ts_utc", "open", "high", "low", "close",
                                     "volume", "end_idx"])
    g = t.assign(_i=np.arange(len(t)), _m=t["ts_utc"].dt.floor("1min")).groupby("_m", sort=True)
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
    slope_ppm, slope_deg = _vwap_slope(mid, high, low, close)

    return {
        "band_cross_rate": _per_hour(_crossings(close, up1), n),
        "upper_channel_occupancy": _f(np.nanmean(in_upper.astype("float64"))),
        "touch_hold_ratio": _touch_hold(close, low, up1, in_upper, upper=True),
        "lower_touch_hold_ratio": _touch_hold(close, high, lo1, in_lower, upper=False),
        "vwap_cross_rate": _per_hour(_crossings(close, mid), n),
        "vwap_slope_ppm": slope_ppm,
        "vwap_slope_deg": slope_deg,
    }


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
    empty = {"abr": None, "bbr": None, "longest_hold_min": None,
             "longest_hold_below_min": None, "quadrant_transitions_rate": None,
             "norm_spread": None, "spread_slope": None}
    if n < 2:
        return empty

    close = b["close"].to_numpy(dtype="float64")
    mid_g = wg["mid"].to_numpy(dtype="float64")
    mid_n = wn["mid"].to_numpy(dtype="float64")
    std_g = wg["std"].to_numpy(dtype="float64")

    above_both = (close > mid_g) & (close > mid_n)
    below_both = (close < mid_g) & (close < mid_n)
    state = _quadrant(close, mid_g, mid_n)
    transitions = int(np.count_nonzero(state[1:] != state[:-1]))

    with np.errstate(divide="ignore", invalid="ignore"):
        spread = np.where(std_g > 0, (mid_n - mid_g) / std_g, np.nan)
    k = min(SPREAD_SLOPE_MIN, n - 1)  # bars are minutes, so k bars back is k minutes back

    return {
        "abr": _f(above_both.mean()),
        "bbr": _f(below_both.mean()),
        "longest_hold_min": _longest_run(above_both),
        "longest_hold_below_min": _longest_run(below_both),
        "quadrant_transitions_rate": _per_hour(transitions, n),
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


# --- classification ---------------------------------------------------------

def classify(k: dict) -> str:
    """A day's regime from its end-of-day KPIs.

    PROVISIONAL — these thresholds are a first guess, not a fitted model. They
    exist so the calendar and the tiles have something to say on day one; the
    KPI-vs-P&L scatter is the thing that will actually tune them, and until it
    has, nothing should be trusted to more than "this day looked like that day".
    """
    abr, bbr = k.get("abr"), k.get("bbr")
    trans = k.get("quadrant_transitions_rate")
    # The NY anchor is the intraday reference — it is the one a 09:30 decision
    # watches develop, and the one the churn shows up against first.
    vx = k.get("ny_vwap_cross_rate")
    if abr is None or bbr is None:
        return "unknown"  # no Globex anchor: there is no dual-VWAP regime to read
    if abr >= 0.7 and (trans is None or trans <= 2):
        return "trend_up"
    if bbr >= 0.7 and (trans is None or trans <= 2):
        return "trend_down"
    if abr < 0.6 and bbr < 0.6 and (vx is not None and vx >= 2):
        return "balance"
    return "mixed"


# --- the artifact -----------------------------------------------------------

def _et_utc(day: date, t: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, t), tz=ET_TZ).tz_convert("UTC")


def _sample(w: pd.DataFrame, pos: np.ndarray) -> pd.DataFrame:
    """The band frame at the ticks where those bars closed."""
    return w.iloc[pos].reset_index(drop=True)


def compute_regime(symbol: str, day: date) -> dict | None:
    """Regime artifact for one session, straight from the tick cache.

    Reads only what is on disk. Returns None when the session has no RTH ticks
    cached at all — there is no day to describe.
    """
    rth = tickmod.cached_rth(symbol, day)
    if rth is None or rth.empty:
        return None
    on = tickmod.cached_overnight(symbol, day)
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
            kp.update({f"gx_{k}": None for k in
                       ("band_cross_rate", "upper_channel_occupancy", "touch_hold_ratio",
                        "lower_touch_hold_ratio", "vwap_cross_rate", "vwap_slope_ppm",
                        "vwap_slope_deg")})
            kp.update(_dual_kpis(rb.iloc[:0], rb.iloc[:0], rb.iloc[:0]))
        else:
            kp.update({f"gx_{k}": v for k, v in _band_kpis(rb, gx).items()})
            kp.update(_dual_kpis(rb, gx, ny))
        kp["bars"] = int(len(rb))
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
        "class": classify(checkpoints["eod"]),
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
