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
from . import profile as profmod
from . import ticks as tickmod
from . import vwap as vwapmod
from .regime import minute_bars

INTERACTIONS_VERSION = 1
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

DEFAULTS = {
    "bin_size": None,          # None -> the instrument tick grid (matches the chart)
    "va_pct": profmod.VALUE_AREA_PCT,
    "sources": ["ny", "globex"],
    "outcome_window_min": 10,
    "zone_cluster_pts": 10.0,
}

BAND_LABELS = (">+2σ", "+1..+2", "vwap..+1", "-1..vwap", "-2..-1", "<-2σ")


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


def _sample_bands(bands: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """VWAP-band row at each bar's close (by positional end_idx into the ticks)."""
    idx = bars["end_idx"].to_numpy(dtype="int64")
    return bands.iloc[idx].reset_index(drop=True)


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

    if "ny" in cfg.sources:
        sess.series += [
            _Series("ny", "VAH", prof_ny.vah, True),
            _Series("ny", "VAL", prof_ny.val, True),
            _Series("ny", "POC", prof_ny.poc, True),
        ]
    if "vwap_bands" in cfg.sources:
        sess.series += [
            _Series("ny", "+1σ", sess.ny_up1, False),
            _Series("ny", "-1σ", sess.ny_lo1, False),
            _Series("ny", "+2σ", sess.ny_up2, False),
            _Series("ny", "-2σ", sess.ny_lo2, False),
        ]

    if "globex" in cfg.sources:
        glob = pd.concat([on, rth], ignore_index=True) if on is not None else rth
        bars_gx = minute_bars(glob)
        prof_gx = profmod.developing_profile(glob, bars_gx, cfg.bin_size, cfg.va_pct)
        # Align the Globex bars (which span the overnight) onto the RTH minutes.
        gx = pd.DataFrame({
            "ts_utc": bars_gx["ts_utc"].astype("int64") // 1_000_000_000,
            "VAH": prof_gx.vah, "VAL": prof_gx.val, "POC": prof_gx.poc,
        }).set_index("ts_utc").reindex(minute_utc)
        for kind in ("VAH", "VAL", "POC"):
            sess.series.append(_Series("globex", kind, gx[kind].to_numpy(), True))

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
            band_state.append({
                "day": sess.day.isoformat(), "ts": int(sess.minute_utc[i]),
                "hhmm": sess.et_time[i].strftime("%H:%M"),
                "band": BAND_LABELS[_band_label_idx(b)],
                "max_band_abs": int(max_band_abs), "bars_since_outer_tag": int(outer_ago),
            })

        # --- VA-snaps: a value boundary crossing price by its own jump ---
        if i > 0:
            for s in sess.series:
                if not s.is_va:
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
                        "day": sess.day.isoformat(), "ts": int(sess.minute_utc[i]),
                        "hhmm": sess.et_time[i].strftime("%H:%M"),
                        "source": s.source, "level_type": s.kind,
                        "snap_dir": "up_over_price" if cur_side > 0 else "down_under_price",
                        "level_jump_pts": round(float(jump), 2),
                        "excursion_bars_before": _excursion_before(s.values, sess.close, i),
                        "band_at_snap": band_state[-1]["band"] if band_state else None,
                        "px": round(float(c), 2),
                    })

        # --- touches: minute comes within tolerance of a level; cluster co-located ---
        hits = [(k, s.values[i]) for k, s in enumerate(sess.series)
                if not np.isnan(s.values[i])
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
            nth = nth_seen.get(bucket_key, 0) + 1
            nth_seen[bucket_key] = nth
            touches.append({
                "day": sess.day.isoformat(), "ts": int(sess.minute_utc[i]),
                "hhmm": sess.et_time[i].strftime("%H:%M"),
                "zone_px": round(zone_px, 2),
                "source": rep.source, "level_type": rep.kind, "label": rep.label,
                "sources": src_labels, "n_sources": n_sources,
                "nearest_other_source_dist": _nearest_other(sess, i, grp_series, zone_px),
                "nth_touch": nth, "approach": "below" if from_below else "above",
                "level_slope": _slope(rep.values, i),
                "touch_vol": int(sess.volume[i]), "signed_delta": int(sess.delta[i]),
                "time_bucket": _bucket(sess.et_time[i]),
                "outcome": outcome,
                "mfe": None if np.isnan(mfe) else round(mfe, 2),
                "mae": None if np.isnan(mae) else round(mae, 2),
                "reaction_min": react,
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


def _vasnap_agg(snaps: list[dict], sessions: dict) -> list[dict]:
    """Group snaps by (level_type, direction); did price revert to VWAP after."""
    buckets: dict = {}
    for s in snaps:
        buckets.setdefault(f"{s['level_type']} {s['snap_dir']}", []).append(s)
    rows = []
    for label, grp in buckets.items():
        reverts = [s for s in grp if s.get("reverted")]
        moves = [s["revert_move"] for s in grp if s.get("revert_move") is not None]
        rows.append({
            "label": label, "n": len(grp),
            "revert_rate": round(len(reverts) / len(grp), 3) if grp else None,
            "avg_move": round(float(np.mean(moves)), 2) if moves else None,
        })
    rows.sort(key=lambda r: -r["n"])
    return rows


def _annotate_snap_reversion(snaps: list[dict], sess: _Session) -> None:
    """For each snap, did price reach NY VWAP before the session ended, and how far did it get."""
    by_ts = {int(sess.minute_utc[i]): i for i in range(len(sess.minute_utc))}
    for s in snaps:
        i = by_ts.get(s["ts"])
        if i is None:
            continue
        mid_fwd = sess.ny_mid[i:]
        close_fwd = sess.close[i:]
        px = s["px"]
        if s["snap_dir"] == "up_over_price":   # rally capped -> expect drift down to VWAP
            reached = bool(np.any(close_fwd <= mid_fwd))
            move = float(px - np.nanmin(close_fwd))
        else:                                   # break supported -> expect drift up to VWAP
            reached = bool(np.any(close_fwd >= mid_fwd))
            move = float(np.nanmax(close_fwd) - px)
        s["reverted"] = reached
        s["revert_move"] = round(move, 2)


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
            "vasnap_reversion": _vasnap_agg(snaps, {}),
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


def _finite(x) -> bool:
    return bool(np.isfinite(x))


def day_chart(
    symbol: str,
    day: date,
    bin_size: float | None = None,
    va_pct: float = profmod.VALUE_AREA_PCT,
    sources: tuple[str, ...] = ("ny", "globex"),
) -> dict:
    """A day's candles + both anchored VWAPs + both developing profiles, built
    from the *same tick engine* as the interaction events — so the overlay dots
    sit on exactly the levels they were computed against. GET-safe (cache only),
    trade-independent: works for any cached session, traded or not.
    """
    contract = tickmod.contract_for_cached(symbol, day)
    if contract is None:
        return {"available": False}
    rth = tickmod.cached_rth(contract, day)
    if rth is None or rth.empty:
        return {"available": False}
    binsz = bin_size if bin_size is not None else tick_size(symbol)

    bars_ny = minute_bars(rth)
    if bars_ny.empty:
        return {"available": True, "instrument": contract, "bars": []}
    t_ny = (bars_ny["ts_utc"].astype("int64") // 1_000_000_000).to_numpy()

    bars = [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(lo),
         "close": float(c), "volume": float(v)}
        for t, o, h, lo, c, v in zip(
            t_ny, bars_ny["open"], bars_ny["high"], bars_ny["low"],
            bars_ny["close"], bars_ny["volume"])
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

    on = tickmod.cached_overnight(contract, day)
    if "globex" in sources and on is not None:
        glob = pd.concat([on, rth], ignore_index=True)
        bars_gx = minute_bars(glob)
        t_gx = (bars_gx["ts_utc"].astype("int64") // 1_000_000_000).to_numpy()
        # keep only the Globex rows that fall on the RTH minutes we drew candles for
        keep = np.isin(t_gx, t_ny)
        prof_gx = profmod.developing_profile(glob, bars_gx, binsz, va_pct)
        vwap_gx = _sample_bands(vwapmod.vwap_bands(glob), bars_gx)
        result["vwap_globex"] = vwap_rows(vwap_gx[keep].reset_index(drop=True), t_gx[keep])
        result["profile_globex"] = prof_rows(
            profmod.DevelopingProfile(prof_gx.poc[keep], prof_gx.vah[keep], prof_gx.val[keep]),
            t_gx[keep],
        )
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
