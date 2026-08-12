"""Chart payloads for simulated trades.

Emits the same shape as ``charts_data.trade_chart`` (TradeChartData) so the
existing CandlestickChart renders a sim trade with no frontend changes.

The session underneath — bars, both anchored VWAPs, both developing profiles, the
IB, EMA/RSI/ATR, footprint and CVD — is built by ``api.session_chart``, which is
this module's own ``_session_frame`` with the strategy factored out of it. What
stays here is the only part that was ever about a *run*: which anchor the engine
traded, where its trades went, and the numbers its tooltip shows.

The sigma on those bands is tick-derived, and that is the point: the engine
trades tick-derived sigma, and drawing one while trading the other would make
the chart useless for the only thing it is for — confirming the engine fired
where you would have.
"""

from __future__ import annotations

import pandas as pd

from journal.config import point_value, tick_size
from journal.sim import registry, store
from journal.sim import ticks as tickmod

from . import session_chart
from .charts_data import ACCENT, BLUE, GOLD, GREEN, ORANGE, RED
from .session_chart import (  # re-exported: the names this module used to own
    DIV_ZZ_TICKS,
    SessionFrame,
    _footprint,
    _lead_bars,
    _post_bars,
    _profile_slots,
    _utc,
    vwap_slots,
)

__all__ = ["sim_trade_chart", "sim_day_chart", "DIV_ZZ_TICKS", "SessionFrame"]


def _is_globex(slug: str) -> bool:
    return registry.get(slug).session == "globex"


def _session_frame(cfg, day, tz, overnight: bool = False, resolution: str = "tick",
                   div_ticks: int | None = None) -> SessionFrame | None:
    """``session_chart.session_frame`` with a run's config read for it.

    The three things the session builder needs that a config happens to carry.
    ``allow_fetch`` is left on: a run is where ticks get paid for, and a chart of
    a run may legitimately buy the session it is charting. The journal's charts
    are the other case and pass it off.
    """
    return session_chart.session_frame(
        contract=cfg.contract,
        day=day,
        tz=tz,
        ticks_per_bar=cfg.ticks_per_bar,
        overnight=overnight,
        resolution=resolution,
        div_ticks=div_ticks,
    )


def _markers(tr, entry_t: int, exit_t: int, acc_t: int | None, text: bool) -> list[dict]:
    """Acceptance / entry / exit marks, oriented by the trade's direction.

    A short's arrows point the other way and sit on the other side of the candle:
    an up-arrow under the bar for a sell would read as a buy at a glance, which is
    exactly the thing you are looking at this chart to check.
    """
    short = str(tr.get("direction", "Long")) == "Short"
    entry_side = "aboveBar" if short else "belowBar"
    exit_side = "belowBar" if short else "aboveBar"

    out: list[dict] = []
    if acc_t is not None:
        out.append({"time": acc_t, "position": entry_side, "shape": "circle",
                    "color": GREEN, **({"text": "acceptance"} if text else {})})
    out.append({
        "time": entry_t, "position": entry_side,
        "shape": "arrowDown" if short else "arrowUp", "color": BLUE,
        **({"text": f"entry {tr['avg_entry']:.2f}"} if text else {}),
    })
    out.append({
        "time": exit_t, "position": exit_side,
        "shape": "arrowUp" if short else "arrowDown", "color": ORANGE,
        **({"text": f"{tr['exit_reason']} {tr['avg_exit']:.2f}"} if text else {}),
    })
    return out




