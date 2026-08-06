"""Composite volume profiles — how the compositing rule changes what you see.

A composite profile merges several sessions into one distribution and you read
levels off the result: value-area edges, POC, HVN humps, LVN troughs. The
question nobody answers out loud is *which* sessions belong in it. This page
puts four rules side by side on the same data:

    balance   accumulate while each new session's value area still touches the
              composite-so-far's; restart on a clean break (the orthodox rule —
              the market decides, not a lookback constant)
    3-day     fixed window, the pragmatic stand-in
    10-day    fixed window
    20-day    fixed window, the common convention

The point of the comparison is what happens to *value*. Stretch the window and
the value area inflates until it stops naming a location at all — on NQ the
median 70% value area runs ~226 pt under the balance rule and ~1630 pt over 20
sessions, which is most of the range. A "value-area edge" that wide is not a
level.

Two things the page deliberately does not claim. Bimodality is *not* evidence of
merged auctions — a single-session double distribution is a textbook trend-day
shape, and ~30% of lone NQ sessions print two prominent humps at a strict
prominence threshold. And the split-value-area flag fires often under every rule
here, because NQ sessions are frequently split on their own; it is a property
worth seeing, not a discriminator between rules.

Everything is drawn as one SVG per composite — a price panel and the profile
share a single price axis, so a hump lines up with the prices it came from.

The page also overlays **events**: Pulcini's step 2 is something happening *at*
the level, which in his hands is an MBO iceberg or stop-run label. We have no
MBO, so two trade-print proxies stand in — sweep bursts (aggressive size
arriving at once) and absorption (size trading with nowhere to go). They come
with a null, because events land where price traded and price traded most where
the composite says value is: the read-out compares their distance from the
frozen POC/VAH/VAL against the same distance for the session's own volume. On
this data they sit *further* out than the tape, on both proxies.

Reads only the existing tick cache (data/cache/ticks/*.parquet) — never
fetches, so it costs nothing at Databento.

    uv run python demo/composite_profile_demo.py          # last 40 sessions
    uv run python demo/composite_profile_demo.py 60       # last 60
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
TICK = 0.25
N_SESSIONS = 40          # how much history to composite over
VA_PCT = 0.70            # value area, the Market Profile convention
BAR_MINS = (5, 15, 30, 60)   # price-panel timeframes, switchable in the page
DEFAULT_BAR_MIN = 30
LIVE_STEP_MIN = 15           # scrub resolution of the live view
LIVE_BUCKETS = 110           # histogram resolution for the developing profile
MAX_BUCKETS = 320        # histogram resolution shipped to the page
BALANCE_CAP = 5          # runaway guard on the balance rule (p90 of runs is 4)

# A hump counts as a node only if it stands this far clear of the deeper of the
# two valleys flanking it, relative to the tallest hump. Prominence rather than
# raw height, so a shoulder on the POC is not counted as a second mode.
# Both are swept and shipped so the page can vary them live; the defaults are
# what the CLI summary reports. 0.35/0.04 was chosen by checking how many humps
# a *single* session shows — at 0.25 it was 85% of sessions, which is how the
# "bimodality means merged auctions" claim got disproved.
PROM_LEVELS = (0.15, 0.25, 0.35, 0.45, 0.60)
SMOOTH_LEVELS = (0.02, 0.04, 0.08)
DEFAULT_PROM, DEFAULT_SMOOTH = 0.35, 0.04
# A value area is "split" when its own deepest trough falls below this share of
# the tallest hump — i.e. the middle of value is a price the auction rejected.
SPLIT_VA_FLOOR = 0.35

RULES = ("balance", "3-day", "10-day", "20-day")
DEFAULT_RULE = "balance"

# --- Event proxies --------------------------------------------------------
# Pulcini's step 2 is an *event at the level* — an MBO-labelled iceberg or
# stop-run, which is exactly the feed we don't have. Two things reachable from
# trade prints stand in, and they stand for different halves of it:
#
#   sweep    aggressive size arriving at once — the stop-run / initiative half.
#            Consecutive same-side fills glue into one order-shaped sweep, then
#            big sweeps near each other in time and price glue into a burst.
#   absorb   size trading with nowhere to go — the iceberg half. A window whose
#            volume-per-point-traversed runs far above the session's own median.
#
# Neither is the MBO label. A sweep is the aggressor's footprint, not the
# resting order that refilled; absorption cannot tell a refilling iceberg from
# a crowd of small passive sellers. They are here to be looked at against the
# composite levels, not to be believed.
BUY, SELL = "B", "A"          # 'B' lifts the offer — measured, see big_trades_demo

SWEEP_GAP_MS = 250            # consecutive fills merge into one sweep …
SWEEP_SPAN_PTS = 1.00         # … while they stay inside this span
SWEEP_LOTS = 50               # a sweep this size counts toward a burst
BURST_GAP_S = 60              # big sweeps this close in time join one burst …
BURST_SPAN_PTS = 5.0          # … if they also stay this close in price
BURST_LOTS = 150              # a burst needs this much size (strength 1.0)

# Absorption is defined *relative to the session*, not in absolute points: a
# 4-point band means nothing on a day whose median 15-second range is 12 points.
# (Measured: median 15s RTH range runs 4.75-6.00 pt across 2025-26 NQ, and an
# absolute band threshold finds either everything or nothing depending on the
# regime.) Concentration = lots per point traversed, scored against the
# session's own median.
ABSORB_WIN_S = 15
ABSORB_MULT = 3.0             # concentration this many x the median = strength 1.0

# Shown-event filter in the page, in units of that per-kind strength.
STRENGTHS = (1, 2, 3)
DEFAULT_STRENGTH = 1
DEFAULT_EVENTS = "off"
# "Near" a composite level, for the live view's read-out.
NEAR_PTS = 10.0

OUT_DIR = Path(__file__).resolve().parent
HTML_OUT = ROOT / "docs" / "research" / "composite-profile.html"


# --- Profile primitives ---------------------------------------------------
def value_area(levels: np.ndarray, vol: np.ndarray,
               pct: float = VA_PCT) -> tuple[float, float, float]:
    """POC and the tightest contiguous band holding `pct` of the volume.

    The standard expand-from-the-POC walk: repeatedly take whichever neighbour
    holds more volume until the band covers the target share.
    """
    lo = int(levels.min())
    h = np.bincount(levels - lo, weights=vol, minlength=int(levels.max()) - lo + 1)
    poc = int(h.argmax())
    total = h.sum()
    a = b = poc
    acc = h[poc]
    while acc < pct * total and (a > 0 or b < len(h) - 1):
        left = h[a - 1] if a > 0 else -1.0
        right = h[b + 1] if b < len(h) - 1 else -1.0
        if right >= left:
            b += 1
            acc += h[b]
        else:
            a -= 1
            acc += h[a]
    return (lo + a) * TICK, (lo + b) * TICK, (lo + poc) * TICK


def smoothed(vol: np.ndarray, smooth: float) -> np.ndarray:
    k = max(3, int(len(vol) * smooth) | 1)
    return pd.Series(vol).rolling(k, center=True, min_periods=1).mean().to_numpy()


def nodes(prices: np.ndarray, vol: np.ndarray, prom: float,
          smooth: float) -> tuple[list[dict], list[dict]]:
    """HVN humps and the LVN troughs between them, by prominence.

    Prominence is the standard definition: how far a peak stands above the
    higher of the two saddles separating it from any taller peak. Raw height
    would count every shoulder on the POC as its own node.

    Both knobs are swept at build time and shipped, because the right setting
    is not knowable in advance — it is exactly what a reader needs to feel out.
    """
    if len(vol) < 5:
        return [], []
    k = max(3, int(len(vol) * smooth) | 1)
    sm = pd.Series(vol).rolling(k, center=True, min_periods=1).mean().to_numpy()
    top = sm.max()
    if top <= 0:
        return [], []

    peaks = []
    for i in range(1, len(sm) - 1):
        if not (sm[i] >= sm[i - 1] and sm[i] > sm[i + 1]):
            continue
        l = i
        while l > 0 and sm[l - 1] <= sm[i]:
            l -= 1
        r = i
        while r < len(sm) - 1 and sm[r + 1] <= sm[i]:
            r += 1
        saddle = max(sm[l:i + 1].min(), sm[i:r + 1].min())
        peaks.append((i, sm[i], sm[i] - saddle))

    hvn = [{"price": round(float(prices[i]), 2),
            "height": round(float(v / top), 3),
            "prom": round(float(p / top), 3)}
           for i, v, p in peaks if p / top >= prom]
    hvn.sort(key=lambda d: -d["height"])

    # An LVN is only meaningful *between* two accepted humps — a trough at the
    # edge of the distribution is just where the auction stopped.
    lvn = []
    order = sorted(hvn, key=lambda d: d["price"])
    for x, y in zip(order, order[1:]):
        i = int(np.searchsorted(prices, x["price"]))
        j = int(np.searchsorted(prices, y["price"]))
        if j - i < 2:
            continue
        m = i + int(sm[i:j].argmin())
        lvn.append({"price": round(float(prices[m]), 2),
                    "depth": round(float(sm[m] / top), 3)})
    return hvn, lvn


# --- Event detection ------------------------------------------------------
def sweeps(win: pd.DataFrame) -> pd.DataFrame:
    """Merge consecutive same-side fills into order-shaped sweeps.

    Same construction as demo/big_trades_demo.py: a resting-size order works
    through the book as many prints, and the sweep is the unit a human reading
    the tape would call one trade. The run breaks on a side change, a time gap,
    or a price that has walked further than the span from where the run began.
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
    return g.agg(ts_utc=("ts_utc", "first"), end_utc=("ts_utc", "last"),
                 lo=("price", "min"), hi=("price", "max"),
                 size=("size", "sum"), side=("side", "first")).reset_index(drop=True)


