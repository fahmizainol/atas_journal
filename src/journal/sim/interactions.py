"""Level-interaction tracking — how price meets the developing NY and Globex
volume-profile levels (POC/VAH/VAL) and VWAP bands, session by session.

This is a *research* layer, deliberately separate from ``regime_pnl``:

  - ``regime_pnl`` reduces a whole session to ~35 scalar KPIs and correlates them
    with a strategy's realised P&L. It answers "what kind of day is it, and does
    that make my strategy money".
  - This module keeps the events themselves — every *touch* of a level, and every
    *VA-snap* (a developing value-area boundary jumping across price by its own
    motion) — with each event's own forward outcome (reject / accept, MFE, MAE).
    It answers "where does price actually turn at these levels, and is fading them
    an edge". The regime KPIs are aggregations of exactly these events; this is
    the granular substrate underneath them.

Nothing here reads trades. It is pure market structure over the tick cache, so a
run exists for any cached session whether or not it was ever traded. Every read
is GET-safe (``cached_rth`` / ``cached_overnight`` / ``contract_for_cached``) —
it never reaches Databento.

Two event kinds, and they are genuinely different:

  - **touch**: price crosses a (roughly static) level. Detected when a minute's
    high-low straddles the level. Classified reject/accept/chop by a forward
    window, with MFE (how far a fade would have run) and MAE (how far it went the
    other way) — i.e. each touch is scored as if it were a trade.
  - **VA-snap**: the *level* crosses price, because the developing value area
    recomputed and its boundary leapt. A VAH snapping up over price marks the
    moment an up-excursion is accepted back into value — the breakout failing.
    This is the event regime does not keep, and the one worth the most.

Levels within ``zone_cluster_pts`` are collapsed per-minute into one zone, so two
sources coinciding count as a single, higher-confluence touch rather than two.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, time

import numpy as np
import pandas as pd

from ..config import CACHE_DIR, ET_TZ, point_value, root_symbol, tick_size
from . import bars as barmod
from . import profile as profmod
from . import ticks as tickmod
from . import vwap as vwapmod
from .regime import minute_bars

INTERACTIONS_VERSION = 8  # v8: Globex (ON) VWAP band-occupancy aggregate
INTERACTIONS_DIR = CACHE_DIR / "interactions"

# Outcome thresholds, in index points. A fade has to clear REJECT_MIN to count as
# a reject rather than noise; an "accept" needs the bar to close ACCEPT_MARGIN
# beyond the level, not merely tag it. Fixed (not per-run config) so runs stay
# comparable — the tunable knobs are bin/VA%/window, which change what a level is,
# not what counts as a reaction to one.
REJECT_MIN_PTS = 3.0
ACCEPT_MARGIN_PTS = 2.0
# A level counts as touched if the minute's range comes within this of it — price
# often rides just under a support without wicking through, and that is still an
# interaction. Without it, a level price hugs registers zero touches.
TOUCH_TOL_PTS = 2.0
# Consecutive straddling minutes are one interaction, not many. A new touch of the
# same price zone only counts once price has been away from it for this many bars —
# otherwise a 25-minute rotation at a level reads as 25 separate "tests".
TOUCH_GAP_BARS = 3
# Bars used to measure whether a developing level is rising / flat / falling.
SLOPE_BARS = 10
SLOPE_FLAT_PPM = 0.5  # |pts/min| below this is "flat"
# A VA-snap must have the boundary jump at least this far in the crossing bar,
# and jump further than price itself moved — that is what makes it the level
# crossing price rather than price crossing the level.
SNAP_MIN_JUMP_PTS = 4.0
# An NY-anchored level is degenerate while its profile is minutes old — POC, VAH
# and VAL collapse onto the open print and the VWAP σ is ~0, so a "touch" there is
# the open itself, not a test of a level. No event fires until the level's anchor
# is at least this old. Globex levels are ~15 hours old by the RTH open, so this
# only gates the NY session's first bars.
LEVEL_WARMUP_MIN = 15
# Every touch is scored at each of these forward windows in addition to the
# config's primary window. A short window calls a V-reversal "accept" because the
# bounce hadn't happened yet; the longer reads catch it.
OUTCOME_HORIZONS_MIN = (10, 30, 60)

DEFAULTS = {
    "bin_size": None,          # None -> the instrument tick grid (matches the chart)
    "va_pct": profmod.VALUE_AREA_PCT,
    "sources": ["ny", "globex"],
    "outcome_window_min": 10,
    "zone_cluster_pts": 10.0,
}

BAND_LABELS = (">+2σ", "+1..+2", "vwap..+1", "-1..vwap", "-2..-1", "<-2σ")

# The measuring stick for every touch aggregate: phantom levels (a session's own
# close sampled at arbitrary minutes, scored by the same _outcome) come out at
# 60.5% "reject" with symmetric median MFE/MAE — the 3-pt reject threshold makes
# the label nearly free. A real cut must beat this rate AND show MFE/MAE
# asymmetry. Measured on NQ 2025-06→2026-01 at the 30m window; re-measure if the
# thresholds above change.
NULL_BASELINE_ROW = {
    "label": "null baseline (phantom levels)", "n": None,
    "reject_rate": 0.605, "med_mfe": 20.9, "med_mae": 21.2, "ratio": 0.99,
}


# --- config -----------------------------------------------------------------


@dataclass(frozen=True)
class InteractionConfig:
    """Everything that changes the numbers. Hashes to the snapshot key."""

    symbol: str
    start: date
    end: date
    bin_size: float
    va_pct: float
    sources: tuple[str, ...]
    outcome_window_min: int
    zone_cluster_pts: float

    @classmethod
    def build(cls, symbol: str, start: date, end: date, **over) -> "InteractionConfig":
        o = {**DEFAULTS, **{k: v for k, v in over.items() if v is not None}}
        bin_size = o["bin_size"] if o["bin_size"] is not None else tick_size(symbol)
        return cls(
            symbol=symbol, start=start, end=end,
            bin_size=float(bin_size), va_pct=float(o["va_pct"]),
            sources=tuple(o["sources"]), outcome_window_min=int(o["outcome_window_min"]),
            zone_cluster_pts=float(o["zone_cluster_pts"]),
        )

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol, "start": self.start.isoformat(),
            "end": self.end.isoformat(), "bin_size": self.bin_size,
            "va_pct": self.va_pct, "sources": list(self.sources),
            "outcome_window_min": self.outcome_window_min,
            "zone_cluster_pts": self.zone_cluster_pts,
        }

    def run_id(self) -> str:
        blob = json.dumps({"config": self.to_json(), "version": INTERACTIONS_VERSION},
                          sort_keys=True)
        h = hashlib.sha1(blob.encode()).hexdigest()[:12]
        return f"{self.symbol}_{self.start:%Y%m%d}-{self.end:%Y%m%d}_v{INTERACTIONS_VERSION}-{h}"


# --- per-session frame ------------------------------------------------------


@dataclass
class _Series:
    """One touchable level as it develops across the RTH minutes."""

    source: str          # "ny" | "globex"
    kind: str            # "VAH" | "VAL" | "POC" | "+1σ" | "-1σ" | "+2σ" | "-2σ"
    values: np.ndarray   # (n_min,), price per RTH minute (NaN before it exists)
    is_va: bool          # value-area boundary (can VA-snap) vs a VWAP band
    anchor_ts: int       # epoch seconds of the anchor's first bar — drives level age

    @property
    def label(self) -> str:
        return f"{'NY' if self.source == 'ny' else 'Globex'} {self.kind}"


@dataclass
class _Session:
    day: date
    minute_utc: np.ndarray   # (n_min,) int epoch seconds, per RTH minute
    et_time: list[time]
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    delta: np.ndarray        # signed aggressor volume per minute
    series: list[_Series] = field(default_factory=list)
    # NY VWAP bands per minute, for band_state
    ny_mid: np.ndarray = None
    ny_up1: np.ndarray = None
    ny_up2: np.ndarray = None
    ny_lo1: np.ndarray = None
    ny_lo2: np.ndarray = None
    # Globex (overnight-anchored) VWAP bands per RTH minute; None if globex is
    # not among the sources. Used only for the parallel band_state occupancy.
    gx_mid: np.ndarray = None
    gx_up1: np.ndarray = None
    gx_up2: np.ndarray = None
    gx_lo1: np.ndarray = None
    gx_lo2: np.ndarray = None


def _sample_bands(bands: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """VWAP-band row at each bar's close (by positional end_idx into the ticks)."""
    idx = bars["end_idx"].to_numpy(dtype="int64")
    return bands.iloc[idx].reset_index(drop=True)


