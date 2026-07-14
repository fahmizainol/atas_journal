"""Chart payloads for simulated trades.

Emits the same shape as ``charts_data.trade_chart`` (TradeChartData) so the
existing CandlestickChart renders a sim trade with no frontend changes. What it
does NOT do is reuse ``charts_data._vwap_rows``: that derives sigma from 1-minute
bars, while the engine trades tick-derived sigma. Drawing one and trading the
other would make the chart useless for the only thing it is for — confirming the
engine fired where you would have.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from journal.config import point_value, tick_size
from journal.sim import bars as barmod
from journal.sim import profile as profmod
from journal.sim import registry, store
from journal.sim import ticks as tickmod
from journal.sim import vwap as vwapmod

from .charts_data import ACCENT, BLUE, GOLD, GREEN, ORANGE, RED, _epoch_local


def _is_globex(slug: str) -> bool:
    return registry.get(slug).session == "globex"


def _vwap_slots(gx_rows: list[dict], ny_rows: list[dict], globex: bool) -> dict:
    """Both anchors, each in the slot that names it — the frontend colours from
    the slot (``vwap_globex`` gray/white, ``vwap_ny`` purple) and gives each its
    own legend toggle. ``vwap_anchor`` says which of the two the engine actually
    traded; the other is context. The chart must never leave that ambiguous."""
    return {
        "vwap_globex": gx_rows,
        "vwap_ny": ny_rows,
        "vwap_anchor": "globex" if globex else "ny",
    }


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


def _utc(ts) -> pd.Timestamp:
    """Parquet round-trips can hand back naive stamps; the tick frame is always
    tz-aware, so normalise before any comparison."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _strictly_increasing(times: np.ndarray) -> np.ndarray:
    """lightweight-charts requires unique, ascending, second-resolution times.
    Two tick-bars can complete inside the same second in a burst, so push any
    collision forward by a second. Self-corrects at the next gap; the engine ran
    on true tick timestamps, this only nudges the display axis.
    """
    out = times.astype("int64").copy()
    for i in range(1, len(out)):
        if out[i] <= out[i - 1]:
            out[i] = out[i - 1] + 1
    return out


def _footprint(t: pd.DataFrame, b: pd.DataFrame, ts_size: float) -> list[list[list[float]]]:
    """Per-bar volume-at-price, straight off the tape: one [price, size] list per
    bar, aligned 1:1 with the bar rows.

    This is what lets the chart's volume profile be *exact*. The journal's charts
    can only spread a bar's volume across its high-low range (Databento 1m bars
    carry a single volume number), but the sim already holds the trades that made
    each bar, so the real distribution is a groupby away. Shipping it per bar
    rather than pre-aggregated is deliberate: the frontend's fixed-range tool
    profiles arbitrary sub-ranges as you drag, and summing these maps client-side
    keeps that exact and instant instead of a round-trip per mousemove.
    """
    n = len(b)
    price = t["price"].to_numpy()
    size = t["size"].to_numpy()
    # Bars are contiguous runs of ticks, so a tick's bar is where its index falls
    # relative to the bar end offsets. Trailing ticks (past the last full bar)
    # land at n and are dropped — they belong to no drawn bar.
    bar_of = np.searchsorted(b["end_idx"].to_numpy(), np.arange(len(t)), side="left")
    keep = bar_of < n

    df = pd.DataFrame({
        "bar": bar_of[keep],
        # Snap to the contract's tick grid: float prices off the wire can carry
        # representation noise, and two spellings of 20134.25 must be one level.
        "price": np.round(price[keep] / ts_size) * ts_size,
        "size": size[keep],
    })
    g = df.groupby(["bar", "price"], sort=True)["size"].sum().reset_index()

    out: list[list[list[float]]] = [[] for _ in range(n)]
    for bar, p, s in zip(g["bar"].to_numpy(), g["price"].to_numpy(), g["size"].to_numpy()):
        out[int(bar)].append([float(p), float(s)])
    return out