def burst_events(rth: pd.DataFrame) -> list[dict]:
    """Big sweeps clustered in time and price — the stop-run / initiative half.

    No cooldown between bursts, unlike the ATR demo's version: that one needed
    one clean event to hang a trade off, and here repeated hits on one price are
    the whole thing worth seeing.
    """
    sw = sweeps(rth)
    big = sw[sw["size"] >= SWEEP_LOTS].reset_index(drop=True)
    events: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if not cur:
            return
        lots = sum(r["size"] for r in cur)
        if lots < BURST_LOTS:
            return
        buy = sum(r["size"] for r in cur if r["side"] == BUY)
        events.append({
            "kind": "sweep",
            "ts": cur[0]["ts_utc"],
            "lo": min(r["lo"] for r in cur), "hi": max(r["hi"] for r in cur),
            "lots": float(lots), "st": lots / BURST_LOTS,
            "buy": buy >= lots / 2, "n": len(cur),
        })

    for r in big.to_dict("records"):
        if cur:
            gap = (r["ts_utc"] - cur[-1]["end_utc"]).total_seconds()
            span = max(abs(r["hi"] - c["lo"]) for c in cur)
            if gap > BURST_GAP_S or span > BURST_SPAN_PTS:
                flush()
                cur = []
        cur.append(r)
    flush()
    return events


