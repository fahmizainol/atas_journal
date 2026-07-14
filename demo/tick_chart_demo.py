"""NQ tick-chart demo — one tabbed HTML spanning two demos.

Tab "Basic"  : 500-tick candles + VWAP @ 09:30 cash open + ±1σ/±2σ/±3σ bands,
               09:30-11:00 ET, for 2026-06-01 and 2026-06-22 (reuses cached
               price/size/time parquet — no re-fetch).
Tab "Extra"  : 500-tick candles + cumulative volume delta (CVD) pane + large-
               trade markers + per-minute trade-count/delta table, 09:30-09:45
               ET, for 2026-04-06 .. 2026-04-10 (new full-field `trades` fetch:
               keeps side, flags, ts_recv, sequence, …).

`_fetch_trades` now keeps every `trades` schema field — Databento bills by
uncompressed binary size per schema, not per column, so the extra fields are
free on a given fetch. Re-runs read from data/cache/trades/*.parquet.

    uv run python demo/tick_chart_demo.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from journal.config import (  # noqa: E402
    CACHE_DIR,
    DATABENTO_DATASET,
    ET_TZ,
    continuous_symbol,
    databento_key,
)

# --- Config ---------------------------------------------------------------
INSTRUMENT = "NQ"
TICKS_PER_BAR = 500
LARGE_TRADE_SIZE = 50          # contracts; trades >= this are flagged "large"

# Tab 1 — basic (candles + VWAP bands), reuses existing 3-col cache.
BASIC_DATES: list[date] = [datetime(2026, 6, 1).date(), datetime(2026, 6, 22).date()]
BASIC_START_ET = time(9, 30)
BASIC_END_ET = time(11, 0)

# Tab 2 — extra (CVD + tables + large trades), new full-field fetch.
EXTRA_DATES: list[date] = [datetime(2026, 4, d).date() for d in range(6, 11)]
EXTRA_START_ET = time(9, 30)
EXTRA_END_ET = time(9, 45)

OUT_DIR = Path(__file__).resolve().parent
HTML_OUT = OUT_DIR / "tick_chart.html"
TRADES_CACHE_DIR = CACHE_DIR / "trades"
TRADES_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Every `trades` field Databento emits; keep whichever columns are present.
KEEP_FIELDS = [
    "ts_utc", "price", "size", "side", "action", "flags",
    "ts_recv", "sequence", "publisher_id", "instrument_id",
]


# --- Databento fetch + cache ---------------------------------------------
def _cache_path(symbol: str, start_naive: datetime, end_naive: datetime) -> Path:
    return TRADES_CACHE_DIR / (
        f"{symbol}_trades_{start_naive:%Y%m%dT%H%M}_{end_naive:%Y%m%dT%H%M}.parquet"
    )


def _fetch_trades(symbol: str, start_naive: datetime, end_naive: datetime) -> pd.DataFrame:
    """Raw `trades` rows for [start, end) from Databento (continuous symbol).

    Keeps every schema field so CVD (side), block/condition decoding (flags),
    and latency (ts_recv vs ts_event) are available downstream at no extra
    API cost.
    """
    import databento as dbn

    key = databento_key()
    if key is None:
        raise SystemExit("DATABENTO_API_KEY not set in .env")

    client = dbn.Historical(key)

    def _query(e: datetime) -> pd.DataFrame:
        return client.timeseries.get_range(
            dataset=DATABENTO_DATASET,
            schema="trades",
            stype_in="continuous",
            symbols=[symbol],
            start=start_naive,
            end=e,
        ).to_df(price_type="float", pretty_ts=True)

    try:
        df = _query(end_naive)
    except dbn.common.error.BentoClientError as exc:
        m = re.search(r"end time before (\S+)", str(exc))
        if not m:
            raise
        allowed = pd.Timestamp(m.group(1)).tz_localize(None)
        if allowed <= pd.Timestamp(start_naive):
            return pd.DataFrame()
        df = _query(allowed.to_pydatetime())

    if df.empty:
        return df
    df = df.reset_index()
    ts_col = "ts_event" if "ts_event" in df.columns else df.columns[0]
    df = df.rename(columns={ts_col: "ts_utc"})
    size_col = "size" if "size" in df.columns else "volume"
    df = df.rename(columns={size_col: "size"})
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    keep = [c for c in KEEP_FIELDS if c in df.columns]
    return df[keep].sort_values("ts_utc").reset_index(drop=True)


def get_trades(symbol: str, start_naive: datetime, end_naive: datetime) -> pd.DataFrame:
    cache = _cache_path(symbol, start_naive, end_naive)
    if cache.exists():
        return pd.read_parquet(cache)
    df = _fetch_trades(symbol, start_naive, end_naive)
    if df is not None and not df.empty:
        df.to_parquet(cache, index=False)
    return df


# --- Order-flow prep ------------------------------------------------------
def normalize_side(s) -> str | None:
    """Databento `side`: Bid = buy aggressor, Ask = sell aggressor, None = N."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip().upper()
    if s.startswith("B"):
        return "buy"
    if s.startswith("A"):
        return "sell"
    return None


