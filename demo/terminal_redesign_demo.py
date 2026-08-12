"""A terminal redesign, driven by our own tape — clickable, not a picture.

The Charts workspace already draws no shell chrome: a 36px top bar, a floating
legend, a tool rail down the right of the canvas, and a side panel you summon.
It also already splits — into exactly two panes, one tradable and one read-only
context chart on a different bucketing (see `panes` in pages/Simulator.tsx).

Two panes is where the current chrome stops making sense. Everything that page
floats over "the chart" — the legend, the tools, the order dock, the indicator
strip, the timeframe control on the bar — was written when there was one chart
to float over, and each of them answers the question "which chart?" in its own
way or not at all. Going to arbitrary layouts (2-col, 2-row, 3, 2x2) is what
forces the question, so this page answers it in one place:

    * a LEFT RAIL that owns the tools, because a tool is a mode of the whole
      terminal and not a property of one canvas;
    * a TOP BAR that owns the layout and acts on the FOCUSED pane, with focus
      drawn loudly enough that "which chart?" is never a guess;
    * a PER-PANE LEGEND, because what is *drawn* is per chart and always was;
    * a RIGHT DOCK holding the trade panel, because order entry is per
      *account*, not per chart, and there is exactly one account;
    * a FLOATING TICKET over the trading pane, because the two buttons you can
      least afford to hunt for belong under the thumb, on the chart you trade.

None of it is wired into the app. It is a prototype to argue with: every layout
switches, every divider drags, the tools arm, the panes sync, and the trade
panel takes mock orders against a mock tape. The candles underneath are real —
a cached NQ session, straight out of data/cache/ticks — because chrome that
looks fine over a synthetic sine wave is chrome that has not been tested.

WHAT IS DELIBERATELY NOT HERE. No DOM ladder: the Live page books through
Rithmic's own bracket and the ladder is a separate argument (see the
live-trail-native-bracket note). No mobile layout: the mobile pass stops at the
Lab, and four half-width charts on a phone is four charts you cannot read.

Reads only the existing tick cache (data/cache/ticks/*.parquet) — never fetches,
so it costs nothing at Databento.

    uv run python demo/terminal_redesign_demo.py               # 3 recent sessions
    uv run python demo/terminal_redesign_demo.py 2026-06-30
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from journal.config import ET_TZ  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

# --- Config ---------------------------------------------------------------
INSTRUMENT = "NQ"
N_SESSIONS = 3
# The four the top bar shows as primary today (500t/1m/5m/15m), minus the tick
# bar — a tick bucketing is a tape-driven chart and this page is about chrome —
# plus the hour, because a 2x2 wants something on the slow end or the fourth
# pane is just the third one again.
TIMEFRAMES = (1, 5, 15, 60)
DEFAULT_TF = 5
# What each pane opens on, per layout size. The point of a grid is that the
# panes differ; four panes all on 5m is one chart rendered four times.
PANE_TFS = (5, 15, 1, 60)
# Bars kept per timeframe. 480 at 1 min is the whole RTH session plus the hour
# in front of it; at 60 it is more days than the cache is asked for, so the
# hourly pane simply gets what the context days give it.
MAX_BARS = 480
# Days of context behind the session, so 15m and 60m have something to scroll
# back into. Same contract only — a roll inside the window puts a price gap
# through the middle of the chart.
CONTEXT_DAYS = 4

TICK = 0.25
POINT_USD = 20.0          # NQ. MNQ is 2.0; the guardrails note says 1 NQ, not 5 MNQ.
# What routing may be pointed at while the tape stays where it is — the mini and
# its micro, exactly as `RoutingStatus.instruments` carries them today. The tape
# does not follow the switch: one login is one socket, and the subscription was
# made at connect. `mult` is dollars per point on the *routed* contract, which is
# what every chip, meter and review sentence on the page has to be measured in.
ROUTES = [
    {"suffix": "", "mult": POINT_USD, "micro": False},
    {"suffix": "micro", "mult": 2.0, "micro": True},
]

OUT_DIR = Path(__file__).resolve().parent
# Lands in docs/research, where the Lab's Research tab lists it and serves it
# into a sandboxed iframe (api/routers/research.py). Written as bare page
# content — the router wraps it in a document shell.
HTML_OUT = ROOT / "docs" / "research" / "terminal-redesign.html"
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

    Bars exist only where ticks do — a hole in the tape closes up rather than
    becoming an empty bar, which is what every other chart in the app does too.
    """
    df = ticks.sort_values("ts_utc")
    et = df["ts_utc"].dt.tz_convert(ET_TZ)
    g = df.groupby(et.dt.floor(f"{minutes}min"), sort=True)
    return g.agg(open=("price", "first"), high=("price", "max"),
                 low=("price", "min"), close=("price", "last"),
                 volume=("size", "sum")).reset_index(names="et")