def absorb_events(rth: pd.DataFrame, et: pd.Series) -> list[dict]:
    """Windows where volume-per-point-traversed runs far above the session's own.

    The iceberg half of the proxy, and the weaker of the two: it sees that size
    traded without the price going anywhere, which is what a refilling passive
    order looks like from the trade feed — and also what a thick crowd of small
    passive orders looks like. Scored relative to the session because the same
    absolute band means opposite things in a quiet and a violent regime.
    """
    k = et.dt.floor(f"{ABSORB_WIN_S}s")
    a = rth.assign(_k=k, _b=rth["size"].where(rth["side"] == BUY, 0.0)).groupby(
        "_k", sort=True).agg(v=("size", "sum"), buy=("_b", "sum"),
                             lo=("price", "min"), hi=("price", "max"),
                             ts=("ts_utc", "first"))
    if len(a) < 20:
        return []
    conc = a["v"] / (a["hi"] - a["lo"]).clip(lower=TICK)
    med = float(conc.median())
    if not np.isfinite(med) or med <= 0:
        return []
    hot = (conc >= ABSORB_MULT * med).to_numpy()
    if not hot.any():
        return []

    # Adjacent hot windows are one absorption, not three — but a merge widens
    # the band as well as adding volume, so it is only taken while the *merged*
    # block still clears the bar. Otherwise the reported concentration could
    # fall below the threshold that selected the event in the first place, and
    # a strength floored at 1.0 would be a lie. A lone hot window always clears.
    keys = a.index.to_numpy("datetime64[s]").astype("int64")
    contiguous = np.empty(len(a), dtype=bool)
    contiguous[0] = False
    contiguous[1:] = np.diff(keys) <= ABSORB_WIN_S
    lo_a, hi_a, v_a = (a[c].to_numpy(float) for c in ("lo", "hi", "v"))
    conc_a = conc.to_numpy(float)
    floor = ABSORB_MULT * med
    events, i = [], 0
    while i < len(a):
        if not hot[i]:
            i += 1
            continue
        j, lo, hi, vol, cc = i + 1, lo_a[i], hi_a[i], v_a[i], conc_a[i]
        while j < len(a) and hot[j] and contiguous[j]:
            nlo, nhi, nv = min(lo, lo_a[j]), max(hi, hi_a[j]), vol + v_a[j]
            nc = nv / max(nhi - nlo, TICK)
            if nc < floor:
                break
            lo, hi, vol, cc = nlo, nhi, nv, nc
            j += 1
        events.append({
            "kind": "absorb", "ts": a["ts"].iloc[i],
            "lo": float(lo), "hi": float(hi), "lots": float(vol),
            "st": float(cc) / floor,
            "buy": float(a["buy"].iloc[i:j].sum()) >= vol / 2, "n": int(j - i),
        })
        i = j
    return events


