"""Modern VWAP [GBB], ported to our tape and drawn.

The indicator is the subject of lab-backlog item 9 — the "VWAP Is Outdated"
video (The Good, The Bad And The Bitcoin, 2026-08-04). It is open source, so
this is a port of the published Pine rather than a reading of the video:
``data/research/modern-vwap/modern_vwap_gbb.pine`` is the source it was written
from, pulled from TradingView's pine-facade.

Four constructions, all of them his:

    anchor      session / week / **swing** — re-anchor at every confirmed swing
                pivot instead of at a fixed time. The swing anchor is the one
                construct on our shelf that is neither built nor falsified.
    bands       k x volume-weighted sigma, optionally widened by up to 50% when
                Kaufman efficiency is low (choppy tape widens, trending tightens)
    regime      KER(20) vs its 200-bar median x ATR%(14) vs its 200-bar median,
                four quadrants; low-KER half = "ranging", high-KER half =
                "trending"
    signals     MR = close outside +/-2 sigma then close back inside, ranging
                only. TC = one side of the VWAP for 8 of the last 10 closes,
                touch it, close back on side within 3 bars, trending only.

The page ships every timeframe in ``TIMEFRAMES`` and switches between them —
each one a full rebuild, not a resample, because every window in the indicator
counts bars. Its **split** view puts a plain globex-anchored session VWAP on the
same candles beside the indicator, time-axis and crosshair linked, so a signal
can be read against the chart we already look at.

**This is a drawing, not a backtest.** There is no exit rule and no sample
worth a statistic; the table reports how far price travelled after each signal
and nothing that looks like an expectancy. His own six-year crypto test came
back at zero, which is stated in the video and worth believing.

Two things to keep in view while looking at it. MR is our touch-bar close-back
artifact verbatim — the same shape the weekly-band context study found to be an
artifact of scoring the touch bar's own close. And the KER-scaled band is a
band scaled by a vol proxy, which is the shape the ATR x upper-band study
resolved as "intraday ATR is the band renamed". The swing anchor is the part
worth eyes.

Reads only the existing tick cache (data/cache/ticks/*.parquet) — never
fetches, so it costs nothing at Databento.

    uv run python demo/modern_vwap_demo.py             # 5 most recent sessions
    uv run python demo/modern_vwap_demo.py 2026-06-30 2026-06-27
    uv run python demo/modern_vwap_demo.py --tf 2,5,15  # which bar sizes to build
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from journal.atr import atr_series  # noqa: E402
from journal.config import ET_TZ  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

# --- Config ---------------------------------------------------------------
INSTRUMENT = "NQ"
N_SESSIONS = 5           # when no dates are given on the command line
# His chart was hourly crypto; ours are intraday. Every listed size is built and
# ships in the page — the switch on the page swaps between them. 2 min is where
# a swing anchor has enough pivots to be worth looking at; 5 is the reading size.
TIMEFRAMES = (2, 5)
DEFAULT_TF = 5

# His constants, unchanged. They are constants in the Pine too — not inputs —
# so there is nothing to sweep here without leaving the indicator behind.
KER_LEN = 20
ATR_LEN = 14
REGIME_LEN = 200         # trailing median window for both regime axes
OCC_WINDOW = 10          # side-occupancy lookback for trend continuation
OCC_MIN = 8              # ... and how many of those closes must be one side
HOLD_BARS = 3            # bars a touch stays live waiting for the close back
KER_WEIGHT = 0.5         # adaptive band scale = 1 + w x (1 - KER); his default

# The one input worth more than one value on our timeframe. At 5-min bars a
# pivot length of 10 wants 50 minutes of confirmation on each side, which is a
# structural swing on NQ; 5 is a scalper's swing and 20 is a half-session one.
PIVOTS = (5, 10, 20)
DEFAULT_MODE = "swing10"
# The globex anchor re-anchors at 18:00, so it starts fresh at the left edge of
# the drawn window. The rth one is still on the switch, but as a default it
# draws yesterday morning's line running through tonight, which reads as noise.
DEFAULT_COMPARE = "globex"
# The right half of the split view: the same bars carrying a plain session VWAP
# with fixed sigma bands — no swing anchor, no KER, no gate. It is the chart we
# already read every day, sitting next to the one being evaluated.
SPLIT_MODE = "globex"

# 200 bars of median plus 20 of KER has to be warm before the first drawn bar,
# so the indicator runs across the days behind the session and only the session
# is drawn. Three days is roughly 800 bars at 5 min — comfortably warm.
CONTEXT_DAYS = 3
# How far past a signal the table looks. Held in *minutes* rather than bars so
# the onside/offside columns mean the same thing on every timeframe — a 12-bar
# window is an hour at 5 min and 24 minutes at 2, and those two are not
# comparable numbers to put in the same column.
FWD_MIN = 60


def fwd_bars(minutes: int) -> int:
    return max(1, round(FWD_MIN / minutes))

OUT_DIR = Path(__file__).resolve().parent
# Lands in docs/research, where the Lab's Research tab lists it and serves it
# into a sandboxed iframe (api/routers/research.py). Written as bare page
# content — the router wraps it in a document shell.
HTML_OUT = ROOT / "docs" / "research" / "modern-vwap.html"
LWC_JS = ROOT / "frontend" / "node_modules" / "lightweight-charts" / "dist" / (
    "lightweight-charts.standalone.production.js"
)
LWC_CDN = "https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js"


# --- Session assembly -----------------------------------------------------
def cached_days() -> dict[date, str]:
    """Every (day -> contract) pair with a day file on disk."""
    days: dict[date, str] = {}
    for p in sorted((tickmod.CACHE_DIR / "ticks").glob("*_day.parquet")):
        sym, day_s, _ = p.stem.split("_")
        days[datetime.strptime(day_s, "%Y-%m-%d").date()] = sym
    return days


def contract_on(day: date, cache: dict[date, str]) -> str | None:
    sym = tickmod.contract_for_cached(INSTRUMENT, day)
    if sym and tickmod.have_segment(sym, day, "rth"):
        return sym
    return cache.get(day)


def day_ticks(symbol: str, day: date) -> pd.DataFrame | None:
    """Every cached segment of one day, in order. Never fetches."""
    parts = [
        tickmod.cached_overnight(symbol, day),
        tickmod.cached_rth(symbol, day),
        tickmod.cached_post(symbol, day),
    ]
    parts = [p for p in parts if p is not None and not p.empty]
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True)


def bars_from(ticks: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """OHLCV bars on an ET clock, indexed by bar-open minute.

    Bars exist only where ticks do. A hole in the tape closes up rather than
    becoming an empty bar, which is what the Pine sees too — a chart has no
    bars over a halt either, and every window in the indicator counts bars,
    not minutes.
    """
    df = ticks.sort_values("ts_utc")
    et = df["ts_utc"].dt.tz_convert(ET_TZ)
    g = df.groupby(et.dt.floor(f"{minutes}min"), sort=True)
    return g.agg(open=("price", "first"), high=("price", "max"),
                 low=("price", "min"), close=("price", "last"),
                 volume=("size", "sum")).reset_index(names="et")


def context_frame(day: date, symbol: str, cache: dict[date, str],
                  minutes: int) -> tuple[pd.DataFrame, int]:
    """Bars for `day` plus the cached days behind it, and where `day` starts.

    Context days must be the *same contract*: a roll inside the window would
    put a price gap through the middle of every trailing median. When there
    aren't enough same-contract days on disk the frame is simply shorter, and
    the regime reads undefined (grey) until its medians fill.
    """
    prior = [d for d in sorted(cache) if d < day and cache[d] == symbol]
    days = prior[-CONTEXT_DAYS:] + [day]
    frames = [t for d in days if (t := day_ticks(symbol, d)) is not None]
    if not frames:
        return pd.DataFrame(), 0
    bars = bars_from(pd.concat(frames, ignore_index=True), minutes)
    # The drawn window opens with the night in front of the session, like every
    # other chart in the app.
    start = pd.Timestamp(day, tz=ET_TZ) - pd.Timedelta(hours=6)  # 18:00 prior ET
    return bars, int(bars.index[bars["et"] >= start][0]) if (bars["et"] >= start).any() else len(bars)


# --- The indicator, ported ------------------------------------------------
def ker_series(close: pd.Series, n: int = KER_LEN) -> pd.Series:
    """Kaufman efficiency ratio: net travel / gross travel over n bars."""
    path = close.diff().abs().rolling(n).sum()
    net = (close - close.shift(n)).abs()
    out = np.where(path.to_numpy() == 0.0, 0.0, net.to_numpy() / path.to_numpy())
    out = pd.Series(out, index=close.index)
    return out.where(close.shift(n).notna() & path.notna())


def regime_series(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """His two-axis regime read. Returns (ker, atr%, quadrant).

    Quadrant = 2 x (KER above its median) + (ATR% above its median), so 0/1 are
    the low-efficiency "ranging" half and 2/3 the "trending" half. Ties count as
    low, as in the Pine. -1 = not enough history for the medians.

    ATR is Wilder's, from journal.atr — the seeding differs from Pine's ta.rma
    (SMA seed there, first-value seed here) by an amount that has decayed to
    nothing long before the drawn window opens.
    """
    ker = ker_series(bars["close"])
    atr_pct = atr_series(bars, ATR_LEN) / bars["close"]
    # pandas' rolling median on an even window is the mean of the two middle
    # order statistics, which is the exact median the Pine hand-rolls because
    # ta.median is nearest-rank.
    med_k = ker.rolling(REGIME_LEN).median()
    med_a = atr_pct.rolling(REGIME_LEN).median()
    quad = np.where(
        med_k.isna() | med_a.isna() | ker.isna() | atr_pct.isna(), -1,
        2 * (ker > med_k).astype(int) + (atr_pct > med_a).astype(int),
    )
    return ker, atr_pct, pd.Series(quad, index=bars.index)


def periodic_key(et: pd.Series, mode: str) -> np.ndarray:
    """The epoch each bar belongs to, for the fixed-clock anchors.

    `globex` is the CME trading day (18:00 ET roll) — what `timeframe.change("D")`
    resolves to on a futures feed, so it is the closest thing to his "session".
    `rth` is our NY anchor at 09:30, the one the app already draws. `week` is the
    trading week those globex days fall in.
    """
    if mode == "rth":
        # A bar before 09:30 belongs to the anchor that opened the morning
        # before it, so the line runs on through the night rather than
        # re-anchoring at midnight.
        shifted = et - pd.Timedelta(hours=9, minutes=30)
        return shifted.dt.normalize().to_numpy()
    sess = (et - pd.Timedelta(hours=18)).dt.normalize()   # globex trading date
    if mode == "globex":
        return sess.to_numpy()
    iso = sess.dt.isocalendar()
    return (iso["year"].astype(int) * 100 + iso["week"].astype(int)).to_numpy()


def swing_events(bars: pd.DataFrame, pl: int) -> np.ndarray:
    """Bars at which a swing pivot `pl` bars back becomes confirmed.

    Strict on both sides — the centre must beat all 2*pl neighbours outright,
    which is what the Pine spells out by hand rather than using ta.pivothigh
    (that one is not strict on the left). A simultaneous high and low is one
    event, as there.
    """
    n = len(bars)
    hi = bars["high"].to_numpy(float)
    lo = bars["low"].to_numpy(float)
    ev = np.zeros(n, bool)
    w = 2 * pl + 1
    if n < w:
        return ev
    # Rolling windows over the raw arrays: the centre is a pivot high iff it is
    # the unique maximum of its window.
    win_h = np.lib.stride_tricks.sliding_window_view(hi, w)
    win_l = np.lib.stride_tricks.sliding_window_view(lo, w)
    centre_h = win_h[:, pl]
    centre_l = win_l[:, pl]
    is_ph = (win_h < centre_h[:, None]).sum(axis=1) == w - 1
    is_pl = (win_l > centre_l[:, None]).sum(axis=1) == w - 1
    # Window i covers bars i..i+2pl, so its centre confirms at bar i+2pl.
    ev[2 * pl:] = is_ph | is_pl
    return ev


def engine(bars: pd.DataFrame, mode: str) -> dict:
    """One anchored VWAP instance: line, sigma, and its anchor bars.

    Mirrors ``f_engine``: reset the accumulators on an anchor event, and on a
    swing anchor backfill the pivot bar through the confirmation bar so the new
    line starts at the swing rather than at the moment we noticed it. The
    backfill is the reason the line in TradingView "redraws" — though note it
    only ever redraws forward from the confirmation bar; bars already plotted
    keep the old anchor's values, so what a live trader sees is a step, not a
    rewritten past.
    """
    n = len(bars)
    tp = (bars["high"] + bars["low"] + bars["close"]).to_numpy(float) / 3.0
    vol = bars["volume"].to_numpy(float)

    if mode.startswith("swing"):
        pl = int(mode[5:])
        ev = swing_events(bars, pl)
    else:
        pl = 0
        key = periodic_key(bars["et"], mode)
        ev = np.empty(n, bool)
        ev[0] = True
        ev[1:] = key[1:] != key[:-1]
    if n:
        ev[0] = True                      # barstate.isfirst

    vwap = np.full(n, np.nan)
    sigma = np.full(n, np.nan)
    s_pv = s_v = s_p2v = 0.0
    for i in range(n):
        if ev[i]:
            s_pv = s_v = s_p2v = 0.0
            if pl and i >= pl:
                for j in range(i - pl, i):
                    s_pv += tp[j] * vol[j]
                    s_v += vol[j]
                    s_p2v += tp[j] * tp[j] * vol[j]
        s_pv += tp[i] * vol[i]
        s_v += vol[i]
        s_p2v += tp[i] * tp[i] * vol[i]
        if s_v > 0:
            vwap[i] = s_pv / s_v
            sigma[i] = np.sqrt(max(s_p2v / s_v - vwap[i] * vwap[i], 0.0))
    return {"vwap": vwap, "sigma": sigma, "events": ev}


def signals(bars: pd.DataFrame, inst: dict, ker: pd.Series, quad: pd.Series,
            adaptive: bool, fwd: int) -> list[dict]:
    """MR and TC, with the regime gate recorded rather than applied.

    Every raw trigger is returned; `gated` says whether the regime it fired in
    was the one it belongs to. Keeping the blocked ones is the point — the gate
    is the indicator's central claim, and you cannot see what it is doing by
    looking only at what survived it.
    """
    n = len(bars)
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    et = bars["et"]
    vwap, sigma, ev = inst["vwap"], inst["sigma"], inst["events"]
    kv = ker.to_numpy(float)
    q = quad.to_numpy(int)

    adapt = 1.0 + KER_WEIGHT * (1.0 - kv) if adaptive else np.ones(n)
    adapt = np.where(np.isfinite(adapt), adapt, 1.0)
    upper2 = vwap + 2.0 * adapt * sigma
    lower2 = vwap - 2.0 * adapt * sigma

    out: list[dict] = []
    anchor_bar = 0
    long_dl = short_dl = -1
    for i in range(n):
        if ev[i]:
            anchor_bar = i
        ranging, trending = q[i] in (0, 1), q[i] in (2, 3)

        # --- L3.1 mean reversion: close-only on both legs, and the re-entry
        # must land inside *both* bands, so a full traverse fires nothing.
        if i and np.isfinite(upper2[i]) and np.isfinite(upper2[i - 1]):
            side = None
            if close[i - 1] < lower2[i - 1] and lower2[i] <= close[i] <= upper2[i]:
                side = "long"
            elif close[i - 1] > upper2[i - 1] and lower2[i] <= close[i] <= upper2[i]:
                side = "short"
            if side:
                out.append({"i": i, "kind": "MR", "side": side,
                            "gated": ranging, "regime": int(q[i])})

        # --- L3 side occupancy: the OCC_WINDOW bars *before* this one, and only
        # once the anchor is that far back. Causal — the current close is not in
        # its own window.
        ctx = 0
        if i - anchor_bar >= OCC_WINDOW:
            cnt = sum(1 for j in range(i - OCC_WINDOW, i)
                      if np.isfinite(vwap[j]) and close[j] > vwap[j])
            ctx = 1 if cnt >= OCC_MIN else -1 if (OCC_WINDOW - cnt) >= OCC_MIN else 0

        # --- L3.2 trend continuation: a touch opens a window of HOLD_BARS for
        # the close back on side. Overlapping touches merge; the episode dies on
        # a context flip or an anchor reset; one signal per episode.
        if not np.isfinite(vwap[i]):
            long_dl = short_dl = -1
        else:
            if ctx != 1:
                long_dl = -1
            if ctx != -1:
                short_dl = -1
            if ctx == 1 and low[i] <= vwap[i]:
                long_dl = max(long_dl, i + HOLD_BARS)
            if ctx == -1 and high[i] >= vwap[i]:
                short_dl = max(short_dl, i + HOLD_BARS)
            if long_dl >= i and close[i] > vwap[i]:
                long_dl = -1
                out.append({"i": i, "kind": "TC", "side": "long",
                            "gated": trending, "regime": int(q[i])})
            if short_dl >= i and close[i] < vwap[i]:
                short_dl = -1
                out.append({"i": i, "kind": "TC", "side": "short",
                            "gated": trending, "regime": int(q[i])})

    # Forward travel, from the next bar's open — his backtest's entry, and the
    # only honest one for a close-confirmed signal. These are distances, not
    # results: there is no target and no exit rule anywhere in this page.
    for s in out:
        i = s["i"]
        s["time"] = int(et.iloc[i].tz_localize(None).timestamp())
        s["clock"] = et.iloc[i].strftime("%H:%M")
        s["close"] = round(float(close[i]), 2)
        s["vwap"] = round(float(vwap[i]), 2) if np.isfinite(vwap[i]) else None
        if i + 1 >= n:
            s["entry"] = s["mfe"] = s["mae"] = None
            continue
        entry = float(bars["open"].to_numpy(float)[i + 1])
        seg = slice(i + 1, min(i + 1 + fwd, n))
        top, bot = float(high[seg].max()), float(low[seg].min())
        long_ = s["side"] == "long"
        s["entry"] = round(entry, 2)
        s["mfe"] = round(top - entry if long_ else entry - bot, 2)
        s["mae"] = round(entry - bot if long_ else top - entry, 2)
    return out


# --- Assembly -------------------------------------------------------------
MODES = tuple(f"swing{p}" for p in PIVOTS) + ("rth", "globex", "week")


def build_tf(day: date, symbol: str, cache: dict[date, str],
             minutes: int) -> dict | None:
    bars, first = context_frame(day, symbol, cache, minutes)
    if bars.empty or first >= len(bars):
        return None
    ker, atr_pct, quad = regime_series(bars)
    fwd = fwd_bars(minutes)

    insts = {m: engine(bars, m) for m in MODES}
    sigs = {f"{m}|{int(a)}": signals(bars, insts[m], ker, quad, a, fwd)
            for m in MODES for a in (False, True)}

    # Only the drawn window ships. Everything above ran on the full context so
    # the medians, the KER and the anchors are all warm at the first drawn bar.
    draw = bars.iloc[first:]
    idx = draw.index
    epoch = draw["et"].dt.tz_localize(None).astype("datetime64[s]").astype("int64")
    # 16:00 exclusive: the post-close hour is in the cache and the indicator ran
    # across it, but a 16:00 bar drawn on its own puts the whole settlement move
    # on the end of the price scale and squashes the session that is the subject.
    end = pd.Timestamp(f"{day} 16:00", tz=ET_TZ)
    keep = draw["et"] < end
    epoch, draw, idx = epoch[keep.to_numpy()], draw[keep.to_numpy()], idx[keep.to_numpy()]

    def series(a: np.ndarray, r: int = 2) -> list:
        return [None if not np.isfinite(v) else round(float(v), r) for v in a[idx]]

    lo, hi = int(epoch.iloc[0]), int(epoch.iloc[-1])
    return {
        "tf": minutes,
        "fwd_bars": fwd,
        "fwd_min": FWD_MIN,
        "info": {
            "title": f"{day.strftime('%a %Y-%m-%d')} · 18:00 prior – 16:00 ET",
            "symbol": symbol,
            "bars": int(len(draw)),
            "warmup": int(first),
            "range": f"{draw['low'].min():.2f} – {draw['high'].max():.2f}",
            "range_pts": round(float(draw["high"].max() - draw["low"].min()), 2),
            # Share of drawn bars the gate calls trending — the denominator for
            # everything the gate does.
            "trend_pct": round(100.0 * float((quad[idx] >= 2).mean()), 1),
            "undef_pct": round(100.0 * float((quad[idx] < 0).mean()), 1),
        },
        "times": [int(t) for t in epoch],
        "rth_open": int(pd.Timestamp(f"{day} 09:30", tz=ET_TZ)
                        .tz_localize(None).timestamp()),
        "bars": [
            {"time": int(t), "open": round(float(o), 2), "high": round(float(h), 2),
             "low": round(float(l), 2), "close": round(float(c), 2)}
            for t, o, h, l, c in zip(epoch, draw["open"], draw["high"],
                                     draw["low"], draw["close"])
        ],
        "ker": series(ker.to_numpy(float), 3),
        "regime": [int(v) for v in quad.to_numpy(int)[idx]],
        "inst": {
            m: {"vwap": series(insts[m]["vwap"]), "sigma": series(insts[m]["sigma"]),
                "anchors": [int(t) for t, e in zip(epoch, insts[m]["events"][idx]) if e]}
            for m in MODES
        },
        # Signals are kept whole-frame-relative until here; drop the ones off
        # the drawn window and re-stamp nothing else.
        "signals": {k: [{kk: vv for kk, vv in s.items() if kk != "i"}
                        for s in v if lo <= s["time"] <= hi]
                    for k, v in sigs.items()},
    }


def build_session(day: date, symbol: str, cache: dict[date, str],
                  tfs: tuple[int, ...]) -> dict | None:
    """One day, built once per timeframe.

    The whole indicator is rebuilt per bar size rather than resampled off the
    finest one: every window in it counts bars, so KER(20), the 200-bar medians
    and the pivot lengths all mean something different at 2 min than at 5, and
    that difference is the thing the switch is for.
    """
    built = {str(tf): p for tf in tfs
             if (p := build_tf(day, symbol, cache, tf)) is not None}
    if not built:
        return None
    return {"date": day.isoformat(), "tf": built}


# --- Output ---------------------------------------------------------------
def main() -> None:
    argv = sys.argv[1:]
    tfs = TIMEFRAMES
    if "--tf" in argv:
        k = argv.index("--tf")
        tfs = tuple(sorted({int(x) for x in argv[k + 1].split(",") if x.strip()}))
        argv = argv[:k] + argv[k + 2:]
    if not tfs:
        raise SystemExit("--tf needs at least one bar size")
    default_tf = DEFAULT_TF if DEFAULT_TF in tfs else tfs[0]

    cache = cached_days()
    if argv:
        wanted = [datetime.strptime(a, "%Y-%m-%d").date() for a in argv]
        pairs = [(d, s) for d in sorted(wanted) if (s := contract_on(d, cache))]
        missing = sorted(set(wanted) - {d for d, _ in pairs})
        if missing:
            print("no cached ticks for: " + ", ".join(str(m) for m in missing))
    else:
        pairs = [(d, cache[d]) for d in sorted(cache)[-N_SESSIONS:]]
    if not pairs:
        raise SystemExit("nothing to draw — no cached sessions found")

    sessions = []
    for day, sym in pairs:
        print(f"[{sym} {day}] reading cached ticks … "
              + ", ".join(f"{t}m" for t in tfs))
        s = build_session(day, sym, cache, tfs)
        if s:
            sessions.append(s)
    if not sessions:
        raise SystemExit("every requested session came back empty")

    _write_html(sessions, tfs, default_tf)

    key = f"{DEFAULT_MODE}|0"
    for tf in tfs:
        print(f"\n{DEFAULT_MODE} anchor, fixed bands, {tf}-min bars:")
        for s in sessions:
            p = s["tf"].get(str(tf))
            if p is None:
                print(f"  {s['date']}: nothing built at this bar size")
                continue
            sig = p["signals"][key]
            kept = [x for x in sig if x["gated"]]
            mr = sum(1 for x in kept if x["kind"] == "MR")
            tc = len(kept) - mr
            anchors = len(p["inst"][DEFAULT_MODE]["anchors"])
            print(f"  {s['date']}: {anchors:>3} anchors · {len(sig):>3} raw signals, "
                  f"{len(kept):>3} survive the gate ({mr} MR, {tc} TC) · "
                  f"trending {p['info']['trend_pct']:>5}% of bars"
                  + (f" · {p['info']['undef_pct']}% regime undefined"
                     if p["info"]["undef_pct"] else ""))
    print(f"\nWrote {HTML_OUT}")
    print("Open:  file://" + str(HTML_OUT))


def _chart_lib() -> str:
    """The charting library, inlined, so the page stands alone off disk."""
    if LWC_JS.exists():
        return LWC_JS.read_text()
    print(f"! {LWC_JS.name} not found — falling back to the CDN (page needs network)")
    return f'</script><script src="{LWC_CDN}">'


def _write_html(sessions: list[dict], tfs: tuple[int, ...], default_tf: int) -> None:
    template = (OUT_DIR / "_modern_vwap_template.html").read_text()
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    html = (
        template
        .replace("__LWC_JS__", _chart_lib())
        .replace("__SESSIONS_JSON__", json.dumps(sessions))
        .replace("__MODES_JSON__", json.dumps(list(MODES)))
        .replace("__TFS_JSON__", json.dumps(list(tfs)))
        .replace("__DEFAULT_TF__", str(default_tf))
        .replace("__DEFAULT_MODE__", DEFAULT_MODE)
        .replace("__DEFAULT_COMPARE__", DEFAULT_COMPARE)
        .replace("__SPLIT_MODE__", SPLIT_MODE)
        .replace("__KER_LEN__", str(KER_LEN))
        .replace("__ATR_LEN__", str(ATR_LEN))
        .replace("__REGIME_LEN__", str(REGIME_LEN))
        .replace("__OCC_MIN__", str(OCC_MIN))
        .replace("__OCC_WINDOW__", str(OCC_WINDOW))
        .replace("__HOLD_BARS__", str(HOLD_BARS))
        .replace("__KER_WEIGHT__", f"{KER_WEIGHT:g}")
        .replace("__FWD_MIN__", str(FWD_MIN))
    )
    HTML_OUT.write_text(html)


if __name__ == "__main__":
    main()