def _vwap_rows(w: pd.DataFrame, pos: np.ndarray, times: np.ndarray) -> list[dict]:
    """Sample a band frame at the tick positions where bars closed. ``pos`` is
    positional into *w*, so a negative entry (a bar that closed before this
    anchor even started) is simply not drawn."""
    rows = []
    for tm, p in zip(times, pos):
        if p < 0:
            continue
        r = w.iloc[int(p)]
        rows.append({
            "time": int(tm), "middle": float(r["mid"]),
            "upper1": float(r["upper1"]), "lower1": float(r["lower1"]),
            "upper2": float(r["upper2"]), "lower2": float(r["lower2"]),
        })
    return rows


def _lead_bars(on: pd.DataFrame, n: int) -> pd.DataFrame:
    """Overnight context candles, chunked **backwards** from the bell.

    The last overnight bar must end on the tick immediately before 09:30, so the
    night runs into the open with no gap. Counting forward from 18:00 instead
    would drop up to n-1 ticks as an unclosed tail — a hole right where the chart
    is most worth looking at. So the remainder is dropped at the *start* of the
    night (a partial first candle at 18:00, which is what a session's first
    candle always is) and the rest chunk cleanly into the open.

    Returned indices are positions back into ``on``.
    """
    off = len(on) % n
    lead = barmod.tick_bars(on.iloc[off:].reset_index(drop=True), n)
    if lead.empty:
        return lead
    return lead.assign(start_idx=lead["start_idx"] + off, end_idx=lead["end_idx"] + off)


def _session_frame(cfg, day, tz, overnight: bool = False):
    """One session's bars + both anchored VWAPs + display times, shared by the
    per-trade and full-day payloads. Returns
    (ticks, bars_rows, vwap_gx_rows, vwap_ny_rows, profile_rows, bar_time,
    footprint), or None if no data.

    Every strategy's chart shows the same thing: the overnight candles from 18:00
    ET, the RTH session, both anchored VWAPs, and the developing profile. What
    differs between strategies is only which of those the *engine* read, and the
    payload says so (``vwap_anchor``) rather than hiding a layer.

    The one thing that may never bend for the sake of a uniform picture: the
    candles an engine traded must be the candles you are shown. So the bars are
    built in two pieces, not one stream —

      * the engine's own bars, from its own tick frame (RTH-anchored for a
        session strategy, 18:00-anchored for a Globex one). Bit-for-bit the bars
        whose closes armed, entered and invalidated the trade.
      * for a session strategy, the overnight leg in front of them, built by
        ``_lead_bars`` as pure context.

    Building one continuous 18:00 stream for a session strategy instead would
    shift every RTH boundary by the night's remainder, and the acceptance marker
    could land on a candle that never satisfied acceptance. Hence a session
    strategy's candles simply do not straddle 09:30 — neither did its engine's.

    Same discipline for the profile: it is anchored where the engine anchored it,
    so a session strategy's value area starts developing at the bell, not at
    18:00. Drawing an overnight-anchored profile for a run whose gate read an
    RTH-anchored one would show levels nothing traded against.

    The Globex VWAP needs the night on disk; when it isn't (a window whose
    overnight was never bought) that leg is simply absent rather than fetched —
    see ticks.cached_overnight.
    """
    sym = tickmod.contract_for(cfg.contract, day)
    t = tickmod.get_day_ticks(sym, day, include_overnight=overnight)
    if t is None or t.empty:
        return None
    b = barmod.tick_bars(t, cfg.ticks_per_bar)  # the engine's bars, from its own ticks
    if b.empty:
        return None

    # `full` is the tick frame the anchors are measured from and the footprint is
    # binned over; `rth_i0` is where RTH starts inside it; `b_all` is every drawn
    # candle with end_idx positions into `full`; `eng0` is the row where the
    # engine's own bars begin (everything before it is overnight context).
    if overnight:
        # The engine already read the night: its ticks and bars are the whole chart.
        full = t
        rth_i0 = int(t["ts_utc"].searchsorted(tickmod.session_bounds_utc(day)[0], side="left"))
        b_all, eng0 = b, 0
    else:
        on = tickmod.cached_overnight(sym, day)
        if on is None or on.empty:
            full, rth_i0, b_all, eng0 = t, 0, b, 0
        else:
            full = pd.concat([on, t], ignore_index=True)
            rth_i0 = len(on)
            lead = _lead_bars(on, cfg.ticks_per_bar)
            eng = b.assign(start_idx=b["start_idx"] + rth_i0, end_idx=b["end_idx"] + rth_i0)
            b_all = pd.concat([lead, eng], ignore_index=True)
            eng0 = len(lead)

    bar_pos = b_all["end_idx"].to_numpy()

    # Globex-anchored: accumulates from the first tick of `full`. Only meaningful
    # when `full` actually starts at the Globex open.
    w_gx = vwapmod.vwap_bands(full) if rth_i0 > 0 else None
    # NY-anchored: the same accumulation restarted at the bell. For a session
    # strategy this slice IS the engine's own tick frame, so the numbers match
    # exactly what it traded.
    w_ny = vwapmod.vwap_bands(full.iloc[rth_i0:].reset_index(drop=True))

    times = _strictly_increasing(np.asarray(_epoch_local(b_all["ts_utc"], tz)))

    bars_rows = [
        {"time": int(tm), "open": float(o), "high": float(h),
         "low": float(lo), "close": float(c), "volume": float(v)}
        for tm, o, h, lo, c, v in zip(
            times, b_all["open"], b_all["high"], b_all["low"], b_all["close"],
            b_all["volume"])
    ]
    vwap_gx_rows = [] if w_gx is None else _vwap_rows(w_gx, bar_pos, times)
    # A bar that closed overnight has no NY VWAP yet — the anchor hasn't started,
    # so its position is negative and _vwap_rows drops it.
    vwap_ny_rows = _vwap_rows(w_ny, bar_pos - rth_i0, times)

    # Anchored at the engine's session start, and aligned to the engine's bars —
    # hence indexed from eng0, leaving the overnight candles without a value area
    # on a session strategy's chart. Computed for every run, not only the ones
    # that read it: the layer is the same on every chart, and the run's config
    # (not the picture) is what says whether a rule was looking at it.
    prof = profmod.developing_profile(t, b, tick_size(cfg.instrument))
    profile_rows = [
        {"time": int(times[eng0 + k]), "poc": float(poc),
         "vah": float(vah), "val": float(val)}
        for k, (poc, vah, val) in enumerate(zip(prof.poc, prof.vah, prof.val))
        if not np.isnan(vah)
    ]

    # Snap trade instants (tick times) onto the bar grid: the frontend's
    # nearestBar would do this anyway, but doing it here keeps the marker on the
    # exact candle the engine acted on even after the uniqueness nudge above.
    # Compare in epoch-ns so a tz-naive parquet stamp can't blow up the compare.
    bar_ns = b_all["ts_utc"].astype("int64").to_numpy()

    def bar_time(ts) -> int:
        i = int(np.searchsorted(bar_ns, _utc(ts).value, side="left"))
        return int(times[min(i, len(times) - 1)])

    return (full, bars_rows, vwap_gx_rows, vwap_ny_rows, profile_rows, bar_time,
            _footprint(full, b_all, tick_size(cfg.contract)))