def ema(values: np.ndarray, span: int) -> np.ndarray:
    """Standard-span EMA, seeded from the first observation (pandas' adjust=False)."""
    return pd.Series(values).ewm(span=span, adjust=False).mean().to_numpy()


def session_vwap(bars: pd.DataFrame, anchor: pd.Timestamp) -> np.ndarray:
    """Typical-price VWAP re-anchored at `anchor`, NaN before it.

    Bar-resolution rather than tick-resolution: this page is chrome, and the
    legend only has to show a number that moves the way the real one does. The
    app's own VWAPs are tick-derived (see VwapBandPrimitive).
    """
    tp = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    vol = bars["volume"].astype(float)
    live = (bars["et"] >= anchor).to_numpy()
    pv = np.where(live, tp * vol, 0.0).cumsum()
    vv = np.where(live, vol, 0.0).cumsum()
    out = np.where(vv > 0, pv / np.maximum(vv, 1e-9), np.nan)
    return np.where(live, out, np.nan)


def build_session(day: date, symbol: str, cache: dict[date, str]) -> dict | None:
    prior = [d for d in sorted(cache) if d < day and cache[d] == symbol]
    days = prior[-CONTEXT_DAYS:] + [day]
    frames = [t for d in days if (t := day_ticks(symbol, d)) is not None]
    if not frames:
        return None
    ticks = pd.concat(frames, ignore_index=True)

    # The session's own day, for the levels and the NY anchor.
    open_et = pd.Timestamp(day, tz=ET_TZ) + pd.Timedelta(hours=9, minutes=30)
    close_et = pd.Timestamp(day, tz=ET_TZ) + pd.Timedelta(hours=16)
    night_et = pd.Timestamp(day, tz=ET_TZ) - pd.Timedelta(hours=6)   # 18:00 prior

    out_tfs: dict[str, dict] = {}
    for m in TIMEFRAMES:
        bars = bars_from(ticks, m)
        bars = bars[bars["et"] <= close_et]
        if bars.empty:
            continue
        # Warm the EMAs and the VWAP on everything, then draw the tail. Three
        # spans rather than one because the point of the collapsible legend is
        # that a real pane carries more rows than fit — one indicator does not
        # make that case, and the app's own list is a dozen deep.
        close = bars["close"].to_numpy()
        emas = {9: ema(close, 9), 20: ema(close, 20), 50: ema(close, 50)}
        vw = session_vwap(bars, open_et)
        keep = bars.index[-MAX_BARS:]
        b = bars.loc[keep]
        # ET wall-clock stamped as if UTC, so the axis reads in market time
        # without a timezone plugin — the same trick the other demo pages use.
        epoch = b["et"].dt.tz_localize(None).astype("datetime64[s]").astype("int64")
        out_tfs[str(m)] = {
            "bars": [
                {"time": int(t), "open": round(float(o), 2), "high": round(float(h), 2),
                 "low": round(float(lo), 2), "close": round(float(c), 2)}
                for t, o, h, lo, c in zip(epoch, b["open"], b["high"], b["low"], b["close"])
            ],
            "volume": [
                {"time": int(t), "value": int(v), "up": bool(c >= o)}
                for t, v, c, o in zip(epoch, b["volume"], b["close"], b["open"])
            ],
            **{
                f"ema{n}": [
                    {"time": int(t), "value": round(float(v), 2)}
                    for t, v in zip(epoch, s[keep]) if np.isfinite(v)
                ]
                for n, s in emas.items()
            },
            "vwap": [
                {"time": int(t), "value": round(float(v), 2)}
                for t, v in zip(epoch, vw[keep]) if np.isfinite(v)
            ],
            # Where the drawn window should open: the night in front of the
            # session, like every other chart in the app.
            "open_at": int(pd.Timestamp(night_et).tz_localize(None).timestamp()),
        }

    if not out_tfs:
        return None

    rth = ticks[(ticks["ts_utc"].dt.tz_convert(ET_TZ) >= open_et)
                & (ticks["ts_utc"].dt.tz_convert(ET_TZ) <= close_et)]
    night = ticks[(ticks["ts_utc"].dt.tz_convert(ET_TZ) >= night_et)
                  & (ticks["ts_utc"].dt.tz_convert(ET_TZ) < open_et)]
    pri = ticks[ticks["ts_utc"].dt.tz_convert(ET_TZ).dt.date == (prior[-1] if prior else day)]

    def hl(df: pd.DataFrame) -> tuple[float, float] | None:
        if df.empty:
            return None
        return round(float(df["price"].max()), 2), round(float(df["price"].min()), 2)

    levels = {}
    for name, src in (("on", night), ("pd", pri)):
        got = hl(src)
        if got:
            levels[f"{name}h"], levels[f"{name}l"] = got

    last = float(rth["price"].iloc[-1]) if not rth.empty else float(ticks["price"].iloc[-1])
    return {
        "date": day.isoformat(),
        "symbol": symbol,
        # The routable pair for this session: NQU6 and MNQU6. Derived rather
        # than hard-coded so a different root or roll still names itself right.
        "routes": [
            {"symbol": ("M" + symbol) if r["micro"] else symbol,
             "mult": r["mult"], "micro": r["micro"]}
            for r in ROUTES
        ],
        "title": f"{day.strftime('%a %d %b %Y')}",
        "tfs": out_tfs,
        "levels": levels,
        "last": round(last, 2),
    }


