"""Four lightweight-charts community addons, driven by our own tape.

An evaluation, not an adoption. We render every chart in the app on
lightweight-charts with sixteen primitives we wrote ourselves
(frontend/src/components/charts/*Primitive.ts). Four community packages claim to
cover ground we either built by hand or left unbuilt, and none of them had been
run against real NQ data before this page:

    lightweight-orderflow-charts    footprint custom series, session volume
                                    profile primitive, delta-summary subchart.
                                    Overlaps VolumeProfilePrimitive and
                                    BigTradePrimitive; goes past both.
    box-whisker-series              the official plugin-example custom series.
                                    Not on npm — vendored from plugin-examples.
                                    The only quartile mark we can draw on the
                                    same engine as everything else.
    lightweight-charts-indicators   446 indicators, 317 of them transpiled from
                                    Pine. Rides oakscriptjs, a PineScript-v6
                                    runtime.
    lightweight-charts-drawing      68 drawing tools behind a DrawingManager.

The page answers one question per section: *does this thing render our data
correctly*, not *is it pretty*. Section 3 is the one that matters — it diffs the
library's ATR/RSI/EMA against ours bar for bar and prints the worst error. A
transpiled indicator that is subtly wrong is a fast route to a fake edge, and
the scoreboard is already 1 pass per 12 fails without help from a bad EMA.

Aggressor side is the trap. The cache stores 'B' for a buy aggressor and 'A' for
a sell (verified in demo/big_trades_demo.py against the local mid, and again in
api/sim_charts.py; src/journal/sim/interactions.py signs it the other way). The
footprint convention is the mirror of the name: aggressive buys lift the offer
and belong in *askVolume*, aggressive sells hit the bid and belong in
*bidVolume*. Get that backwards and every imbalance on the page inverts while
still looking plausible, which is exactly the failure the drift-fade study spent
a pass discovering.

Reads only the existing tick cache (data/cache/ticks/*.parquet) — never fetches,
so it costs nothing at Databento.

    uv run python demo/lwc_addons_demo.py                # 3 most recent sessions
    uv run python demo/lwc_addons_demo.py 2026-06-30 2026-06-26
    uv run python demo/lwc_addons_demo.py --footprint 15 # minutes per footprint bar
    uv run python demo/lwc_addons_demo.py --whisker 120  # sessions in the vol clock
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

from journal.atr import atr_series  # noqa: E402
from journal.config import ET_TZ  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

INSTRUMENT = "NQ"
TICK = 0.25                  # NQ minimum price increment, in points
SESSIONS = 3                 # sessions on the footprint / indicator / drawing charts
WHISKER_SESSIONS = 120       # sessions behind the vol-clock distribution
FOOTPRINT_MINUTES = 5        # one footprint bar per N minutes of RTH
BUCKET_MINUTES = 30          # one box-whisker box per N minutes of RTH
REFERENCE_DP = 10            # decimals kept on the Python reference series

# Aggressor side, as stored in the cache. See the module docstring: the mapping
# into footprint columns is deliberately crossed.
BUY, SELL = "B", "A"

OUT_DIR = Path(__file__).resolve().parent
# Bare page content — no <!doctype>/<html>/<body>. api/routers/research.py wraps
# it in a document shell on the way to the sandboxed iframe.
HTML_OUT = ROOT / "docs" / "research" / "lwc-addons.html"
BUNDLE_JS = OUT_DIR / "vendor" / "lwc-addons.iife.js"


# --- Session assembly -----------------------------------------------------
def cached_days() -> list[date]:
    """Every ET session date with ticks on disk, oldest first."""
    days = set()
    for p in (tickmod.TICK_CACHE_DIR).glob("*_day.parquet"):
        _, day_s, _ = p.stem.split("_")
        days.add(datetime.strptime(day_s, "%Y-%m-%d").date())
    return sorted(days)


def session_frame(day: date) -> tuple[str, pd.DataFrame] | None:
    """(contract, ticks) for one ET session, or None when it was never bought."""
    sym = tickmod.contract_for_cached(INSTRUMENT, day)
    if sym is None:
        for p in tickmod.TICK_CACHE_DIR.glob(f"*_{day:%Y-%m-%d}_day.parquet"):
            sym = p.stem.split("_")[0]
            break
    if sym is None:
        return None
    df = pd.read_parquet(tickmod.TICK_CACHE_DIR / f"{sym}_{day:%Y-%m-%d}_day.parquet")
    return (sym, df) if not df.empty else None


def et_seconds(ts: pd.Series | pd.Timestamp) -> np.ndarray | int:
    """ET wall clock as a UTC epoch — the convention the other demo pages use.

    lightweight-charts has no timezone; every page here hands it ET-as-if-UTC so
    the axis reads in session time without a formatter doing arithmetic.
    """
    if isinstance(ts, pd.Timestamp):
        return int(ts.tz_localize(None).timestamp())
    return (ts.dt.tz_localize(None).astype("int64") // 10**9).to_numpy()


# --- Order flow -----------------------------------------------------------
def order_flow_bars(rth: pd.DataFrame, minutes: int) -> list[dict]:
    """RTH ticks -> OrderFlowBar[], the contract lightweight-orderflow-charts wants.

    Levels stay at the raw 0.25 increment. The page clusters them to whatever
    mintick the reader picks (clusterOrderFlowBarsByMintick), which is the whole
    argument for shipping the fine grain: re-bucketing a footprint is a display
    decision and should not need a round trip.
    """
    df = rth.sort_values("ts_utc")
    et = df["ts_utc"].dt.tz_convert(ET_TZ)
    bucket = et.dt.floor(f"{minutes}min")
    price = df["price"].astype(float)
    size = df["size"].astype(float)
    # 'B' lifts the offer -> ask column. 'A' hits the bid -> bid column.
    ask = size.where(df["side"] == BUY, 0.0)
    bid = size.where(df["side"] == SELL, 0.0)

    frame = pd.DataFrame(
        {"bucket": bucket.to_numpy(), "price": price, "size": size, "ask": ask, "bid": bid}
    )
    bars: list[dict] = []
    for bucket_ts, g in frame.groupby("bucket", sort=True):
        levels_df = (
            g.groupby("price", sort=True)[["bid", "ask"]].sum().reset_index()
        )
        levels_df = levels_df[(levels_df["bid"] + levels_df["ask"]) > 0]
        if levels_df.empty:
            continue
        volume = levels_df["bid"].to_numpy() + levels_df["ask"].to_numpy()
        poc_i = int(np.argmax(volume))
        bars.append(
            {
                "time": int(pd.Timestamp(bucket_ts).tz_localize(None).timestamp()),
                "open": round(float(g["price"].iloc[0]), 2),
                "high": round(float(g["price"].max()), 2),
                "low": round(float(g["price"].min()), 2),
                "close": round(float(g["price"].iloc[-1]), 2),
                "totalVolume": int(g["size"].sum()),
                "askVolume": int(g["ask"].sum()),
                "bidVolume": int(g["bid"].sum()),
                "delta": int(g["ask"].sum() - g["bid"].sum()),
                "tradeCount": int(len(g)),
                "pocPrice": round(float(levels_df["price"].iloc[poc_i]), 2),
                "pocVolume": int(volume[poc_i]),
                # Flat integer triples, not objects. A session of 5-minute bars
                # at the raw increment is ~17k levels, and
                # {"price":29431.25,"bidVolume":12,"askVolume":7} costs 47 bytes
                # of which 44 are punctuation and key names. As
                # [offsetInTicks, bid, ask] against a per-bar base it is nearer
                # 9, and the page is a committed artifact — this is the
                # difference between a 4.5 MB file in git and a 2 MB one.
                # hydrate() in the template turns them back into the package's
                # PriceLevelVolume shape once, at load.
                "levelBase": round(float(levels_df["price"].iloc[0]), 2),
                "levels": [
                    v
                    for p, b, a in zip(
                        levels_df["price"], levels_df["bid"], levels_df["ask"], strict=True
                    )
                    for v in (
                        int(round((p - levels_df["price"].iloc[0]) / TICK)), int(b), int(a)
                    )
                ],
            }
        )
    # deltaMin/deltaMax bound the intrabar delta excursion; the package uses them
    # for the delta-summary rows. Cumulative within the bar, not across it.
    for bar, (_, g) in zip(bars, frame.groupby("bucket", sort=True), strict=False):
        run = (g["ask"] - g["bid"]).cumsum().to_numpy()
        bar["deltaMin"] = int(run.min())
        bar["deltaMax"] = int(run.max())

    # The encoding above is only safe if it round-trips. Check it here rather
    # than trusting the page to notice: a level list that decodes to the wrong
    # prices would still draw a plausible-looking footprint.
    for bar in bars:
        ask = sum(bar["levels"][i + 2] for i in range(0, len(bar["levels"]), 3))
        bid = sum(bar["levels"][i + 1] for i in range(0, len(bar["levels"]), 3))
        assert ask == bar["askVolume"], f"{bar['time']}: ask {ask} != {bar['askVolume']}"
        assert bid == bar["bidVolume"], f"{bar['time']}: bid {bid} != {bar['bidVolume']}"
    return bars


# --- Minute bars + reference indicators -----------------------------------
def minute_bars(session: pd.DataFrame) -> pd.DataFrame:
    """1-minute OHLCV over the whole session, indexed by ET minute."""
    df = session.sort_values("ts_utc")
    minute = df["ts_utc"].dt.tz_convert(ET_TZ).dt.floor("min")
    return (
        df.groupby(minute, sort=True)
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("size", "sum"),
        )
        .reset_index(names="minute")
    )


def reference_series(bars: pd.DataFrame) -> dict[str, list[float | None]]:
    """Our own ATR/RSI/EMA, to diff the library against.

    ATR comes from journal.atr — the function the sim actually uses — so the
    comparison is against production, not against a second implementation
    written for this page. RSI is Wilder's with the same alpha=1/period
    smoothing, which is what Pine's ta.rsi is.
    """
    close = bars["close"].astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi14 = (100 - 100 / (1 + rs)).where(avg_loss != 0, 100.0)

    atr14 = atr_series(bars, 14)

    # 10 decimals, not the 2 the price columns get: these exist to be diffed
    # against the library, so the rounding must sit well below any difference
    # worth seeing. At 6dp the agreement floor was 5e-7 and the test was
    # measuring this function instead of the library.
    def clean(s: pd.Series) -> list[float | None]:
        return [None if pd.isna(v) else round(float(v), REFERENCE_DP) for v in s]

    return {"ema20": clean(ema20), "rsi14": clean(rsi14), "atr14": clean(atr14)}


# --- Box-whisker: the vol clock as a distribution -------------------------
def whisker_buckets(days: list[date], minutes: int) -> list[dict]:
    """Per-bucket quartiles of 1-minute bar range, in ticks, across many sessions.

    This is the vol-clock finding drawn instead of tabulated: ATR sets the clock,
    so the interesting object is the *shape* of the range distribution through
    the session, not its mean. A box plot is the honest mark for it, and until
    box-whisker-series there was no way to draw one on the same engine as the
    candles — DistributionChart.tsx does it in Recharts, off-engine.

    Outliers use the standard 1.5 x IQR fence. They are the point: the tails are
    where the sub-30s trades and the stop-outs live.
    """
    per_bucket: dict[int, list[float]] = {}
    for day in days:
        got = session_frame(day)
        if got is None:
            continue
        _, df = got
        rth = df[df["seg"] == "rth"]
        if rth.empty:
            continue
        bars = minute_bars(rth)
        rng = ((bars["high"] - bars["low"]) / TICK).astype(float)
        et = bars["minute"].dt.tz_convert(ET_TZ) if bars["minute"].dt.tz is not None else bars["minute"]
        key = et.dt.hour * 60 + (et.dt.minute // minutes) * minutes
        for k, v in zip(key.to_numpy(), rng.to_numpy(), strict=True):
            per_bucket.setdefault(int(k), []).append(float(v))

    out: list[dict] = []
    for k in sorted(per_bucket):
        vals = np.asarray(per_bucket[k], dtype=float)
        if len(vals) < 30:
            continue
        q1, q2, q3 = (float(x) for x in np.percentile(vals, [25, 50, 75]))
        iqr = q3 - q1
        lo_fence, hi_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        inside = vals[(vals >= lo_fence) & (vals <= hi_fence)]
        outliers = vals[(vals < lo_fence) | (vals > hi_fence)]
        # A quartiles tuple is [q0, q1, q2, q3, q4]; the series treats q0/q4 as
        # the whisker ends, so they are the fenced extremes, not the raw min/max.
        out.append(
            {
                "time": int(
                    pd.Timestamp(f"2026-01-01 {k // 60:02d}:{k % 60:02d}").timestamp()
                ),
                "label": f"{k // 60:02d}:{k % 60:02d}",
                "quartiles": [
                    round(float(inside.min()), 2),
                    round(q1, 2),
                    round(q2, 2),
                    round(q3, 2),
                    round(float(inside.max()), 2),
                ],
                "outliers": [
                    round(float(v), 2)
                    for v in np.unique(np.round(outliers, 1))[:40]
                ],
                "n": int(len(vals)),
            }
        )
    return out


# --- Assembly -------------------------------------------------------------
def build_payload(days: list[date], whisker_days: list[date], footprint_minutes: int) -> dict:
    sessions = []
    for day in days:
        got = session_frame(day)
        if got is None:
            print(f"! {day}: no ticks on disk, skipped")
            continue
        sym, df = got
        rth = df[df["seg"] == "rth"]
        if rth.empty:
            print(f"! {day}: no RTH segment, skipped")
            continue
        m1 = minute_bars(df)
        of = order_flow_bars(rth, footprint_minutes)
        print(
            f"  {day} {sym}: {len(df):>7,} ticks -> {len(of)} footprint bars, "
            f"{len(m1)} minute bars"
        )
        sessions.append(
            {
                "day": f"{day:%Y-%m-%d}",
                "symbol": sym,
                "orderFlow": of,
                "bars": [
                    {
                        "time": int(t),
                        "open": round(float(o), 2),
                        "high": round(float(h), 2),
                        "low": round(float(lo), 2),
                        "close": round(float(c), 2),
                        "volume": int(v),
                    }
                    for t, o, h, lo, c, v in zip(
                        et_seconds(m1["minute"]),
                        m1["open"], m1["high"], m1["low"], m1["close"], m1["volume"],
                        strict=True,
                    )
                ],
                "reference": reference_series(m1),
                "rthOpen": int(
                    pd.Timestamp(f"{day} 09:30").timestamp()
                ),
            }
        )

    print(f"  vol clock: {len(whisker_days)} sessions", end="", flush=True)
    whisker = whisker_buckets(whisker_days, BUCKET_MINUTES)
    print(f" -> {len(whisker)} buckets")

    return {
        "meta": {
            "instrument": INSTRUMENT,
            "tick": TICK,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "footprintMinutes": footprint_minutes,
            "bucketMinutes": BUCKET_MINUTES,
            "referenceEpsilon": 0.5 * 10 ** -REFERENCE_DP,
            "whiskerSessions": len(whisker_days),
        },
        "sessions": sessions,
        "whisker": whisker,
    }


def chart_lib() -> str:
    if BUNDLE_JS.exists():
        return BUNDLE_JS.read_text()
    raise SystemExit(
        f"! {BUNDLE_JS} not found — run `pnpm install && pnpm build` in demo/vendor first"
    )


def main() -> None:
    args = sys.argv[1:]
    footprint_minutes = FOOTPRINT_MINUTES
    whisker_n = WHISKER_SESSIONS
    explicit: list[date] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--footprint":
            footprint_minutes = int(args[i + 1]); i += 2
        elif a == "--whisker":
            whisker_n = int(args[i + 1]); i += 2
        else:
            explicit.append(datetime.strptime(a, "%Y-%m-%d").date()); i += 1

    available = cached_days()
    if not available:
        raise SystemExit("! no tick cache on disk")
    days = sorted(explicit) if explicit else available[-SESSIONS:]
    whisker_days = available[-whisker_n:]

    print(f"lwc-addons: {len(days)} session(s), footprint {footprint_minutes}m")
    payload = build_payload(days, whisker_days, footprint_minutes)
    if not payload["sessions"]:
        raise SystemExit("! nothing to draw")

    template = (OUT_DIR / "_lwc_addons_template.html").read_text()
    html = (
        template
        .replace("__LWC_ADDONS_JS__", chart_lib())
        .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    )
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(html)
    print(f"-> {HTML_OUT.relative_to(ROOT)}  ({len(html) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