def event_rows(events: list[dict], et: pd.Series,
               bar_index: dict[str, dict]) -> list[dict]:
    """Events keyed to the bar they land in, per timeframe, plus minutes-from-open.

    The minute is what the live view filters on, so a scrubbed session shows
    only the events that had already printed — same causality rule as the
    developing profile.
    """
    open_et = et.iloc[0]
    open_min = (open_et - open_et.normalize()).total_seconds() / 60
    rows = []
    for e in sorted(events, key=lambda d: d["ts"]):
        t = e["ts"].tz_convert(ET_TZ)
        bars = {}
        for m, idx in bar_index.items():
            b = idx.get(t.floor(f"{m}min"))
            if b is not None:
                bars[m] = int(b)
        if len(bars) != len(bar_index):
            continue
        rows.append({
            "k": e["kind"], "b": bars,
            "min": round((t - t.normalize()).total_seconds() / 60 - open_min, 1),
            "lo": round(e["lo"], 2), "hi": round(e["hi"], 2),
            "lots": int(e["lots"]), "st": round(float(e["st"]), 2),
            "buy": bool(e["buy"]),
        })
    return rows


# --- Session assembly -----------------------------------------------------
def cached_sessions(limit: int) -> list[tuple[date, str]]:
    days: dict[date, str] = {}
    for p in sorted((tickmod.CACHE_DIR / "ticks").glob("*_day.parquet")):
        sym, day_s, _ = p.stem.split("_")
        days[datetime.strptime(day_s, "%Y-%m-%d").date()] = sym
    return [(d, days[d]) for d in sorted(days)[-limit:]]


def load_session(day: date, symbol: str) -> dict | None:
    rth = tickmod.cached_rth(symbol, day)
    if rth is None or rth.empty:
        return None
    rth = rth.sort_values("ts_utc")
    lv = np.round(rth["price"].to_numpy(float) / TICK).astype(int)
    g = pd.Series(rth["size"].to_numpy(float)).groupby(lv).sum()
    val, vah, poc = value_area(g.index.to_numpy(), g.to_numpy())

    et = rth["ts_utc"].dt.tz_convert(ET_TZ)
    bars, bar_index = {}, {}
    for m in BAR_MINS:
        b = rth.assign(k=et.dt.floor(f"{m}min")).groupby("k").agg(
            open=("price", "first"), high=("price", "max"),
            low=("price", "min"), close=("price", "last"))
        bars[str(m)] = [{"o": round(o, 2), "h": round(h, 2),
                         "l": round(lo, 2), "c": round(c, 2)}
                        for o, h, lo, c in zip(b["open"], b["high"],
                                               b["low"], b["close"])]
        bar_index[str(m)] = {k: i for i, k in enumerate(b.index)}

    events = event_rows(burst_events(rth) + absorb_events(rth, et), et, bar_index)
    return {
        "day": day, "symbol": symbol,
        "levels": g.index.to_numpy(), "vol": g.to_numpy(),
        "val": val, "vah": vah, "poc": poc,
        "bars": bars, "events": events,
        "developing": developing(rth, et, lv,
                                 {k: len(v) for k, v in bars.items()}),
    }


