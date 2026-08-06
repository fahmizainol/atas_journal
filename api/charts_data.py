"""Server-side chart payload builders for the candlestick views.

Keeps VWAP session-anchoring and the dollar-PnL derivation on the server (the
client just draws). Times are emitted as epoch-seconds of the *naive-local*
instant (the ``_to_local`` trick from charts.py) so lightweight-charts reads the
axis in the display tz and weekend/overnight gaps collapse.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pandas as pd

from journal import excursion
from journal import levels as levels_mod
from journal import databento_client as dbn
from journal.atr import atr_series
from journal.config import ET_TZ, point_value, tick_size

_RULE = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min"}
_ATR_PERIOD = 14
# Extra resampled bars pulled *before* the plotted window so Wilder's ATR has
# converged by the time the visible candles start (period + smoothing margin).
_ATR_LOOKBACK_BARS = 30

# The two VWAP anchors, as ET wall-clock opens. Each starts a fresh accumulation
# that runs until the next one 24h later (so the NY VWAP keeps extending through
# the evening rather than terminating at the cash close).
VWAP_ANCHORS = {"globex": time(18, 0), "ny": time(9, 30)}
# A VWAP group is only honest if the loaded bars actually reach back to its
# anchor. Bars starting at the Globex open, say, join the *prior* day's 09:30
# group mid-flight — accumulating from 18:00 would draw a band that never
# existed. Groups whose first bar lags the anchor by more than this are dropped.
_ANCHOR_TOLERANCE = pd.Timedelta(minutes=20)

# Palette (mirrors charts.py / theme.ts) for marker + line colors.
GREEN = "#21c07a"
RED = "#f5455f"
BLUE = "#3b82f6"
ORANGE = "#f97316"
ACCENT = "#6c5ce7"
GOLD = "#e0a52a"

# Session-level lines: distinct color per Tier-A level.
LEVEL_STYLE = {
    "onh": ("#8b5cf6", "ON high"),
    "onl": ("#8b5cf6", "ON low"),
    "prior_high": ("#ef4444", "PD high"),
    "prior_low": ("#22c55e", "PD low"),
    "prior_close": ("#94a3b8", "prior close ~16:15"),
    "today_open": ("#38bdf8", "open"),
}


# --- Pure session/resample helpers (ported from the deleted journal.charts) ---
def resample_ohlc(bars: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """Aggregate 1m bars to a coarser timeframe (e.g. '5min')."""
    if bars is None or bars.empty or rule in (None, "1min"):
        return bars
    df = bars.set_index("ts_utc")
    agg = df.resample(rule, label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    )
    return agg.dropna(subset=["open"]).reset_index()


def session_open_utc(ts_utc) -> pd.Timestamp:
    """UTC timestamp of the Globex session open (18:00 ET) containing `ts_utc`."""
    et = pd.Timestamp(ts_utc)
    et = et.tz_localize("UTC") if et.tzinfo is None else et.tz_convert("UTC")
    et = et.tz_convert(ET_TZ)
    sess_date = (et - pd.Timedelta(hours=18)).date()
    open_et = pd.Timestamp(datetime.combine(sess_date, time(18, 0)), tz=ET_TZ)
    return open_et.tz_convert("UTC")


def adaptive_window(entry_utc, exit_utc) -> tuple:
    """Pad each side by 2 hours so the trade has surrounding session context."""
    pad = timedelta(hours=2)
    return pd.Timestamp(entry_utc) - pad, pd.Timestamp(exit_utc) + pad


def _epoch_local(ts_utc, tz) -> pd.Series | int:
    """UTC instants -> epoch-seconds of the naive wall-clock in *tz*."""
    s = pd.to_datetime(ts_utc, utc=True)
    if isinstance(s, pd.Series):
        local = s.dt.tz_convert(tz).dt.tz_localize(None)
        # Unit-agnostic: source may be ns or us resolution, so floor to seconds.
        return local.astype("datetime64[s]").astype("int64")
    local = s.tz_convert(tz).tz_localize(None)
    return int(local.value // 10**9)


def _bars_rows(bars: pd.DataFrame, tz) -> list[dict]:
    t = _epoch_local(bars["ts_utc"], tz)
    out = []
    for time, o, h, low, c, v in zip(
        t, bars["open"], bars["high"], bars["low"], bars["close"], bars["volume"]
    ):
        out.append({
            "time": int(time), "open": float(o), "high": float(h),
            "low": float(low), "close": float(c), "volume": float(v),
        })
    return out


def _vwap_rows(
    plot_bars: pd.DataFrame,
    anchor_bars: pd.DataFrame | None,
    tz,
    anchor: str = "globex",
) -> list[dict]:
    """Anchored VWAP with ±1σ and ±2σ bands.

    *anchor* is a key of ``VWAP_ANCHORS`` — "globex" (18:00 ET) or "ny" (09:30
    ET). Accumulation restarts at each anchor and runs the full 24h until the
    next one. Computed over *anchor_bars* (the full session, so the band is
    right even when the visible window starts mid-session) then restricted to
    *plot_bars* timestamps.
    """
    src = anchor_bars if anchor_bars is not None else plot_bars
    if src is None or src.empty:
        return []
    open_t = VWAP_ANCHORS[anchor]
    off = pd.Timedelta(hours=open_t.hour, minutes=open_t.minute)

    # Wall-clock ET, so the anchor stays at 18:00/09:30 local across DST.
    et = pd.to_datetime(src["ts_utc"], utc=True).dt.tz_convert(ET_TZ).dt.tz_localize(None)
    session = (et - off).dt.date

    typ = (src["high"] + src["low"] + src["close"]) / 3
    vol = src["volume"].astype(float)
    cum = vol.groupby(session).cumsum().where(lambda c: c != 0)
    vwap = (typ * vol).groupby(session).cumsum() / cum
    var = (typ * typ * vol).groupby(session).cumsum() / cum - vwap**2
    std = var.clip(lower=0) ** 0.5

    df = pd.DataFrame({
        "ts_utc": pd.to_datetime(src["ts_utc"], utc=True),
        "middle": vwap,
        "upper1": vwap + std, "lower1": vwap - std,
        "upper2": vwap + 2 * std, "lower2": vwap - 2 * std,
    })
    # Drop groups the loaded bars joined late (see _ANCHOR_TOLERANCE).
    first_bar = et.groupby(session).transform("min")
    anchor_at = pd.to_datetime(pd.Index(session)) + off
    df = df[(first_bar - pd.Series(anchor_at, index=df.index)) <= _ANCHOR_TOLERANCE]
    df = df.dropna(subset=["middle"])

    if anchor_bars is not None:
        window = pd.to_datetime(plot_bars["ts_utc"], utc=True)
        df = df[df["ts_utc"].isin(window)]
    if df.empty:
        return []
    times = _epoch_local(df["ts_utc"], tz)
    return [
        {"time": int(t), "middle": float(m),
         "upper1": float(u1), "lower1": float(l1),
         "upper2": float(u2), "lower2": float(l2)}
        for t, m, u1, l1, u2, l2 in zip(
            times, df["middle"], df["upper1"], df["lower1"], df["upper2"], df["lower2"]
        )
    ]


def _atr_rows(plot_bars: pd.DataFrame, compute_bars: pd.DataFrame | None, tz) -> list[dict]:
    """ATR(14) Wilder, computed over *compute_bars* (which can include a warmup
    lookback before the plotted window) and restricted to *plot_bars* timestamps
    so the sub-pane lines up under the visible candles."""
    src = compute_bars if compute_bars is not None else plot_bars
    if src is None or src.empty:
        return []
    atr = atr_series(src, _ATR_PERIOD)
    df = pd.DataFrame({"ts_utc": pd.to_datetime(src["ts_utc"], utc=True), "atr": atr}).dropna()
    if compute_bars is not None and not plot_bars.empty:
        window = pd.to_datetime(plot_bars["ts_utc"], utc=True)
        df = df[df["ts_utc"].isin(window)]
    if df.empty:
        return []
    times = _epoch_local(df["ts_utc"], tz)
    return [{"time": int(t), "atr": float(a)} for t, a in zip(times, df["atr"])]


def _fill_markers(fills, tz) -> list[dict]:
    if not isinstance(fills, list) or not fills:
        return []
    fdf = pd.DataFrame(fills)
    times = _epoch_local(fdf["ts_utc"], tz)
    out = []
    for t, d in zip(times, fdf["direction"]):
        if d == "Buy":
            out.append({"time": int(t), "position": "belowBar",
                        "shape": "arrowUp", "color": BLUE})
        else:
            out.append({"time": int(t), "position": "aboveBar",
                        "shape": "arrowDown", "color": ORANGE})
    return out


def _excursion_markers(exc: dict | None, tz) -> list[dict]:
    if not exc:
        return []
    mfe_t = _epoch_local(exc["mfe_time"], tz)
    mae_t = _epoch_local(exc["mae_time"], tz)
    return [
        {"time": int(mfe_t), "position": "aboveBar", "shape": "circle",
         "color": GREEN, "text": "MFE"},
        {"time": int(mae_t), "position": "belowBar", "shape": "circle",
         "color": RED, "text": "MAE"},
    ]


def _trade_rect(trade: pd.Series, tz) -> dict | None:
    if pd.isna(trade["avg_entry"]) or pd.isna(trade["avg_exit"]):
        return None
    return {
        "entry_time": int(_epoch_local(trade["entry_ts_utc"], tz)),
        "exit_time": int(_epoch_local(trade["exit_ts_utc"], tz)),
        "entry_price": float(trade["avg_entry"]),
        "exit_price": float(trade["avg_exit"]),
        "net_pnl": float(trade["net_pnl"]),
        "profitable": bool(trade["net_pnl"] >= 0),
    }


def _price_lines(trade: pd.Series) -> list[dict]:
    lines = []
    if pd.notna(trade["avg_entry"]):
        lines.append({"price": float(trade["avg_entry"]), "color": ACCENT,
                      "title": f"avg entry {trade['avg_entry']:.2f}"})
    if pd.notna(trade["avg_exit"]):
        lines.append({"price": float(trade["avg_exit"]), "color": GOLD,
                      "title": f"avg exit {trade['avg_exit']:.2f}"})
    return lines


def _levels_rows(lv: dict | None) -> list[dict]:
    """Map computed Tier-A levels to PriceLineSpec rows (skip missing ones)."""
    if not lv:
        return []
    rows = []
    for key, (color, label) in LEVEL_STYLE.items():
        price = lv.get(key)
        if price is None:
            continue
        rows.append({"price": float(price), "color": color,
                     "title": f"{label} {float(price):.2f}"})
    return rows


def _near_levels(rows: list[dict], plot_bars: pd.DataFrame, margin: float = 0.10) -> list[dict]:
    """Keep only levels inside the visible candle range (+margin), so a far level
    can't force the trade-chart price scale to zoom out and squish the candles."""
    if not rows or plot_bars is None or plot_bars.empty:
        return []
    lo = float(plot_bars["low"].min())
    hi = float(plot_bars["high"].max())
    pad = (hi - lo) * margin
    lo -= pad
    hi += pad
    return [r for r in rows if lo <= r["price"] <= hi]


