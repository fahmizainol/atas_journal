"""Daily-ATR volatility regime — the "vol clock" label for a session.

The same causal measure the vol-clock study cut its baselines by (see
`docs/research/vol-clock.md` and `data/research/atr-band/build_features.py`),
promoted out of the research scripts so the app can label a session with it:

  - ``daily_atr14``   Wilder ATR(14) of the globex-day (on+rth+post) true range,
                     through the *prior* session. Shifted, so the label a day
                     carries is what was knowable walking into it — a day never
                     sees its own range.
  - ``datr_pctl60``   where that ATR sits within the trailing 60 sessions.
  - ``label``         tercile of the percentile: quiet / mid / hot.

``session_atr`` serves the first of those one session at a time, for rules that
size themselves off the day's volatility rather than just labelling it.

Terciling on the percentile rather than on the raw ATR is what makes a label
computable per session instead of only within a cohort: the percentile is
already self-normalising against recent history, so cutting it at 1/3 and 2/3
reproduces the study's terciles without needing the whole date range in hand.

Read-only over the tick cache, like every other GET-backed artifact here: a
session whose ticks were never bought is simply not labelled, never fetched.
The per-session globex bar is cached as a tiny JSON so a range read costs one
parquet scan per *new* day rather than one per day, every time.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd

from ..atr import atr_series
from ..config import CACHE_DIR
from . import ticks as tickmod

VOL_DIR = CACHE_DIR / "vol_regime"
BAR_VERSION = 1
# The per-session ATR read (``session_atr``) caches its own answer, not just the
# bars it is built from: the engine asks once per session and the alternative is
# re-walking the whole warm-up window every time.
ATR_VERSION = 1

ATR_PERIOD = 14
PCTL_WINDOW = 60
PCTL_MIN = 20
# Sessions of history pulled in ahead of a requested range so the first day of
# the range is labelled from a converged ATR and a full percentile window, not
# from whatever happens to start at `start`. 60 for the percentile window + the
# ATR's own warm-up, rounded up.
WARMUP_SESSIONS = 90

LABELS = ["quiet", "mid", "hot"]


def _bar_path(symbol: str, day: date):
    return VOL_DIR / f"{symbol}_{day.isoformat()}_bar_v{BAR_VERSION}.json"


def _compute_bar(symbol: str, day: date) -> dict | None:
    """Globex-day OHLC from whatever segments are cached for this session.

    Same frame the study built its daily bars over: on + rth + post, in clock
    order. A session with no RTH on disk isn't a session here — the night alone
    would give a true range that means something else.
    """
    rth = tickmod.cached_rth(symbol, day)
    if rth is None or rth.empty:
        return None
    segs = [s for s in (tickmod.cached_overnight(symbol, day), rth,
                        tickmod.cached_post(symbol, day))
            if s is not None and not s.empty]
    px = pd.concat([s["price"] for s in segs], ignore_index=True).astype(float)
    return {
        "version": BAR_VERSION,
        "open": float(px.iloc[0]),
        "high": float(px.max()),
        "low": float(px.min()),
        "close": float(px.iloc[-1]),
    }


def daily_bar(symbol: str, day: date) -> dict | None:
    """Cached globex-day OHLC for one session. Never fetches.

    ``symbol`` may be a rolling root ("NQ"); it resolves to the contract that
    actually traded the day, offline, exactly as the regime artifact does.
    """
    contract = tickmod.contract_for_cached(symbol, day)
    if contract is None:
        return None
    p = _bar_path(contract, day)
    if p.exists():
        try:
            d = json.loads(p.read_text())
            if d.get("version") == BAR_VERSION:
                return d
        except (json.JSONDecodeError, OSError):
            pass  # a truncated write is a cache miss, not a failure
    d = _compute_bar(contract, day)
    if d is None:
        return None
    VOL_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))
    return d


def _series(symbol: str, days: list[date]) -> pd.DataFrame:
    """The ATR/percentile/label frame over a contiguous run of sessions."""
    rows = []
    for d in days:
        bar = daily_bar(symbol, d)
        if bar is None:
            continue
        rows.append({"session": d.isoformat(), "high": bar["high"],
                     "low": bar["low"], "close": bar["close"]})
    if not rows:
        return pd.DataFrame(columns=["session", "daily_atr14", "datr_pctl60",
                                     "label", "tr_pts"])
    db = pd.DataFrame(rows)
    atr = atr_series(db, period=ATR_PERIOD)
    # The value a trader knows entering session d is the ATR through d-1.
    db["daily_atr14"] = atr.shift(1)
    prev_close = db["close"].shift(1)
    db["tr_pts"] = pd.concat([db["high"] - db["low"],
                              (db["high"] - prev_close).abs(),
                              (db["low"] - prev_close).abs()], axis=1).max(axis=1)
    db["datr_pctl60"] = (
        db["daily_atr14"].rolling(PCTL_WINDOW, min_periods=PCTL_MIN)
        .apply(lambda w: (w.iloc[:-1] <= w.iloc[-1]).mean(), raw=False)
    )
    db["label"] = pd.cut(db["datr_pctl60"], [-0.01, 1 / 3, 2 / 3, 1.01],
                         labels=LABELS)
    return db


def session_atr(symbol: str, day: date) -> float | None:
    """Daily ATR(14) in POINTS that a trader walks into ``day`` knowing.

    The same number ``range_labels`` reports as ``daily_atr14``, addressable one
    session at a time so a rule inside the engine can read it. Built over the
    globex-day bars STRICTLY BEFORE ``day`` and taken unshifted, which is the
    same value ``_series`` lands on that row by shifting — but this way the
    requested session's own bar is never touched, so the read is causal by
    construction rather than by remembering to shift, and it is safe to call
    for a day still in progress (whose cached bar would be partial).

    None when the history isn't there: a warm-up window with too few cached
    sessions to converge Wilder's smoothing, or a symbol the roll map can't
    resolve. Callers decide what to do without it — the engine falls back to its
    fixed distance rather than silently dropping the rule.
    """
    contract = tickmod.contract_for_cached(symbol, day)
    if contract is None:
        return None
    p = _atr_path(contract, day)
    if p.exists():
        try:
            d = json.loads(p.read_text())
            if d.get("version") == ATR_VERSION:
                return d["atr"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # a truncated write is a cache miss, not a failure

    warm = day - timedelta(days=WARMUP_SESSIONS * 7 // 5 + 10)
    rows = []
    for d in tickmod.session_dates(warm, day - timedelta(days=1)):
        # ``symbol``, not ``contract``: the warm-up window reaches back across
        # rolls, and pinning it to the requested day's front month would silently
        # drop every session that traded under the previous one — which is most of
        # the window on the day after a roll.
        bar = _memo_bar(symbol, d)
        if bar is not None:
            rows.append({"high": bar["high"], "low": bar["low"], "close": bar["close"]})
    atr = None
    if len(rows) > ATR_PERIOD:
        v = atr_series(pd.DataFrame(rows), period=ATR_PERIOD).iloc[-1]
        atr = None if pd.isna(v) else round(float(v), 4)

    VOL_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": ATR_VERSION, "atr": atr}))
    return atr


def _atr_path(symbol: str, day: date):
    return VOL_DIR / f"{symbol}_{day.isoformat()}_atr_v{ATR_VERSION}.json"


# Per-process memo over ``daily_bar``. A session's ATR reads ~90 prior days and a
# run reads it once per session, so without this a 600-day run opens 54,000 JSON
# files to answer 600 questions — and the windows overlap almost entirely.
# Deliberately NOT applied to a day that could still be trading: ``daily_bar``
# builds its bar from whatever ticks are cached, so a partial day's OHLC is a
# moving number, and freezing one in a long-lived process (the API, the live
# shadow) would be a different kind of wrong than re-reading it.
_BAR_MEMO: dict[tuple[str, date], dict | None] = {}


def _memo_bar(symbol: str, day: date) -> dict | None:
    if day >= date.today():
        return daily_bar(symbol, day)
    key = (symbol, day)
    if key not in _BAR_MEMO:
        _BAR_MEMO[key] = daily_bar(symbol, day)
    return _BAR_MEMO[key]


def range_labels(symbol: str, start: date, end: date) -> dict:
    """Vol-regime label per cached session in [start, end].

    The frame is built from WARMUP_SESSIONS days *before* `start` so the first
    labelled day is as well-anchored as the last; the warm-up rows are then
    dropped. ``skipped`` names the in-range days with no cached ticks, so the
    caller can render a hole rather than an unlabelled day.
    """
    warm = start - timedelta(days=WARMUP_SESSIONS * 7 // 5 + 10)
    days = tickmod.session_dates(warm, end)
    db = _series(symbol, days)
    covered = set(db["session"]) if len(db) else set()

    out = []
    for _, r in db.iterrows():
        if r["session"] < start.isoformat():
            continue
        atr = r["daily_atr14"]
        pctl = r["datr_pctl60"]
        out.append({
            "date": r["session"],
            "atr": None if pd.isna(atr) else round(float(atr), 1),
            "pctl": None if pd.isna(pctl) else round(float(pctl), 3),
            "label": None if pd.isna(r["label"]) else str(r["label"]),
            "tr_pts": None if pd.isna(r["tr_pts"]) else round(float(r["tr_pts"]), 1),
        })
    skipped = [d.isoformat() for d in tickmod.session_dates(start, end)
               if d.isoformat() not in covered]
    return {
        "days": out,
        "skipped": skipped,
        "period": ATR_PERIOD,
        "pctl_window": PCTL_WINDOW,
    }