def developing(rth: pd.DataFrame, et: pd.Series, lv: np.ndarray,
               bar_counts: dict[str, int]) -> list[dict]:
    """The session's profile as it actually looked, every LIVE_STEP_MIN.

    Strictly cumulative from the open — snapshot *k* uses only ticks that had
    printed by then, so nothing here can report a level the session had not yet
    built. (The developing-VA lookahead bug fixed on 2026-07-31 was exactly this
    mistake made the other way.)
    """
    size = rth["size"].to_numpy(float)
    mins = ((et - et.dt.normalize()).dt.total_seconds() / 60).to_numpy()
    open_min = mins[0]
    out = []
    lo_all, hi_all = int(lv.min()), int(lv.max())
    span = hi_all - lo_all + 1
    step = max(1, int(np.ceil(span / LIVE_BUCKETS)))
    # Stop at the session's own length: a checkpoint past the close would
    # label itself 16:30 and claim more bars than the session ever printed.
    last = int(mins[-1] - open_min)
    stops = list(range(LIVE_STEP_MIN, last + 1, LIVE_STEP_MIN))
    if not stops or stops[-1] < last:
        stops.append(last)
    for t in stops:
        n = int(np.searchsorted(mins, open_min + t, "left"))
        if n < 50:
            continue
        g = pd.Series(size[:n]).groupby(lv[:n]).sum()
        val, vah, poc = value_area(g.index.to_numpy(), g.to_numpy())
        full = np.zeros(span)
        full[g.index.to_numpy() - lo_all] = g.to_numpy()
        pad = (-len(full)) % step
        binned = np.append(full, np.zeros(pad)).reshape(-1, step).sum(axis=1)
        top = binned.max() or 1.0
        out.append({
            "min": int(t),
            # Clamped to what the session actually printed, not ceil(t/m) —
            # the last checkpoint lands mid-bar.
            "bars": {str(m): min(int(np.ceil(t / m)), bar_counts[str(m)])
                     for m in BAR_MINS},
            "val": round(val, 2), "vah": round(vah, 2), "poc": round(poc, 2),
            "lo": round(lo_all * TICK, 2), "hi": round(hi_all * TICK, 2),
            "hist": [round(float(v / top), 3) for v in binned],
        })
    return out


def group(sessions: list[dict], rule: str) -> list[list[dict]]:
    """Split the session list into composites under one rule."""
    if rule != "balance":
        n = int(rule.split("-")[0])
        return [sessions[i:i + n] for i in range(0, len(sessions), n)]

    out: list[list[dict]] = []
    cur: list[dict] = []
    for s in sessions:
        if cur:
            lv = np.concatenate([x["levels"] for x in cur])
            vo = np.concatenate([x["vol"] for x in cur])
            k = pd.Series(vo).groupby(lv).sum()
            cval, cvah, _ = value_area(k.index.to_numpy(), k.to_numpy())
            # A clean break: the new session's value sits entirely clear of the
            # value the composite has built so far. Anything touching is still
            # the same auction.
            broke = s["val"] > cvah or s["vah"] < cval
            if broke or len(cur) >= BALANCE_CAP:
                out.append(cur)
                cur = []
        cur.append(s)
    if cur:
        out.append(cur)
    return out