def excursion_summary(trade: pd.Series) -> dict:
    """trade_excursion minus the heavy ``bars`` frame, or {available:false}."""
    if not dbn.is_available():
        return {"available": False}
    exc = excursion.trade_excursion(trade)
    if exc is None:
        return {"available": True, "has_data": False}
    out = {k: v for k, v in exc.items() if k != "bars"}
    out["available"] = True
    out["has_data"] = True
    return out


def trade_chart(trade: pd.Series, tf: str, tz) -> dict:
    """Composite single-trade reconstruction payload."""
    if not dbn.is_available():
        return {"available": False}

    instrument = trade["instrument"]
    entry_utc, exit_utc = trade["entry_ts_utc"], trade["exit_ts_utc"]
    start_utc, end_utc = adaptive_window(entry_utc, exit_utc)
    bars = dbn.get_bars(instrument, start_utc, end_utc, slice_to_window=True)
    if bars is None or bars.empty:
        return {"available": True, "bars": []}

    sess_open = session_open_utc(entry_utc)
    sess_bars = dbn.get_bars(instrument, sess_open.to_pydatetime(), end_utc,
                             slice_to_window=True)
    rule = _RULE.get(tf, "1min")
    pbars = resample_ohlc(bars, rule)
    psess = resample_ohlc(sess_bars, rule) if sess_bars is not None else None

    exc = excursion.trade_excursion(trade)
    markers = sorted(
        _fill_markers(trade.get("fills"), tz) + _excursion_markers(exc, tz),
        key=lambda m: m["time"],
    )
    lv = levels_mod.compute_levels(instrument, levels_mod.rth_date_for(entry_utc))
    payload = {
        "available": True,
        "bars": _bars_rows(pbars, tz),
        "vwap_globex": _vwap_rows(pbars, psess, tz, "globex"),
        "vwap_ny": _vwap_rows(pbars, psess, tz, "ny"),
        "atr_points": _atr_rows(pbars, psess, tz),
        "markers": markers,
        "price_lines": _price_lines(trade),
        "levels": _near_levels(_levels_rows(lv), pbars),
        "trade_rect": _trade_rect(trade, tz),
        "tick_size": tick_size(instrument),
        "point_value": point_value(instrument),
    }
    if exc:
        payload["excursion"] = {k: v for k, v in exc.items() if k != "bars"}
    return payload