def attach_order_flow(trades: pd.DataFrame) -> pd.DataFrame:
    """Add session VWAP/std (for bands) + per-trade signed volume for CVD.
    CVD resets at the anchor (first trade of the fetch = 09:30 cash open)."""
    df = trades.sort_values("ts_utc").reset_index(drop=True)
    p = df["price"].astype(float)
    v = df["size"].astype(float)
    cum_v = v.cumsum().where(lambda c: c != 0)
    df["vwap"] = (p * v).cumsum() / cum_v
    var = (p * p * v).cumsum() / cum_v - df["vwap"] ** 2
    df["std"] = var.clip(lower=0) ** 0.5
    if "side" in df.columns:
        side = df["side"].map(normalize_side)
        df["signed_vol"] = v.where(side == "buy", -v.where(side == "sell", 0.0))
        df["side_norm"] = side
    else:
        df["signed_vol"] = 0.0
        df["side_norm"] = None
    return df


# --- Tick bars ------------------------------------------------------------
def build_tick_bars(win: pd.DataFrame, ticks_per_bar: int) -> pd.DataFrame:
    """Group every N consecutive trades into one OHLCV bar. Carries VWAP/std
    (for bands) and, when side is present, buy/sell vol + per-bar delta and a
    cumulative CVD line (per-session reset at the first bar)."""
    buckets = np.arange(len(win)) // ticks_per_bar
    agg = {
        "open_time": ("ts_utc", "first"),
        "open": ("price", "first"), "high": ("price", "max"),
        "low": ("price", "min"), "close": ("price", "last"),
        "volume": ("size", "sum"),
        "vwap": ("vwap", "last"), "std": ("std", "last"),
    }
    has_side = "signed_vol" in win.columns and win["signed_vol"].abs().sum() > 0
    if has_side:
        agg["buy_vol"] = ("size", lambda s: float(s.where(win.loc[s.index, "side_norm"] == "buy", 0).sum()))
        agg["sell_vol"] = ("size", lambda s: float(s.where(win.loc[s.index, "side_norm"] == "sell", 0).sum()))
        agg["bar_delta"] = ("signed_vol", "sum")
    bars = win.assign(_g=buckets).groupby("_g", sort=False).agg(**agg).reset_index(drop=True)
    bars["upper1"] = bars["vwap"] + bars["std"]
    bars["lower1"] = bars["vwap"] - bars["std"]
    bars["upper2"] = bars["vwap"] + 2 * bars["std"]
    bars["lower2"] = bars["vwap"] - 2 * bars["std"]
    bars["upper3"] = bars["vwap"] + 3 * bars["std"]
    bars["lower3"] = bars["vwap"] - 3 * bars["std"]
    if has_side:
        bars["cvd"] = bars["bar_delta"].cumsum()
    return bars


def to_ny_epoch(ts_utc: pd.Series) -> np.ndarray:
    """UTC -> epoch-seconds of naive NY wall-clock (axis reads in ET)."""
    s = pd.to_datetime(ts_utc, utc=True)
    local = s.dt.tz_convert(ET_TZ).dt.tz_localize(None)
    return local.astype("datetime64[s]").astype("int64").to_numpy()


def enforce_monotonic(times: np.ndarray) -> np.ndarray:
    out = times.astype("int64").copy()
    for i in range(1, len(out)):
        if out[i] <= out[i - 1]:
            out[i] = out[i - 1] + 1
    return out


# --- Large trades + per-minute table --------------------------------------
def large_trades_in_window(win: pd.DataFrame, min_size: int) -> pd.DataFrame:
    cols = ["ts_utc", "price", "size", "side_norm", "flags"]
    keep = [c for c in cols if c in win.columns]
    lt = win[keep][win["size"] >= min_size].copy()
    if lt.empty:
        return lt
    lt["et"] = lt["ts_utc"].dt.tz_convert(ET_TZ).dt.strftime("%H:%M:%S")
    return lt


