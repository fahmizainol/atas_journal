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
from journal.sim import ib as ibmod
from journal.sim.interactions import _atr_rows, _rsi_rows
from journal.sim import profile as profmod
from journal.sim import registry, store
from journal.sim import ticks as tickmod
from journal.sim import vwap as vwapmod
from journal.sim import weekly as weeklymod

from .charts_data import ACCENT, BLUE, GOLD, GREEN, ORANGE, RED, _epoch_local


def _is_globex(slug: str) -> bool:
    return registry.get(slug).session == "globex"


def _vwap_slots(gx_rows: list[dict], ny_rows: list[dict], wk_rows: list[dict],
                globex: bool) -> dict:
    """Each anchor in the slot that names it — the frontend colours from the
    slot (``vwap_globex`` gray/white, ``vwap_ny`` purple, ``vwap_weekly``
    orange) and gives each its own legend toggle. ``vwap_anchor`` says which
    the engine actually traded; the others are context. No engine trades the
    weekly anchor, so it is never a ``vwap_anchor`` value. The chart must never
    leave that ambiguous."""
    return {
        "vwap_globex": gx_rows,
        "vwap_ny": ny_rows,
        "vwap_weekly": wk_rows,
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


# Swing size for CVD-divergence pivots, in ticks: a swing counts once price
# retraces this far from its running extreme. The structure-clarity gate runs
# the same zigzag at 40 ticks, but that classifies *every bar's* trend state —
# for discrete marks it fires ~65×/session (intrabar noise). 120 ticks (30 NQ
# points) keeps the marks on session-scale swings — a handful of the notable
# order-flow non-confirmations per session rather than a wall of circles.
DIV_ZZ_TICKS = 120


def _cvd_series(t: pd.DataFrame, b: pd.DataFrame) -> np.ndarray | None:
    """Cumulative volume delta per bar: the running sum of signed aggressor
    volume (buy market orders minus sell market orders) as of each bar's close.

    Straight off the tape's aggressor ``side``, binned over the same tick frame
    the footprint is — so it lines up with the candles bar-for-bar. Anchored at
    the first drawn bar (the 18:00 Globex open when the night is on the chart,
    else the bell) and accumulated across the whole session: one line to read
    order-flow pressure against price.

    Returns the per-bar cumulative series aligned 1:1 with ``b``, or ``None``
    when the feed carried no aggressor side (every tick ``"N"`` — an older
    cache, or a symbol the vendor never tagged): there is nothing to accumulate.
    """
    n = len(b)
    side = t["side"].to_numpy()
    size = t["size"].to_numpy().astype(float)
    signed = np.where(side == "B", size, np.where(side == "A", -size, 0.0))
    if not signed.any():
        return None
    # Same bar assignment as _footprint: a tick belongs to the bar whose end it
    # falls before; trailing ticks past the last full bar land at n and drop.
    bar_of = np.searchsorted(b["end_idx"].to_numpy(), np.arange(len(t)), side="left")
    keep = bar_of < n
    per_bar = np.zeros(n)
    np.add.at(per_bar, bar_of[keep], signed[keep])
    return np.cumsum(per_bar)


def _cvd_rows(cvd: np.ndarray | None, times: np.ndarray) -> list[dict]:
    """The CVD series as ``{time, value}`` rows for the chart's own pane. Empty
    when there was no aggressor side, so the pane and its legend toggle simply
    don't appear rather than drawing a flat zero line."""
    if cvd is None:
        return []
    return [{"time": int(tm), "value": float(v)} for tm, v in zip(times, cvd)]


def _cvd_divergences(
    b: pd.DataFrame, cvd: np.ndarray | None, times: np.ndarray, thr: float
) -> list[dict]:
    """Regular price/CVD divergences, via the same causal zigzag the
    structure-clarity gate uses to find swings.

    A swing where price prints a *higher high* while cumulative delta prints a
    *lower high* is bearish — price extended but the aggressive buying behind it
    didn't confirm; the mirror (a *lower low* in price against a *higher low* in
    delta) is bullish. CVD is read at each price pivot, so the two series are
    compared at the same bar. ``thr`` is a price distance: a swing counts once
    price retraces that far from its running extreme.

    Unlike the gate — which only trusts a pivot the moment a *later* bar's close
    confirms it, because it decides live entries — this is a completed-session
    review chart, so each endpoint sits on the actual swing bar.

    Returns one dict per divergence carrying *both* swing points in CVD
    coordinates (``v1``/``v2`` are cumulative-delta values, ``t1``/``t2`` bar
    times), so the frontend can draw the A→B line on the CVD pane that makes the
    non-confirmation legible — the delta line sloping the opposite way to price.
    """
    if cvd is None:
        return []
    highs = b["high"].to_numpy()
    lows = b["low"].to_numpy()

    # Causal zigzag → pivots as (bar index of the extreme, price, kind).
    pivots: list[tuple[int, float, str]] = []
    direction, max_i, min_i = 0, 0, 0
    for i in range(len(highs)):
        if highs[i] >= highs[max_i]:
            max_i = i
        if lows[i] <= lows[min_i]:
            min_i = i
        if direction >= 0 and highs[max_i] - lows[i] >= thr:
            pivots.append((max_i, float(highs[max_i]), "H"))
            direction, min_i = -1, i
        elif direction <= 0 and highs[i] - lows[min_i] >= thr:
            pivots.append((min_i, float(lows[min_i]), "L"))
            direction, max_i = 1, i

    # Compare each pivot with the previous one of its kind: price vs the CVD
    # value sampled at that same swing bar. A hit emits the A→B segment.
    out: list[dict] = []
    last_h: tuple[int, float, float] | None = None  # (idx, price, cvd) prev high
    last_l: tuple[int, float, float] | None = None  # (idx, price, cvd) prev low
    for idx, price, kind in pivots:
        c = float(cvd[idx])
        if kind == "H":
            if last_h is not None and price > last_h[1] and c < last_h[2]:
                out.append({
                    "kind": "bear",
                    "t1": int(times[last_h[0]]), "v1": last_h[2],
                    "t2": int(times[idx]), "v2": c,
                })
            last_h = (idx, price, c)
        else:
            if last_l is not None and price < last_l[1] and c > last_l[2]:
                out.append({
                    "kind": "bull",
                    "t1": int(times[last_l[0]]), "v1": last_l[2],
                    "t2": int(times[idx]), "v2": c,
                })
            last_l = (idx, price, c)
    return out


def _ema_rows(closes: np.ndarray, times: np.ndarray, span: int) -> list[dict]:
    """A 1-minute EMA as ``{time, value}`` rows — the 9/20 the institutional
    day-trading convention watches. Recursive EMA (``adjust=False``), so each
    value depends only on prior closes: the same line a live 1-minute chart
    draws. Computed on the minute grid regardless of the drawn candle timeframe;
    the frontend samples it onto the tick-bar grid when the chart is tick bars."""
    if len(closes) == 0:
        return []
    ema = pd.Series(closes, dtype="float64").ewm(span=span, adjust=False).mean().to_numpy()
    return [{"time": int(t), "value": round(float(v), 2)}
            for t, v in zip(times, ema) if np.isfinite(v)]


def _vwap_rows(w: pd.DataFrame, pos: np.ndarray, times: np.ndarray) -> list[dict]:
    """Sample a band frame at the tick positions where bars closed. ``pos`` is
    positional into *w*, so a negative entry (a bar that closed before this
    anchor even started) is simply not drawn."""
    rows = []
    for tm, p in zip(times, pos):
        if p < 0 or p >= len(w):
            continue
        r = w.iloc[int(p)]
        rows.append({
            "time": int(tm), "middle": float(r["mid"]),
            "upper1": float(r["upper1"]), "lower1": float(r["lower1"]),
            "upper2": float(r["upper2"]), "lower2": float(r["lower2"]),
        })
    return rows


def _profile_rows(prof: profmod.DevelopingProfile, times: np.ndarray) -> list[dict]:
    """Per-bar developing POC/VAH/VAL, positionally aligned to ``times`` (one entry
    per drawn bar). A bar with no value area yet — before its anchor started, or a
    whole bar of zero-size prints — comes back NaN and is dropped, so the line
    simply begins where the profile first exists rather than at a made-up level."""
    return [
        {"time": int(times[k]), "poc": float(poc),
         "vah": float(vah), "val": float(val)}
        for k, (poc, vah, val) in enumerate(zip(prof.poc, prof.vah, prof.val))
        if not np.isnan(vah)
    ]


def _profile_slots(gx_rows: list[dict], ny_rows: list[dict]) -> dict:
    """Both developing value areas, each in the slot that names it — the frontend
    colours from the slot (``profile_globex`` silver, ``profile_ny`` fuchsia) and
    gives each its own legend toggle. Mirrors ``_vwap_slots``: both anchors are
    always drawn, and which the engine traded is already said by ``vwap_anchor``."""
    return {"profile_globex": gx_rows, "profile_ny": ny_rows}


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


def _post_bars(post: pd.DataFrame, n: int, offset: int) -> pd.DataFrame:
    """Post-RTH (16:00-17:00 ET) context candles, chunked forward from 16:00.

    The mirror of ``_lead_bars`` on the far side of the session: pure context the
    engine never traded (like the overnight lead), so a partial last candle at the
    17:00 halt is fine — it is the session's final candle. Indices are offset to
    sit right after RTH in ``full``."""
    pb = barmod.tick_bars(post, n)
    if pb.empty:
        return pb
    return pb.assign(start_idx=pb["start_idx"] + offset, end_idx=pb["end_idx"] + offset)


def _session_frame(cfg, day, tz, overnight: bool = False, resolution: str = "tick",
                   div_ticks: int | None = None):
    """One session's bars + the anchored VWAPs + display times, shared by the
    per-trade and full-day payloads. Returns
    (ticks, bars_rows, vwap_gx_rows, vwap_ny_rows, vwap_wk_rows,
    profile_gx_rows, profile_ny_rows, bar_time, ib, footprint, cvd_rows,
    divergences, ema9_rows, ema20_rows, ema50_rows, ema200_rows, rsi_rows,
    atr_rows), or None if no data.

    Every strategy's chart shows the same thing: the overnight candles from 18:00
    ET, the RTH session, both anchored VWAPs, and both developing profiles. What
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

    The two developing profiles follow the two VWAP anchors exactly: the NY one
    starts developing at the bell, the Globex one at 18:00, and both are drawn on
    every chart so a level is always readable whichever anchor a rule consulted —
    ``vwap_anchor`` still says which the engine traded.

    The Globex anchor needs the night on disk; when it isn't (a window whose
    overnight was never bought) both the Globex VWAP and the Globex profile are
    simply absent rather than fetched — see ticks.cached_overnight.
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
    # candle with end_idx positions into `full`.
    if overnight:
        # The engine already read the night: its ticks and bars are the whole chart.
        full = t
        rth_i0 = int(t["ts_utc"].searchsorted(tickmod.session_bounds_utc(day)[0], side="left"))
        b_all = b
    else:
        on = tickmod.cached_overnight(sym, day)
        if on is None or on.empty:
            full, rth_i0, b_all = t, 0, b
        else:
            full = pd.concat([on, t], ignore_index=True)
            rth_i0 = len(on)
            lead = _lead_bars(on, cfg.ticks_per_bar)
            eng = b.assign(start_idx=b["start_idx"] + rth_i0, end_idx=b["end_idx"] + rth_i0)
            b_all = pd.concat([lead, eng], ignore_index=True)

    # Splice the recovered 16:00-17:00 post hour as a context tail: the Globex and
    # weekly anchors carry through it and its candles are drawn (its own context
    # bars in tick mode; time_bars picks it up for a minute chart). NY is clipped to
    # [rth_i0, rth_end) below, so it still ends at the bell's close.
    rth_end = len(full)
    post = tickmod.cached_post(sym, day)
    if post is not None and not post.empty:
        if resolution == "tick":
            b_all = pd.concat([b_all, _post_bars(post, cfg.ticks_per_bar, rth_end)],
                              ignore_index=True)
        full = pd.concat([full, post], ignore_index=True)

    # Time-resolution view is a context alternative to the engine's tick bars:
    # every drawn candle is rebuilt as clock bars over the whole `full` frame.
    # The engine still traded the tick bars above — this deliberately trades the
    # "the candles you see are the candles it traded" guarantee for a familiar
    # minute picture, so it is opt-in. start_idx/end_idx still index into `full`,
    # so every VWAP, profile, footprint and CVD lookup below is unchanged, and
    # the two-piece overnight construction above is simply moot: one continuous
    # stream is exactly what a minute chart wants.
    if resolution != "tick":
        b_all = barmod.time_bars(full, resolution)
        if b_all.empty:
            return None

    bar_pos = b_all["end_idx"].to_numpy()

    # Globex-anchored: accumulates from the first tick of `full`. Only meaningful
    # when `full` actually starts at the Globex open.
    w_gx = vwapmod.vwap_bands(full) if rth_i0 > 0 else None
    # NY-anchored: the same accumulation restarted at the bell. For a session
    # strategy this slice IS the engine's own tick frame, so the numbers match
    # exactly what it traded.
    w_ny = vwapmod.vwap_bands(full.iloc[rth_i0:rth_end].reset_index(drop=True))
    # Weekly-anchored: the Globex accumulation seeded with the week's prior
    # sessions. Same honesty rule as the Globex anchor — absent, not
    # approximated, when the night isn't on disk or the week has a hole
    # (weekly.weekly_seed returns None). On the week's first session the seed
    # is zero and the weekly line coincides with the Globex one, which is what
    # a weekly anchor genuinely looks like on a Monday.
    wk_seed = weeklymod.weekly_seed(cfg.contract, day) if rth_i0 > 0 else None
    w_wk = vwapmod.vwap_bands(full, seed=wk_seed) if wk_seed is not None else None

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
    vwap_wk_rows = [] if w_wk is None else _vwap_rows(w_wk, bar_pos, times)

    # Two developing value areas, anchored exactly where the two VWAPs are and
    # drawn together: the Globex one accumulates from the 18:00 open, the NY one
    # restarts at the bell. Both are computed for every run — the layer is the same
    # on every chart, and the run's config (not the picture) says which anchor a
    # rule read. Each result indexes straight into `b_all`/`times`, so a bar with
    # no value area yet is dropped by the NaN filter rather than mis-drawn: the
    # overnight candles on the NY anchor (their shifted end is negative, so nothing
    # accumulates), and — when the night is absent — the Globex anchor entirely.
    tsz = tick_size(cfg.instrument)
    profile_gx_rows = (
        _profile_rows(profmod.developing_profile(full, b_all, tsz), times)
        if rth_i0 > 0 else []
    )
    ny_shift = b_all["end_idx"].to_numpy() - rth_i0
    ny_in = (ny_shift >= 0) & (ny_shift < rth_end - rth_i0)   # RTH bars only (drop post)
    ny_bars = b_all[ny_in].reset_index(drop=True).assign(end_idx=ny_shift[ny_in])
    profile_ny_rows = _profile_rows(
        profmod.developing_profile(full.iloc[rth_i0:rth_end].reset_index(drop=True), ny_bars, tsz),
        times[ny_in],
    )

    # Snap trade instants (tick times) onto the bar grid: the frontend's
    # nearestBar would do this anyway, but doing it here keeps the marker on the
    # exact candle the engine acted on even after the uniqueness nudge above.
    # Compare in epoch-ns so a tz-naive parquet stamp can't blow up the compare.
    bar_ns = b_all["ts_utc"].astype("int64").to_numpy()

    def bar_time(ts) -> int:
        i = int(np.searchsorted(bar_ns, _utc(ts).value, side="left"))
        return int(times[min(i, len(times) - 1)])

    # The Initial Balance (first 60 min of RTH), measured on the same RTH ticks
    # the engine traded and the same window as the IB/ORB study — what the chart
    # draws is what the study's break/extension stats were measured against.
    ib = ibmod.chart_overlay(full.iloc[rth_i0:rth_end], day, b_all["ts_utc"], times)

    # 9/20 EMA on the 1-minute grid (over the whole `full` stream, overnight
    # included), independent of whether the drawn candles are tick or minute
    # bars — the convention is a 1-minute average. Stamped in the same local
    # epoch as `times`, so the frontend samples it onto the drawn bar grid.
    m = barmod.time_bars(full, "1min")
    if m.empty:
        ema9_rows = ema20_rows = ema50_rows = ema200_rows = []
    else:
        m_times = np.asarray(_epoch_local(m["ts_utc"], tz))
        m_closes = m["close"].to_numpy()
        ema9_rows = _ema_rows(m_closes, m_times, 9)
        ema20_rows = _ema_rows(m_closes, m_times, 20)
        ema50_rows = _ema_rows(m_closes, m_times, 50)
        ema200_rows = _ema_rows(m_closes, m_times, 200)

    # One CVD pass feeds both the pane series and the divergence marks, so they
    # can't drift out of sync. thr is a price distance off the contract's grid.
    cvd_arr = _cvd_series(full, b_all)
    cvd_rows = _cvd_rows(cvd_arr, times)
    divergences = _cvd_divergences(b_all, cvd_arr, times, (div_ticks or DIV_ZZ_TICKS) * tsz)

    # RSI(14) and ATR(14) on the *drawn* timeframe — the tick bars on screen, like
    # the Interactions/Drafts charts — not the 1-minute grid the EMA uses, so both
    # read against the same candles they sit under.
    rsi_rows = _rsi_rows(b_all["close"].to_numpy(), times, 14)
    atr_rows = _atr_rows(b_all, times, 14)

    return (full, bars_rows, vwap_gx_rows, vwap_ny_rows, vwap_wk_rows,
            profile_gx_rows, profile_ny_rows, bar_time, ib,
            _footprint(full, b_all, tick_size(cfg.contract)),
            cvd_rows, divergences, ema9_rows, ema20_rows,
            ema50_rows, ema200_rows, rsi_rows, atr_rows)


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
    (t, bars_rows, vwap_gx, vwap_ny, vwap_wk, profile_gx, profile_ny,
     bar_time, ib, footprint, cvd, divergences, ema9, ema20,
     ema50, ema200, rsi, atr) = frame

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
        **_vwap_slots(vwap_gx, vwap_ny, vwap_wk, globex),
        **_profile_slots(profile_gx, profile_ny),
        "ema9": ema9,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi,
        "atr_points": atr,
        "markers": markers,
        "price_lines": price_lines,
        "levels": [],
        "ib": ib,
        "trade_rect": _trade_rect(trade, entry_t, exit_t, tz, cfg),
        "excursion": excursion,
        "instrument": tickmod.contract_for(cfg.contract, day),
        "footprint": footprint,
        "cvd": cvd,
        "cvd_divergences": divergences,
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
    (_, bars_rows, vwap_gx, vwap_ny, vwap_wk, profile_gx, profile_ny,
     bar_time, ib, footprint, cvd, divergences, ema9, ema20,
     ema50, ema200, rsi, atr) = frame

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
        **_vwap_slots(vwap_gx, vwap_ny, vwap_wk, globex),
        **_profile_slots(profile_gx, profile_ny),
        "ema9": ema9,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi,
        "atr_points": atr,
        "markers": markers,
        "levels": [],
        "ib": ib,
        "trades": rects,
        "footprint": footprint,
        "cvd": cvd,
        "cvd_divergences": divergences,
        "tick_size": tick_size(cfg.contract),
        "point_value": point_value(cfg.contract),
    }