def sim_trade_chart(slug: str, run_id: str, trade_no: int, tz, resolution: str = "tick",
                    div_ticks: int | None = None) -> dict:
    r = store.read_run(slug, run_id)
    if r is None:
        return {"available": False}
    cfg_json, trades, _ = r
    if trades.empty:
        return {"available": False}
    row = trades[trades["trade_no"] == trade_no]
    if row.empty:
        return {"available": False}
    trade = row.iloc[0]

    cfg = store.config_from_json(cfg_json, registry.get(slug).config_cls)
    globex = _is_globex(slug)
    entry_ts, exit_ts = _utc(trade["entry_ts_utc"]), _utc(trade["exit_ts_utc"])
    day = entry_ts.tz_convert("America/New_York").date()

    frame = _session_frame(cfg, day, tz, overnight=globex, resolution=resolution,
                           div_ticks=div_ticks)
    if frame is None:
        return {"available": False}
    t, bar_time = frame.ticks, frame.bar_time

    entry_t, exit_t = bar_time(entry_ts), bar_time(exit_ts)

    acc_t = bar_time(trade["acceptance_ts"]) if pd.notna(trade.get("acceptance_ts")) else None
    markers = _markers(trade, entry_t, exit_t, acc_t, text=True)

    price_lines = [
        {"price": float(trade["avg_entry"]), "color": ACCENT,
         "title": f"entry {trade['avg_entry']:.2f}"},
        {"price": float(trade["avg_exit"]), "color": GOLD,
         "title": f"exit {trade['avg_exit']:.2f}"},
        {"price": float(trade["stop_price"]), "color": RED,
         "title": f"stop {trade['stop_price']:.2f} ({cfg.stop_ticks}t)"},
    ]

    # Where the trail had ratcheted the stop by the exit. Only drawn when it
    # actually moved — on an untrailed run it would sit exactly under the stop
    # line and read as a rendering bug. Older runs predate the column entirely.
    final_stop = trade.get("final_stop_price")
    if final_stop is not None and pd.notna(final_stop) and float(final_stop) != float(trade["stop_price"]):
        price_lines.append(
            {"price": float(final_stop), "color": GOLD,
             "title": f"trailed stop {float(final_stop):.2f}"}
        )

    # MFE/MAE straight off the ticks held. journal.excursion can't be reused: it
    # re-fetches 1m bars for a *continuous* symbol, and would measure excursion
    # at minute granularity against a series the engine never saw.
    held = t[(t["ts_utc"] >= entry_ts) & (t["ts_utc"] <= exit_ts)]
    excursion = None
    if not held.empty:
        e = float(trade["avg_entry"])
        usd = point_value(cfg.instrument) * int(trade["max_contracts"])
        # Signed by direction: a short's favourable excursion is the LOW it saw.
        sgn = -1.0 if str(trade.get("direction", "Long")) == "Short" else 1.0
        best = float(held["price"].max() if sgn > 0 else held["price"].min())
        worst = float(held["price"].min() if sgn > 0 else held["price"].max())
        excursion = {
            "mfe_usd": sgn * (best - e) * usd,
            "mae_usd": sgn * (worst - e) * usd,
            "exit_efficiency": None,
            "avg_atr_pts": None,
            "avg_atr_usd": None,
        }

    return {
        "available": True,
        "bars": frame.bars,
        **vwap_slots(frame.vwap_globex, frame.vwap_ny, frame.vwap_weekly,
                     "globex" if globex else "ny"),
        **_profile_slots(frame.profile_globex, frame.profile_ny),
        "ema9": frame.ema9,
        "ema20": frame.ema20,
        "ema50": frame.ema50,
        "ema200": frame.ema200,
        "rsi": frame.rsi,
        "atr_points": frame.atr_points,
        "markers": markers,
        "price_lines": price_lines,
        "levels": [],
        "ib": frame.ib,
        "trade_rect": _trade_rect(trade, entry_t, exit_t, tz, cfg),
        "excursion": excursion,
        "instrument": tickmod.contract_for(cfg.contract, day),
        "footprint": frame.footprint,
        "cvd": frame.cvd,
        "cvd_divergences": frame.cvd_divergences,
        "tick_size": tick_size(cfg.contract),
        "point_value": point_value(cfg.contract),
    }