def per_minute_table(win: pd.DataFrame, the_date: date) -> list[dict]:
    """One row per 1-min ET bin in the window: trades, buy/sell vol, delta, vwap."""
    w = win.copy()
    w["et"] = w["ts_utc"].dt.tz_convert(ET_TZ)
    w["bin"] = w["et"].dt.floor("min")
    w["pv"] = w["price"].astype(float) * w["size"].astype(float)
    has_side = "side_norm" in w.columns
    if has_side:
        w["buy_v"] = w["size"].where(w["side_norm"] == "buy", 0.0)
        w["sell_v"] = w["size"].where(w["side_norm"] == "sell", 0.0)
    g = w.groupby("bin")
    rows = []
    for b, grp in g:
        vol = float(grp["size"].sum())
        row = {
            "time": b.strftime("%H:%M"),
            "trades": int(len(grp)),
            "vol": int(vol),
            "vwap": round(float(grp["pv"].sum() / vol), 2) if vol else None,
        }
        if has_side:
            bv, sv = float(grp["buy_v"].sum()), float(grp["sell_v"].sum())
            row.update({"buy": int(bv), "sell": int(sv), "delta": int(bv - sv)})
        rows.append(row)
    return rows


# --- Panel builders -------------------------------------------------------
def _window_utc(the_date: date, start_et: time, end_et: time):
    s = pd.Timestamp(datetime.combine(the_date, start_et), tz=ET_TZ).tz_convert("UTC")
    e = pd.Timestamp(datetime.combine(the_date, end_et), tz=ET_TZ).tz_convert("UTC")
    return s, e


def build_basic_panel(the_date: date) -> dict:
    symbol = continuous_symbol(INSTRUMENT)
    s_utc, e_utc = _window_utc(the_date, BASIC_START_ET, BASIC_END_ET)
    fetch_end = e_utc + pd.Timedelta(seconds=1)
    print(f"[basic] {the_date}  {s_utc} -> {fetch_end} UTC")
    trades = attach_order_flow(get_trades(symbol, s_utc.tz_localize(None).to_pydatetime(),
                                          fetch_end.tz_localize(None).to_pydatetime()))
    win = trades[(trades["ts_utc"] >= s_utc) & (trades["ts_utc"] <= e_utc)]
    if win.empty:
        raise SystemExit(f"No trades for basic {the_date}")
    bars = build_tick_bars(win.reset_index(drop=True), TICKS_PER_BAR)
    times = enforce_monotonic(to_ny_epoch(bars["open_time"]))
    bars_json = [
        {"time": int(t), "open": round(float(o), 2), "high": round(float(h), 2),
         "low": round(float(lo), 2), "close": round(float(c), 2), "volume": int(v)}
        for t, o, h, lo, c, v in zip(times, bars["open"], bars["high"], bars["low"],
                                     bars["close"], bars["volume"])
    ]
    vwap_json = [
        {"time": int(t), "middle": round(float(w), 2), "upper1": round(float(u1), 2),
         "lower1": round(float(l1), 2), "upper2": round(float(u2), 2),
         "lower2": round(float(l2), 2), "upper3": round(float(u3), 2),
         "lower3": round(float(l3), 2)}
        for t, w, u1, l1, u2, l2, u3, l3 in zip(
            times, bars["vwap"], bars["upper1"], bars["lower1"], bars["upper2"],
            bars["lower2"], bars["upper3"], bars["lower3"])
    ]
    last = vwap_json[-1] if vwap_json else None
    return {
        "kind": "basic",
        "info": {
            "title": f"{the_date.strftime('%a %Y-%m-%d')} · 09:30-11:00 ET",
            "symbol": symbol, "bars": len(bars_json), "trades": int(len(win)),
            "price_low": round(float(bars["low"].min()), 2),
            "price_high": round(float(bars["high"].max()), 2),
            "vwap_start": vwap_json[0]["middle"] if vwap_json else None,
            "vwap_end": last["middle"] if last else None,
            "bands_end": (f"{last['middle']:.2f} ±{last['upper1']-last['middle']:.2f}"
                          f" / ±{last['upper2']-last['middle']:.2f}"
                          f" / ±{last['upper3']-last['middle']:.2f}" if last else None),
        },
        "bars": bars_json, "vwap": vwap_json,
    }


