"""Weekly-VWAP interaction study — how sessions live around the weekly anchor.

The research bench for the weekly anchor (``weekly.py``) before anything becomes
a gate or a strategy — Lab-first, like ``interactions`` and ``ib``: no trades, a
row exists for any cached session with an honest weekly line, every read is
GET-safe (tick cache only).

What one session produces:

  - **where the bell printed** against the developing weekly VWAP — distance in
    points and in weekly sigmas, and which side — the "distance-to-weekly as a
    regime feature" read;
  - **the day's drift** from there: did the session move with its side of the
    weekly anchor (the day-with hypothesis that holds for every daily anchor)
    or revert toward the weekly mid;
  - **band interactions**: for each weekly level (mid, ±1σ, ±2σ) the session
    started clear of — whether RTH touched it, when, and what the touch was
    worth over the outcome window: how far price then traded back toward the
    weekly mid vs how far it kept going, and whether the mid itself printed.
    That is the dev-band fade/bounce question, asked the same way the
    Interactions study asks it of session levels.

Sessions the weekly line cannot be honestly drawn for are skipped and counted,
not approximated: the week's first session carries a zero seed (the weekly IS
the Globex anchor that day — tagged, so aggregates can cut it out), but a week
with a missing prior session, or a session with no cached overnight, has no
weekly truth to study.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ..config import CACHE_DIR
from . import ticks as tickmod
from . import vwap as vwapmod
from . import weekly as weeklymod
from .regime import minute_bars

WEEKLY_VWAP_VERSION = 2  # v2: seed includes the 16:00-17:00 'post' segment
WEEKLY_VWAP_DIR = CACHE_DIR / "weekly_vwap"

# The levels a session can interact with, as (name, band-frame column, sign of
# "toward the mid" for a touch from inside). Mid has no fade direction — its
# touch is tabulated (a magnet read) but not scored toward/beyond.
BAND_LEVELS = ("upper2", "upper1", "mid", "lower1", "lower2")

DEFAULTS = {
    "outcome_window_min": 60,
}


# --- config -----------------------------------------------------------------


@dataclass(frozen=True)
class WeeklyVwapConfig:
    """Everything that changes the numbers. Hashes to the snapshot key."""

    symbol: str
    start: date
    end: date
    outcome_window_min: int

    @classmethod
    def build(cls, symbol: str, start: date, end: date, **over) -> "WeeklyVwapConfig":
        o = {**DEFAULTS, **{k: v for k, v in over.items() if v is not None}}
        return cls(symbol=symbol, start=start, end=end,
                   outcome_window_min=int(o["outcome_window_min"]))

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol, "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "outcome_window_min": self.outcome_window_min,
        }

    def run_id(self) -> str:
        blob = json.dumps({"config": self.to_json(), "version": WEEKLY_VWAP_VERSION},
                          sort_keys=True)
        h = hashlib.sha1(blob.encode()).hexdigest()[:12]
        return (f"{self.symbol}_{self.start:%Y%m%d}-{self.end:%Y%m%d}"
                f"_v{WEEKLY_VWAP_VERSION}-{h}")


# --- per-session compute ----------------------------------------------------


def _band_cols(w: pd.DataFrame, pos: np.ndarray) -> dict[str, np.ndarray]:
    """Sample the band frame at tick positions — one value per minute bar."""
    out = {}
    for col in ("mid", "std", "upper1", "upper2", "lower1", "lower2"):
        out[col] = w[col].to_numpy()[pos]
    return out


def _touch(name: str, level: np.ndarray, bars: pd.DataFrame, mid: np.ndarray,
           open_px: float, window: int) -> dict | None:
    """First RTH touch of a (developing) weekly level the session opened clear
    of, and what the touch was worth over the next *window* minutes.

    ``toward_pts`` is the best excursion from the touched level back toward the
    weekly mid, ``beyond_pts`` the best excursion through it — the pair the
    fade-vs-break read needs. ``hit_mid`` is whether the weekly mid itself
    printed inside the window. A session that opens already past the level has
    no first touch to score (the touch happened some other day) and returns
    None, exactly like the Interactions study's "must approach the level" rule.
    """
    started_below = open_px < level[0]
    # Only score approaches from the mid's side: an upper band first "touched"
    # by a session that opened beyond it is not a band test, it is a retreat.
    if name.startswith("upper") and not started_below:
        return None
    if name.startswith("lower") and started_below:
        return None
    hi = bars["high"].to_numpy()
    lo = bars["low"].to_numpy()
    hit = np.flatnonzero((lo <= level) & (level <= hi))
    if len(hit) == 0:
        return {"name": name, "touched": False}
    i = int(hit[0])
    j = min(i + window, len(bars) - 1)
    lvl = float(level[i])
    if name == "mid":
        return {"name": name, "touched": True, "min_after_open": i,
                "level": round(lvl, 2)}
    win_hi = float(hi[i:j + 1].max())
    win_lo = float(lo[i:j + 1].min())
    if name.startswith("upper"):
        toward = lvl - win_lo
        beyond = win_hi - lvl
        mid_hit = bool((lo[i:j + 1] <= mid[i:j + 1]).any())
    else:
        toward = win_hi - lvl
        beyond = lvl - win_lo
        mid_hit = bool((hi[i:j + 1] >= mid[i:j + 1]).any())
    return {
        "name": name, "touched": True, "min_after_open": i,
        "level": round(lvl, 2),
        "toward_pts": round(toward, 2), "beyond_pts": round(beyond, 2),
        "hit_mid": mid_hit,
    }


def session_row(bars: pd.DataFrame, w_at_min: dict[str, np.ndarray],
                first_session: bool, window: int) -> dict | None:
    """One session's weekly-interaction record from its RTH minute bars and the
    weekly band values sampled at each bar's close. Pure, for synthetic tests."""
    if bars.empty or len(bars) < 2:
        return None
    open_px = float(bars["open"].iloc[0])
    close_px = float(bars["close"].iloc[-1])
    mid0, std0 = float(w_at_min["mid"][0]), float(w_at_min["std"][0])
    mid_c, std_c = float(w_at_min["mid"][-1]), float(w_at_min["std"][-1])
    if not (np.isfinite(mid0) and np.isfinite(std0)):
        return None

    dist0 = open_px - mid0
    drift = close_px - open_px
    touches = []
    for name in BAND_LEVELS:
        t = _touch(name, w_at_min[name] if name != "mid" else w_at_min["mid"],
                   bars, w_at_min["mid"], open_px, window)
        if t is not None:
            touches.append(t)

    return {
        "first_session": first_session,
        "open": round(open_px, 2), "close": round(close_px, 2),
        "wk_mid_open": round(mid0, 2), "wk_std_open": round(std0, 2),
        "wk_mid_close": round(mid_c, 2),
        "open_dist_pts": round(dist0, 2),
        "open_dist_sigma": round(dist0 / std0, 3) if std0 > 0 else None,
        "close_dist_sigma": (round((close_px - mid_c) / std_c, 3)
                             if std_c > 0 else None),
        "side": "above" if dist0 > 0 else "below",
        "drift_pts": round(drift, 2),
        # with the side = drifted further from the weekly mid; against = reverted
        "drift_with_side": bool(drift * dist0 > 0) if dist0 != 0 else None,
        "touches": touches,
    }