def build_composite(days: list[dict], rule: str, n: int) -> dict:
    lv = np.concatenate([d["levels"] for d in days])
    vo = np.concatenate([d["vol"] for d in days])
    k = pd.Series(vo).groupby(lv).sum().sort_index()
    val, vah, poc = value_area(k.index.to_numpy(), k.to_numpy())

    # Bin down to a drawable resolution. The full tick grid over a 20-day NQ
    # composite is thousands of levels — more than a 300px panel can show and
    # more than the peak finder needs.
    lo, hi = int(k.index.min()), int(k.index.max())
    span = hi - lo + 1
    step = max(1, int(np.ceil(span / MAX_BUCKETS)))
    full = np.zeros(span)
    full[k.index.to_numpy() - lo] = k.to_numpy()
    pad = (-len(full)) % step
    binned = np.append(full, np.zeros(pad)).reshape(-1, step).sum(axis=1)
    prices = (lo + np.arange(len(binned)) * step + (step - 1) / 2) * TICK

    top = binned.max()
    # Is the value area one region of acceptance, or does it span a gap?
    # Bimodality alone does NOT answer this: a single-session double
    # distribution is a textbook trend-day shape, and ~30% of lone NQ sessions
    # print two prominent humps. What actually breaks a composite is a *deep
    # trough inside its own value area* — then "value" is two separated pockets
    # and the band between VAL and VAH describes no single auction.
    inside = (prices >= val) & (prices <= vah)
    variants = {}
    for pr in PROM_LEVELS:
        for sf in SMOOTH_LEVELS:
            hv, lv = nodes(prices, binned, pr, sf)
            sm = smoothed(binned, sf)
            floor = (float(sm[inside].min() / sm.max())
                     if inside.any() and sm.max() > 0 else 1.0)
            variants[f"{pr:.2f}|{sf:.2f}"] = {
                "hvn": hv, "lvn": lv, "modes": len(hv),
                "va_floor": round(floor, 3),
                "split_va": bool(floor < SPLIT_VA_FLOOR),
            }
    dflt = variants[f"{DEFAULT_PROM:.2f}|{DEFAULT_SMOOTH:.2f}"]
    return {
        "id": f"{rule}-{n}",
        "rule": rule,
        "days": [str(d["day"]) for d in days],
        "symbol": days[-1]["symbol"],
        "n_days": len(days),
        "val": round(val, 2), "vah": round(vah, 2), "poc": round(poc, 2),
        "va_width": round(vah - val, 2),
        # The drawn price domain is the raw level range, NOT the binned bucket
        # centres: buckets are midpoints, so prices[-1] can sit up to half a
        # bucket below VAH and the level line would render off the panel.
        "lo": round(lo * TICK, 2), "hi": round(hi * TICK, 2),
        "range": round((hi - lo) * TICK, 2),
        "hist": [{"p": round(float(p), 2), "v": round(float(v / top), 4)}
                 for p, v in zip(prices, binned)],
        # Node readings at every (prominence, smoothing) pair. `split_va` is the
        # validity check, not a signal: below the floor the value area has a
        # hole in it, so VAH/VAL are real boundaries but the band between them
        # is two pockets of acceptance with rejected prices in the middle.
        "variants": variants,
        "va_width_pt": round(vah - val, 2),
        "modes": dflt["modes"], "split_va": dflt["split_va"],
        "day_va": [{"val": round(d["val"], 2), "vah": round(d["vah"], 2),
                    "poc": round(d["poc"], 2)} for d in days],
    }


def live_frames(sessions: list[dict]) -> list[dict]:
    """Each session paired with the composite that was already complete.

    This is the usage question the static view can't answer: the composite is
    frozen at the prior close and today is measured *against* it. Letting today
    feed the composite would be circular — the POC would drift toward wherever
    price sat, so the level could never be meaningfully violated.
    """
    out = []
    for i in range(1, len(sessions)):
        prior = group(sessions[:i], "balance")[-1]
        comp = build_composite(prior, "live", i)
        today = sessions[i]
        out.append({
            "day": str(today["day"]),
            "comp": comp,
            "comp_days": [str(d["day"]) for d in prior],
            "checkpoints": today["developing"],
            "final": {"val": round(today["val"], 2), "vah": round(today["vah"], 2),
                      "poc": round(today["poc"], 2)},
        })
    return out


def _nearest(prices: np.ndarray, levels: list[float]) -> np.ndarray:
    return np.abs(prices[:, None] - np.array(levels)[None, :]).min(axis=1)