def build_extra_panel(the_date: date, ticks_per_bar: int = TICKS_PER_BAR) -> dict:
    symbol = continuous_symbol(INSTRUMENT)
    s_utc, e_utc = _window_utc(the_date, EXTRA_START_ET, EXTRA_END_ET)
    fetch_end = e_utc + pd.Timedelta(seconds=1)
    print(f"[extra {ticks_per_bar}t] {the_date}  {s_utc} -> {fetch_end} UTC")
    trades = attach_order_flow(get_trades(symbol, s_utc.tz_localize(None).to_pydatetime(),
                                          fetch_end.tz_localize(None).to_pydatetime()))
    win = trades[(trades["ts_utc"] >= s_utc) & (trades["ts_utc"] <= e_utc)].reset_index(drop=True)
    if win.empty:
        raise SystemExit(f"No trades for extra {the_date}")
    bars = build_tick_bars(win, ticks_per_bar)
    times = enforce_monotonic(to_ny_epoch(bars["open_time"]))

    bars_json = [
        {"time": int(t), "open": round(float(o), 2), "high": round(float(h), 2),
         "low": round(float(lo), 2), "close": round(float(c), 2), "volume": int(v)}
        for t, o, h, lo, c, v in zip(times, bars["open"], bars["high"], bars["low"],
                                     bars["close"], bars["volume"])
    ]
    vwap_json = [
        {"time": int(t), "middle": round(float(w), 2), "upper1": round(float(u1), 2),
         "lower1": round(float(l1), 2), "upper2": round(float(u2), 2),
         "lower2": round(float(l2), 2), "upper3": round(float(u3), 2),
         "lower3": round(float(l3), 2)}
        for t, w, u1, l1, u2, l2, u3, l3 in zip(
            times, bars["vwap"], bars["upper1"], bars["lower1"], bars["upper2"],
            bars["lower2"], bars["upper3"], bars["lower3"])
    ]
    has_side = "cvd" in bars.columns
    cvd_json = []
    delta_json = []
    if has_side:
        cvd_json = [{"time": int(t), "value": round(float(c), 0)}
                    for t, c in zip(times, bars["cvd"])]
        delta_json = [
            {"time": int(t), "value": int(d),
             "color": "rgba(33,192,122,0.6)" if d >= 0 else "rgba(245,69,95,0.6)"}
            for t, d in zip(times, bars["bar_delta"])
        ]

    # Large trades: mark each bar that contains >=1 large trade, net-colored.
    markers = []
    large_table = []
    if "size" in win.columns:
        lt = large_trades_in_window(win, LARGE_TRADE_SIZE)
        if not lt.empty:
            # map each large trade to its bar index (bucket = floor(pos/N))
            pos = np.arange(len(win))
            bucket_of_trade = pos // ticks_per_bar
            lt_idx = lt.index.values
            lt_buckets = bucket_of_trade[lt_idx]
            for b in sorted(set(lt_buckets)):
                mask = lt_buckets == b
                sub = lt[mask]
                buys = int((sub["side_norm"] == "buy").sum())
                sells = int((sub["side_norm"] == "sell").sum())
                net = buys - sells
                markers.append({
                    "time": int(times[b]),
                    "position": "aboveBar" if net < 0 else "belowBar",
                    "shape": "arrowDown" if net < 0 else "arrowUp",
                    "color": "#f5455f" if net < 0 else "#21c07a",
                    "text": f"L{len(sub)}",
                })
            for _, r in lt.iterrows():
                flags = r.get("flags")
                flags_hex = f"0x{int(flags):02x}" if flags is not None and not pd.isna(flags) else "—"
                large_table.append({
                    "time": r["et"], "price": round(float(r["price"]), 2),
                    "size": int(r["size"]),
                    "side": (r["side_norm"] or "—"),
                    "flags": flags_hex,
                })
            large_table.sort(key=lambda x: x["size"], reverse=True)

    pm_table = per_minute_table(win, the_date)

    # Session-level delta totals
    buy_tot = int(win["size"].where(win.get("side_norm") == "buy", 0).sum()) if has_side else None
    sell_tot = int(win["size"].where(win.get("side_norm") == "sell", 0).sum()) if has_side else None
    cvd_end = int(bars["cvd"].iloc[-1]) if has_side else None

    return {
        "kind": "extra",
        "info": {
            "title": f"{the_date.strftime('%a %Y-%m-%d')} · 09:30-09:45 ET",
            "symbol": symbol, "ticks_per_bar": ticks_per_bar,
            "bars": len(bars_json), "trades": int(len(win)),
            "price_low": round(float(bars["low"].min()), 2),
            "price_high": round(float(bars["high"].max()), 2),
            "buy_vol": buy_tot, "sell_vol": sell_tot,
            "cvd_end": cvd_end,
            "vwap_start": vwap_json[0]["middle"] if vwap_json else None,
            "vwap_end": vwap_json[-1]["middle"] if vwap_json else None,
            "bands_end": (f"{vwap_json[-1]['middle']:.2f} ±{vwap_json[-1]['upper1']-vwap_json[-1]['middle']:.2f}"
                          f" / ±{vwap_json[-1]['upper2']-vwap_json[-1]['middle']:.2f}"
                          f" / ±{vwap_json[-1]['upper3']-vwap_json[-1]['middle']:.2f}" if vwap_json else None),
            "large_trades": len(large_table),
            "large_threshold": LARGE_TRADE_SIZE,
        },
        "bars": bars_json, "vwap": vwap_json, "cvd": cvd_json, "delta": delta_json,
        "markers": markers, "per_minute": pm_table, "large_trades": large_table,
    }


