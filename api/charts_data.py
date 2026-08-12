"""Server-side chart payload builders for the journal's candlestick views.

Keeps VWAP session-anchoring and the dollar-PnL derivation on the server (the
client just draws). Times are emitted as epoch-seconds of the *naive-local*
instant so lightweight-charts reads the axis in the display tz and
weekend/overnight gaps collapse.

**The session comes from ``api.session_chart`` now (2026-08-08), the same
builder the Lab's charts use, off the same tick cache.** Before, this module
built a thinner picture from Databento ohlcv-1m: bars, two VWAPs and an ATR, and
nothing that needs the tape. A journal chart now carries what a Lab chart
carries — both developing profiles, the IB, EMA 9/20/50/200, RSI, CVD with its
divergences, an exact footprint — because the difference between the two was
never a difference of *kind*, only of what the data source could support.

What stays here is the half that is about **your** trades rather than the
session: the fill markers, MAE/MFE, the holding rectangle, the entry/exit price
lines and the Tier-A levels. That is the same split ``api.sim_charts`` keeps for
a run's trades.

The functions below still take a trade row and a timeframe and return the same
keys they did, so the frontend's TradeChartData/DayChartData contracts hold; the
payload only gained fields.
"""

from __future__ import annotations

import pandas as pd

from journal import excursion
from journal import levels as levels_mod
from journal import tick_bars
from journal.config import point_value, tick_size

from . import session_chart
from .session_chart import _epoch_local, _profile_slots, vwap_slots

_RULE = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min"}

#: How a timeframe key reaches ``session_chart.session_frame``. The minute
#: ladder is the journal's own; ``500t`` is the tick-bar option the research
#: benches already offer and the journal could not, having had no ticks. There
#: is no engine here to inherit a native bar size from, so 500 is the shared
#: default rather than a per-run number.
_RESOLUTION = {**_RULE, "500t": "tick"}

# The VWAP anchoring, the sigma bands and the ATR that used to be re-derived here
# from minute bars now come from ``session_chart``, off the ticks — one
# implementation instead of two that could disagree about where a band was.
# ``journal.sim.vwap`` documents why the tick-derived sigma is the one to keep.

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


