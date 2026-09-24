"""HTF-alignment audit: did manual trades taken against the 5m/15m trend pay?

For every discretionary logical trade (prop, live Lucid, paper, in-app replay,
ATAS replay archive) this reads the tape the trade printed on and labels, at the
entry instant and using ONLY bars closed before it:

  htf5 / htf15   up / down / flat  — close vs EMA20 and EMA20 slope over 3 bars
  htf            up / down / mixed — both frames agree, else mixed
  ltf            up / down / flat  — same rule on 30s bars (the chart you watch)
  align          with / against / mixed — trade direction vs htf
  d5ema_t        signed ticks from entry to the 5m EMA20 (+ = entry above it)

Every trade is checked against its tape: the last print before entry must sit
within PRICE_TOL points of the FIRST fill (a scale-in's average never prints), or the row is dropped (catches the 12h
double-import phantoms and any wrong-contract tape).

Run: PYTHONPATH=src .venv/Scripts/python.exe data/research/htf-alignment/audit.py
Writes trades.parquet next to itself; report.py reads it.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from journal.config import point_value, raw_symbol
from journal.sim import ticks as tickmod
from journal.trades import build_logical_trades

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).with_name("trades.parquet")
PRICE_TOL = 2.0  # points
EMA_SPAN = 20
SLOPE_BARS = 3
FRAMES = {"30s": "30s", "5m": "5min", "15m": "15min"}
COST_PER_SIDE = {"NQ": 3.50, "MNQ": 1.00}  # rough; outcome is read in points

COHORTS = [
    # (db, account filter -> cohort label)
    ("data/journal.db.pre-mode-folders.bak", lambda a: "atas_replay" if a == "Replay" else "prop"),
    ("data/journal.db", lambda a: {"replay": "app_replay", "paper": "paper"}.get(a, "live")),
]


def load_trades() -> pd.DataFrame:
    frames = []
    for db, label in COHORTS:
        con = sqlite3.connect(ROOT / db)
        j = pd.read_sql("select * from atas_journal", con)
        con.close()
        for c in ("open_ts_utc", "close_ts_utc"):
            j[c] = pd.to_datetime(j[c], utc=True, format="ISO8601")
        lt = build_logical_trades(j)
        # lot_keys are in open-time order, so the first names the first fill.
        first_px = dict(zip(j["dedupe_key"], j["open_price"]))
        lt["first_px"] = [first_px[k[0]] for k in lt["lot_keys"]]
        lt["cohort"] = lt["account"].map(label)
        lt["db"] = db
        frames.append(lt)
    df = pd.concat(frames, ignore_index=True)
    df = df[df["instrument"].str.contains(r"^M?NQ", regex=True)]
    return df.reset_index(drop=True)


def tape_symbol(instrument: str) -> str:
    sym = raw_symbol(instrument)
    return sym[1:] if re.match(r"^MNQ", sym) else sym


def session_day(ts_utc: pd.Timestamp):
    et = ts_utc.tz_convert("America/New_York")
    d = et.date()
    return d + timedelta(days=1) if et.hour >= 18 else d


_tape_cache: dict = {}


def tape(sym: str, day) -> pd.DataFrame | None:
    key = (sym, day)
    if key not in _tape_cache:
        parts = [p for p in (tickmod.cached_overnight(sym, day), tickmod.cached_rth(sym, day)) if p is not None]
        if not parts:
            _tape_cache[key] = None
        else:
            t = pd.concat(parts, ignore_index=True)[["ts_utc", "price"]]
            t["ts_utc"] = pd.to_datetime(t["ts_utc"], utc=True)
            _tape_cache[key] = t.sort_values("ts_utc").set_index("ts_utc")["price"]
    return _tape_cache[key]


_bars_cache: dict = {}


def frame_state(sym, day, prices: pd.Series, rule: str):
    """Closed OHLC close series + EMA20 for one frame, cached per tape."""
    key = (sym, day, rule)
    if key not in _bars_cache:
        close = prices.resample(rule, label="left", closed="left").last().dropna()
        ema = close.ewm(span=EMA_SPAN, adjust=False).mean()
        _bars_cache[key] = (close, ema)
    return _bars_cache[key]


def trend_at(close: pd.Series, ema: pd.Series, entry: pd.Timestamp, bar: pd.Timedelta):
    # A bar labelled t closes at t+bar; only bars closed strictly before entry count.
    n = close.index.searchsorted(entry - bar, side="right")
    if n < EMA_SPAN + SLOPE_BARS:
        return None, None
    c, e_now, e_then = close.iloc[n - 1], ema.iloc[n - 1], ema.iloc[n - 1 - SLOPE_BARS]
    if c > e_now and e_now > e_then:
        return "up", e_now
    if c < e_now and e_now < e_then:
        return "down", e_now
    return "flat", e_now


def main() -> None:
    df = load_trades()
    print(f"{len(df)} logical NQ/MNQ trades loaded")
    rows = []
    drop = {"no_tape": 0, "price_mismatch": 0, "warmup": 0}
    for tr in df.itertuples(index=False):
        entry = tr.entry_ts_utc
        sym, day = tape_symbol(tr.instrument), session_day(entry)
        prices = tape(sym, day)
        if prices is None:
            drop["no_tape"] += 1
            continue
        i = prices.index.searchsorted(entry, side="right")
        if i == 0 or abs(prices.iloc[i - 1] - tr.first_px) > PRICE_TOL:
            drop["price_mismatch"] += 1
            continue
        lab = {}
        for name, rule in FRAMES.items():
            close, ema = frame_state(sym, day, prices, rule)
            lab[name], lab[name + "_ema"] = trend_at(close, ema, entry, pd.Timedelta(rule))
        if lab["5m"] is None or lab["15m"] is None or lab["30s"] is None:
            drop["warmup"] += 1
            continue
        h5, h15 = lab["5m"], lab["15m"]
        htf = h5 if h5 == h15 and h5 != "flat" else "mixed"
        side = 1 if tr.direction == "Long" else -1
        align = "mixed" if htf == "mixed" else ("with" if (htf == "up") == (side == 1) else "against")
        pts = (tr.avg_exit - tr.avg_entry) * side
        rows.append({
            "cohort": tr.cohort, "account": tr.account, "trade_key": tr.trade_key,
            "instrument": tr.instrument, "day": day, "entry_ts_utc": entry,
            "et_hour": entry.tz_convert("America/New_York").hour + entry.tz_convert("America/New_York").minute / 60,
            "direction": tr.direction, "qty": tr.max_contracts, "duration_s": tr.duration_s,
            "pts": pts, "net_pnl": tr.net_pnl,
            "net_1nq": pts * 20.0 - 2 * COST_PER_SIDE["NQ"],
            "win": pts > 0,
            "htf5": h5, "htf15": h15, "htf": htf, "ltf": lab["30s"], "align": align,
            "d5ema_t": (tr.avg_entry - lab["5m_ema"]) * 4,
        })
    out = pd.DataFrame(rows)
    out.to_parquet(OUT)
    print(f"labelled {len(out)}; dropped {drop}")
    print(out.groupby("cohort").size())


if __name__ == "__main__":
    main()
