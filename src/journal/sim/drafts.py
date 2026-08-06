"""Draft strategies: research events laid out as trades on real charts.

A draft is the step between a study and a strategy. A study ends with an event
table and a stat ("lower1 deep traverses bounce 57.9% next-bar"); an engine
strategy is a full rule set with fills, stops and sizing. The gap between them
is where post-hoc leads keep dying (see the gate-robustness scorecard), so a
draft deliberately stays on the study's side of the line: it *materializes* the
study's own event rows into a trade-shaped list — entry on the bar after the
event, exit at the study's race thresholds — and renders them with the same
chart viewer the Strategies section uses. Nothing is simulated: no fills, no
slippage, no sizing, no commission, and overlapping trades are counted rather
than resolved.

That makes a draft explicitly NOT a backtest. It answers one question — "what
do this study's events look like as trades, on the actual sessions?" — so the
eye can do what the aggregate stats cannot: spot the regime, the clustering,
the entries that no human would take. Promotion out of Drafts goes through the
usual ladder (split-half, monthly consistency, engine A/B), never straight to
belief.

Specs are JSON files in ``data/drafts/`` — the registry is the directory, the
same convention as ``docs/research``. Each spec names an events parquet, a
pandas ``query`` over it, a direction, and the exit race; materialized results
snapshot to ``data/cache/drafts/`` keyed by a hash of the spec (the
weekly_vwap pattern), so every read after the first is instant and GET-safe.

Entry is the bar AFTER the event bar — the touch-bar artifact lesson from the
weekly-vwap-context study: the event bar's extremes predate the event within
the bar, and any "trade" entered on it re-counts the approach as the outcome.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import CACHE_DIR, DATA_DIR, point_value, tick_size
from . import regime as regimemod
from . import ticks as tickmod

DRAFTS_VERSION = 2

SPECS_DIR = DATA_DIR / "drafts"
SNAP_DIR = CACHE_DIR / "drafts"


# --- specs ------------------------------------------------------------------


@dataclass(frozen=True)
class DraftSpec:
    """One draft, as declared by its JSON file. Everything that changes the
    materialized trades is part of the identity hash; prose fields are not."""

    slug: str
    name: str
    hypothesis: str
    source_doc: str            # docs/research slug this draft came from
    events_parquet: str        # repo-relative path to the study's event table
    query: str                 # pandas .query() over the event table
    direction: str             # "long" | "short" ("both" for passthrough)
    race_sigma: float          # exit thresholds at level ± race_sigma·std
    horizon_min: int           # time exit if neither threshold prints
    symbol: str
    notes: str
    checklist: dict            # promotion ladder, display-only
    trades_parquet: str = ""   # passthrough mode: already-trade-shaped rows

    def identity(self) -> dict:
        return {
            "events_parquet": self.events_parquet, "query": self.query,
            "direction": self.direction, "race_sigma": self.race_sigma,
            "horizon_min": self.horizon_min, "symbol": self.symbol,
            "trades_parquet": self.trades_parquet,
        }

    def run_id(self) -> str:
        blob = json.dumps({"identity": self.identity(),
                           "version": DRAFTS_VERSION}, sort_keys=True)
        h = hashlib.sha1(blob.encode()).hexdigest()[:12]
        return f"{self.slug}_v{DRAFTS_VERSION}-{h}"


def _spec_path(slug: str) -> Path | None:
    # Resolve against the directory listing, never by joining the slug into a
    # path — same traversal guard as the research router.
    if SPECS_DIR.is_dir():
        for p in SPECS_DIR.iterdir():
            if p.suffix == ".json" and p.stem == slug:
                return p
    return None


def load_spec(slug: str) -> DraftSpec | None:
    p = _spec_path(slug)
    if p is None:
        return None
    d = json.loads(p.read_text())
    return DraftSpec(
        slug=slug, name=d["name"], hypothesis=d.get("hypothesis", ""),
        source_doc=d.get("source_doc", ""),
        events_parquet=d.get("events_parquet", ""),
        query=d["query"], direction=d.get("direction", "both"),
        race_sigma=float(d.get("race_sigma", 0.0)),
        horizon_min=int(d.get("horizon_min", 0)),
        symbol=d.get("symbol", "NQ"), notes=d.get("notes", ""),
        checklist=d.get("checklist", {}),
        trades_parquet=d.get("trades_parquet", ""),
    )


def list_specs() -> list[DraftSpec]:
    if not SPECS_DIR.is_dir():
        return []
    specs = []
    for p in sorted(SPECS_DIR.iterdir()):
        if p.suffix == ".json" and (s := load_spec(p.stem)) is not None:
            specs.append(s)
    return specs


# --- materialize ------------------------------------------------------------


def _day_bars(symbol: str, day) -> pd.DataFrame | None:
    """The session's 1-minute bars on the same ON+RTH frame the studies use,
    so an event's bar timestamp lines up exactly with a bar here."""
    contract = tickmod.contract_for_cached(symbol, day)
    if contract is None:
        return None
    rth = tickmod.cached_rth(contract, day)
    if rth is None or rth.empty:
        return None
    on = tickmod.cached_overnight(contract, day)
    full = rth if on is None or on.empty else pd.concat([on, rth],
                                                        ignore_index=True)
    b = regimemod.minute_bars(full)
    return None if b.empty else b