# --- Emit -----------------------------------------------------------------
def _chart_lib() -> str:
    """The charting library, inlined, so the page stands alone off disk."""
    if LWC_JS.exists():
        return LWC_JS.read_text()
    print(f"! {LWC_JS.name} not found — falling back to the CDN (page needs network)")
    return f'</script><script src="{LWC_CDN}">'


def _write_html(sessions: list[dict]) -> None:
    template = (OUT_DIR / "_terminal_redesign_template.html").read_text()
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    html = (
        template
        .replace("__LWC_JS__", _chart_lib())
        .replace("__SESSIONS_JSON__", json.dumps(sessions))
        .replace("__TFS_JSON__", json.dumps([str(t) for t in TIMEFRAMES]))
        .replace("__PANE_TFS_JSON__", json.dumps([str(t) for t in PANE_TFS]))
        .replace("__DEFAULT_TF__", str(DEFAULT_TF))
        .replace("__TICK__", str(TICK))
        .replace("__POINT_USD__", str(POINT_USD))
    )
    HTML_OUT.write_text(html)


def main(argv: list[str]) -> None:
    cache = cached_days()
    if not cache:
        raise SystemExit("no cached tick days under data/cache/ticks")
    if argv:
        wanted = [datetime.strptime(a, "%Y-%m-%d").date() for a in argv]
    else:
        wanted = sorted(cache)[-N_SESSIONS:]

    sessions = []
    for day in wanted:
        sym = cache.get(day)
        if not sym:
            print(f"! {day}: no cached day file, skipped")
            continue
        s = build_session(day, sym, cache)
        if not s:
            print(f"! {day}: no ticks, skipped")
            continue
        sessions.append(s)
        counts = " · ".join(f"{tf}m {len(v['bars'])}" for tf, v in s["tfs"].items())
        print(f"  {day} {sym}: {counts} · last {s['last']}")

    if not sessions:
        raise SystemExit("nothing to draw")
    sessions.sort(key=lambda s: s["date"], reverse=True)
    _write_html(sessions)
    print(f"\nWrote {HTML_OUT}")
    print("Open:  file://" + str(HTML_OUT))


if __name__ == "__main__":
    main(sys.argv[1:])
