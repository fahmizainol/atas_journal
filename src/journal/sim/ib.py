"""Initial Balance / opening-range study — session structure over the tick cache.

The research bench for the IB & ORB families before anything becomes a gate or a
strategy (see docs/research/initial-balance-orb.md for the source review this
implements). Like ``interactions``, this is pure market structure: no trades, a
row exists for any cached session, every read is GET-safe (tick cache only).

What one session produces:

  - the **Initial Balance** — high/low/mid/range of the first ``ib_minutes`` of
    RTH (default 60, the two-TPO convention; the window is a knob, not gospel) —
    plus when and on which side price first left it, whether it broke both
    sides, and how far it extended in IB-range multiples;
  - the **CBOT day type** from those extension multiples (normal / normal
    variation / trend / neutral), the classifier the day-type base-rate lore
    never quantified for NQ;
  - **opening-range candles** at each ORB window (5/15/30 min): direction of
    the window's candle and whether the session followed it, scored as the
    Zarattini entry (enter at the window close in the candle's direction, stop
    at the candle's opposite extreme, exit at the close) so the aggregate reads
    in R-multiples, not just hit-rate;
  - **context** the aggregates condition on: overnight (Globex) range and where
    the open/IB sit inside it, the opening gap, and the prior-14-session
    average day range (``adr14`` — an ATR stand-in built from the study's own
    sessions, so no external daily bars are needed).

The aggregates exist to check the research doc's claims against *our* NQ data:
break rates (the "IB extends 70–80% of days" lore vs the ~96% measured
elsewhere), the extension distribution (how rare 1×/1.5×/2× really is), day-type
base rates, second-break-wins on double-break days, IB-width-vs-ADR terciles
(the strongest documented conditioner), ORB follow-through per window, gap
alignment, weekday — and the one nobody has published: the overnight range vs
IB relationship.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from ..config import CACHE_DIR, ET_TZ
from . import ticks as tickmod
from .regime import minute_bars

IB_VERSION = 1
IB_DIR = CACHE_DIR / "ib"

RTH_OPEN = time(9, 30)
NOON = time(12, 0)

# Day-type thresholds, from the CBOT/Steidlmayer definitions (research doc §3).
# All are ratios of the day's range to the IB range ("range_x"):
#   normal           range_x <= 1/0.85 — the IB is >=85% of the day's range
#   normal variation range_x <= 2      — extension up to double the IB
#   trend            range_x  > 2 AND the close sits in the outer quarter of the
#                    day's range on the break side ("close at the directional
#                    extreme"); a >2x day that closes mid-range stays normal
#                    variation rather than inventing a class the source doesn't
#   neutral          both IB sides broken, regardless of range_x; split into
#                    center/extreme by where the close sits (Dalton)
NORMAL_MAX_RANGE_X = 1.0 / 0.85
NV_MAX_RANGE_X = 2.0
CLOSE_EXTREME_Q = 0.75  # close_pos beyond this (or below 1-this) = "at the extreme"

# Extension milestones for the distribution table — the 1x/1.5x/2x rows are the
# platform-drawn "targets" the research doc found no evidence for; the point of
# tabulating them is to show how rarely they print.
EXT_MILESTONES = (0.5, 1.0, 1.5, 2.0)

# A gap is "flat" when it is small relative to the prior-14-session average day
# range; days without an adr14 yet (the first two weeks of a run) go unlabelled
# rather than getting a made-up threshold.
GAP_FLAT_ADR_X = 0.15
ADR_LOOKBACK = 14

DEFAULTS = {
    "ib_minutes": 60,
    "orb_windows": (5, 15, 30),
}


# --- config -----------------------------------------------------------------


@dataclass(frozen=True)
class IbConfig:
    """Everything that changes the numbers. Hashes to the snapshot key."""

    symbol: str
    start: date
    end: date
    ib_minutes: int
    orb_windows: tuple[int, ...]

    @classmethod
    def build(cls, symbol: str, start: date, end: date, **over) -> "IbConfig":
        o = {**DEFAULTS, **{k: v for k, v in over.items() if v is not None}}
        return cls(
            symbol=symbol, start=start, end=end,
            ib_minutes=int(o["ib_minutes"]),
            orb_windows=tuple(int(w) for w in o["orb_windows"]),
        )

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol, "start": self.start.isoformat(),
            "end": self.end.isoformat(), "ib_minutes": self.ib_minutes,
            "orb_windows": list(self.orb_windows),
        }

    def run_id(self) -> str:
        blob = json.dumps({"config": self.to_json(), "version": IB_VERSION},
                          sort_keys=True)
        h = hashlib.sha1(blob.encode()).hexdigest()[:12]
        return f"{self.symbol}_{self.start:%Y%m%d}-{self.end:%Y%m%d}_v{IB_VERSION}-{h}"


# --- per-session compute ----------------------------------------------------


def _minutes_after_open(t: time) -> int:
    return (t.hour - RTH_OPEN.hour) * 60 + (t.minute - RTH_OPEN.minute)


def _first_cross(high: np.ndarray, low: np.ndarray, ib_hi: float, ib_lo: float,
                 ) -> tuple[str | None, int | None, str | None, int | None]:
    """First post-IB minute whose range exceeds each IB side.

    Returns (first_side, first_idx, second_side, second_idx) with indices into
    the post-IB arrays; a side never broken is None. When both sides first break
    in the same minute (a huge bar), the side with the larger overshoot that
    minute counts as first — arbitrary, but stated.
    """
    up = np.flatnonzero(high > ib_hi)
    dn = np.flatnonzero(low < ib_lo)
    i_up = int(up[0]) if len(up) else None
    i_dn = int(dn[0]) if len(dn) else None
    if i_up is None and i_dn is None:
        return None, None, None, None
    if i_dn is None or (i_up is not None and i_up < i_dn):
        return "up", i_up, ("down" if i_dn is not None else None), i_dn
    if i_up is None or i_dn < i_up:
        return "down", i_dn, ("up" if i_up is not None else None), i_up
    # same minute: larger overshoot wins
    if high[i_up] - ib_hi >= ib_lo - low[i_dn]:
        return "up", i_up, "down", i_dn
    return "down", i_dn, "up", i_up


def _orb(bars_et: pd.DataFrame, window: int, day_close: float) -> dict | None:
    """The ORB window's candle and its Zarattini-style score.

    ``dir`` is the sign of the window candle (close vs open). The follow read
    enters at the window's close in that direction and exits at the session
    close: ``move_pts`` is the signed favourable move, ``r_mult`` that move
    over the Zarattini stop distance (the candle's opposite extreme) — used as
    a *unit*, not enforced intraday: a day that would have stopped out shows
    its raw close-to-close loss. Stop enforcement is the sim engine's job when
    this graduates to a strategy. A doji window (dir 0) or a zero stop distance
    yields no trade (None fields).
    """
    end = _time_plus(RTH_OPEN, window)
    w = bars_et[bars_et["_et"] < end]
    if w.empty:
        return None
    o = float(w["open"].iloc[0])
    c = float(w["close"].iloc[-1])
    hi = float(w["high"].max())
    lo = float(w["low"].min())
    direction = 1 if c > o else -1 if c < o else 0
    row = {
        "window": window, "high": round(hi, 2), "low": round(lo, 2),
        "range": round(hi - lo, 2), "dir": direction,
        "follow": None, "move_pts": None, "r_mult": None,
    }
    if direction == 0:
        return row
    stop_dist = (c - lo) if direction == 1 else (hi - c)
    move = (day_close - c) * direction
    row["follow"] = bool(move > 0)
    row["move_pts"] = round(move, 2)
    if stop_dist > 0:
        row["r_mult"] = round(move / stop_dist, 2)
    return row


def _time_plus(t: time, minutes: int) -> time:
    return (datetime.combine(date(2000, 1, 1), t) + timedelta(minutes=minutes)).time()


def session_row(bars: pd.DataFrame, cfg: IbConfig, *,
                on_high: float | None = None, on_low: float | None = None,
                prior_close: float | None = None,
                adr14: float | None = None) -> dict | None:
    """One session's full IB/ORB record from its RTH minute bars.

    Pure over the bars (plus the overnight extremes / prior close / adr14 the
    caller carries between sessions), so tests can feed synthetic days.
    """
    if bars.empty:
        return None
    b = bars.assign(_et=bars["ts_utc"].dt.tz_convert(ET_TZ).dt.time)
    ib_end = _time_plus(RTH_OPEN, cfg.ib_minutes)
    ib = b[b["_et"] < ib_end]
    post = b[b["_et"] >= ib_end]
    if ib.empty:
        return None

    open_px = float(ib["open"].iloc[0])
    close_px = float(b["close"].iloc[-1])
    ib_hi = float(ib["high"].max())
    ib_lo = float(ib["low"].min())
    ib_range = ib_hi - ib_lo
    day_hi = float(b["high"].max())
    day_lo = float(b["low"].min())
    day_range = day_hi - day_lo
    if ib_range <= 0 or day_range <= 0:
        return None

    post_hi = post["high"].to_numpy(dtype="float64")
    post_lo = post["low"].to_numpy(dtype="float64")
    post_et = list(post["_et"])
    side1, i1, side2, i2 = _first_cross(post_hi, post_lo, ib_hi, ib_lo)
    broke_up = bool(len(post) and (post_hi > ib_hi).any())
    broke_down = bool(len(post) and (post_lo < ib_lo).any())

    def _brk(side, i):
        if side is None or i is None:
            return None
        return {"side": side, "hhmm": post_et[i].strftime("%H:%M"),
                "min_after_open": _minutes_after_open(post_et[i])}

    first_break = _brk(side1, i1)
    # second break only exists on double-break days (the other side after the first)
    second_break = _brk(side2, i2) if (broke_up and broke_down) else None

    ext_up_x = max(0.0, day_hi - ib_hi) / ib_range
    ext_dn_x = max(0.0, ib_lo - day_lo) / ib_range
    range_x = day_range / ib_range
    close_pos = (close_px - day_lo) / day_range

    day_type = _classify(broke_up, broke_down, range_x, close_pos, side1)

    # single-side-break epilogue: did the break hold into the close?
    close_beyond_break = None
    if first_break and not (broke_up and broke_down):
        close_beyond_break = bool(close_px > ib_hi) if side1 == "up" else bool(close_px < ib_lo)

    gap_pts = None if prior_close is None else round(open_px - prior_close, 2)
    gap_x = (round(gap_pts / adr14, 3)
             if gap_pts is not None and adr14 else None)

    on_range = (round(on_high - on_low, 2)
                if on_high is not None and on_low is not None and on_high > on_low
                else None)
    open_vs_on = ib_vs_on = None
    if on_range is not None:
        open_vs_on = ("above" if open_px > on_high
                      else "below" if open_px < on_low else "inside")
        ib_vs_on = ("inside" if ib_hi <= on_high and ib_lo >= on_low
                    else "broke_high" if ib_hi > on_high and ib_lo >= on_low
                    else "broke_low" if ib_lo < on_low and ib_hi <= on_high
                    else "engulfed")

    return {
        "open": round(open_px, 2), "close": round(close_px, 2),
        "ib_high": round(ib_hi, 2), "ib_low": round(ib_lo, 2),
        "ib_mid": round((ib_hi + ib_lo) / 2, 2), "ib_range": round(ib_range, 2),
        "day_high": round(day_hi, 2), "day_low": round(day_lo, 2),
        "day_range": round(day_range, 2),
        "ib_pct_of_day": round(ib_range / day_range, 3),
        "broke_up": broke_up, "broke_down": broke_down,
        "broke_both": broke_up and broke_down,
        "first_break": first_break, "second_break": second_break,
        "ext_up_x": round(ext_up_x, 3), "ext_dn_x": round(ext_dn_x, 3),
        "max_ext_x": round(max(ext_up_x, ext_dn_x), 3),
        "range_x": round(range_x, 3), "close_pos": round(close_pos, 3),
        "day_type": day_type, "close_beyond_break": close_beyond_break,
        "gap_pts": gap_pts, "gap_x": gap_x,
        "adr14": round(adr14, 2) if adr14 else None,
        "ib_vs_adr": round(ib_range / adr14, 3) if adr14 else None,
        "on_high": round(on_high, 2) if on_high is not None else None,
        "on_low": round(on_low, 2) if on_low is not None else None,
        "on_range": on_range, "open_vs_on": open_vs_on, "ib_vs_on": ib_vs_on,
        "orb": {str(w): _orb(b, w, close_px) for w in cfg.orb_windows},
    }


def chart_overlay(rth_ticks: pd.DataFrame, day: date, bar_ts, times,
                  ib_minutes: int = DEFAULTS["ib_minutes"]) -> dict | None:
    """The Initial Balance as a chart layer: high/low plus the drawn-bar times
    the overlay spans (the bell → the close; extension guides start where the
    IB completes).

    Same window as ``session_row`` — the first ``ib_minutes`` of RTH — so the
    lines on a chart are exactly the levels the study's break/extension stats
    are measured against. Computed straight off the ticks: the max over the
    window is identical on ticks or minute bars, and the sim charts draw tick
    bars. Honest-absence rule like the weekly anchor: a session whose data ends
    inside the window draws nothing rather than a made-up IB.

    ``bar_ts`` is the drawn bars' UTC stamps (datetime-like) and ``times`` their
    display times — display time conventions differ per chart (the sim charts
    shift to the viewer's wall clock, the Lab does not), so the endpoints are
    snapped onto the caller's own axis rather than computed here.
    """
    if rth_ticks.empty or len(times) == 0:
        return None
    open_utc, _ = tickmod.session_bounds_utc(day)
    ib_end = open_utc + timedelta(minutes=ib_minutes)
    ts = rth_ticks["ts_utc"]
    if ts.iloc[-1] < ib_end:
        return None
    window = rth_ticks.loc[ts < ib_end, "price"]
    if window.empty:
        return None

    # Explicitly ns: a frame that round-tripped through parquet can carry us
    # resolution, where astype("int64") would silently disagree with .value.
    bar_ns = pd.DatetimeIndex(bar_ts).as_unit("ns").asi8

    def snap(t: pd.Timestamp) -> int:
        i = int(np.searchsorted(bar_ns, t.value, side="left"))
        return int(times[min(i, len(times) - 1)])

    return {
        "high": round(float(window.max()), 2),
        "low": round(float(window.min()), 2),
        "start": snap(open_utc), "formed": snap(ib_end), "end": int(times[-1]),
    }


def _classify(broke_up: bool, broke_down: bool, range_x: float,
              close_pos: float, first_side: str | None) -> str:
    if broke_up and broke_down:
        at_extreme = close_pos >= CLOSE_EXTREME_Q or close_pos <= 1 - CLOSE_EXTREME_Q
        return "neutral_extreme" if at_extreme else "neutral_center"
    if range_x <= NORMAL_MAX_RANGE_X:
        return "normal"
    directional = ((first_side == "up" and close_pos >= CLOSE_EXTREME_Q)
                   or (first_side == "down" and close_pos <= 1 - CLOSE_EXTREME_Q))
    if range_x > NV_MAX_RANGE_X and directional:
        return "trend"
    return "normal_variation"


# --- aggregates -------------------------------------------------------------

# Every aggregate row is {label, n, ...columns}. Rates are fractions (the UI
# scales), medians are in the row's own unit (points or ×IB), n is the row's
# denominator so a thin cut is visibly thin.


def _rate(rows: list[dict], pred) -> tuple[int, float | None]:
    hits = [r for r in rows if pred(r)]
    return len(hits), round(len(hits) / len(rows), 3) if rows else None


def _med(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(float(np.median(vals)), 3) if vals else None


def _break_rates(rows: list[dict], ib_minutes: int = 60) -> list[dict]:
    n = len(rows)
    out = []
    for label, pred in [
        ("broke either side", lambda r: r["broke_up"] or r["broke_down"]),
        ("first break up", lambda r: r["first_break"] and r["first_break"]["side"] == "up"),
        ("first break down", lambda r: r["first_break"] and r["first_break"]["side"] == "down"),
        ("broke both sides", lambda r: r["broke_both"]),
        ("no break", lambda r: not (r["broke_up"] or r["broke_down"])),
        ("first break within 30m of IB end",
         lambda r: r["first_break"] and r["first_break"]["min_after_open"] <= ib_minutes + 30),
        ("first break by noon",
         lambda r: r["first_break"] and r["first_break"]["min_after_open"] <= 150),
    ]:
        k, rate = _rate(rows, pred)
        out.append({"label": label, "n": k, "pct": rate, "of": n})
    return out


def _ext_distribution(rows: list[dict]) -> list[dict]:
    exts = [r["max_ext_x"] for r in rows]
    out = []
    if exts:
        for q in (0.25, 0.5, 0.75, 0.9):
            out.append({"label": f"p{int(q * 100)} extension (×IB)",
                        "n": len(exts), "value": round(float(np.quantile(exts, q)), 3),
                        "pct": None})
    for m in EXT_MILESTONES:
        k, rate = _rate(rows, lambda r, m=m: r["max_ext_x"] >= m)
        out.append({"label": f"reached ≥{m}× IB", "n": k, "value": None, "pct": rate})
    return out


def _day_types(rows: list[dict]) -> list[dict]:
    order = ["normal", "normal_variation", "trend", "neutral_center", "neutral_extreme"]
    out = []
    for t in order:
        k, rate = _rate(rows, lambda r, t=t: r["day_type"] == t)
        out.append({"label": t, "n": k, "pct": rate})
    return out


def _break_epilogue(rows: list[dict]) -> list[dict]:
    """What a break was worth: held vs failed on single-break days, and who won
    the close on double-break days (the 'second break wins' claim)."""
    single = [r for r in rows if r["first_break"] and not r["broke_both"]]
    double = [r for r in rows if r["broke_both"] and r["second_break"]]
    out = []
    if single:
        k, rate = _rate(single, lambda r: r["close_beyond_break"])
        out.append({"label": "single break: close held beyond IB", "n": k, "pct": rate,
                    "of": len(single)})
        k, rate = _rate(single, lambda r: not r["close_beyond_break"])
        out.append({"label": "single break: close back inside IB (failed)", "n": k,
                    "pct": rate, "of": len(single)})
    if double:
        def _second_wins(r):
            up = r["close"] > r["ib_mid"]
            return (r["second_break"]["side"] == "up") == up
        k, rate = _rate(double, _second_wins)
        out.append({"label": "double break: close on second break's side", "n": k,
                    "pct": rate, "of": len(double)})
    return out


def _cut_stats(label: str, rows: list[dict]) -> dict:
    """The comparable read for any conditioning cut: how directional did the
    days in it turn out."""
    _, both = _rate(rows, lambda r: r["broke_both"])
    _, trend = _rate(rows, lambda r: r["day_type"] == "trend")
    return {
        "label": label, "n": len(rows),
        "trend_rate": trend, "both_rate": both,
        "med_ext_x": _med([r["max_ext_x"] for r in rows]),
        "med_range_x": _med([r["range_x"] for r in rows]),
    }


def _ib_width_terciles(rows: list[dict]) -> list[dict]:
    """The strongest documented conditioner: IB width relative to recent range.
    Days before the ADR warm-up are excluded (no denominator, no bucket)."""
    have = [r for r in rows if r["ib_vs_adr"] is not None]
    if len(have) < 6:
        return []
    xs = [r["ib_vs_adr"] for r in have]
    lo, hi = np.quantile(xs, [1 / 3, 2 / 3])
    return [
        _cut_stats(f"narrow IB (<{lo:.2f}× ADR)", [r for r in have if r["ib_vs_adr"] < lo]),
        _cut_stats("mid IB", [r for r in have if lo <= r["ib_vs_adr"] <= hi]),
        _cut_stats(f"wide IB (>{hi:.2f}× ADR)", [r for r in have if r["ib_vs_adr"] > hi]),
    ]


def _globex_cuts(rows: list[dict]) -> list[dict]:
    """The unpublished cut: how the IB sits in the overnight range, and where
    the open printed in it. Only days with a cached overnight participate."""
    have = [r for r in rows if r["on_range"] is not None]
    if not have:
        return []
    out = [_cut_stats(f"open {w} ON range", [r for r in have if r["open_vs_on"] == w])
           for w in ("inside", "above", "below")]
    labels = {"inside": "IB inside ON range", "broke_high": "IB broke ON high",
              "broke_low": "IB broke ON low", "engulfed": "IB engulfed ON range"}
    out += [_cut_stats(lbl, [r for r in have if r["ib_vs_on"] == key])
            for key, lbl in labels.items()]
    return [r for r in out if r["n"] > 0]


def _orb_follow(rows: list[dict], windows: tuple[int, ...]) -> list[dict]:
    """Zarattini read per window: enter at the window candle's close in its
    direction, stop at its opposite extreme, exit at the session close. The R
    columns are what matter — the paper's edge was +0.13R at a 24% win rate, so
    judge the distribution, not the hit rate."""
    out = []
    for w in windows:
        trades = [r["orb"][str(w)] for r in rows
                  if r["orb"].get(str(w)) and r["orb"][str(w)]["dir"] != 0]
        follows = [t for t in trades if t["follow"]]
        rs = [t["r_mult"] for t in trades if t["r_mult"] is not None]
        out.append({
            "label": f"{w}m window", "n": len(trades),
            "follow_rate": round(len(follows) / len(trades), 3) if trades else None,
            "avg_r": round(float(np.mean(rs)), 2) if rs else None,
            "med_r": round(float(np.median(rs)), 2) if rs else None,
            "med_move_pts": _med([t["move_pts"] for t in trades]),
        })
    return out


def _gap_cuts(rows: list[dict], w: int) -> list[dict]:
    """Gap direction and gap-vs-ORB alignment, scored on the w-minute ORB trade.
    The research doc calls gap direction a documented dud — this is the local
    check. Days without an adr14 (no gap_x) sit out."""
    have = [r for r in rows if r["gap_x"] is not None and r["orb"].get(str(w))
            and r["orb"][str(w)]["dir"] != 0]

    def _row(label, sub):
        follows = [r for r in sub if r["orb"][str(w)]["follow"]]
        rs = [r["orb"][str(w)]["r_mult"] for r in sub
              if r["orb"][str(w)]["r_mult"] is not None]
        return {"label": label, "n": len(sub),
                "follow_rate": round(len(follows) / len(sub), 3) if sub else None,
                "avg_r": round(float(np.mean(rs)), 2) if rs else None,
                "med_r": round(float(np.median(rs)), 2) if rs else None,
                "med_move_pts": _med([r["orb"][str(w)]["move_pts"] for r in sub])}

    gap_up = [r for r in have if r["gap_x"] > GAP_FLAT_ADR_X]
    gap_dn = [r for r in have if r["gap_x"] < -GAP_FLAT_ADR_X]
    flat = [r for r in have if abs(r["gap_x"]) <= GAP_FLAT_ADR_X]
    aligned = [r for r in gap_up + gap_dn
               if np.sign(r["gap_x"]) == r["orb"][str(w)]["dir"]]
    opposed = [r for r in gap_up + gap_dn
               if np.sign(r["gap_x"]) != r["orb"][str(w)]["dir"]]
    rows_out = [_row("gap up", gap_up), _row("gap down", gap_dn),
                _row("flat open", flat),
                _row(f"{w}m candle aligned with gap", aligned),
                _row(f"{w}m candle against gap", opposed)]
    return [r for r in rows_out if r["n"] > 0]


def _weekday_cuts(rows: list[dict]) -> list[dict]:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    out = []
    for wd in range(5):
        sub = [r for r in rows if date.fromisoformat(r["day"]).weekday() == wd]
        if sub:
            out.append(_cut_stats(names[wd], sub))
    return out


# --- study ------------------------------------------------------------------


def study(cfg: IbConfig) -> dict:
    """Run the IB/ORB study over the config's date range (cache-only).

    Sessions are processed in date order because two context fields chain
    through the sequence: ``prior_close`` (yesterday's RTH close, for the gap)
    and ``adr14`` (mean of the prior 14 sessions' day ranges — deliberately
    excluding today, so it is knowable at the open).
    """
    requested = tickmod.session_dates(cfg.start, cfg.end)
    days: list[dict] = []
    skipped: list[str] = []
    prior_close: float | None = None
    ranges: list[float] = []

    for day in requested:
        contract = tickmod.contract_for_cached(cfg.symbol, day)
        rth = tickmod.cached_rth(contract, day) if contract else None
        if rth is None or rth.empty:
            skipped.append(day.isoformat())
            continue
        bars = minute_bars(rth)
        on = tickmod.cached_overnight(contract, day)
        on_hi = float(on["price"].max()) if on is not None and not on.empty else None
        on_lo = float(on["price"].min()) if on is not None and not on.empty else None
        adr = (round(float(np.mean(ranges[-ADR_LOOKBACK:])), 2)
               if len(ranges) >= ADR_LOOKBACK else None)
        row = session_row(bars, cfg, on_high=on_hi, on_low=on_lo,
                          prior_close=prior_close, adr14=adr)
        if row is None:
            skipped.append(day.isoformat())
            continue
        row["day"] = day.isoformat()
        days.append(row)
        prior_close = row["close"]
        ranges.append(row["day_range"])

    primary_w = cfg.orb_windows[0] if cfg.orb_windows else 5
    return {
        "ib_version": IB_VERSION,
        **cfg.to_json(),
        "coverage": {
            "requested_days": len(requested), "ran_days": len(days), "skipped": skipped,
        },
        "days": days,
        "aggregates": {
            "break_rates": _break_rates(days, cfg.ib_minutes),
            "ext_distribution": _ext_distribution(days),
            "day_types": _day_types(days),
            "break_epilogue": _break_epilogue(days),
            "ib_width_terciles": _ib_width_terciles(days),
            "globex_cuts": _globex_cuts(days),
            "orb_follow": _orb_follow(days, cfg.orb_windows),
            "gap_cuts": _gap_cuts(days, primary_w),
            "weekday": _weekday_cuts(days),
        },
    }


# --- snapshot store ---------------------------------------------------------


def _path(cfg: IbConfig):
    return IB_DIR / f"{cfg.run_id()}.json"


def read(cfg: IbConfig) -> dict | None:
    p = _path(cfg)
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return d if d.get("ib_version") == IB_VERSION else None


def write(cfg: IbConfig, result: dict) -> None:
    IB_DIR.mkdir(parents=True, exist_ok=True)
    _path(cfg).write_text(json.dumps(result, indent=2))


def get(cfg: IbConfig, refresh: bool = False) -> dict:
    if not refresh and (cached := read(cfg)) is not None:
        return cached
    result = study(cfg)
    write(cfg, result)
    return result


def list_runs() -> list[dict]:
    """Summaries of every snapshot on disk, newest first."""
    if not IB_DIR.exists():
        return []
    runs = []
    for p in sorted(IB_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime,
                    reverse=True):
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("ib_version") != IB_VERSION:
            continue
        runs.append({
            "run_id": p.stem,
            "config": {k: d[k] for k in ("symbol", "start", "end", "ib_minutes",
                                         "orb_windows")},
            "coverage": d["coverage"],
            "n_days": len(d["days"]),
            "saved_at": p.stat().st_mtime,
        })
    return runs