def _one_trade(ev, bars: pd.DataFrame, sgn: int, race_sigma: float,
               horizon_min: int, tick: float, pval: float) -> dict | None:
    """One event row → one trade, or None when it can't honestly be one
    (event on the session's last bar, or the race already decided at entry)."""
    # Compare in epoch-ns so tz-aware bar stamps and the event's ISO string
    # can't disagree about zones.
    ts = pd.Timestamp(ev.ts_et).tz_convert("UTC")
    bar_ns = bars["ts_utc"].astype("int64").to_numpy()
    i = int(np.searchsorted(bar_ns, ts.value))
    if i >= len(bars) or bar_ns[i] != ts.value:
        return None                       # event bar not in this frame
    e = i + 1                             # enter on the NEXT bar's open
    if e >= len(bars):
        return None

    lvl, std = float(ev.level_px), float(ev.std)
    target = lvl + sgn * race_sigma * std
    stop = lvl - sgn * race_sigma * std
    entry = float(bars["open"].iloc[e])
    if sgn * (entry - target) >= 0 or sgn * (entry - stop) <= 0:
        return None                       # open gapped past a threshold

    last = int(np.searchsorted(bar_ns, (ts + pd.Timedelta(minutes=horizon_min))
                               .value, side="right"))
    hi = bars["high"].to_numpy()[e:last]
    lo = bars["low"].to_numpy()[e:last]
    t_hit = np.nonzero(hi >= target if sgn > 0 else lo <= target)[0]
    s_hit = np.nonzero(lo <= stop if sgn > 0 else hi >= stop)[0]
    t0 = int(t_hit[0]) if len(t_hit) else None
    s0 = int(s_hit[0]) if len(s_hit) else None

    # Both thresholds inside the same minute bar is unresolvable at this
    # resolution — score it as the stop, so ambiguity can only hurt the draft.
    if s0 is not None and (t0 is None or s0 <= t0):
        j, exit_px, reason = e + s0, stop, "stop"
    elif t0 is not None:
        j, exit_px, reason = e + t0, target, "target"
    else:
        j = min(last, len(bars)) - 1
        if j < e:
            return None
        exit_px, reason = float(bars["close"].iloc[j]), "time"

    points = sgn * (exit_px - entry)
    risk = sgn * (entry - stop)
    entry_ts = bars["ts_utc"].iloc[e]
    exit_ts = bars["ts_utc"].iloc[j]
    return {
        "day": str(ev.day),
        "direction": "Long" if sgn > 0 else "Short",
        "entry_ts_utc": entry_ts.isoformat(),
        "exit_ts_utc": exit_ts.isoformat(),
        "avg_entry": entry, "avg_exit": float(exit_px),
        "stop_price": float(stop), "target_price": float(target),
        "exit_reason": reason,
        "points": float(points),
        "r_multiple": float(points / risk) if risk > 0 else 0.0,
        "net_pnl": float(points * pval),   # 1 contract, gross — not a backtest
        "duration_s": float((exit_ts - entry_ts).total_seconds()),
        "band_width_ticks": float(std / tick),
        "is_rth": bool(ev.is_rth),
    }


def _passthrough_trades(spec: DraftSpec) -> list[dict]:
    """Passthrough mode: the parquet rows are already trades (engine output or
    a study's pre-built trade table), so no race is run — the draft is purely a
    chart layout of decisions that were made elsewhere. Rows carry their own
    direction, prices and exit reasons; only the fields the chart layer reads
    are copied through, plus any extras (e.g. a ``strategy`` tag) verbatim."""
    df = pd.read_parquet(Path(spec.trades_parquet))
    if spec.query and spec.query != "True":
        df = df.query(spec.query)
    keep = ["direction", "avg_entry", "avg_exit", "stop_price", "target_price",
            "exit_reason", "points", "r_multiple", "net_pnl", "duration_s",
            "band_width_ticks", "strategy"]
    # Columns the row layout above already spends, so a verbatim copy would
    # either duplicate them or fight the timestamp normalisation.
    spent = {"day", "session", "entry_ts_utc", "exit_ts_utc", "is_rth", *keep}
    extra = [c for c in df.columns if c not in spent]
    out = []
    for row in df.itertuples():
        tr = {"day": str(getattr(row, "day", getattr(row, "session", ""))),
              "entry_ts_utc": pd.Timestamp(row.entry_ts_utc).isoformat(),
              "exit_ts_utc": pd.Timestamp(row.exit_ts_utc).isoformat(),
              "is_rth": True}
        for c in keep:
            if hasattr(row, c):
                v = getattr(row, c)
                tr[c] = v if isinstance(v, str) else float(v)
        # Source-specific columns ride along untouched (provenance, labels) —
        # the chart layer ignores what it doesn't know, the table can show it.
        for c in extra:
            v = getattr(row, c, None)
            tr[c] = v if isinstance(v, str) else (
                None if v is None or pd.isna(v) else float(v))
        out.append(tr)
    return out