def _strictly_increasing(times: np.ndarray) -> np.ndarray:
    """lightweight-charts wants unique, ascending, second-resolution times; two
    tick bars can finish inside the same second in a burst, so nudge a collision
    forward a second (it self-corrects at the next gap). Mirrors api.sim_charts."""
    out = times.astype("int64").copy()
    for i in range(1, len(out)):
        if out[i] <= out[i - 1]:
            out[i] = out[i - 1] + 1
    return out


def _vwap_pos_rows(bands: pd.DataFrame, pos: np.ndarray, times: np.ndarray) -> list[dict]:
    """VWAP-band row at each bar's close, sampled by positional index into the
    per-tick band frame. A negative position — a bar that closed before this
    anchor started — is skipped rather than drawn off a made-up level."""
    out = []
    for tm, p in zip(times, pos):
        if p < 0:
            continue
        r = bands.iloc[int(p)]
        if not _finite(r["mid"]):
            continue
        out.append({"time": int(tm), "middle": round(float(r["mid"]), 2),
                    "upper1": round(float(r["upper1"]), 2), "lower1": round(float(r["lower1"]), 2),
                    "upper2": round(float(r["upper2"]), 2), "lower2": round(float(r["lower2"]), 2)})
    return out


def _profile_pos_rows(prof: profmod.DevelopingProfile, times: np.ndarray) -> list[dict]:
    """Developing POC/VAH/VAL positionally aligned to ``times`` (one entry per
    bar); a bar with no value area yet comes back NaN and is dropped."""
    out = []
    for tm, poc, vah, val in zip(times, prof.poc, prof.vah, prof.val):
        if not _finite(poc):
            continue
        out.append({"time": int(tm), "poc": round(float(poc), 2),
                    "vah": round(float(vah), 2), "val": round(float(val), 2)})
    return out


def _minute_delta(rth: pd.DataFrame) -> pd.Series:
    """Signed aggressor volume (ask-lift minus bid-hit) per floored UTC minute."""
    side = rth["side"].to_numpy()
    size = rth["size"].to_numpy(dtype="float64")
    signed = np.where(side == "A", size, np.where(side == "B", -size, 0.0))
    tmp = pd.DataFrame({"_m": rth["ts_utc"].dt.floor("1min"), "d": signed})
    return tmp.groupby("_m")["d"].sum()