# --- Output ---------------------------------------------------------------
def main() -> None:
    basic = [build_basic_panel(d) for d in BASIC_DATES]
    extra = [build_extra_panel(d, 500) for d in EXTRA_DATES]
    extra250 = [build_extra_panel(d, 250) for d in EXTRA_DATES]
    _write_html(basic, extra, extra250)

    print("\n— basic —")
    for p in basic:
        i = p["info"]
        print(f"  {i['title']}: {i['bars']} bars, {i['trades']} trades, "
              f"{i['price_low']}-{i['price_high']}, VWAP {i['vwap_start']}->{i['vwap_end']}")
    for label, panels in [("— extra (500-tick) —", extra), ("— extra (250-tick) —", extra250)]:
        print(label)
        for p in panels:
            i = p["info"]
            print(f"  {i['title']}: {i['bars']} bars, {i['trades']} trades, "
                  f"buy {i['buy_vol']} / sell {i['sell_vol']} / CVD {i['cvd_end']}, "
                  f"large(>={i['large_threshold']}) {i['large_trades']}")
    print(f"\nWrote {HTML_OUT}")
    print("Open:  file://" + str(HTML_OUT))


def _write_html(basic: list[dict], extra: list[dict], extra250: list[dict]) -> None:
    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>NQ tick-chart demo</title>