# --- aggregates -------------------------------------------------------------

# Every aggregate row is {label, n, ...columns}. Rates are fractions (the UI
# scales), medians in points or sigmas, n is the denominator so a thin cut is
# visibly thin.


def _med(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(float(np.median(vals)), 3) if vals else None


def _drift_row(label: str, rows: list[dict]) -> dict:
    withs = [r for r in rows if r["drift_with_side"] is not None]
    n_with = sum(1 for r in withs if r["drift_with_side"])
    return {
        "label": label, "n": len(rows),
        "med_drift_pts": _med([r["drift_pts"] for r in rows]),
        "with_side_rate": round(n_with / len(withs), 3) if withs else None,
        "med_close_dist_sigma": _med([r["close_dist_sigma"] for r in rows]),
    }


def _open_position_cuts(rows: list[dict]) -> list[dict]:
    """Distance-to-weekly at the bell as a regime feature: does where the day
    opens in the weekly envelope say anything about how it closes?"""
    buckets = [("< −2σ", -np.inf, -2), ("−2σ…−1σ", -2, -1), ("−1σ…0", -1, 0),
               ("0…+1σ", 0, 1), ("+1σ…+2σ", 1, 2), ("> +2σ", 2, np.inf)]
    have = [r for r in rows if r["open_dist_sigma"] is not None]
    out = []
    for label, lo, hi in buckets:
        sub = [r for r in have if lo <= r["open_dist_sigma"] < hi]
        if sub:
            out.append(_drift_row(label, sub))
    return out


def _side_cuts(rows: list[dict]) -> list[dict]:
    return [_drift_row(f"open {s} weekly VWAP",
                       [r for r in rows if r["side"] == s])
            for s in ("above", "below")]


def _touch_rates(rows: list[dict]) -> list[dict]:
    out = []
    for name in BAND_LEVELS:
        seen = [t for r in rows for t in r["touches"] if t["name"] == name]
        touched = [t for t in seen if t["touched"]]
        if not seen:
            continue
        out.append({
            "label": name, "n": len(touched), "of": len(seen),
            "touch_rate": round(len(touched) / len(seen), 3),
            "med_min_after_open": _med([t.get("min_after_open") for t in touched]),
        })
    return out


def _band_fades(rows: list[dict]) -> list[dict]:
    """What a first band touch was worth: excursion back toward the weekly mid
    vs through the band, and how often the mid itself printed in the window."""
    out = []
    for name in ("upper2", "upper1", "lower1", "lower2"):
        ts = [t for r in rows for t in r["touches"]
              if t["name"] == name and t["touched"]]
        if not ts:
            continue
        n_mid = sum(1 for t in ts if t["hit_mid"])
        toward = [t["toward_pts"] for t in ts]
        beyond = [t["beyond_pts"] for t in ts]
        out.append({
            "label": name, "n": len(ts),
            "hit_mid_rate": round(n_mid / len(ts), 3),
            "med_toward_pts": _med(toward),
            "med_beyond_pts": _med(beyond),
            "med_edge_pts": _med([a - b for a, b in zip(toward, beyond)]),
        })
    return out


def _weekday_cuts(rows: list[dict]) -> list[dict]:
    """The seed grows through the week — is the open-side drift read stronger
    once the weekly mid is more than one session old?"""
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    out = []
    for wd in range(5):
        sub = [r for r in rows if date.fromisoformat(r["day"]).weekday() == wd]
        if sub:
            out.append(_drift_row(names[wd], sub))
    return out


# --- study ------------------------------------------------------------------


def study(cfg: WeeklyVwapConfig) -> dict:
    """Run the weekly-VWAP study over the config's date range (cache-only)."""
    requested = tickmod.session_dates(cfg.start, cfg.end)
    days: list[dict] = []
    skipped: list[dict] = []

    for day in requested:
        contract = tickmod.contract_for_cached(cfg.symbol, day)
        rth = tickmod.cached_rth(contract, day) if contract else None
        if rth is None or rth.empty:
            skipped.append({"day": day.isoformat(), "why": "no rth ticks"})
            continue
        on = tickmod.cached_overnight(contract, day)
        if on is None or on.empty:
            # Without the night the weekly line would be missing this session's
            # own overnight volume — a hole, so the day sits out (not fudged).
            skipped.append({"day": day.isoformat(), "why": "no overnight ticks"})
            continue
        seed = weeklymod.weekly_seed(cfg.symbol, day)
        if seed is None:
            skipped.append({"day": day.isoformat(), "why": "week has a hole"})
            continue

        full = pd.concat([on, rth], ignore_index=True)
        w = vwapmod.vwap_bands(full, seed=seed)
        bars = minute_bars(rth)
        if bars.empty:
            skipped.append({"day": day.isoformat(), "why": "no rth bars"})
            continue
        pos = bars["end_idx"].to_numpy() + len(on)
        row = session_row(bars, _band_cols(w, pos),
                          first_session=seed == (0.0, 0.0, 0.0),
                          window=cfg.outcome_window_min)
        if row is None:
            skipped.append({"day": day.isoformat(), "why": "degenerate session"})
            continue
        row["day"] = day.isoformat()
        days.append(row)

    # The week's first session has no weekly history — its "weekly" VWAP is the
    # session's own Globex anchor. It stays in the day list (the chart draws it)
    # but the aggregates read only days where the anchor carries prior sessions.
    seasoned = [r for r in days if not r["first_session"]]
    return {
        "weekly_vwap_version": WEEKLY_VWAP_VERSION,
        **cfg.to_json(),
        "coverage": {
            "requested_days": len(requested), "ran_days": len(days),
            "seasoned_days": len(seasoned), "skipped": skipped,
        },
        "days": days,
        "aggregates": {
            "open_position": _open_position_cuts(seasoned),
            "side": _side_cuts(seasoned),
            "touch_rates": _touch_rates(seasoned),
            "band_fades": _band_fades(seasoned),
            "weekday": _weekday_cuts(seasoned),
        },
    }


# --- snapshot store ---------------------------------------------------------


def _path(cfg: WeeklyVwapConfig):
    return WEEKLY_VWAP_DIR / f"{cfg.run_id()}.json"


def read(cfg: WeeklyVwapConfig) -> dict | None:
    p = _path(cfg)
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return d if d.get("weekly_vwap_version") == WEEKLY_VWAP_VERSION else None


def write(cfg: WeeklyVwapConfig, result: dict) -> None:
    WEEKLY_VWAP_DIR.mkdir(parents=True, exist_ok=True)
    _path(cfg).write_text(json.dumps(result, indent=2))


def get(cfg: WeeklyVwapConfig, refresh: bool = False) -> dict:
    if not refresh and (cached := read(cfg)) is not None:
        return cached
    result = study(cfg)
    write(cfg, result)
    return result


def list_runs() -> list[dict]:
    """Summaries of every snapshot on disk, newest first."""
    if not WEEKLY_VWAP_DIR.exists():
        return []
    runs = []
    for p in sorted(WEEKLY_VWAP_DIR.glob("*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("weekly_vwap_version") != WEEKLY_VWAP_VERSION:
            continue
        runs.append({
            "run_id": p.stem,
            "config": {k: d[k] for k in ("symbol", "start", "end",
                                         "outcome_window_min")},
            "coverage": d["coverage"],
            "n_days": len(d["days"]),
            "saved_at": p.stat().st_mtime,
        })
    return runs