def sim_trade_chart(slug: str, run_id: str, trade_no: int, tz) -> dict:
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

    frame = _session_frame(cfg, day, tz, overnight=globex)
    if frame is None:
        return {"available": False}
    t, bars_rows, vwap_gx, vwap_ny, profile_rows, bar_time, footprint = frame

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
        "bars": bars_rows,
        **_vwap_slots(vwap_gx, vwap_ny, globex),
        "profile": profile_rows,
        "atr_points": [],
        "markers": markers,
        "price_lines": price_lines,
        "levels": [],
        "trade_rect": _trade_rect(trade, entry_t, exit_t, tz, cfg),
        "excursion": excursion,
        "instrument": tickmod.contract_for(cfg.contract, day),
        "footprint": footprint,
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


def sim_day_chart(slug: str, run_id: str, day, tz) -> dict:
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

    frame = _session_frame(cfg, day, tz, overnight=globex)
    if frame is None:
        return {"available": False}
    _, bars_rows, vwap_gx, vwap_ny, profile_rows, bar_time, footprint = frame

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
        "bars": bars_rows,
        **_vwap_slots(vwap_gx, vwap_ny, globex),
        "profile": profile_rows,
        "atr_points": [],
        "markers": markers,
        "levels": [],
        "trades": rects,
        "footprint": footprint,
        "tick_size": tick_size(cfg.contract),
        "point_value": point_value(cfg.contract),
    }
