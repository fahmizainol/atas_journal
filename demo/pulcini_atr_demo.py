"""Pulcini's ATR skeleton, drawn on our tape.

Scott Pulcini's 19 "strategies" are one skeleton (see
``docs/research/pulcini-scalper-podcast-2026-08.md``): a location, a volume
event at it, then everything after that denominated in ATR —

    3. confirmation  price escapes the event zone by one 5-min ATR + 15%
    4. stop          far side of the zone, another ATR + 15% beyond it
    5. size          flat ~$500 of risk, contracts derived from that distance

Steps 1-2 need CME MBO (iceberg / stop-run labels) that we don't have. Steps
3-5 are pure geometry and need nothing but ticks, so that's what this draws.
The event is stood in for by a **big-lot burst** — a cluster of large sweeps at
one price inside a minute. That is a leaky proxy for an iceberg, deliberately
so: the point of the demo is the ATR geometry hanging off the event, not the
event detector.

Read it as a diagram, not a backtest. There is no exit rule, no trade
management, no sample worth a p-value — the page reports how far the geometry
reaches and whether a stop was touched, and nothing that looks like an
expectancy.

The one comparison it does make is his stated cardinal retail sin: the same
entries carrying a fixed 15-point stop instead of an ATR-scaled one.

Reads only the existing tick cache (data/cache/ticks/*.parquet) — never
fetches, so it costs nothing at Databento.

    uv run python demo/pulcini_atr_demo.py            # 5 most recent sessions
    uv run python demo/pulcini_atr_demo.py 2026-06-30 2026-06-27
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

# --- Config ---------------------------------------------------------------
INSTRUMENT = "NQ"
N_SESSIONS = 5           # when no dates are given on the command line

BAR_MIN = 5              # his ATR is read off the 5-minute chart — this is his
# The lookback is *not* his: the episode says "5-min ATR" and never names a
# period. 14 is Wilder's default; 5 and 10 are what a scalper reading a faster
# number would more plausibly have on the chart. The switch exists to show that
# it barely matters — 5-min NQ vol is persistent enough over 25-70 minutes that
# ATR(5) and ATR(14) land within a few points of each other, so the stop width
# is not an artifact of this choice. See `risk_atr`: the stop is ~2.3x ATR by
# construction (zone width + 2 x mult x ATR), whatever the lookback.
ATR_PERIODS = (5, 10, 14)
DEFAULT_PERIOD = 14

# Confirmation / stop distance = ATR x MULT. 1.15 is his "ATR + 15%"; the
# other two are drawn so the page can show what the multiplier is doing.
MULTS = (1.00, 1.15, 1.50)
DEFAULT_MULT = 1.15

# --- Event proxy (stands in for the iceberg / stop-run print) --------------
SWEEP_GAP_MS = 250       # consecutive fills merge into one order-shaped sweep …
SWEEP_SPAN_PTS = 1.00    # … while they stay within this price span
SWEEP_LOTS = 50          # a sweep this size counts toward a burst
EVENT_GAP_S = 60         # sweeps this close in time join the same burst
EVENT_SPAN_PTS = 5.0     # … if they also stay this close in price
EVENT_LOTS = 150         # a burst needs this much size in total to be an event
EVENT_COOLDOWN_S = 300   # ignore a new event this soon after the last one
MIN_ZONE_PTS = 1.0       # a zone is never thinner than this (4 ticks)

CONFIRM_WINDOW_MIN = 60  # price gets this long to escape the zone by an ATR
RESOLVE_MIN = 120        # how far past entry the stop watch runs

RISK_USD = 500.0         # his flat per-trade risk
POINT_USD = 2.0          # MNQ — he says "mostly micros"
FIXED_STOP_PTS = 15.0    # the fixed-point stop he calls the cardinal sin

OUT_DIR = Path(__file__).resolve().parent
# Lands in docs/research, where the Lab's Research tab lists it and serves it
# into a sandboxed iframe (api/routers/research.py). Written as bare page
# content — the router wraps it in a document shell.
HTML_OUT = ROOT / "docs" / "research" / "pulcini-atr.html"
LWC_JS = ROOT / "frontend" / "node_modules" / "lightweight-charts" / "dist" / (
    "lightweight-charts.standalone.production.js"
)
LWC_CDN = "https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js"

# 'B' lifts the offer (buy aggressor), 'A' hits the bid — measured against the
# cache, same sign as api/sim_charts.py. Only used for colouring the burst here;
# the event proxy itself is side-agnostic, like the big-lot signal in
# demo/big_trades_demo.py.
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


def bars_from(ticks: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """OHLC bars on an ET clock, indexed by bar-open minute."""
    df = ticks.sort_values("ts_utc")
    et = df["ts_utc"].dt.tz_convert(ET_TZ)
    key = et.dt.floor(f"{minutes}min")
    g = df.groupby(key, sort=True)
    bars = g.agg(open=("price", "first"), high=("price", "max"),
                 low=("price", "min"), close=("price", "last"),
                 volume=("size", "sum")).reset_index(names="et")
    return bars


def sweeps(win: pd.DataFrame) -> pd.DataFrame:
    """Merge consecutive same-side fills into order-shaped sweeps.

    Lifted from demo/big_trades_demo.py: a resting-size order works through the
    book as many prints, and the sweep is the unit a human would call one trade.
    """
    ts = win["ts_utc"].to_numpy("datetime64[ns]")
    side = win["side"].to_numpy()
    price = win["price"].to_numpy(dtype=float)
    size = win["size"].to_numpy(dtype=float)

    gap_ms = np.diff(ts).astype("timedelta64[ms]").astype(float)
    new_run = np.empty(len(win), dtype=bool)
    new_run[0] = True
    new_run[1:] = (gap_ms > SWEEP_GAP_MS) | (side[1:] != side[:-1])
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
    sw = g.agg(ts_utc=("ts_utc", "first"), end_utc=("ts_utc", "last"),
               price=("price", "last"), size=("size", "sum"),
               side=("side", "first"))
    return sw.reset_index(drop=True)


def bursts(sw: pd.DataFrame) -> list[dict]:
    """Cluster big sweeps into the volume events this demo trades off.

    A burst is consecutive big sweeps inside EVENT_GAP_S and EVENT_SPAN_PTS of
    each other. Its zone is the price span it printed across — the band of
    committed/trapped size his skeleton hangs off.
    """
    big = sw[sw["size"] >= SWEEP_LOTS].reset_index(drop=True)
    events: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if not cur:
            return
        lots = sum(r["size"] for r in cur)
        if lots < EVENT_LOTS:
            return
        lo = min(r["price"] for r in cur)
        hi = max(r["price"] for r in cur)
        if hi - lo < MIN_ZONE_PTS:                 # a single-price event still
            mid = (hi + lo) / 2.0                  # needs a zone with width
            lo, hi = mid - MIN_ZONE_PTS / 2, mid + MIN_ZONE_PTS / 2
        buy = sum(r["size"] for r in cur if r["side"] == BUY)
        events.append({
            "start": cur[0]["ts_utc"], "end": cur[-1]["end_utc"],
            "lo": lo, "hi": hi, "lots": lots, "sweeps": len(cur),
            "buy_lots": buy, "sell_lots": lots - buy,
        })

    for r in big.to_dict("records"):
        if cur:
            gap = (r["ts_utc"] - cur[-1]["end_utc"]).total_seconds()
            span = max(abs(r["price"] - c["price"]) for c in cur)
            if gap > EVENT_GAP_S or span > EVENT_SPAN_PTS:
                flush()
                cur = []
        cur.append(r)
    flush()

    # Drop events that land on top of the one before — the skeleton needs a
    # zone to escape, not a rolling stream of overlapping ones.
    kept: list[dict] = []
    for e in events:
        if kept and (e["start"] - kept[-1]["start"]).total_seconds() < EVENT_COOLDOWN_S:
            continue
        kept.append(e)
    return kept


# --- The skeleton ---------------------------------------------------------
def atr_at(bars: pd.DataFrame, atr: pd.Series, ts: pd.Timestamp) -> float:
    """ATR off the last 5-min bar that had *closed* before `ts`.

    Causal on purpose: the bar the event lands in is still forming when the
    trader would be reading the number, and its own range is partly the event.
    """
    et = ts.tz_convert(ET_TZ)
    idx = bars.index[bars["et"] + pd.Timedelta(minutes=BAR_MIN) <= et]
    if len(idx) == 0:
        return float("nan")
    return float(atr.iloc[idx[-1]])


def trace(ev: dict, atr_pts: float, mult: float,
          ts: np.ndarray, px: np.ndarray) -> dict:
    """Walk the tape forward through steps 3-5 for one event and multiplier."""
    d = atr_pts * mult
    up, dn = ev["hi"] + d, ev["lo"] - d          # confirmation levels
    out: dict = {"atr": round(atr_pts, 2), "reach": round(d, 2),
                 "up": round(up, 2), "dn": round(dn, 2)}

    start = int(np.searchsorted(ts, np.datetime64(ev["end"].tz_convert("UTC").tz_localize(None)), "right"))
    deadline = ts[start] + np.timedelta64(CONFIRM_WINDOW_MIN * 60, "s") if start < len(ts) else None
    win = slice(start, int(np.searchsorted(ts, deadline, "left")) if deadline is not None else start)
    seg = px[win]
    if seg.size == 0:
        out["state"] = "no room"                 # event too late in the session
        return out

    hit_up = np.flatnonzero(seg >= up)
    hit_dn = np.flatnonzero(seg <= dn)
    i_up = hit_up[0] if hit_up.size else None
    i_dn = hit_dn[0] if hit_dn.size else None
    if i_up is None and i_dn is None:
        out["state"] = "no escape"               # snapped back, never confirmed
        return out
    long_ = i_dn is None or (i_up is not None and i_up < i_dn)
    i = int(win.start + (i_up if long_ else i_dn))

    entry = float(px[i])
    stop = ev["lo"] - d if long_ else ev["hi"] + d
    risk = abs(entry - stop)
    lots = int(RISK_USD // (risk * POINT_USD))

    # Forward path: the ATR stop, the fixed-point stop he calls the retail sin,
    # and how far the move ran. No exit rule, so no result beyond that.
    end = int(np.searchsorted(ts, ts[i] + np.timedelta64(RESOLVE_MIN * 60, "s"), "left"))
    fwd = px[i:end]
    sgn = 1.0 if long_ else -1.0
    adverse = (entry - fwd) * sgn                 # points offside
    run = (fwd - entry) * sgn                     # points onside
    j_atr = np.flatnonzero(adverse >= risk)
    j_fix = np.flatnonzero(adverse >= FIXED_STOP_PTS)

    out.update({
        "state": "confirmed",
        "long": bool(long_),
        "entry_ts": str(pd.Timestamp(ts[i]).tz_localize("UTC").tz_convert(ET_TZ).strftime("%H:%M:%S")),
        "entry_bar": int(pd.Timestamp(ts[i]).tz_localize("UTC")
                         .tz_convert(ET_TZ).floor(f"{BAR_MIN}min")
                         .tz_localize(None).timestamp()),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "risk": round(risk, 2),
        "risk_usd": round(risk * POINT_USD, 2),
        # The invariant: zone width + 2 x mult x ATR, so ~2.3x ATR at x1.15
        # however loud or quiet the day is. This is what makes the stop wide,
        # not the lookback and not the vol regime.
        "risk_atr": round(risk / atr_pts, 2),
        "lots": lots,
        "wait_s": int((ts[i] - ts[win.start]).astype("timedelta64[s]").astype(int)),
        "mfe": round(float(run.max()) if run.size else 0.0, 2),
        "mae": round(float(adverse.max()) if adverse.size else 0.0, 2),
        "atr_stop_hit": bool(j_atr.size),
        "fixed_stop_hit": bool(j_fix.size),
        # Ordering is the whole point of the comparison: a 15-pt stop inside an
        # ATR-wide zone gets touched by noise the ATR stop never notices.
        "fixed_first": bool(j_fix.size and (not j_atr.size or j_fix[0] < j_atr[0])),
    })
    return out


def build_session(day: date, symbol: str) -> dict | None:
    rth = tickmod.cached_rth(symbol, day)
    if rth is None or rth.empty:
        return None
    rth = rth.sort_values("ts_utc").reset_index(drop=True)
    on = tickmod.cached_overnight(symbol, day)

    # ATR is computed across the overnight so it is already warm at 09:30 —
    # otherwise Wilder needs `period` bars and the first hour-odd of RTH, the
    # part of the session with most of the events, would have no number to
    # scale by. Matters most at 14, which is why the warm-up is unconditional.
    context = pd.concat([on, rth]) if on is not None and not on.empty else rth
    bars = bars_from(context, BAR_MIN)
    atrs = {p: atr_series(bars, p) for p in ATR_PERIODS}

    ts = rth["ts_utc"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    px = rth["price"].to_numpy(dtype=float)

    events = []
    for n, ev in enumerate(bursts(sweeps(rth)), 1):
        # An event survives only if every period has a number for it, so the
        # switch compares the same events rather than a shifting sample.
        vals = {p: atr_at(bars, a, ev["end"]) for p, a in atrs.items()}
        if any(not np.isfinite(v) or v <= 0 for v in vals.values()):
            continue
        et0 = ev["start"].tz_convert(ET_TZ)
        row = {
            "n": n,
            "t0": et0.strftime("%H:%M:%S"),
            "t1": ev["end"].tz_convert(ET_TZ).strftime("%H:%M:%S"),
            "bar": int(et0.floor(f"{BAR_MIN}min").tz_localize(None).timestamp()),
            "end_bar": int(ev["end"].tz_convert(ET_TZ).floor(f"{BAR_MIN}min")
                           .tz_localize(None).timestamp()),
            "lo": round(ev["lo"], 2), "hi": round(ev["hi"], 2),
            "lots": int(ev["lots"]), "sweeps": int(ev["sweeps"]),
            "buy_lots": int(ev["buy_lots"]), "sell_lots": int(ev["sell_lots"]),
            "runs": {f"{p}|{m:.2f}": trace(ev, vals[p], m, ts, px)
                     for p in ATR_PERIODS for m in MULTS},
        }
        events.append(row)

    # Chart shows the overnight in front of the session, like every other chart
    # in the app — and here it also shows the ATR warming up before the open.
    draw = bars[bars["et"] >= pd.Timestamp(day, tz=ET_TZ) - pd.Timedelta(hours=12)]
    epoch = draw["et"].dt.tz_localize(None).astype("datetime64[s]").astype("int64")
    rth_open = int(pd.Timestamp(f"{day} 09:30", tz=ET_TZ).tz_localize(None).timestamp())

    bars_json = [
        {"time": int(t), "open": round(float(o), 2), "high": round(float(h), 2),
         "low": round(float(lo), 2), "close": round(float(c), 2)}
        for t, o, h, lo, c in zip(epoch, draw["open"], draw["high"],
                                  draw["low"], draw["close"])
    ]
    atr_json = {
        str(p): [{"time": int(t), "value": round(float(v), 2)}
                 for t, v in zip(epoch, a.loc[draw.index]) if np.isfinite(v)]
        for p, a in atrs.items()
    }

    # Two ATR readings per period, and they differ on purpose: the session
    # median is the day's vol regime, the event median is the regime the events
    # actually landed in — bursts cluster in the violent part of the morning.
    rth_bars = bars["et"] >= pd.Timestamp(f"{day} 09:30", tz=ET_TZ)
    atr_med, atr_events = {}, {}
    for p, a in atrs.items():
        sess = a[rth_bars & a.notna()]
        atr_med[str(p)] = round(float(sess.median()), 1) if len(sess) else None
        at_ev = [e["runs"][f"{p}|{DEFAULT_MULT:.2f}"]["atr"] for e in events]
        atr_events[str(p)] = round(float(np.median(at_ev)), 1) if at_ev else None
    return {
        "date": day.isoformat(),
        "info": {
            "title": f"{day.strftime('%a %Y-%m-%d')} · 09:30-16:00 ET",
            "symbol": symbol,
            "trades": int(len(rth)),
            "range": f"{rth['price'].min():.2f} – {rth['price'].max():.2f}",
            "range_pts": round(float(rth["price"].max() - rth["price"].min()), 2),
            "atr_med": atr_med,
            "atr_events": atr_events,
            "events": len(events),
        },
        "rth_open": rth_open,
        "bars": bars_json,
        "atr": atr_json,
        "events": events,
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
        print(f"[{sym} {day}] reading cached ticks …")
        s = build_session(day, sym)
        if s:
            sessions.append(s)
    if not sessions:
        raise SystemExit("every requested session came back empty")

    _write_html(sessions)

    # The period sweep is the point of the readout: the stop width is roughly
    # linear in the lookback, and the lookback is the one number he never gave.
    for p in ATR_PERIODS:
        key = f"{p}|{DEFAULT_MULT:.2f}"
        print(f"\nATR({p}) on {BAR_MIN}-min bars, at x{DEFAULT_MULT}:")
        for s in sessions:
            runs = [e["runs"][key] for e in s["events"]]
            conf = [r for r in runs if r["state"] == "confirmed"]
            head = (f"  {s['date']}: {len(runs):>2} events, "
                    f"{len(conf):>2} confirmed")
            if not conf:
                print(head)
                continue
            over = sum(1 for r in conf if r["lots"] == 0)
            print(f"{head}  ·  session ATR {s['info']['atr_med'][str(p)]:>5}pt  "
                  f"median stop {np.median([r['risk'] for r in conf]):>6.1f}pt "
                  f"(${np.median([r['risk_usd'] for r in conf]):>6.0f}/micro)  ·  "
                  f"{sum(r['fixed_first'] for r in conf)}/{len(conf)} trip a "
                  f"{FIXED_STOP_PTS:.0f}pt stop first, {over} over the "
                  f"${RISK_USD:.0f} budget")
    print(f"\nWrote {HTML_OUT}")
    print("Open:  file://" + str(HTML_OUT))


def _chart_lib() -> str:
    """The charting library, inlined, so the page stands alone off disk."""
    if LWC_JS.exists():
        return LWC_JS.read_text()
    print(f"! {LWC_JS.name} not found — falling back to the CDN (page needs network)")
    return f'</script><script src="{LWC_CDN}">'


def _write_html(sessions: list[dict]) -> None:
    template = (OUT_DIR / "_pulcini_atr_template.html").read_text()
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    html = (
        template
        .replace("__LWC_JS__", _chart_lib())
        .replace("__SESSIONS_JSON__", json.dumps(sessions))
        .replace("__MULTS_JSON__", json.dumps([f"{m:.2f}" for m in MULTS]))
        .replace("__DEFAULT_MULT__", f"{DEFAULT_MULT:.2f}")
        .replace("__PERIODS_JSON__", json.dumps([str(p) for p in ATR_PERIODS]))
        .replace("__DEFAULT_PERIOD__", str(DEFAULT_PERIOD))
        .replace("__BAR_MIN__", str(BAR_MIN))
        .replace("__EVENT_LOTS__", str(EVENT_LOTS))
        .replace("__SWEEP_LOTS__", str(SWEEP_LOTS))
        .replace("__RISK_USD__", f"{RISK_USD:.0f}")
        .replace("__POINT_USD__", f"{POINT_USD:.0f}")
        .replace("__FIXED_STOP__", f"{FIXED_STOP_PTS:.0f}")
        .replace("__CONFIRM_WINDOW__", str(CONFIRM_WINDOW_MIN))
        .replace("__RESOLVE_MIN__", str(RESOLVE_MIN))
    )
    HTML_OUT.write_text(html)


if __name__ == "__main__":
    main()