<script src="https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0b0e13; color:#cbd5e1; font:14px/1.5 Inter,system-ui,sans-serif; }
  #wrap { max-width:1180px; margin:0 auto; padding:18px 20px 48px; }
  h1 { font-size:20px; font-weight:600; margin:0 0 10px; }
  .tabs { display:flex; gap:8px; margin:0 0 18px; border-bottom:1px solid #1c2230; }
  .tab { padding:8px 16px; cursor:pointer; border:1px solid #1c2230; border-bottom:none;
         border-radius:8px 8px 0 0; background:#11151d; color:#94a3b8; font:inherit; }
  .tab.active { background:#1c2230; color:#e2e8f0; }
  .tabpane { display:none; }
  .tabpane.active { display:block; }
  .panel { margin-bottom:28px; }
  .panel:last-child { margin-bottom:0; }
  .panel h2 { font-size:15px; font-weight:600; margin:0 0 6px; }
  .sub { color:#64748b; margin:0 0 10px; font-size:12.5px; }
  .info { display:flex; flex-wrap:wrap; gap:6px 22px; margin:0 0 10px;
          padding:11px 14px; background:#11151d; border:1px solid #1c2230; border-radius:8px; font-size:12.5px; }
  .info b { color:#e2e8f0; }
  .chart { height:560px; background:#0b0e13; border:1px solid #1c2230; border-radius:8px; }
  .legend { display:flex; flex-wrap:wrap; gap:18px; margin:8px 2px 0; font-size:12px; color:#64748b; }
  .swatch { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:middle; }
  .tables { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:14px; }
  .tbl-card { background:#11151d; border:1px solid #1c2230; border-radius:8px; padding:10px 12px; }
  .tbl-card h3 { font-size:12.5px; margin:0 0 8px; color:#e2e8f0; font-weight:600; }
  .scroll { max-height:230px; overflow:auto; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:4px 8px; text-align:right; border-bottom:1px solid #1c2230; white-space:nowrap; }
  th { color:#94a3b8; font-weight:600; position:sticky; top:0; background:#11151d; }
  td:first-child, th:first-child { text-align:left; }
  .pos { color:#21c07a; } .neg { color:#f5455f; } .muted { color:#64748b; }
</style>
</head>
<body>
<div id="wrap">
  <h1>NQ — tick-chart demo</h1>
  <div class="tabs">
    <button class="tab active" data-tab="basic">Basic · Jun 1 &amp; 22 · 09:30-11:00 · VWAP bands</button>
    <button class="tab" data-tab="extra">Apr 6-10 · 500-tick · CVD + tables</button>
    <button class="tab" data-tab="extra250">Apr 6-10 · 250-tick · CVD + tables</button>
  </div>

  <div id="tab-basic" class="tabpane active"></div>
  <div id="tab-extra" class="tabpane"></div>
  <div id="tab-extra250" class="tabpane"></div>
</div>
<script>
const BASIC = __BASIC_JSON__;
const EXTRA = __EXTRA_JSON__;
const EXTRA250 = __EXTRA250_JSON__;
const charts = { basic: [], extra: [], extra250: [] };
const rendered = { basic: false, extra: false, extra250: false };
const TABDATA = { basic: BASIC, extra: EXTRA, extra250: EXTRA250 };
const TABRENDER = { basic: renderBasicPanel, extra: renderExtraPanel, extra250: renderExtraPanel };

function infoCells(info, keys) {
  return keys.map(([k, v]) => `<span><b>${k}</b> ${v ?? '—'}</span>`).join('');
}

// --- VWAP + ±1σ/±2σ/±3σ bands in purple shades (outer = darkest), with the
// ±2σ-±3σ region shaded. Filling between two arbitrary lines isn't a built-in
// lightweight-charts feature, so BandFillPrimitive draws a polygon between the
// outer (±3σ) and inner (±2σ) lines via the ISeriesPrimitive canvas hook.
const VWAP_PURPLE = '#a78bfa';
const BAND_COLORS = [['upper3','#5b21b6'],['upper2','#7c3aed'],['upper1','#8b5cf6'],
                     ['lower1','#8b5cf6'],['lower2','#7c3aed'],['lower3','#5b21b6']];
const BAND_FILL = 'rgba(124, 58, 237, 0.16)';

class BandFillRenderer {
  constructor(outer, inner, color, chart, series) {
    this.outer = outer; this.inner = inner; this.color = color;
    this.chart = chart; this.series = series;
  }
  draw(target) {
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const ts = this.chart.timeScale();
      const pts = [];
      for (let i = 0; i < this.outer.length; i++) {
        const x = ts.timeToCoordinate(this.outer[i].time);
        const yo = this.series.priceToCoordinate(this.outer[i].value);
        const yi = this.series.priceToCoordinate(this.inner[i].value);
        if (x == null || yo == null || yi == null) continue;
        pts.push({ x, yo, yi });
      }
      if (pts.length < 2) return;
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].yo);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].yo);
      for (let i = pts.length - 1; i >= 0; i--) ctx.lineTo(pts[i].x, pts[i].yi);
      ctx.closePath();
      ctx.fillStyle = this.color;
      ctx.fill();
    });
  }
}
class BandFillView {
  constructor(outer, inner, color, chart, series) {
    this._r = new BandFillRenderer(outer, inner, color, chart, series);
  }
  update() {}
  renderer() { return this._r; }
  zOrder() { return 'bottom'; }
}
class BandFillPrimitive {
  constructor(outer, inner, color) { this.outer = outer; this.inner = inner; this.color = color; }
  attached(p) {
    this.chart = p.chart; this.series = p.series; this.requestUpdate = p.requestUpdate;
    this._view = new BandFillView(this.outer, this.inner, this.color, this.chart, this.series);
    this.requestUpdate?.();
  }
  detached() { this._view = null; }
  updateAllViews() { this._view?.update(); }
  paneViews() { return this._view ? [this._view] : []; }
}

function addVwapBands(chart, candle, vwap) {
  if (!vwap || !vwap.length) return;
  // Shaded ±2σ-±3σ rings (primitive draws behind series via zOrder 'bottom').
  candle.attachPrimitive(new BandFillPrimitive(
    vwap.map(v => ({ time: v.time, value: v.upper3 })),
    vwap.map(v => ({ time: v.time, value: v.upper2 })), BAND_FILL));
  candle.attachPrimitive(new BandFillPrimitive(
    vwap.map(v => ({ time: v.time, value: v.lower3 })),
    vwap.map(v => ({ time: v.time, value: v.lower2 })), BAND_FILL));
  // VWAP mid (solid) + ±1σ/±2σ/±3σ dashed lines, purple darkening outward.
  const mid = chart.addSeries(LightweightCharts.LineSeries,
    { color: VWAP_PURPLE, lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
  mid.setData(vwap.map(v => ({ time: v.time, value: v.middle })));
  for (const [key, color] of BAND_COLORS) {
    const band = chart.addSeries(LightweightCharts.LineSeries,
      { color, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
    band.setData(vwap.map(v => ({ time: v.time, value: v[key] })));
  }
}

// ---- Basic panel: candles + VWAP ±1σ/±2σ/±3σ ---------------------------
function renderBasicPanel(host, p) {
  const i = p.info;
  const sec = document.createElement('section');
  sec.className = 'panel';
  sec.innerHTML = `
    <h2>${i.title}</h2>
    <div class="sub">500-tick candles · VWAP @ 09:30 cash open · ±1σ / ±2σ / ±3σ bands</div>
    <div class="info">${infoCells(i, [['symbol',i.symbol],['bars',i.bars],['trades',i.trades],
      ['range', i.price_low+' - '+i.price_high],['VWAP', i.vwap_start+' -> '+i.vwap_end],
      ['bands(end)', i.bands_end]])}</div>
    <div class="chart"></div>
    <div class="legend">
      <span><span class="swatch" style="background:#21c07a"></span>up</span>
      <span><span class="swatch" style="background:#f5455f"></span>down</span>
      <span><span class="swatch" style="background:#a78bfa"></span>VWAP</span>
      <span><span class="swatch" style="background:#8b5cf6"></span>±1σ</span>
      <span><span class="swatch" style="background:#7c3aed"></span>±2σ</span>
      <span><span class="swatch" style="background:#5b21b6"></span>±3σ (shaded ±2σ-±3σ)</span>
    </div>`;
  host.appendChild(sec);
  const el = sec.querySelector('.chart');
  const chart = LightweightCharts.createChart(el, {
    width: el.clientWidth, height: 560,
    layout: { background: { type: LightweightCharts.ColorType.Solid, color: '#0b0e13' },
              textColor: '#cbd5e1', fontFamily: 'Inter, sans-serif' },
    grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
    rightPriceScale: { borderColor: '#1c2230' },
    timeScale: { borderColor: '#1c2230', timeVisible: true, secondsVisible: false, rightOffset: 4 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  charts.basic.push({ chart, el });
  const candle = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: '#21c07a', downColor: '#f5455f', wickUpColor: '#21c07a', wickDownColor: '#f5455f', borderVisible: false,
  });
  candle.setData(p.bars);
  const vol = chart.addSeries(LightweightCharts.HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' });
  vol.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  vol.setData(p.bars.map(b => ({ time: b.time, value: b.volume,
    color: b.close >= b.open ? 'rgba(33,192,122,0.45)' : 'rgba(245,69,95,0.45)' })));
  addVwapBands(chart, candle, p.vwap);
  chart.timeScale().fitContent();
}

// ---- Extra panel: candles + CVD/delta pane + large-trade markers + tables
function renderExtraPanel(host, p) {
  const i = p.info;
  const pmRows = p.per_minute.map(r => `
    <tr><td>${r.time}</td><td>${r.trades}</td><td>${r.vol}</td>
    <td class="pos">${r.buy ?? '—'}</td><td class="neg">${r.sell ?? '—'}</td>
    <td class="${(r.delta??0)>=0?'pos':'neg'}">${r.delta ?? '—'}</td><td>${r.vwap ?? '—'}</td></tr>`).join('');
  const ltRows = p.large_trades.length ? p.large_trades.map(r => `
    <tr><td>${r.time}</td><td>${r.price}</td><td>${r.size}</td>
    <td class="${r.side==='buy'?'pos':r.side==='sell'?'neg':'muted'}">${r.side}</td>
    <td class="muted">${r.flags}</td></tr>`).join('') : `<tr><td colspan="5" class="muted">none</td></tr>`;
  const sec = document.createElement('section');
  sec.className = 'panel';
  sec.innerHTML = `
    <h2>${i.title}</h2>
    <div class="sub">${i.ticks_per_bar}-tick candles · VWAP @ 09:30 + ±1σ/±2σ/±3σ · CVD pane (buy−sell aggressor, 09:30 reset) · large-trade markers (size ≥ ${i.large_threshold})</div>
    <div class="info">${infoCells(i, [['symbol',i.symbol],['bars',i.bars],['trades',i.trades],
      ['range', i.price_low+' - '+i.price_high],['VWAP', i.vwap_start+' -> '+i.vwap_end],
      ['bands(end)', i.bands_end],['buy vol', i.buy_vol],['sell vol', i.sell_vol],
      ['CVD(end)', i.cvd_end],['large trades', i.large_trades]])}</div>
    <div class="chart"></div>
    <div class="legend">
      <span><span class="swatch" style="background:#21c07a"></span>up</span>
      <span><span class="swatch" style="background:#f5455f"></span>down</span>
      <span><span class="swatch" style="background:#a78bfa"></span>VWAP</span>
      <span><span class="swatch" style="background:#8b5cf6"></span>±1σ</span>
      <span><span class="swatch" style="background:#5b21b6"></span>±2σ/±3σ shaded</span>
      <span><span class="swatch" style="background:#3b82f6"></span>CVD</span>
      <span><span class="swatch" style="background:#21c07a"></span>+delta</span>
      <span><span class="swatch" style="background:#f5455f"></span>−delta</span>
    </div>
    <div class="tables">
      <div class="tbl-card"><h3>Per-minute trade count &amp; delta</h3><div class="scroll"><table>
        <thead><tr><th>time</th><th>trades</th><th>vol</th><th>buy</th><th>sell</th><th>delta</th><th>vwap</th></tr></thead>
        <tbody>${pmRows}</tbody></table></div></div>
      <div class="tbl-card"><h3>Large trades (size ≥ ${i.large_threshold}, sorted by size)</h3><div class="scroll"><table>
        <thead><tr><th>time</th><th>price</th><th>size</th><th>side</th><th>flags</th></tr></thead>
        <tbody>${ltRows}</tbody></table></div></div>
    </div>`;
  host.appendChild(sec);
  const el = sec.querySelector('.chart');
  const chart = LightweightCharts.createChart(el, {
    width: el.clientWidth, height: 560,
    layout: { background: { type: LightweightCharts.ColorType.Solid, color: '#0b0e13' },
              textColor: '#cbd5e1', fontFamily: 'Inter, sans-serif' },
    grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
    rightPriceScale: { borderColor: '#1c2230' },
    timeScale: { borderColor: '#1c2230', timeVisible: true, secondsVisible: true, rightOffset: 4 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  charts.extra.push({ chart, el });
  const candle = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: '#21c07a', downColor: '#f5455f', wickUpColor: '#21c07a', wickDownColor: '#f5455f', borderVisible: false,
  });
  candle.setData(p.bars);
  const vol = chart.addSeries(LightweightCharts.HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' });
  vol.priceScale().applyOptions({ scaleMargins: { top: 0.80, bottom: 0 } });
  vol.setData(p.bars.map(b => ({ time: b.time, value: b.volume,
    color: b.close >= b.open ? 'rgba(33,192,122,0.40)' : 'rgba(245,69,95,0.40)' })));

  // VWAP @ 09:30 cash open + purple ±1σ/±2σ/±3σ bands (±2σ-±3σ shaded).
  addVwapBands(chart, candle, p.vwap);

  // Pane 1: per-bar delta histogram + CVD line.
  const delta = chart.addSeries(LightweightCharts.HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' }, 1);
  delta.priceScale().applyOptions({ scaleMargins: { top: 0.5, bottom: 0 } });
  delta.setData(p.delta);
  const cvd = chart.addSeries(LightweightCharts.LineSeries, { color: '#3b82f6', lineWidth: 2, priceLineVisible: false, lastValueVisible: true }, 1);
  cvd.setData(p.cvd);
  const panes = chart.panes();
  if (panes.length > 1) { panes[0].setStretchFactor(1000); panes[1].setStretchFactor(420); }

  if (p.markers.length) LightweightCharts.createSeriesMarkers(candle, p.markers);
  chart.timeScale().fitContent();
}

function renderTab(which) {
  const host = document.getElementById('tab-' + which);
  if (rendered[which]) {
    charts[which].forEach(c => c.chart.applyOptions({ width: c.el.clientWidth }));
    return;
  }
  rendered[which] = true;
  TABDATA[which].forEach(p => TABRENDER[which](host, p));
}

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const which = btn.dataset.tab;
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tabpane').forEach(p => p.classList.remove('active'));
    document.getElementById('tab-' + which).classList.add('active');
    renderTab(which);
  });
});

renderTab('basic');
window.addEventListener('resize', () => {
  ['basic','extra','extra250'].forEach(w => charts[w].forEach(c => c.chart.applyOptions({ width: c.el.clientWidth })));
});
</script>
</body>
</html>
"""
    html = (
        template
        .replace("__BASIC_JSON__", json.dumps(basic))
        .replace("__EXTRA_JSON__", json.dumps(extra))
        .replace("__EXTRA250_JSON__", json.dumps(extra250))
    )
    HTML_OUT.write_text(html)


if __name__ == "__main__":
    main()