def _wmedian(x: np.ndarray, w: np.ndarray) -> float:
    o = np.argsort(x)
    x, w = x[o], w[o]
    c = np.cumsum(w)
    return float(x[np.searchsorted(c, c[-1] / 2)])


def event_scorecard(live: list[dict], by_day: dict) -> dict:
    """Do events land nearer the frozen composite levels than volume does?

    The question the overlay begs and the eye cannot answer. Events cluster
    where price traded, and price traded most where the composite says value is
    — so "events stack at the POC" is true of *any* subset of the tape. The
    control is therefore the session's own volume distribution: distance from
    each traded contract to the nearest frozen level, weighted by size. If the
    events aren't nearer than that, the overlay is showing where the market was,
    not where it reacted.

    Distances use only POC / VAH / VAL, so the number doesn't move when the
    HVN/LVN knobs do. Scored at each session's close, on today's events against
    a composite that was already frozen — the one non-circular cut here.
    """
    out: dict[str, dict] = {}
    for kind in ("sweep", "absorb", "null"):
        d, w, paired = [], [], []
        for f in live:
            c = f["comp"]
            levels = [c["poc"], c["vah"], c["val"]]
            k = f["checkpoints"][-1]
            step = (k["hi"] - k["lo"]) / max(len(k["hist"]), 1)
            vpx = k["lo"] + step * (np.arange(len(k["hist"])) + 0.5)
            vd, vw = _nearest(vpx, levels), np.array(k["hist"], dtype=float)
            if kind == "null":
                d.append(vd)
                w.append(vw)
                continue
            ev = [e for e in by_day[f["day"]]["events"] if e["k"] == kind]
            if not ev:
                continue
            px = np.array([(e["lo"] + e["hi"]) / 2 for e in ev])
            ed = _nearest(px, levels)
            d.append(ed)
            w.append(np.ones(len(px)))
            # Pooling mixes sessions whose frozen composite sits 20 pt away with
            # ones where it sits 400 pt away, and the far days swamp the median.
            # The paired figure asks the question inside each session: were that
            # day's events nearer its levels than that day's volume was?
            paired.append(float(np.median(ed) - _wmedian(vd, vw)))
        if not d:
            out[kind] = {"n": 0}
            continue
        d, w = np.concatenate(d), np.concatenate(w)
        out[kind] = {"n": int(len(d)),
                     "median": round(_wmedian(d, w), 1),
                     "near": round(100.0 * w[d <= NEAR_PTS].sum() / w.sum(), 1)}
        if paired:
            out[kind]["paired"] = round(float(np.median(paired)), 1)
            out[kind]["sessions"] = len(paired)
            out[kind]["closer"] = int(sum(x < 0 for x in paired))
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N_SESSIONS
    pairs = cached_sessions(n)
    if not pairs:
        raise SystemExit("nothing to draw — no cached RTH sessions found")

    print(f"reading {len(pairs)} cached sessions …")
    sessions = [s for d, sym in pairs if (s := load_session(d, sym))]
    if not sessions:
        raise SystemExit("every session came back empty")

    rules = {}
    for rule in RULES:
        groups = group(sessions, rule)
        rules[rule] = [build_composite(g, rule, i) for i, g in enumerate(groups)]

    # Sessions are shipped once and referenced by date, so four timeframes cost
    # one copy rather than one per rule per composite.
    by_day = {str(s["day"]): {"symbol": s["symbol"], "bars": s["bars"],
                              "events": s["events"],
                              "val": round(s["val"], 2), "vah": round(s["vah"], 2),
                              "poc": round(s["poc"], 2)}
              for s in sessions}
    out = {"sessions": by_day, "rules": rules, "live": live_frames(sessions)}
    out["scorecard"] = event_scorecard(out["live"], by_day)
    _write_html(out, sessions)

    print(f"\n{len(sessions)} sessions, {sessions[0]['day']} → {sessions[-1]['day']}\n")
    print(f"{'rule':>8} {'composites':>11} {'med days':>9} {'split VA':>12} "
          f"{'med modes':>10} {'med VA width':>13}")
    for rule in RULES:
        cs = rules[rule]
        mm = sum(c["split_va"] for c in cs)
        print(f"{rule:>8} {len(cs):>11} {np.median([c['n_days'] for c in cs]):>9.0f} "
              f"{mm:>5}/{len(cs):<6} {np.median([c['modes'] for c in cs]):>10.0f} "
              f"{np.median([c['va_width'] for c in cs]):>12.1f}p")

    ev = sum(len(s["events"]) for s in sessions)
    print(f"\nevents: {ev} across {len(sessions)} sessions "
          f"({ev / len(sessions):.1f} per session)")
    sc = out["scorecard"]
    print(f"{'':>11} {'n':>6} {'med dist':>10} {'≤' + str(int(NEAR_PTS)) + 'pt':>7} "
          f"{'paired vs volume':>18} {'sessions nearer':>16}")
    for kind, label in (("sweep", "sweeps"), ("absorb", "absorption"),
                        ("null", "volume null")):
        d = sc[kind]
        if not d["n"]:
            print(f"{label:>11} {'—':>6}")
            continue
        pair = (f"{d['paired']:+.1f} pt" if "paired" in d else "—")
        near = (f"{d['closer']}/{d['sessions']}" if "paired" in d else "—")
        print(f"{label:>11} {d['n']:>6} {d['median']:>7.1f} pt {d['near']:>6.1f}% "
              f"{pair:>18} {near:>16}")
    print("  distance to the frozen composite's POC/VAH/VAL. `paired` is the median\n"
          "  per-session gap between the events and that session's own volume —\n"
          "  negative means events really did land nearer the level than the tape did.")

    print(f"\nWrote {HTML_OUT}")
    print("Open:  file://" + str(HTML_OUT))