def _trade_rect(trade: pd.Series, tz, bar_time=None) -> dict | None:
    """The holding rectangle. ``bar_time`` snaps its corners onto the drawn bar
    grid, which matters once the candles can be tick bars: a raw wall-clock edge
    would land between two candles that span an uneven slice of time, and the
    rectangle would start half a bar away from the fill it describes."""
    if pd.isna(trade["avg_entry"]) or pd.isna(trade["avg_exit"]):
        return None
    stamp = bar_time or (lambda ts: int(_epoch_local(ts, tz)))
    entry_t = int(stamp(trade["entry_ts_utc"]))
    exit_t = int(stamp(trade["exit_ts_utc"]))
    # A trade that opened and closed inside one candle snaps to a zero-width
    # rectangle, which draws as nothing at all — so a 30-second scalp would
    # vanish from its own chart. Give it the candle it happened in.
    if exit_t <= entry_t:
        exit_t = entry_t + 1
    return {
        "entry_time": entry_t,
        "exit_time": exit_t,
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



def _near_levels(rows: list[dict], bars: list[dict], margin: float = 0.10) -> list[dict]:
    """Keep only levels inside the visible candle range (+margin), so a far level
    can't force the trade-chart price scale to zoom out and squish the candles.

    Takes the drawn bar *rows* rather than a frame: the session builder hands
    back rows, and a level is judged against what is actually on screen.
    """
    if not rows or not bars:
        return []
    lo = min(b["low"] for b in bars)
    hi = max(b["high"] for b in bars)
    pad = (hi - lo) * margin
    lo -= pad
    hi += pad
    return [r for r in rows if lo <= r["price"] <= hi]


def _session_for(trade_or_instrument, day, tf: str, tz):
    """The session a journal chart draws, off the tick cache and never buying.

    ``allow_fetch=False`` is the whole difference from the Lab's call: these are
    GETs behind the Trades page, and a chart must never be the thing that spends
    money at Databento. A session that was never bought comes back None and the
    caller says so plainly.
    """
    return session_chart.session_frame(
        contract=trade_or_instrument,
        day=day,
        tz=tz,
        resolution=_RESOLUTION.get(tf, "1min"),
        allow_fetch=False,
    )


def _no_ticks(instrument: str, day) -> dict:
    """The honest empty state. The Databento path used to return an empty bar
    list for a swallowed 402, which read on screen as "this session had no
    trades" — indistinguishable from missing data. Saying which session and
    which contract is missing is the difference between a bug report and a
    one-line answer."""
    return {
        "available": True,
        "bars": [],
        "instrument": instrument,
        "reason": f"no ticks cached for {instrument} on {day}",
    }


def _session_payload(frame, instrument: str) -> dict:
    """Everything a chart draws that is about the session rather than the trade.

    The same slots ``sim_charts`` fills, so one CandlestickChart renders either.
    ``vwap_anchor`` is "ny" because a journal chart has no engine — nothing here
    *traded* an anchor, and the RTH session is what the day is read against.
    """
    return {
        "bars": frame.bars,
        **vwap_slots(frame.vwap_globex, frame.vwap_ny, frame.vwap_weekly, "ny"),
        **_profile_slots(frame.profile_globex, frame.profile_ny),
        "ema9": frame.ema9,
        "ema20": frame.ema20,
        "ema50": frame.ema50,
        "ema200": frame.ema200,
        "rsi": frame.rsi,
        "atr_points": frame.atr_points,
        "ib": frame.ib,
        "footprint": frame.footprint,
        "cvd": frame.cvd,
        "cvd_divergences": frame.cvd_divergences,
        # The contract the roll resolved, not the export's stale label — so the
        # chart header names the contract whose ticks are actually drawn.
        "instrument": frame.symbol or instrument,
        "tick_size": tick_size(instrument),
        "point_value": point_value(instrument),
    }


def excursion_summary(trade: pd.Series) -> dict:
    """trade_excursion minus the heavy ``bars`` frame, or {available:false}."""
    if not tick_bars.is_available():
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
    if not tick_bars.is_available():
        return {"available": False}

    instrument = trade["instrument"]
    entry_utc = trade["entry_ts_utc"]
    # The ET session the trade belongs to — not the calendar date of its UTC
    # stamp, which for an overnight fill is the next day.
    day = levels_mod.rth_date_for(entry_utc)

    frame = _session_for(instrument, day, tf, tz)
    if frame is None:
        return _no_ticks(instrument, day)

    exc = excursion.trade_excursion(trade)
    markers = sorted(
        _fill_markers(trade.get("fills"), tz) + _excursion_markers(exc, tz),
        key=lambda m: m["time"],
    )
    lv = levels_mod.compute_levels(instrument, day)
    payload = {
        "available": True,
        **_session_payload(frame, instrument),
        "markers": markers,
        "price_lines": _price_lines(trade),
        "levels": _near_levels(_levels_rows(lv), frame.bars),
        "trade_rect": _trade_rect(trade, tz, frame.bar_time),
    }
    if exc:
        payload["excursion"] = {k: v for k, v in exc.items() if k != "bars"}
    return payload


def day_chart(day_df: pd.DataFrame, day, tf: str, tz) -> dict:
    """Composite full-day session payload: candles + VWAP + volume + per-trade
    fills and holding rectangles.

    The session builder already draws from the 18:00 ET Globex open, so the
    VWAP band accumulates over the whole session and the night is on screen —
    which is what the old path went out of its way to load a day early for.
    """
    if not tick_bars.is_available():
        return {"available": False}
    instrument = day_df["instrument"].value_counts().idxmax()

    frame = _session_for(instrument, day, tf, tz)
    if frame is None:
        return _no_ticks(instrument, day)

    fills = [tr["fills"] for _, tr in day_df.iterrows() if tr.get("fills")]
    flat = [f for sub in fills for f in sub]
    markers = sorted(_fill_markers(flat, tz), key=lambda m: m["time"])
    rects = [r for _, tr in day_df.iterrows()
             if (r := _trade_rect(tr, tz, frame.bar_time)) is not None]
    lv = levels_mod.compute_levels(instrument, day)
    return {
        "available": True,
        **_session_payload(frame, instrument),
        "markers": markers,
        "levels": _levels_rows(lv),
        "trades": rects,
    }


def bars_window(instrument: str, start_utc, end_utc, tf: str, tz) -> dict:
    """Plain resampled bars for a UTC window (timeframe-radio refetch)."""
    if not tick_bars.is_available():
        return {"available": False}
    bars = tick_bars.get_bars(instrument, start_utc, end_utc, slice_to_window=True)
    if bars is None or bars.empty:
        return {"available": True, "bars": []}
    pbars = resample_ohlc(bars, _RULE.get(tf, "1min"))
    return {"available": True, "bars": _bars_rows(pbars, tz)}