def day_chart(day_df: pd.DataFrame, day, tf: str, tz) -> dict:
    """Composite full-day session payload: candles + VWAP + volume + per-trade
    fills and holding rectangles. Loads bars from the 18:00 ET Globex session
    open so the VWAP band accumulates over the whole session (matching ATAS)."""
    if not dbn.is_available():
        return {"available": False}
    instrument = day_df["instrument"].value_counts().idxmax()

    # Load from the *prior trading day's* Globex open (18:00 ET the evening before
    # it) so the prior cash session — where the PD high/low/close levels formed —
    # is on screen too, not just floating lines. Walk-back handles weekends.
    prior = levels_mod.prior_trading_date(instrument, day)
    load_from = (prior if prior is not None else day) - timedelta(days=1)
    sess_open = pd.Timestamp(datetime.combine(load_from, time(18, 0)), tz=ET_TZ)
    day_end = pd.Timestamp(datetime.combine(day, datetime.min.time()), tz=tz) + pd.Timedelta(days=1)
    bars = dbn.get_bars(instrument, sess_open.tz_convert("UTC").to_pydatetime(),
                        day_end.tz_convert("UTC").to_pydatetime())
    if bars is None or bars.empty:
        return {"available": True, "instrument": instrument, "bars": []}

    pbars = resample_ohlc(bars, _RULE.get(tf, "1min"))
    fills = [tr["fills"] for _, tr in day_df.iterrows() if tr.get("fills")]
    flat = [f for sub in fills for f in sub]
    markers = sorted(_fill_markers(flat, tz), key=lambda m: m["time"])
    rects = [r for _, tr in day_df.iterrows() if (r := _trade_rect(tr, tz)) is not None]
    lv = levels_mod.compute_levels(instrument, day)
    return {
        "available": True,
        "instrument": instrument,
        "bars": _bars_rows(pbars, tz),
        "vwap_globex": _vwap_rows(pbars, None, tz, "globex"),
        "vwap_ny": _vwap_rows(pbars, None, tz, "ny"),
        "atr_points": _atr_rows(pbars, None, tz),
        "markers": markers,
        "levels": _levels_rows(lv),
        "trades": rects,
        "tick_size": tick_size(instrument),
        "point_value": point_value(instrument),
    }


def bars_window(instrument: str, start_utc, end_utc, tf: str, tz) -> dict:
    """Plain resampled bars for a UTC window (timeframe-radio refetch)."""
    if not dbn.is_available():
        return {"available": False}
    bars = dbn.get_bars(instrument, start_utc, end_utc, slice_to_window=True)
    if bars is None or bars.empty:
        return {"available": True, "bars": []}
    pbars = resample_ohlc(bars, _RULE.get(tf, "1min"))
    return {"available": True, "bars": _bars_rows(pbars, tz)}
