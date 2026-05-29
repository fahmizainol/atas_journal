"""Server-side chart payload builders for the candlestick views.

Keeps VWAP session-anchoring and the dollar-PnL derivation on the server (the
client just draws). Times are emitted as epoch-seconds of the *naive-local*
instant (the ``_to_local`` trick from charts.py) so lightweight-charts reads the
axis in the display tz and weekend/overnight gaps collapse.
"""

from __future__ import annotations

import pandas as pd

from journal import charts, excursion
from journal import databento_client as dbn
from journal.config import ET_TZ

_RULE = {"1m": "1min", "5m": "5min", "15m": "15min"}

# Palette (mirrors charts.py / theme.ts) for marker + line colors.
GREEN = "#21c07a"
RED = "#f5455f"
BLUE = "#3b82f6"
ORANGE = "#f97316"
ACCENT = "#6c5ce7"
GOLD = "#e0a52a"


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


def _vwap_rows(plot_bars: pd.DataFrame, anchor_bars: pd.DataFrame | None, tz) -> list[dict]:
    """Session VWAP ±1σ band. Anchored at the 18:00 ET Globex open, computed
    over *anchor_bars* (full session) then restricted to *plot_bars* timestamps.
    Ports charts._vwap_series."""
    src = anchor_bars if anchor_bars is not None else plot_bars
    typ = (src["high"] + src["low"] + src["close"]) / 3
    vol = src["volume"].astype(float)
    et = pd.to_datetime(src["ts_utc"], utc=True).dt.tz_convert(ET_TZ)
    session = (et - pd.Timedelta(hours=18)).dt.date
    cum = vol.groupby(session).cumsum().where(lambda c: c != 0)
    vwap = (typ * vol).groupby(session).cumsum() / cum
    var = (typ * typ * vol).groupby(session).cumsum() / cum - vwap**2
    std = var.clip(lower=0) ** 0.5
    df = pd.DataFrame({
        "ts_utc": pd.to_datetime(src["ts_utc"], utc=True),
        "upper": vwap + std, "middle": vwap, "lower": vwap - std,
    }).dropna(subset=["middle"])
    if anchor_bars is not None:
        window = pd.to_datetime(plot_bars["ts_utc"], utc=True)
        df = df[df["ts_utc"].isin(window)]
    times = _epoch_local(df["ts_utc"], tz)
    return [
        {"time": int(t), "upper": float(u), "middle": float(m), "lower": float(low)}
        for t, u, m, low in zip(times, df["upper"], df["middle"], df["lower"])
    ]


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
    start_utc, end_utc = charts.adaptive_window(entry_utc, exit_utc)
    bars = dbn.get_bars(instrument, start_utc, end_utc, slice_to_window=True)
    if bars is None or bars.empty:
        return {"available": True, "bars": []}

    sess_open = charts.session_open_utc(entry_utc)
    sess_bars = dbn.get_bars(instrument, sess_open.to_pydatetime(), end_utc,
                             slice_to_window=True)
    rule = _RULE.get(tf, "1min")
    pbars = charts.resample_ohlc(bars, rule)
    psess = charts.resample_ohlc(sess_bars, rule) if sess_bars is not None else None

    exc = excursion.trade_excursion(trade)
    markers = sorted(
        _fill_markers(trade.get("fills"), tz) + _excursion_markers(exc, tz),
        key=lambda m: m["time"],
    )
    payload = {
        "available": True,
        "bars": _bars_rows(pbars, tz),
        "vwap": _vwap_rows(pbars, psess, tz),
        "markers": markers,
        "price_lines": _price_lines(trade),
        "trade_rect": _trade_rect(trade, tz),
    }
    if exc:
        payload["excursion"] = {k: v for k, v in exc.items() if k != "bars"}
    return payload


def day_chart(day_trades: pd.DataFrame, instrument: str, bars: pd.DataFrame, tz) -> dict:
    """Composite full-day session payload: candles + VWAP + volume + per-trade
    fills and holding rectangles."""
    fills = [tr["fills"] for _, tr in day_trades.iterrows() if tr.get("fills")]
    flat = [f for sub in fills for f in sub]
    markers = sorted(_fill_markers(flat, tz), key=lambda m: m["time"])
    rects = []
    for _, tr in day_trades.iterrows():
        rect = _trade_rect(tr, tz)
        if rect is not None:
            rects.append(rect)
    return {
        "available": True,
        "instrument": instrument,
        "bars": _bars_rows(bars, tz),
        "vwap": _vwap_rows(bars, None, tz),
        "markers": markers,
        "trades": rects,
    }


def bars_window(instrument: str, start_utc, end_utc, tf: str, tz) -> dict:
    """Plain resampled bars for a UTC window (timeframe-radio refetch)."""
    if not dbn.is_available():
        return {"available": False}
    bars = dbn.get_bars(instrument, start_utc, end_utc, slice_to_window=True)
    if bars is None or bars.empty:
        return {"available": True, "bars": []}
    pbars = charts.resample_ohlc(bars, _RULE.get(tf, "1min"))
    return {"available": True, "bars": _bars_rows(pbars, tz)}