def materialize(spec: DraftSpec) -> dict:
    """Run the spec over its event table (cache-only) and lay out the trades."""
    if spec.trades_parquet:
        trades = _passthrough_trades(spec)
        n_events, skipped, days_no_data = len(trades), 0, 0
        trades.sort(key=lambda t: t["entry_ts_utc"])
        return _finish(spec, n_events, trades, skipped, days_no_data)

    events = pd.read_parquet(Path(spec.events_parquet)).query(spec.query)
    sgn = 1 if spec.direction == "long" else -1
    tick = tick_size(spec.symbol)
    pval = point_value(spec.symbol)

    trades: list[dict] = []
    skipped = 0
    days_no_data = 0
    for day, evs in events.groupby("day", sort=True):
        bars = _day_bars(spec.symbol, pd.Timestamp(day).date())
        if bars is None:
            days_no_data += 1
            skipped += len(evs)
            continue
        for ev in evs.itertuples():
            tr = _one_trade(ev, bars, sgn, spec.race_sigma, spec.horizon_min,
                            tick, pval)
            if tr is None:
                skipped += 1
            else:
                trades.append(tr)

    trades.sort(key=lambda t: t["entry_ts_utc"])
    return _finish(spec, len(events), trades, skipped, days_no_data)


def _finish(spec: DraftSpec, n_events: int, trades: list[dict],
            skipped: int, days_no_data: int) -> dict:
    for no, tr in enumerate(trades, start=1):
        tr["trade_no"] = no

    # Overlap is counted, not resolved: a second entry while the first is
    # still open would need a second contract live. The count is a guardrail
    # stat — a big number means the aggregate R is not a sequential result.
    overlaps = 0
    open_until: dict[str, str] = {}
    for tr in trades:
        prev = open_until.get(tr["day"])
        tr["overlapped"] = prev is not None and tr["entry_ts_utc"] < prev
        overlaps += tr["overlapped"]
        if prev is None or tr["exit_ts_utc"] > prev:
            open_until[tr["day"]] = tr["exit_ts_utc"]

    r = np.array([t["r_multiple"] for t in trades])
    reasons = [t["exit_reason"] for t in trades]
    by_month: dict[str, dict] = {}
    for tr in trades:
        m = by_month.setdefault(tr["day"][:7], {"n": 0, "sum_r": 0.0})
        m["n"] += 1
        m["sum_r"] = round(m["sum_r"] + tr["r_multiple"], 3)

    decided = sum(x in ("target", "stop") for x in reasons)
    # by_reason is the general form: passthrough drafts carry whatever exit
    # vocabulary their source uses (an engine's fills, a transcript's spoken
    # exits), and the fixed target/stop/time triple reads as three zeros there.
    by_reason: dict[str, int] = {}
    for x in reasons:
        by_reason[x] = by_reason.get(x, 0) + 1

    return {
        "drafts_version": DRAFTS_VERSION,
        "run_id": spec.run_id(),
        "identity": spec.identity(),
        "summary": {
            "n_events": int(n_events),
            "n_trades": len(trades),
            "n_skipped": int(skipped),
            "days_no_data": int(days_no_data),
            "n_sessions": len({t["day"] for t in trades}),
            "first_day": trades[0]["day"] if trades else None,
            "last_day": trades[-1]["day"] if trades else None,
            "targets": reasons.count("target"),
            "stops": reasons.count("stop"),
            "time_exits": reasons.count("time"),
            "win_rate": round(reasons.count("target") / decided, 4)
                        if decided else None,
            "avg_r": round(float(r.mean()), 4) if len(r) else None,
            "total_r": round(float(r.sum()), 2) if len(r) else None,
            "total_points": round(sum(t["points"] for t in trades), 2),
            "by_reason": by_reason,
            "overlapping_trades": int(overlaps),
            "by_month": by_month,
        },
        "trades": trades,
    }


# --- snapshot store ---------------------------------------------------------


def _snap_path(spec: DraftSpec) -> Path:
    return SNAP_DIR / f"{spec.run_id()}.json"


def read(spec: DraftSpec) -> dict | None:
    p = _snap_path(spec)
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return d if d.get("drafts_version") == DRAFTS_VERSION else None


def get(spec: DraftSpec, refresh: bool = False) -> dict:
    if not refresh and (cached := read(spec)) is not None:
        return cached
    result = materialize(spec)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    _snap_path(spec).write_text(json.dumps(result, indent=2))
    return result
