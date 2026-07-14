"""Does the kind of day explain the day's P&L?

Joins a run's per-session net against the regime KPIs of the same session (see
``journal.sim.regime``) and scores every KPI at every checkpoint at once, so the
answer to "which of these twenty do I even look at" is a table rather than an
afternoon of squinting at scatters.

The checkpoint axis is the point of the whole thing. A KPI read at ``eod`` that
correlates with the day's P&L proves nothing tradeable — both are computed from
the same session, so it describes the day rather than predicting it. A KPI read
at 09:45 that still separates the winners is a signal you could have acted on.

The ``luck`` column is the other point. With ~20 KPIs on the board the best of
them is worth nothing on its own: something always wins. So every score is
measured against the same score computed on *shuffled* P&L, which is the only
honest way to know whether the best of twenty means anything.

This is a straight port of the browser's frontend/src/lib/regimeStats.ts, seeded
RNG included, so the numbers here are bit-for-bit the ones the panel used to draw
— the permutation p-value does not deserve to disagree with itself depending on
who computed it. tests/test_regime_pnl.py pins that against golden values taken
from the TypeScript.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from . import regime as regmod
from . import ticks as tickmod

# Bump when a definition here changes meaning. A snapshot written under an older
# number is recomputed rather than served — the same guard REGIME_VERSION applies
# to the artifacts this reads.
STATS_VERSION = 1

PERMUTATIONS = 500
SEED = 0x5EED

# The KPIs worth ranking against P&L, and how to print them. This list is the
# source of truth: the browser's picker renders whatever the payload carries,
# rather than keeping its own copy that can drift out of step with the scores.
KPIS: list[dict] = [
    {"key": "abr", "label": "Above both VWAPs (ABR)", "pct": True},
    {"key": "bbr", "label": "Below both VWAPs (BBR)", "pct": True},
    {"key": "quadrant_transitions_rate", "label": "Quadrant transitions / hr"},
    {"key": "ny_touch_hold_ratio", "label": "NY +1σ touch → hold", "pct": True},
    {"key": "gx_touch_hold_ratio", "label": "Globex +1σ touch → hold", "pct": True},
    {"key": "ny_upper_channel_occupancy", "label": "NY upper-channel occupancy", "pct": True},
    {"key": "gx_upper_channel_occupancy", "label": "Globex upper-channel occupancy", "pct": True},
    {"key": "ny_band_cross_rate", "label": "NY +1σ crossings / hr"},
    {"key": "ny_vwap_cross_rate", "label": "NY VWAP crossings / hr"},
    {"key": "longest_hold_min", "label": "Longest hold above both (min)"},
    {"key": "norm_spread", "label": "VWAP spread (σ)"},
    {"key": "spread_slope", "label": "VWAP spread slope (30m)"},
    {"key": "ny_vwap_slope_ppm", "label": "NY VWAP slope (pts/min, 30m)"},
    {"key": "ny_vwap_slope_deg", "label": "NY VWAP slope (°, ATR-norm)"},
    {"key": "gx_vwap_slope_ppm", "label": "Globex VWAP slope (pts/min, 30m)"},
    {"key": "gx_vwap_slope_deg", "label": "Globex VWAP slope (°, ATR-norm)"},
    {"key": "on_abr", "label": "Overnight above Globex VWAP", "pct": True},
    {"key": "on_vwap_slope_ppm", "label": "Overnight VWAP slope (pts/min, 30m)"},
    {"key": "on_vwap_slope_deg", "label": "Overnight VWAP slope (°, ATR-norm)"},
    {"key": "on_range_pts", "label": "Overnight range (pts)"},
    {"key": "open_z", "label": "Open in Globex σ-terms"},
]

CLASS_LABEL = {
    "trend_up": "Trend up",
    "trend_down": "Trend down",
    "balance": "Balance",
    "mixed": "Mixed",
    "unknown": "Unknown",
}

# Below this many days a tercile is two days wide and there is nothing to rank.
MIN_DAYS = 6


# --- the statistics ---------------------------------------------------------

def ranks(v: np.ndarray) -> np.ndarray:
    """Mean ranks, ties shared — the same tie handling as the TS."""
    n = len(v)
    order = np.argsort(v, kind="stable")
    out = np.empty(n, dtype="float64")
    i = 0
    while i < n:
        j = i
        while j + 1 < n and v[order[j + 1]] == v[order[i]]:
            j += 1
        out[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return out


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    da, db = a - a.mean(), b - b.mean()
    na, nb = math.sqrt(float(da @ da)), math.sqrt(float(db @ db))
    return 0.0 if na == 0 or nb == 0 else float((da @ db) / (na * nb))


def rank_corr(xs, ys) -> float:
    """Spearman ρ. Ranks, not raw values: the relationship is monotone at best,
    and one +3k session would otherwise drag a Pearson r around by itself."""
    return _corr(ranks(np.asarray(xs, dtype="float64")),
                 ranks(np.asarray(ys, dtype="float64")))


def _mulberry32(seed: int):
    """The browser's PRNG, exactly — a shared seed is what stops a p-value from
    flickering between the panel and the file it was supposedly computed from.

    JS bit ops are int32 and `>>>` is a logical shift on the uint32 view; keeping
    the state masked to 32 bits reproduces both, since every operation here is
    well-defined modulo 2**32.
    """
    a = seed & 0xFFFFFFFF

    def rnd() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((a ^ (a >> 15)) * (1 | a)) & 0xFFFFFFFF
        t = (t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) & 0xFFFFFFFF ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return rnd


def _luck(xr: np.ndarray, yr: np.ndarray, rho: float) -> float:
    """How often a KPI this good shows up when the P&L is shuffled — i.e. when the
    KPI is known to be meaningless. The question is never "is ρ big", it's "is ρ
    bigger than what noise hands me for free".

    Not a real p-value: no correction (the caller applies the Bonferroni bar), no
    independence assumption.

    Shuffles the *ranks* rather than the raw nets. Ranking is a pure function of
    the values, so ranks(shuffle(ys)) == shuffle(ranks(ys)) under the same
    permutation — identical numbers, and it lets a permutation collapse to one dot
    product, because a permuted vector keeps its mean and norm.
    """
    n = len(yr)
    rnd = _mulberry32(SEED)
    target = abs(rho)
    y = yr.copy()

    xc = xr - xr.mean()
    yc = y - y.mean()
    denom = math.sqrt(float(xc @ xc)) * math.sqrt(float(yc @ yc))
    if denom == 0:
        return 1.0  # a constant KPI correlates with nothing; noise "beats" it always
    ymean = float(y.mean())
    xsum = float(xc.sum())  # 0 up to float error, but kept for exactness

    beat = 0
    for _ in range(PERMUTATIONS):
        # Fisher-Yates, same direction and same draw order as the TS, over the same
        # cumulatively-shuffled array — the sequence has to match draw for draw.
        for i in range(n - 1, 0, -1):
            j = int(rnd() * (i + 1))
            y[i], y[j] = y[j], y[i]
        r = (float(xc @ y) - ymean * xsum) / denom
        if abs(r) >= target:
            beat += 1
    return beat / PERMUTATIONS


def _win_rate(days: list[dict]) -> float:
    t = sum(d["trades"] for d in days)
    return (sum(d["wins"] for d in days) / t * 100) if t else 0.0


def _band(name: str, days: list[dict]) -> dict:
    """One third of the days, summarised.

    Deliberately no member list: with 21 KPIs at 5 checkpoints that would be 315
    copies of the same ~100 dates, which is most of the file — and the membership
    is already recoverable from `days` plus this band's [lo, hi]. The point of
    writing this artifact is that something other than a browser can read it, and
    a payload nobody will read defeats that.
    """
    net = sum(d["net"] for d in days)
    trades = sum(d["trades"] for d in days)
    return {
        "band": name,
        "days": len(days),
        "net": net,
        "avg_net": net / len(days) if days else None,
        "trades": trades,
        "win_rate": (sum(d["wins"] for d in days) / trades * 100) if trades else None,
        "lo": days[0]["x"] if days else None,
        "hi": days[-1]["x"] if days else None,
    }


def score(points: list[dict]) -> dict | None:
    """Split the days into thirds by KPI value and score the gap between the outer
    two — "what does a day in the good band pay over a day in the bad one" is the
    money question, and it survives a sample size that a regression would not.

    ``points`` are dicts with date / x / net / trades / wins.
    """
    if len(points) < MIN_DAYS:
        return None
    s = sorted(points, key=lambda p: p["x"])
    n = len(s)
    k = n // 3
    lo, hi = s[:k], s[n - k:]

    xs = np.array([p["x"] for p in points], dtype="float64")
    ys = np.array([p["net"] for p in points], dtype="float64")
    xr, yr = ranks(xs), ranks(ys)
    rho = _corr(xr, yr)

    lo_net = sum(d["net"] for d in lo) / len(lo)
    hi_net = sum(d["net"] for d in hi) / len(hi)
    return {
        "rho": rho,
        "edge": hi_net - lo_net,
        "win_edge": _win_rate(hi) - _win_rate(lo),
        "luck": _luck(xr, yr, rho),
        "days": n,
        # Thirds by rank, not by fixed cutoffs: the KPIs are on incompatible scales
        # (a ratio, a rate, a σ), so a threshold that means something for ABR means
        # nothing for the spread — and ranking cannot manufacture a band that isn't
        # in the data.
        "bands": [
            _band("low", s[:k]),
            _band("mid", s[k:n - k]),
            _band("high", s[n - k:]),
        ],
    }


def luck_threshold(kpi_count: int) -> float:
    """With this many KPIs on the board, one of them clearing a 1-in-20 bar is what
    you should EXPECT from pure noise. The bar has to move with the family size —
    this is the Bonferroni line, blunt but erring the safe way."""
    return 0.05 / max(1, kpi_count)


def expected_false_positives(kpi_count: int) -> float:
    """Roughly how many KPIs should clear a plain 5% bar by chance alone."""
    return 0.05 * kpi_count


# --- the per-run study ------------------------------------------------------

def daily_pnl(trades: pd.DataFrame) -> dict[str, dict]:
    """Per-session net / trades / wins. A trade belongs to the session it entered
    in, which is the same bucketing the by-day view uses.

    This rollup exists nowhere else: metrics.json is aggregate-only, one row for
    the whole run.
    """
    by: dict[str, dict] = {}
    if trades.empty:
        return by
    for sess, net in zip(trades["session"].astype(str), trades["net_pnl"].astype(float)):
        d = sess[:10]
        s = by.setdefault(d, {"net": 0.0, "trades": 0, "wins": 0})
        s["net"] += net
        s["trades"] += 1
        if net > 0:
            s["wins"] += 1
    return by


def _class_buckets(days: list[dict]) -> list[dict]:
    """Traded days grouped by the day's regime class — always the end-of-day call,
    whatever checkpoint the KPIs are read at, because the class is a description of
    the session rather than something knowable inside it.
    """
    by: dict[str, list[dict]] = {}
    for d in days:
        by.setdefault(d["class"], []).append(d)
    out = []
    for k, ds in by.items():
        net = sum(d["net"] for d in ds)
        trades = sum(d["trades"] for d in ds)
        out.append({
            "class": k,
            "label": CLASS_LABEL.get(k, k),
            "days": len(ds),
            "net": net,
            "avg_net": net / len(ds),
            "trades": trades,
            "win_rate": (sum(d["wins"] for d in ds) / trades * 100) if trades else None,
            "dates": [d["date"] for d in ds],
        })
    return sorted(out, key=lambda b: b["net"], reverse=True)


def study(symbol: str, start: date, end: date, trades: pd.DataFrame) -> dict:
    """The whole regime-vs-P&L study for one run's window.

    Reads the tick cache only, exactly as the regime endpoints do: a day whose
    ticks were never bought is reported as skipped, never fetched. A GET must not
    spend money at Databento, and neither must a snapshot.
    """
    net_by_day = daily_pnl(trades)

    sessions, skipped = [], []
    for d in tickmod.session_dates(start, end):
        r = regmod.get_regime(symbol, d)
        if r is None:
            skipped.append(d.isoformat())
            continue
        sessions.append(r)

    # Days the run covered but never traded are left out of every score below: a
    # zero from "no setup armed" and a zero from "traded flat" are different facts,
    # and folding both into a band would dilute whichever regime produces the
    # fewest signals — which is exactly the regime you are trying to detect.
    traded = []
    for r in sessions:
        s = net_by_day.get(r["date"])
        if not s:
            continue
        traded.append({
            "date": r["date"], "class": r["class"], "partial": r["partial"],
            "net": s["net"], "trades": s["trades"], "wins": s["wins"],
            "checkpoints": r["checkpoints"],
        })

    boards: dict[str, list[dict]] = {}
    for cp, _t in regmod.CHECKPOINTS:
        rows = []
        for spec in KPIS:
            pts = [
                {"date": d["date"], "x": v, "net": d["net"],
                 "trades": d["trades"], "wins": d["wins"]}
                for d in traded
                if (v := d["checkpoints"].get(cp, {}).get(spec["key"])) is not None
            ]
            s = score(pts)
            if s is None:
                continue
            rows.append({**spec, **s})
        bar = luck_threshold(len(rows))
        for r in rows:
            # "Holds" means it survives the multiple-testing bar — not that it is
            # true. Everything else on the board is within what noise produces.
            r["holds"] = r["luck"] <= bar
        boards[cp] = {
            "luck_bar": bar,
            "expected_false_positives": expected_false_positives(len(rows)),
            "holds": sum(r["holds"] for r in rows),
            # By what a day in the top third pays over one in the bottom third —
            # the ranking is the cheap part; the luck column is what it's worth.
            "rows": sorted(rows, key=lambda r: abs(r["edge"]), reverse=True),
        }

    return {
        "stats_version": STATS_VERSION,
        "regime_version": regmod.REGIME_VERSION,
        "permutations": PERMUTATIONS,
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "checkpoints": [c for c, _ in regmod.CHECKPOINTS],
        "kpis": KPIS,
        "sessions_in_range": len(sessions),
        "traded_days": len(traded),
        "untraded_days": len(sessions) - len(traded),
        "skipped": skipped,
        "days": [{k: v for k, v in d.items() if k != "checkpoints"} for d in traded],
        "class_buckets": _class_buckets(traded),
        "boards": boards,
    }