def _trade_rect(tr, entry_t: int, exit_t: int, tz, cfg) -> dict:
    """TradeRect plus the stats the hover tooltip shows. Tooltip times use the
    display tz so they match the chart axis, not the table's fixed ET.
    stop_ticks is derived from the row's own prices (not cfg.stop_ticks) so the
    tooltip stays honest even if the config knob changes between runs."""
    entry_ts, exit_ts = _utc(tr["entry_ts_utc"]), _utc(tr["exit_ts_utc"])
    tick = tick_size(cfg.instrument)
    return {
        "entry_time": entry_t, "exit_time": exit_t,
        "entry_price": float(tr["avg_entry"]),
        "exit_price": float(tr["avg_exit"]),
        "net_pnl": float(tr["net_pnl"]),
        "profitable": bool(tr["net_pnl"] >= 0),
        "stats": {
            "trade_no": int(tr["trade_no"]),
            "entry_hms": entry_ts.tz_convert(tz).strftime("%H:%M:%S"),
            "exit_hms": exit_ts.tz_convert(tz).strftime("%H:%M:%S"),
            "duration_s": float((exit_ts - entry_ts).total_seconds()),
            "avg_entry": float(tr["avg_entry"]),
            "avg_exit": float(tr["avg_exit"]),
            "stop_price": float(tr["stop_price"]),
            # Distance, not offset: a short's stop sits above its entry.
            "stop_ticks": float(abs(tr["avg_entry"] - tr["stop_price"]) / tick),
            "exit_reason": str(tr["exit_reason"]),
            "r_multiple": float(tr["r_multiple"]),
            "band_width_ticks": float(tr["band_width_ticks"]),
        },
    }


def sim_day_chart(slug: str, run_id: str, day, tz, resolution: str = "tick",
                  div_ticks: int | None = None) -> dict:
    """Whole-session chart with every trade of that day drawn at once.

    Deliberately sparser per trade than the single-trade view: rect + marker
    shapes only — no marker text, no entry/exit/stop price lines. Five trades'
    worth of labels and full-width dashed lines would drown the chart; the hover
    tooltip carries those numbers instead.
    """
    r = store.read_run(slug, run_id)
    if r is None:
        return {"available": False}
    cfg_json, trades, _ = r
    cfg = store.config_from_json(cfg_json, registry.get(slug).config_cls)
    globex = _is_globex(slug)

    frame = _session_frame(cfg, day, tz, overnight=globex, resolution=resolution,
                           div_ticks=div_ticks)
    if frame is None:
        return {"available": False}
    bar_time = frame.bar_time

    day_trades = trades
    if not trades.empty:
        ny_date = pd.to_datetime(trades["entry_ts_utc"], utc=True).dt.tz_convert(
            "America/New_York").dt.date
        day_trades = trades[ny_date == day]

    markers: list[dict] = []
    rects: list[dict] = []
    for _, tr in day_trades.iterrows():
        entry_t, exit_t = bar_time(tr["entry_ts_utc"]), bar_time(tr["exit_ts_utc"])
        acc_t = bar_time(tr["acceptance_ts"]) if pd.notna(tr.get("acceptance_ts")) else None
        markers.extend(_markers(tr, entry_t, exit_t, acc_t, text=False))
        rects.append(_trade_rect(tr, entry_t, exit_t, tz, cfg))

    return {
        "available": True,
        "instrument": tickmod.contract_for(cfg.contract, day),
        "bars": frame.bars,
        **vwap_slots(frame.vwap_globex, frame.vwap_ny, frame.vwap_weekly,
                     "globex" if globex else "ny"),
        **_profile_slots(frame.profile_globex, frame.profile_ny),
        "ema9": frame.ema9,
        "ema20": frame.ema20,
        "ema50": frame.ema50,
        "ema200": frame.ema200,
        "rsi": frame.rsi,
        "atr_points": frame.atr_points,
        "markers": markers,
        "levels": [],
        "ib": frame.ib,
        "trades": rects,
        "footprint": frame.footprint,
        "cvd": frame.cvd,
        "cvd_divergences": frame.cvd_divergences,
        "tick_size": tick_size(cfg.contract),
        "point_value": point_value(cfg.contract),
    }