def _build_session(cfg: InteractionConfig, day: date, contract: str) -> _Session | None:
    rth = tickmod.cached_rth(contract, day)
    if rth is None or rth.empty:
        return None
    on = tickmod.cached_overnight(contract, day)

    bars_ny = minute_bars(rth)
    if bars_ny.empty:
        return None
    vwap_ny = _sample_bands(vwapmod.vwap_bands(rth), bars_ny)
    prof_ny = profmod.developing_profile(rth, bars_ny, cfg.bin_size, cfg.va_pct)

    minute_utc = (bars_ny["ts_utc"].astype("int64") // 1_000_000_000).to_numpy()
    et = bars_ny["ts_utc"].dt.tz_convert(ET_TZ)
    delta_by_min = _minute_delta(rth)
    delta = delta_by_min.reindex(bars_ny["ts_utc"]).fillna(0.0).to_numpy()

    sess = _Session(
        day=day, minute_utc=minute_utc, et_time=list(et.dt.time),
        high=bars_ny["high"].to_numpy(), low=bars_ny["low"].to_numpy(),
        close=bars_ny["close"].to_numpy(), volume=bars_ny["volume"].to_numpy(),
        delta=delta,
        ny_mid=vwap_ny["mid"].to_numpy(), ny_up1=vwap_ny["upper1"].to_numpy(),
        ny_up2=vwap_ny["upper2"].to_numpy(), ny_lo1=vwap_ny["lower1"].to_numpy(),
        ny_lo2=vwap_ny["lower2"].to_numpy(),
    )

    rth_t0 = int(minute_utc[0])
    if "ny" in cfg.sources:
        sess.series += [
            _Series("ny", "VAH", prof_ny.vah, True, rth_t0),
            _Series("ny", "VAL", prof_ny.val, True, rth_t0),
            _Series("ny", "POC", prof_ny.poc, True, rth_t0),
        ]
    if "vwap_bands" in cfg.sources:
        sess.series += [
            _Series("ny", "+1σ", sess.ny_up1, False, rth_t0),
            _Series("ny", "-1σ", sess.ny_lo1, False, rth_t0),
            _Series("ny", "+2σ", sess.ny_up2, False, rth_t0),
            _Series("ny", "-2σ", sess.ny_lo2, False, rth_t0),
        ]

    if "globex" in cfg.sources:
        glob = pd.concat([on, rth], ignore_index=True) if on is not None else rth
        bars_gx = minute_bars(glob)
        prof_gx = profmod.developing_profile(glob, bars_gx, cfg.bin_size, cfg.va_pct)
        # Align the Globex bars (which span the overnight) onto the RTH minutes.
        gx_ts = bars_gx["ts_utc"].astype("int64") // 1_000_000_000
        gx_t0 = int(gx_ts.iloc[0])
        gx = pd.DataFrame({
            "ts_utc": gx_ts,
            "VAH": prof_gx.vah, "VAL": prof_gx.val, "POC": prof_gx.poc,
        }).set_index("ts_utc").reindex(minute_utc)
        for kind in ("VAH", "VAL", "POC"):
            sess.series.append(_Series("globex", kind, gx[kind].to_numpy(), True, gx_t0))
        # Overnight-anchored VWAP bands, sampled per Globex bar then aligned to
        # the RTH minutes — the ON VWAP counterpart to the NY bands above.
        vwap_gx = _sample_bands(vwapmod.vwap_bands(glob), bars_gx)
        gxb = pd.DataFrame({
            "ts_utc": gx_ts, "mid": vwap_gx["mid"].to_numpy(),
            "up1": vwap_gx["upper1"].to_numpy(), "up2": vwap_gx["upper2"].to_numpy(),
            "lo1": vwap_gx["lower1"].to_numpy(), "lo2": vwap_gx["lower2"].to_numpy(),
        }).set_index("ts_utc").reindex(minute_utc)
        sess.gx_mid, sess.gx_up1, sess.gx_up2, sess.gx_lo1, sess.gx_lo2 = (
            gxb["mid"].to_numpy(), gxb["up1"].to_numpy(), gxb["up2"].to_numpy(),
            gxb["lo1"].to_numpy(), gxb["lo2"].to_numpy(),
        )

    return sess


# --- touch / snap detection -------------------------------------------------


def _slope(values: np.ndarray, i: int) -> str:
    j = i - min(SLOPE_BARS, i)
    if j >= i or np.isnan(values[i]) or np.isnan(values[j]):
        return "flat"
    ppm = (values[i] - values[j]) / (i - j)
    return "rising" if ppm > SLOPE_FLAT_PPM else "falling" if ppm < -SLOPE_FLAT_PPM else "flat"


def _bucket(t: time) -> str:
    if t < time(10, 30):
        return "open"
    if t < time(14, 0):
        return "midday"
    return "pm"


def _outcome(sess: _Session, i: int, level: float, from_below: bool,
             window: int) -> tuple[str, float, float, int]:
    """Score a touch by looking forward ``window`` minutes.

    Returns (outcome, mfe, mae, reaction_min). ``mfe`` is the fade excursion (the
    move away from the level, in the direction price approached from); ``mae`` is
    the continuation excursion through the level.
    """
    lo = sess.low[i + 1:i + 1 + window]
    hi = sess.high[i + 1:i + 1 + window]
    cl = sess.close[i + 1:i + 1 + window]
    if len(cl) == 0:
        return "unknown", float("nan"), float("nan"), 0
    if from_below:  # level overhead: a reject fades DOWN, a continuation breaks UP
        fade = level - lo                      # per-bar fade excursion
        cont = hi - level
        end_beyond = cl[-1] > level + ACCEPT_MARGIN_PTS
    else:           # level below: a reject bounces UP, a continuation breaks DOWN
        fade = hi - level
        cont = level - lo
        end_beyond = cl[-1] < level - ACCEPT_MARGIN_PTS
    mfe = max(0.0, float(np.nanmax(fade)))   # floor at breakeven: a fade that never went green
    mae = max(0.0, float(np.nanmax(cont)))
    reaction = int(np.nanargmax(fade)) + 1
    if end_beyond and mae > mfe:
        return "accept", mfe, mae, reaction
    if mfe >= REJECT_MIN_PTS:
        return "reject", mfe, mae, reaction
    return "chop", mfe, mae, reaction


def _cluster(hits: list[tuple[int, float]], cluster_pts: float) -> list[list[int]]:
    """Group touched-series indices whose levels sit within ``cluster_pts``."""
    order = sorted(range(len(hits)), key=lambda k: hits[k][1])
    groups: list[list[int]] = []
    for k in order:
        if groups and hits[k][1] - hits[groups[-1][-1]][1] <= cluster_pts:
            groups[-1].append(k)
        else:
            groups.append([k])
    return groups


def _detect(sess: _Session, cfg: InteractionConfig) -> tuple[list[dict], list[dict], list[dict]]:
    touches: list[dict] = []
    snaps: list[dict] = []
    band_state: list[dict] = []
    n = len(sess.close)
    nth_seen: dict[int, int] = {}   # zone-price bucket -> distinct times tested today
    last_touch_i: dict[int, int] = {}  # zone-price bucket -> last straddling minute
    outer_ago = 0
    max_band_abs = 0

    for i in range(n):
        # --- band_state (NY) ---
        c = sess.close[i]
        if not np.isnan(sess.ny_mid[i]):
            b = _band_index(c, sess.ny_mid[i], sess.ny_up1[i], sess.ny_up2[i],
                            sess.ny_lo1[i], sess.ny_lo2[i])
            outer_ago = 0 if abs(b) >= 3 else outer_ago + 1
            max_band_abs = max(max_band_abs, abs(b))
            gx_band = None
            if sess.gx_mid is not None and not np.isnan(sess.gx_mid[i]):
                gb = _band_index(c, sess.gx_mid[i], sess.gx_up1[i], sess.gx_up2[i],
                                 sess.gx_lo1[i], sess.gx_lo2[i])
                gx_band = BAND_LABELS[_band_label_idx(gb)]
            band_state.append({
                "day": sess.day.isoformat(), "ts": int(sess.minute_utc[i]),
                "hhmm": sess.et_time[i].strftime("%H:%M"),
                "band": BAND_LABELS[_band_label_idx(b)], "gx_band": gx_band,
                "max_band_abs": int(max_band_abs), "bars_since_outer_tag": int(outer_ago),
            })

        # --- VA-snaps: a value boundary crossing price by its own jump ---
        ts_i = int(sess.minute_utc[i])
        if i > 0:
            for s in sess.series:
                if not s.is_va:
                    continue
                age_min = (ts_i - s.anchor_ts) // 60
                if age_min < LEVEL_WARMUP_MIN:
                    continue
                a, bcur = s.values[i - 1], s.values[i]
                if np.isnan(a) or np.isnan(bcur):
                    continue
                jump = bcur - a
                prev_side = a - sess.close[i - 1]
                cur_side = bcur - c
                crossed = (prev_side <= 0 < cur_side) or (prev_side >= 0 > cur_side)
                level_led = abs(jump) > abs(c - sess.close[i - 1])
                if crossed and level_led and abs(jump) >= SNAP_MIN_JUMP_PTS:
                    snaps.append({
                        "day": sess.day.isoformat(), "ts": ts_i,
                        "hhmm": sess.et_time[i].strftime("%H:%M"),
                        "source": s.source, "level_type": s.kind,
                        "snap_dir": "up_over_price" if cur_side > 0 else "down_under_price",
                        "level_jump_pts": round(float(jump), 2),
                        "level_age_min": int(age_min),
                        "excursion_bars_before": _excursion_before(s.values, sess.close, i),
                        "band_at_snap": band_state[-1]["band"] if band_state else None,
                        "px": round(float(c), 2),
                    })

        # --- touches: minute comes within tolerance of a level; cluster co-located ---
        hits = [(k, s.values[i]) for k, s in enumerate(sess.series)
                if (ts_i - s.anchor_ts) // 60 >= LEVEL_WARMUP_MIN
                and not np.isnan(s.values[i])
                and sess.low[i] - TOUCH_TOL_PTS <= s.values[i] <= sess.high[i] + TOUCH_TOL_PTS]
        if not hits:
            continue
        prevc = sess.close[i - 1] if i > 0 else sess.close[i]
        for grp in _cluster(hits, cfg.zone_cluster_pts):
            # grp holds indices into `hits`; map them to series indices.
            grp_series = [hits[k][0] for k in grp]
            zone_px = float(np.mean([hits[k][1] for k in grp]))
            bucket_key = int(round(zone_px / cfg.zone_cluster_pts))
            # Debounce: consecutive straddling minutes are one interaction. Only a
            # re-approach after TOUCH_GAP_BARS away counts as a fresh test.
            last = last_touch_i.get(bucket_key)
            last_touch_i[bucket_key] = i
            if last is not None and i - last <= TOUCH_GAP_BARS:
                continue
            # representative = series whose level is closest to the bar close
            rep_hi = min(grp, key=lambda k: abs(hits[k][1] - c))
            rep = sess.series[hits[rep_hi][0]]
            src_labels = sorted({sess.series[si].label for si in grp_series})
            n_sources = len({sess.series[si].source for si in grp_series})
            from_below = prevc <= zone_px
            outcome, mfe, mae, react = _outcome(sess, i, zone_px, from_below,
                                                cfg.outcome_window_min)
            horizons = {}
            for h in OUTCOME_HORIZONS_MIN:
                o, f, a, r = ((outcome, mfe, mae, react) if h == cfg.outcome_window_min
                              else _outcome(sess, i, zone_px, from_below, h))
                horizons[str(h)] = {
                    "outcome": o,
                    "mfe": None if np.isnan(f) else round(f, 2),
                    "mae": None if np.isnan(a) else round(a, 2),
                    "reaction_min": r,
                }
            nth = nth_seen.get(bucket_key, 0) + 1
            nth_seen[bucket_key] = nth
            touches.append({
                "day": sess.day.isoformat(), "ts": ts_i,
                "hhmm": sess.et_time[i].strftime("%H:%M"),
                "zone_px": round(zone_px, 2),
                "source": rep.source, "level_type": rep.kind, "label": rep.label,
                "sources": src_labels, "n_sources": n_sources,
                "nearest_other_source_dist": _nearest_other(sess, i, grp_series, zone_px),
                "nth_touch": nth, "approach": "below" if from_below else "above",
                "level_slope": _slope(rep.values, i),
                "level_age_min": int((ts_i - rep.anchor_ts) // 60),
                "touch_vol": int(sess.volume[i]), "signed_delta": int(sess.delta[i]),
                "time_bucket": _bucket(sess.et_time[i]),
                "outcome": outcome,
                "mfe": None if np.isnan(mfe) else round(mfe, 2),
                "mae": None if np.isnan(mae) else round(mae, 2),
                "reaction_min": react,
                "outcomes": horizons,
            })
    return touches, snaps, band_state


def _nearest_other(sess: _Session, i: int, grp_series: list[int], zone_px: float) -> float | None:
    """Distance from the zone to the closest active level of a *different* source,
    at minute ``i`` — the continuous confluence measure behind ``n_sources``."""
    zone_sources = {sess.series[k].source for k in grp_series}
    best = None
    for s in sess.series:
        if s.source in zone_sources:
            continue
        v = s.values[i]
        if np.isnan(v):
            continue
        d = abs(float(v) - zone_px)
        if best is None or d < best:
            best = d
    return None if best is None else round(best, 2)


def _excursion_before(values: np.ndarray, close: np.ndarray, i: int) -> int:
    """How many consecutive prior bars price sat on the pre-snap side of the level."""
    side = np.sign(values[i - 1] - close[i - 1])
    j = i - 1
    count = 0
    while j >= 0 and not np.isnan(values[j]) and np.sign(values[j] - close[j]) == side:
        count += 1
        j -= 1
    return count


def _band_index(c, mid, up1, up2, lo1, lo2) -> int:
    if c > up2:
        return 3
    if c > up1:
        return 2
    if c >= mid:
        return 1
    if c > lo1:
        return -1
    if c > lo2:
        return -2
    return -3


def _band_label_idx(b: int) -> int:
    return {3: 0, 2: 1, 1: 2, -1: 3, -2: 4, -3: 5}[b]


# --- aggregates -------------------------------------------------------------


def _agg_touches(touches: list[dict], key) -> list[dict]:
    buckets: dict = {}
    for t in touches:
        if t["outcome"] == "unknown":
            continue
        for k in key(t):
            buckets.setdefault(k, []).append(t)
    rows = []
    for label, ts in buckets.items():
        rej = [t for t in ts if t["outcome"] == "reject"]
        mfes = [t["mfe"] for t in ts if t["mfe"] is not None]
        maes = [t["mae"] for t in ts if t["mae"] is not None]
        rows.append({
            "label": label, "n": len(ts),
            "reject_rate": round(len(rej) / len(ts), 3) if ts else None,
            "avg_mfe": round(float(np.mean(mfes)), 2) if mfes else None,
            "avg_mae": round(float(np.mean(maes)), 2) if maes else None,
        })
    rows.sort(key=lambda r: -r["n"])
    return rows


def _nth_bucket(t: dict) -> list:
    n = t["nth_touch"]
    return [f"{n}" if n < 4 else "4+"]


def _conf_bucket(t: dict) -> list:
    return ["2+ sources" if t["n_sources"] >= 2 else "lone level"]


def _horizon_agg(touches: list[dict]) -> list[dict]:
    """The identical touch set rescored at each fixed window — shows how much of
    an "outcome" is just the clock. Row shape matches _agg_touches so the UI can
    reuse the same table."""
    rows = []
    for h in OUTCOME_HORIZONS_MIN:
        os_ = [t["outcomes"][str(h)] for t in touches
               if t["outcomes"][str(h)]["outcome"] != "unknown"]
        rej = [o for o in os_ if o["outcome"] == "reject"]
        mfes = [o["mfe"] for o in os_ if o["mfe"] is not None]
        maes = [o["mae"] for o in os_ if o["mae"] is not None]
        rows.append({
            "label": f"{h}m", "n": len(os_),
            "reject_rate": round(len(rej) / len(os_), 3) if os_ else None,
            "avg_mfe": round(float(np.mean(mfes)), 2) if mfes else None,
            "avg_mae": round(float(np.mean(maes)), 2) if maes else None,
        })
    return rows


def _row_med30(label: str, touches: list[dict]) -> dict:
    """One aggregate row scored at the fixed 30m window with MEDIAN MFE/MAE.

    Medians, not means, because these rows exist to be read against the null
    baseline: a fat tail on either leg is exactly what a mean hides. ``ratio``
    (med MFE / med MAE) is the asymmetry read — ~1.0 means the levels are doing
    nothing regardless of the reject rate.
    """
    os_ = [t["outcomes"]["30"] for t in touches if t["outcomes"]["30"]["outcome"] != "unknown"]
    mfes = [o["mfe"] for o in os_ if o["mfe"] is not None]
    maes = [o["mae"] for o in os_ if o["mae"] is not None]
    med_mfe = round(float(np.median(mfes)), 2) if mfes else None
    med_mae = round(float(np.median(maes)), 2) if maes else None
    return {
        "label": label, "n": len(os_),
        "reject_rate": round(sum(o["outcome"] == "reject" for o in os_) / len(os_), 3) if os_ else None,
        "med_mfe": med_mfe, "med_mae": med_mae,
        "ratio": round(med_mfe / med_mae, 2) if med_mfe is not None and med_mae else None,
    }


def _band_at(band_state: list[dict]) -> dict:
    return {(b["day"], b["ts"]): b["band"] for b in band_state}


def _band_context_agg(touches: list[dict], band_state: list[dict]) -> list[dict]:
    """Touches re-cut by where price sat in the NY VWAP bands at the touch.

    This is the one conditioner that produced a real cut: the same level means
    different things in different bands (a POC under price in +1..+2 is a
    pullback shelf; the same POC in vwap..+1 is noise). Grouped by
    band × approach side; the null baseline row rides along as the benchmark.
    """
    if not band_state:
        return []
    band_at = _band_at(band_state)
    buckets: dict[tuple, list[dict]] = {}
    for t in touches:
        band = band_at.get((t["day"], t["ts"]))
        if band is not None:
            buckets.setdefault((band, t["approach"]), []).append(t)
    order = {b: i for i, b in enumerate(BAND_LABELS)}
    keys = sorted(buckets, key=lambda k: (order.get(k[0], 99), k[1]))
    rows = [_row_med30(f"{band} · from {appr}", buckets[(band, appr)]) for band, appr in keys]
    rows.append(dict(NULL_BASELINE_ROW))
    return rows


def _upper_band_pullback_agg(touches: list[dict], band_state: list[dict]) -> list[dict]:
    """The named tradeable cut and its sub-cuts, against the null baseline.

    Pullback-from-above onto a developing POC/VAH while price holds the NY VWAP
    +1σ..+2σ channel. The sub-rows are the conditioners that survived testing
    (1st touch, 2+ sources stacked, mature Globex levels) plus the one robust
    exclusion — 15:00+ touches score exactly at null. Day-type filters are
    deliberately absent: no pre-known feature predicted the bounce (the trend-up
    day label is partly *defined by* these pullbacks holding).
    """
    if not band_state:
        return []
    band_at = _band_at(band_state)
    cut = [
        t for t in touches
        if t["approach"] == "above" and t["level_type"] in ("POC", "VAH")
        and band_at.get((t["day"], t["ts"])) == "+1..+2"
    ]
    rows = [
        _row_med30("all touches", cut),
        _row_med30("1st touch", [t for t in cut if t["nth_touch"] == 1]),
        _row_med30("2+ sources stacked", [t for t in cut if t["n_sources"] >= 2]),
        _row_med30("Globex level", [t for t in cut if t["source"] == "globex"]),
        _row_med30("before 15:00", [t for t in cut if int(t["hhmm"][:2]) < 15]),
        _row_med30("15:00+ (dead)", [t for t in cut if int(t["hhmm"][:2]) >= 15]),
        dict(NULL_BASELINE_ROW),
    ]
    return rows


def _band_occupancy_agg(band_state: list[dict], key: str = "band") -> list[dict]:
    """Time price spent in each VWAP band, from the per-minute band_state stream.

    Each band_state row is one RTH minute already classified into a band by
    ``_band_index``, so occupancy is just a tally: ``minutes`` = total minutes in
    the band across the run, ``pct`` its share of all classified minutes, and
    ``avg_min`` the per-session average (the raw total scales with the date
    range; the average is the comparable read). Rows keep BAND_LABELS order so
    the table reads top-to-bottom like the chart (>+2σ down to <-2σ).

    ``key`` selects the classification: "band" for NY VWAP, "gx_band" for the
    overnight-anchored Globex VWAP. Minutes with no value under ``key`` (e.g.
    ``gx_band`` when globex is not a source) are skipped, so the Globex table is
    empty rather than misleading when the anchor wasn't computed."""
    counts: dict[str, int] = {b: 0 for b in BAND_LABELS}
    days: set = set()
    for st in band_state:
        band = st.get(key)
        if band is None:
            continue
        counts[band] = counts.get(band, 0) + 1
        days.add(st["day"])
    total = sum(counts.values())
    if total == 0:
        return []
    n_sessions = len(days)
    return [
        {
            "label": band,
            "minutes": counts[band],
            "pct": round(counts[band] / total, 3) if total else None,
            "avg_min": round(counts[band] / n_sessions, 1) if n_sessions else None,
        }
        for band in BAND_LABELS
    ]


def _snap_buckets(snaps: list[dict]) -> dict[str, list[dict]]:
    """(anchor, level_type, direction) buckets over the non-trivial snaps —
    those with actual room to VWAP. Trivial ones are kept aside per label."""
    buckets: dict[str, list[dict]] = {}
    for s in snaps:
        anchor = "NY" if s["source"] == "ny" else "Globex"
        buckets.setdefault(f"{anchor} {s['level_type']} {s['snap_dir']}", []).append(s)
    return buckets


def _live(grp: list[dict]) -> list[dict]:
    return [s for s in grp if (s.get("vwap_dist_pts") or 0) > 0]


def _vasnap_agg(snaps: list[dict], sessions: dict) -> list[dict]:
    """The fade-to-VWAP trade, by (anchor, level_type, direction).

    Rates run over non-trivial snaps only — ``n_trivial`` counts the ones whose
    close was already at/through VWAP at the snap bar (reversion true at bar
    zero, no trade). ``revert_rate`` is the by-session-end upper bound; the
    30m/60m rates are the bounded reads; ``avg_adverse`` is the worst excursion
    against the fade before it reverted; ``avg_dist`` the room to VWAP at entry."""
    rows = []
    for label, grp in _snap_buckets(snaps).items():
        live = _live(grp)
        mins = [s.get("revert_min") for s in live]
        moves = [s["revert_move"] for s in live if s.get("revert_move") is not None]
        advs = [s["adverse_move"] for s in live if s.get("adverse_move") is not None]
        dists = [s["vwap_dist_pts"] for s in live]
        rows.append({
            "label": label, "n": len(live), "n_trivial": len(grp) - len(live),
            "revert_rate": round(sum(m is not None for m in mins) / len(live), 3) if live else None,
            "revert_rate_30": round(sum(m is not None and m <= 30 for m in mins) / len(live), 3) if live else None,
            "revert_rate_60": round(sum(m is not None and m <= 60 for m in mins) / len(live), 3) if live else None,
            "avg_move": round(float(np.mean(moves)), 2) if moves else None,
            "avg_adverse": round(float(np.mean(advs)), 2) if advs else None,
            "avg_dist": round(float(np.mean(dists)), 2) if dists else None,
        })
    rows.sort(key=lambda r: -r["n"])
    return rows


def _vasnap_cont_agg(snaps: list[dict]) -> list[dict]:
    """The same snaps flipped into the continuation trade: enter in the snap's
    direction, stop on a close through NY VWAP. ``hold_rate`` is the share never
    stopped within the window, ``avg_run`` the mean excursion in the snap
    direction before the stop, ``avg_stop_dist`` the entry-to-VWAP distance the
    stop risks, and ``rr_60`` = avg 60m run / avg stop distance."""
    rows = []
    for label, grp in _snap_buckets(snaps).items():
        live = [s for s in _live(grp) if s.get("cont_move")]
        if not live:
            continue
        mins = [s.get("revert_min") for s in live]
        run30 = [s["cont_move"]["30"] for s in live]
        run60 = [s["cont_move"]["60"] for s in live]
        dists = [s["vwap_dist_pts"] for s in live]
        avg_run_60 = float(np.mean(run60))
        avg_dist = float(np.mean(dists))
        rows.append({
            "label": label, "n": len(live),
            "hold_rate_30": round(sum(m is None or m > 30 for m in mins) / len(live), 3),
            "hold_rate_60": round(sum(m is None or m > 60 for m in mins) / len(live), 3),
            "avg_run_30": round(float(np.mean(run30)), 2),
            "avg_run_60": round(avg_run_60, 2),
            "avg_stop_dist": round(avg_dist, 2),
            "rr_60": round(avg_run_60 / avg_dist, 2) if avg_dist > 0 else None,
        })
    rows.sort(key=lambda r: -r["n"])
    return rows


def _annotate_snap_reversion(snaps: list[dict], sess: _Session) -> None:
    """Score each snap as the fade-to-VWAP trade it implies.

    ``reverted`` (did close reach NY VWAP before the session ended) and
    ``revert_move`` (max favorable excursion to session end) stay the generous
    upper bound. The honest measures added alongside: ``revert_min`` — minutes
    until VWAP was first reached (a snap at 10:00 has all day to wander there; a
    bounded read is what makes the rate meaningful) — and ``adverse_move``, the
    worst excursion against the reversion before it happened (to session end if
    it never did): what holding the fade would have cost.

    ``vwap_dist_pts`` is the room between the snap close and VWAP in the
    reversion direction — the fade's profit potential and, flipped, the
    continuation trade's stop distance. <= 0 means price was already at/through
    VWAP at the snap bar: the "reversion" is true at bar zero and means nothing
    (aggregates exclude these as trivial). ``cont_move`` is the max excursion in
    the snap's own direction before the VWAP touch, bounded per horizon — the
    flip trade's favorable run.
    """
    by_ts = {int(sess.minute_utc[i]): i for i in range(len(sess.minute_utc))}
    for s in snaps:
        i = by_ts.get(s["ts"])
        if i is None:
            continue
        mid_fwd = sess.ny_mid[i:]
        close_fwd = sess.close[i:]
        px = s["px"]
        if s["snap_dir"] == "up_over_price":   # rally capped -> expect drift down to VWAP
            hit = close_fwd <= mid_fwd
            move = float(px - np.nanmin(close_fwd))
            against = close_fwd - px           # continuation up = adverse
            dist = float(px - mid_fwd[0])
        else:                                   # break supported -> expect drift up to VWAP
            hit = close_fwd >= mid_fwd
            move = float(np.nanmax(close_fwd) - px)
            against = px - close_fwd           # continuation down = adverse
            dist = float(mid_fwd[0] - px)
        rev_i = int(np.argmax(hit)) if bool(np.any(hit)) else None
        adverse_span = against[:rev_i + 1] if rev_i is not None else against
        end = rev_i if rev_i is not None else len(against) - 1
        s["reverted"] = rev_i is not None
        s["revert_min"] = rev_i
        s["revert_move"] = round(move, 2)
        s["adverse_move"] = round(max(0.0, float(np.nanmax(adverse_span))), 2)
        s["vwap_dist_pts"] = round(dist, 2) if np.isfinite(dist) else None
        s["cont_move"] = {
            str(h): round(max(0.0, float(np.nanmax(against[:min(h, end) + 1]))), 2)
            for h in OUTCOME_HORIZONS_MIN
        }


# --- study ------------------------------------------------------------------


def study(cfg: InteractionConfig) -> dict:
    """Run the interaction study over the config's date range (cache-only)."""
    requested = tickmod.session_dates(cfg.start, cfg.end)
    touches: list[dict] = []
    snaps: list[dict] = []
    band_state: list[dict] = []
    skipped: list[str] = []
    day_index: dict[str, dict] = {}
    ran = 0

    for day in requested:
        contract = tickmod.contract_for_cached(cfg.symbol, day)
        if contract is None:
            skipped.append(day.isoformat())
            continue
        sess = _build_session(cfg, day, contract)
        if sess is None:
            skipped.append(day.isoformat())
            continue
        t, s, b = _detect(sess, cfg)
        _annotate_snap_reversion(s, sess)
        touches += t
        snaps += s
        band_state += b
        day_index[day.isoformat()] = {"n_touches": len(t), "n_snaps": len(s)}
        ran += 1

    return {
        "interactions_version": INTERACTIONS_VERSION,
        **cfg.to_json(),
        "coverage": {
            "requested_days": len(requested), "ran_days": ran, "skipped": skipped,
        },
        "events": {"touches": touches, "va_snaps": snaps, "band_state": band_state},
        "aggregates": {
            "by_source": _agg_touches(touches, lambda t: t["sources"]),
            "by_nth_touch": _agg_touches(touches, _nth_bucket),
            "confluence_lift": _agg_touches(touches, _conf_bucket),
            "by_horizon": _horizon_agg(touches),
            "by_band_context": _band_context_agg(touches, band_state),
            "band_occupancy": _band_occupancy_agg(band_state, "band"),
            "band_occupancy_gx": _band_occupancy_agg(band_state, "gx_band"),
            "upper_band_pullback": _upper_band_pullback_agg(touches, band_state),
            "vasnap_reversion": _vasnap_agg(snaps, {}),
            "vasnap_continuation": _vasnap_cont_agg(snaps),
        },
        "day_index": day_index,
    }


# --- snapshot store ---------------------------------------------------------


def _path(cfg: InteractionConfig):
    return INTERACTIONS_DIR / f"{cfg.run_id()}.json"


def read(cfg: InteractionConfig) -> dict | None:
    p = _path(cfg)
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return d if d.get("interactions_version") == INTERACTIONS_VERSION else None


def write(cfg: InteractionConfig, result: dict) -> None:
    INTERACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    _path(cfg).write_text(json.dumps(result, indent=2))


def get(cfg: InteractionConfig, refresh: bool = False) -> dict:
    if not refresh and (cached := read(cfg)) is not None:
        return cached
    result = study(cfg)
    write(cfg, result)
    return result


def list_runs() -> list[dict]:
    """Summaries of every snapshot on disk, newest first. Loads each file in
    full (the events dominate the size), so this is a listing endpoint, not
    something to poll — fine for the handful of snapshots a lab accumulates."""
    if not INTERACTIONS_DIR.exists():
        return []
    runs = []
    for p in sorted(INTERACTIONS_DIR.glob("*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("interactions_version") != INTERACTIONS_VERSION:
            continue
        runs.append({
            "run_id": p.stem,
            "config": {k: d[k] for k in (
                "symbol", "start", "end", "bin_size", "va_pct", "sources",
                "outcome_window_min", "zone_cluster_pts",
            )},
            "coverage": d["coverage"],
            "n_touches": len(d["events"]["touches"]),
            "n_snaps": len(d["events"]["va_snaps"]),
            "saved_at": p.stat().st_mtime,
        })
    return runs


def _finite(x) -> bool:
    return bool(np.isfinite(x))


def _day_chart_tickbars(
    symbol: str,
    contract: str,
    rth: pd.DataFrame,
    day: date,
    binsz: float,
    va_pct: float,
    sources: tuple[str, ...],
    n: int,
) -> dict:
    """The day-chart in n-tick bars instead of 1-minute candles.

    One continuous tick stream — the overnight leg (when cached) then RTH — is
    chunked into n-tick bars, and every overlay is sampled by each bar's end_idx
    into that same stream. This is the approach api.sim_charts uses, and why both
    anchors stay on one grid even though the night's tick remainder shifts the RTH
    boundaries: build the bars once over the whole stream, then shift the NY
    anchor's positions rather than rebuilding a separate RTH-only bar set. A tick
    bar built here need not match ATAS tick-for-tick — see journal.sim.bars.
    """
    on = tickmod.cached_overnight(contract, day)
    has_on = on is not None and not on.empty
    if has_on:
        full = pd.concat([on, rth], ignore_index=True)
        rth_i0 = len(on)
    else:
        full, rth_i0 = rth, 0

    b_all = barmod.tick_bars(full, n)
    if b_all.empty:
        return {"available": True, "instrument": contract, "bars": []}
    bar_pos = b_all["end_idx"].to_numpy(dtype="int64")
    times = _strictly_increasing(
        (b_all["ts_utc"].astype("int64") // 1_000_000_000).to_numpy())

    bars = [
        {"time": int(tm), "open": float(o), "high": float(h), "low": float(lo),
         "close": float(c), "volume": float(v)}
        for tm, o, h, lo, c, v in zip(
            times, b_all["open"], b_all["high"], b_all["low"],
            b_all["close"], b_all["volume"])
    ]

    result = {
        "available": True,
        "instrument": contract,
        "bars": bars,
        "vwap_ny": [],
        "profile_ny": [],
        "vwap_globex": [],
        "profile_globex": [],
        "tick_size": tick_size(symbol),
        "point_value": point_value(symbol),
    }

    # NY anchor: the same stream restarted at the bell. A bar that closed overnight
    # has a negative shifted position and is dropped, so the line begins at the open.
    if "ny" in sources:
        rth_ticks = full.iloc[rth_i0:].reset_index(drop=True)
        ny_pos = bar_pos - rth_i0
        result["vwap_ny"] = _vwap_pos_rows(vwapmod.vwap_bands(rth_ticks), ny_pos, times)
        ny_bars = b_all.assign(end_idx=ny_pos)
        result["profile_ny"] = _profile_pos_rows(
            profmod.developing_profile(rth_ticks, ny_bars, binsz, va_pct), times)

    # Globex anchor: accumulates from the 18:00 open over the whole stream — only
    # meaningful, and only drawn, when the night is actually on disk.
    if "globex" in sources and rth_i0 > 0:
        result["vwap_globex"] = _vwap_pos_rows(vwapmod.vwap_bands(full), bar_pos, times)
        result["profile_globex"] = _profile_pos_rows(
            profmod.developing_profile(full, b_all, binsz, va_pct), times)
    return result


def day_chart(
    symbol: str,
    day: date,
    bin_size: float | None = None,
    va_pct: float = profmod.VALUE_AREA_PCT,
    sources: tuple[str, ...] = ("ny", "globex"),
    ticks_per_bar: int | None = None,
) -> dict:
    """A day's candles + both anchored VWAPs + both developing profiles, built
    from the *same tick engine* as the interaction events — so the overlay dots
    sit on exactly the levels they were computed against. GET-safe (cache only),
    trade-independent: works for any cached session, traded or not.

    ``ticks_per_bar`` swaps the 1-minute candles for n-tick bars (the strategies'
    native timeframe); left None it stays on minute bars. The interaction events
    themselves are unchanged (they are computed on the minute grid) — the frontend
    snaps their marks onto whichever bar grid is drawn.
    """
    contract = tickmod.contract_for_cached(symbol, day)
    if contract is None:
        return {"available": False}
    rth = tickmod.cached_rth(contract, day)
    if rth is None or rth.empty:
        return {"available": False}
    binsz = bin_size if bin_size is not None else tick_size(symbol)

    if ticks_per_bar:
        return _day_chart_tickbars(
            symbol, contract, rth, day, binsz, va_pct, sources, int(ticks_per_bar))

    # RTH minute bars — the NY anchor develops over exactly these.
    bars_ny = minute_bars(rth)
    if bars_ny.empty:
        return {"available": True, "instrument": contract, "bars": []}
    t_ny = (bars_ny["ts_utc"].astype("int64") // 1_000_000_000).to_numpy()

    # The drawn candle stream mirrors the strategies day-chart: the overnight leg
    # (18:00 ET → the bell) in front of RTH when the night is on disk, else RTH
    # alone. The overnight is context and shows regardless of the sources toggle,
    # which only governs which level overlays are drawn.
    on = tickmod.cached_overnight(contract, day)
    has_on = on is not None and not on.empty
    if has_on:
        glob = pd.concat([on, rth], ignore_index=True)
        bars_draw = minute_bars(glob)
        t_draw = (bars_draw["ts_utc"].astype("int64") // 1_000_000_000).to_numpy()
    else:
        glob, bars_draw, t_draw = rth, bars_ny, t_ny

    bars = [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(lo),
         "close": float(c), "volume": float(v)}
        for t, o, h, lo, c, v in zip(
            t_draw, bars_draw["open"], bars_draw["high"], bars_draw["low"],
            bars_draw["close"], bars_draw["volume"])
    ]

    def vwap_rows(band_df, times):
        out = []
        for t, r in zip(times, band_df.itertuples(index=False)):
            if not _finite(r.mid):
                continue
            out.append({"time": int(t), "middle": round(float(r.mid), 2),
                        "upper1": round(float(r.upper1), 2), "lower1": round(float(r.lower1), 2),
                        "upper2": round(float(r.upper2), 2), "lower2": round(float(r.lower2), 2)})
        return out

    def prof_rows(prof, times):
        out = []
        for t, poc, vah, val in zip(times, prof.poc, prof.vah, prof.val):
            if not _finite(poc):
                continue
            out.append({"time": int(t), "poc": round(float(poc), 2),
                        "vah": round(float(vah), 2), "val": round(float(val), 2)})
        return out

    result = {
        "available": True,
        "instrument": contract,
        "bars": bars,
        "vwap_ny": vwap_rows(_sample_bands(vwapmod.vwap_bands(rth), bars_ny), t_ny)
        if "ny" in sources else [],
        "profile_ny": prof_rows(profmod.developing_profile(rth, bars_ny, binsz, va_pct), t_ny)
        if "ny" in sources else [],
        "vwap_globex": [],
        "profile_globex": [],
        "tick_size": tick_size(symbol),
        "point_value": point_value(symbol),
    }

    # The Globex anchor develops from the 18:00 open over the same stream we drew,
    # so its levels span the whole chart (overnight leg included) rather than being
    # clipped to the RTH minutes.
    if "globex" in sources and has_on:
        prof_gx = profmod.developing_profile(glob, bars_draw, binsz, va_pct)
        vwap_gx = _sample_bands(vwapmod.vwap_bands(glob), bars_draw)
        result["vwap_globex"] = vwap_rows(vwap_gx, t_draw)
        result["profile_globex"] = prof_rows(prof_gx, t_draw)
    return result


def coverage(symbol: str, start: date, end: date) -> dict:
    """Which sessions in [start, end] have cached ticks (rth / overnight)."""
    days = []
    for day in tickmod.session_dates(start, end):
        contract = tickmod.contract_for_cached(symbol, day)
        rth = contract is not None and tickmod._cache_path(contract, day, "rth").exists()
        on = contract is not None and tickmod._cache_path(contract, day, "on").exists()
        days.append({"date": day.isoformat(), "rth": bool(rth), "on": bool(on)})
    return {"symbol": symbol, "days": days}