def _write_html(out: dict, sessions: list[dict]) -> None:
    template = (OUT_DIR / "_composite_profile_template.html").read_text()
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    html = (
        template
        .replace("__DATA_JSON__", json.dumps(out))
        .replace("__RULES_JSON__", json.dumps(list(RULES)))
        .replace("__DEFAULT_RULE__", DEFAULT_RULE)
        .replace("__N_SESSIONS__", str(len(sessions)))
        .replace("__FIRST_DAY__", str(sessions[0]["day"]))
        .replace("__LAST_DAY__", str(sessions[-1]["day"]))
        .replace("__VA_PCT__", str(int(VA_PCT * 100)))
        .replace("__BAR_MINS_JSON__", json.dumps([str(m) for m in BAR_MINS]))
        .replace("__DEFAULT_BAR_MIN__", str(DEFAULT_BAR_MIN))
        .replace("__LIVE_STEP__", str(LIVE_STEP_MIN))
        .replace("__PROMS_JSON__", json.dumps([f"{p:.2f}" for p in PROM_LEVELS]))
        .replace("__SMOOTHS_JSON__", json.dumps([f"{p:.2f}" for p in SMOOTH_LEVELS]))
        .replace("__DEFAULT_PROM__", f"{DEFAULT_PROM:.2f}")
        .replace("__DEFAULT_SMOOTH__", f"{DEFAULT_SMOOTH:.2f}")
        .replace("__SPLIT__", str(int(SPLIT_VA_FLOOR * 100)))
        .replace("__CAP__", str(BALANCE_CAP))
        .replace("__STRENGTHS_JSON__", json.dumps([str(s) for s in STRENGTHS]))
        .replace("__DEFAULT_STRENGTH__", str(DEFAULT_STRENGTH))
        .replace("__DEFAULT_EVENTS__", DEFAULT_EVENTS)
        .replace("__NEAR__", str(int(NEAR_PTS)))
        .replace("__SWEEP_LOTS__", str(SWEEP_LOTS))
        .replace("__BURST_LOTS__", str(BURST_LOTS))
        .replace("__BURST_GAP__", str(BURST_GAP_S))
        .replace("__BURST_SPAN__", f"{BURST_SPAN_PTS:.0f}")
        .replace("__ABSORB_WIN__", str(ABSORB_WIN_S))
        .replace("__ABSORB_MULT__", f"{ABSORB_MULT:.0f}")
    )
    HTML_OUT.write_text(html)


if __name__ == "__main__":
    main()
