"""Big-trade indicator demo — every print over 50 lots, on the chart.

One HTML page, one tab per session: 1-minute RTH candles + session VWAP, with
each large trade drawn as a bubble at its exact price and time (radius scales
with lot count, green = buy aggressor, red = sell aggressor). Under the chart:
a per-minute big-lot delta pane, a sortable print table, and a session stat row.

Two things are adjustable in the page itself, so the demo is also a probe:

  * threshold slider — the ">50 lots" line moves live (20 … 150)
  * prints vs sweeps — a 300-lot order arrives as a burst of fills, not one
    print. "Sweeps" glues consecutive same-side fills within 250 ms and 1.00 pt
    into a single event before the threshold is applied.

Reads only the existing tick cache (data/cache/ticks/*_day.parquet) — never
fetches, so it costs nothing at Databento.

Writes ``docs/research/big-trades.html``, which the Lab's Research tab lists and
serves; the charting library is inlined, so the page also opens straight off
disk with no network.

    uv run python demo/big_trades_demo.py            # 5 most recent sessions
    uv run python demo/big_trades_demo.py 2026-06-30 2026-06-29
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
BIG_LOTS = 50            # the headline threshold: a "big trade" is > this
EMBED_MIN_LOTS = 20      # rows shipped to the page (slider floor)
N_SESSIONS = 5           # when no dates are given on the command line

SWEEP_GAP_MS = 250       # consecutive fills closer than this can merge …
SWEEP_SPAN_PTS = 1.00    # … if they also stay within this price span

FOLLOW_MIN = 5           # "move after" column: price N minutes later

OUT_DIR = Path(__file__).resolve().parent
# The page lands in docs/research, where the Lab's Research tab lists it and
# serves it into a sandboxed iframe (api/routers/research.py). Written as bare
# page content — no <!doctype>/<html>/<body> — like the other artifact-sourced
# pages there; the router wraps it in a document shell when serving.
HTML_OUT = ROOT / "docs" / "research" / "big-trades.html"
LWC_JS = ROOT / "frontend" / "node_modules" / "lightweight-charts" / "dist" / (
    "lightweight-charts.standalone.production.js"
)
LWC_CDN = "https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js"

# Aggressor side. Verified against the cache rather than taken from the vendor
# doc: over a session, 'B' prints sit ~+0.35 pt above the local 41-trade mid and
# 'A' prints ~0.35 pt below it — i.e. 'B' lifts the offer (buy) and 'A' hits the
# bid (sell). Same sign as api/sim_charts.py; src/journal/sim/interactions.py
# signs these the other way round.
BUY, SELL = "B", "A"


# --- Session assembly -----------------------------------------------------
def cached_sessions(limit: int) -> list[tuple[date, str]]:
    """The `limit` most recent (day, contract) pairs with RTH ticks on disk."""
    days: dict[date, str] = {}
    for p in sorted((tickmod.CACHE_DIR / "ticks").glob("*_day.parquet")):
        sym, day_s, _ = p.stem.split("_")
        days[datetime.strptime(day_s, "%Y-%m-%d").date()] = sym
    return [(d, days[d]) for d in sorted(days)[-limit:]]


def contract_on(day: date) -> str | None:
    sym = tickmod.contract_for_cached(INSTRUMENT, day)
    if sym and tickmod.have_segment(sym, day, "rth"):
        return sym
    for p in (tickmod.CACHE_DIR / "ticks").glob(f"*_{day:%Y-%m-%d}_day.parquet"):
        return p.stem.split("_")[0]
    return None


def minute_bars(rth: pd.DataFrame) -> pd.DataFrame:
    """1-minute OHLCV + running session VWAP/σ, indexed by ET minute."""
    df = rth.sort_values("ts_utc").reset_index(drop=True)
    p, v = df["price"].astype(float), df["size"].astype(float)
    cum_v = v.cumsum().where(lambda c: c != 0)
    df["_vwap"] = (p * v).cumsum() / cum_v
    var = (p * p * v).cumsum() / cum_v - df["_vwap"] ** 2
    df["_std"] = var.clip(lower=0) ** 0.5
    df["_min"] = df["ts_utc"].dt.tz_convert(ET_TZ).dt.floor("min")
    g = df.groupby("_min", sort=True)
    bars = g.agg(
        open=("price", "first"), high=("price", "max"),
        low=("price", "min"), close=("price", "last"),
        volume=("size", "sum"), trades=("price", "size"),
        vwap=("_vwap", "last"), std=("_std", "last"),
    ).reset_index()
    buy = df["size"].where(df["side"] == BUY, 0.0)
    sell = df["size"].where(df["side"] == SELL, 0.0)
    bars["delta"] = (buy.groupby(df["_min"]).sum() - sell.groupby(df["_min"]).sum()).to_numpy()
    return bars


def sweeps(win: pd.DataFrame) -> pd.DataFrame:
    """Merge consecutive same-side fills inside SWEEP_GAP_MS / SWEEP_SPAN_PTS.

    A resting-size order works through the book as many prints; the sweep is the
    order-shaped unit. The run breaks on a side change, a time gap, or a price
    that has travelled further than the span from where the run began.
    """
    ts = win["ts_utc"].to_numpy("datetime64[ns]")
    side = win["side"].to_numpy()
    price = win["price"].to_numpy(dtype=float)
    size = win["size"].to_numpy(dtype=float)

    gap_ms = np.diff(ts).astype("timedelta64[ms]").astype(float)
    new_run = np.empty(len(win), dtype=bool)
    new_run[0] = True
    new_run[1:] = (gap_ms > SWEEP_GAP_MS) | (side[1:] != side[:-1])
    # Price span is measured from each run's own anchor, so a run can only be
    # closed by walking too far — not by a sequence of small steps.
    run = np.cumsum(new_run) - 1
    anchor = price[new_run]
    while True:
        far = np.abs(price - anchor[run]) > SWEEP_SPAN_PTS
        if not far.any():
            break
        new_run |= far
        run = np.cumsum(new_run) - 1
        anchor = price[new_run]

    out = pd.DataFrame({"run": run, "ts_utc": win["ts_utc"].to_numpy(),
                        "price": price, "size": size, "side": side})
    g = out.groupby("run", sort=True)
    sw = g.agg(ts_utc=("ts_utc", "first"), price=("price", "last"),
               size=("size", "sum"), side=("side", "first"), fills=("size", "size"))
    return sw.reset_index(drop=True)


def follow_through(events: pd.DataFrame, rth: pd.DataFrame, minutes: int) -> np.ndarray:
    """Price `minutes` later minus the event price (NaN past the session end)."""
    t = rth["ts_utc"].to_numpy("datetime64[ns]")
    p = rth["price"].to_numpy(dtype=float)
    target = events["ts_utc"].to_numpy("datetime64[ns]") + np.timedelta64(minutes * 60, "s")
    idx = np.searchsorted(t, target, side="left")
    out = np.where(idx < len(p), p[np.clip(idx, 0, len(p) - 1)], np.nan)
    return np.round(out - events["price"].to_numpy(dtype=float), 2)


def event_rows(ev: pd.DataFrame, rth: pd.DataFrame, bar_epoch: dict) -> list[dict]:
    """One JSON row per candidate event, carrying the bar it belongs to and the
    fraction through that minute it landed (the bubble's sub-bar x offset)."""
    et = ev["ts_utc"].dt.tz_convert(ET_TZ)
    minute = et.dt.floor("min")
    ev = ev.assign(_bar=minute.map(bar_epoch), _frac=et.dt.second / 60.0)
    ev = ev[ev["_bar"].notna()]
    fwd = follow_through(ev, rth, FOLLOW_MIN)
    rows = []
    for (_, r), f in zip(ev.iterrows(), fwd):
        rows.append({
            "bar": int(r["_bar"]), "frac": round(float(r["_frac"]), 3),
            "et": r["ts_utc"].tz_convert(ET_TZ).strftime("%H:%M:%S.%f")[:-3],
            "price": round(float(r["price"]), 2), "size": int(r["size"]),
            "buy": bool(r["side"] == BUY),
            "fills": int(r.get("fills", 1)),
            "fwd": None if not np.isfinite(f) else float(f),
        })
    return rows


def build_session(day: date, symbol: str) -> dict | None:
    rth = tickmod.cached_rth(symbol, day)
    if rth is None or rth.empty:
        return None
    rth = rth.sort_values("ts_utc").reset_index(drop=True)
    bars = minute_bars(rth)
    epoch = (bars["_min"].dt.tz_localize(None).astype("datetime64[s]")
             .astype("int64").to_numpy())
    bar_epoch = dict(zip(bars["_min"], epoch))

    bars_json = [
        {"time": int(t), "open": round(float(o), 2), "high": round(float(h), 2),
         "low": round(float(lo), 2), "close": round(float(c), 2), "volume": int(v)}
        for t, o, h, lo, c, v in zip(epoch, bars["open"], bars["high"], bars["low"],
                                     bars["close"], bars["volume"])
    ]
    vwap_json = [{"time": int(t), "value": round(float(w), 2)}
                 for t, w in zip(epoch, bars["vwap"])]

    big = rth[rth["size"] >= EMBED_MIN_LOTS]
    sw = sweeps(rth)
    sw_big = sw[sw["size"] >= EMBED_MIN_LOTS]

    vol = float(rth["size"].sum())
    over = rth[rth["size"] > BIG_LOTS]
    return {
        "date": day.isoformat(),
        "info": {
            "title": f"{day.strftime('%a %Y-%m-%d')} · 09:30-16:00 ET",
            "symbol": symbol,
            "bars": len(bars_json),
            "trades": int(len(rth)),
            "volume": int(vol),
            "range": f"{rth['price'].min():.2f} - {rth['price'].max():.2f}",
            "big_default": int(len(over)),
            "big_lots": int(over["size"].sum()),
            "big_share": round(100.0 * float(over["size"].sum()) / vol, 2) if vol else 0.0,
            "largest": int(rth["size"].max()),
        },
        "bars": bars_json,
        "vwap": vwap_json,
        "prints": event_rows(big, rth, bar_epoch),
        "sweeps": event_rows(sw_big, rth, bar_epoch),
    }


# --- Output ---------------------------------------------------------------
def main() -> None:
    if len(sys.argv) > 1:
        wanted = [datetime.strptime(a, "%Y-%m-%d").date() for a in sys.argv[1:]]
        pairs = [(d, s) for d in sorted(wanted) if (s := contract_on(d))]
        missing = sorted(set(wanted) - {d for d, _ in pairs})
        if missing:
            print("no cached RTH ticks for: " + ", ".join(str(m) for m in missing))
    else:
        pairs = cached_sessions(N_SESSIONS)
    if not pairs:
        raise SystemExit("nothing to draw — no cached RTH sessions found")

    sessions = []
    for day, sym in pairs:
        print(f"[{sym} {day}] reading cached RTH ticks …")
        s = build_session(day, sym)
        if s:
            sessions.append(s)
    if not sessions:
        raise SystemExit("every requested session came back empty")

    _write_html(sessions)
    print(f"\nbig trades (> {BIG_LOTS} lots), per session:")
    for s in sessions:
        i = s["info"]
        print(f"  {i['title']}: {i['big_default']:>3} prints  "
              f"{i['big_lots']:>6,} lots ({i['big_share']:.2f}% of volume)  "
              f"largest {i['largest']}  ·  {i['trades']:,} trades total")
    print(f"\nWrote {HTML_OUT}")
    print("Open:  file://" + str(HTML_OUT))


def _chart_lib() -> str:
    """The charting library, inlined.

    The page has to stand on its own: served into a sandboxed iframe, opened
    straight off disk, or read on a machine that isn't this one. A CDN <script>
    is a live dependency in all three. Falls back to the CDN tag only when the
    local build isn't installed.
    """
    if LWC_JS.exists():
        return LWC_JS.read_text()
    print(f"! {LWC_JS.name} not found — falling back to the CDN (page needs network)")
    return f'</script><script src="{LWC_CDN}">'


def _write_html(sessions: list[dict]) -> None:
    template = (OUT_DIR / "_big_trades_template.html").read_text()
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    html = (
        template
        .replace("__LWC_JS__", _chart_lib())
        .replace("__SESSIONS_JSON__", json.dumps(sessions))
        .replace("__BIG_LOTS__", str(BIG_LOTS))
        .replace("__EMBED_MIN__", str(EMBED_MIN_LOTS))
        .replace("__FOLLOW_MIN__", str(FOLLOW_MIN))
        .replace("__SWEEP_GAP__", str(SWEEP_GAP_MS))
        .replace("__SWEEP_SPAN__", f"{SWEEP_SPAN_PTS:.2f}")
    )
    HTML_OUT.write_text(html)


if __name__ == "__main__":
    main()
