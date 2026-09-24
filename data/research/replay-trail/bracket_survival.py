"""Which bracket survives a prop floor — LucidPro (EOD) against LucidDaily (intraday).

The question this answers is *edge-free on purpose*. Entries are drawn at random
through RTH at the pace the behaviour audit measured, so nothing here is a claim
about a setup: it is the **base rate of the bracket geometry alone**, priced
against the two floor shapes. A geometry that survives a coin flip survives
because of its shape; one that only survives with a good entry is not a rule, it
is a hope.

Four things make the numbers mean something.

**The stop is the vol ruler.** ``PresetRuler``'s ``s30`` reading — the developing
median 30-second bar range from the 09:30 bell, in ticks, and **no fallback**
(``frontend/src/lib/volRuler.ts:384``). Before three closed bars exist there is no
reading and no trade, exactly as the ticket refuses to arm. A fixed 50-tick arm
rides along as the control, gated on the same clock so the arms stay paired.

**The fills are the app's.** Every price decision delegates to
``journal.replay_whatif`` — the same ``cross``/``stop_fill``/``trail_stop``
arithmetic the replay and drill UIs run, at the verified model (1t spread, 1t
queue, $3.50/side, 250ms round trip). The walk below is vectorised rather than
tick-stepped, so it is a second statement of the *control flow*; ``--validate``
refuses to let it be believed until it reproduces ``run_flat`` trade for trade on
real days.

**The floor watches unrealised money.** LucidDaily's peak follows running equity
*including an open position*, so profit you were up and gave back is room you do
not get again — which is the whole reason a trail could be worth more there than
under LucidPro. The equity path is marked at every print with the open trade in
it (``mtm``, the headline reading). Lucid's own wording does not settle whether
the peak watches an open position or only the closed balance, so the
closed-balance reading is priced beside it, as ``intraday_dd.py`` does.

**The account constants are imported, not retyped** — from
``journal.replay_account``, so a rule change moves this study with it.

Usage:
    .venv/bin/python data/research/replay-trail/bracket_survival.py --validate
    .venv/bin/python data/research/replay-trail/bracket_survival.py --seeds 8
"""
from __future__ import annotations

import argparse
import bisect
import json
import multiprocessing as mp
import pathlib
import sys
from datetime import date as date_cls

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from journal.config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS, contract_spec  # noqa: E402
from journal.replay_account import TEMPLATES  # noqa: E402
from journal.replay_whatif import cross, run_flat  # noqa: E402
from journal.sim import ticks as tickmod  # noqa: E402

OUT = HERE / "bracket_survival.json"
#: Deliberately **not** ``bracket-survival.html``. ``api.routers.research._find``
#: resolves a slug by file *stem*, so a page sharing a stem with its write-up
#: shadows it and the markdown becomes unreachable in the Lab.
PAGE = ROOT / "docs/research/bracket-survival-visual.html"
#: The page carries its own data inline. It renders in an iframe inside the Lab
#: and is also opened straight off disk, and neither reliably allows a sibling
#: fetch — so the grid is written *into* the page rather than beside it.
PAGE_MARK = ('<script id="grid-data" type="application/json">',
             "</script>")

ROOT_SYMBOL = "NQ"
SPEC = contract_spec(ROOT_SYMBOL)
TICK = float(SPEC["tick_size"])            # 0.25
POINT_VALUE = float(SPEC["point_value"])   # $20 a point, 1 NQ
TICK_USD = TICK * POINT_VALUE              # $5 a tick

#: The verified fill model (``frontend/src/lib/fillModel.ts:116``).
CFG = dict(commission=3.5, slipTicks=1, queueTicks=1, latencyMs=250,
           tick_size=TICK, point_value=POINT_VALUE)

ENTRIES_PER_DAY = 16       # the behaviour audit's measured 15.8 a session
MIN_BARS = 3               # PresetRuler refuses under three closed bars
BUCKET_S = 30
BELL_SOD = 9 * 3600 + 30 * 60
CLOSE_SOD = 16 * 3600
#: No entry before this, in **every** window including the full-day control. The
#: ruler is technically readable at 09:31:30 (three closed 30s bars) but it is
#: still warming up: its developing median reads ~71 ticks there against a ~42
#: across-day median, so the first minutes arm a stop that is really a statement
#: about having almost no sample. Ten bars is the warmup. This makes the full-day
#: control here *not* the one in §1-§12 — those drew from 09:30 — so the window
#: comparison is internal to this run and the control is re-run alongside it.
ENTRY_OPEN_SOD = 9 * 3600 + 35 * 60
BE_TICKS = 3               # orderPresets.ts, `BE_TICKS`
SELF_STOP = 500.0          # the operating plan's own daily stop
MAX_DAYS = 60              # an evaluation that has not resolved by here is a timeout
BOOTSTRAPS = 400
#: Both templates cap at four minis; the sizer is clamped to it, as the ticket is.
MAX_MINIS = TEMPLATES["lucid_pro"].defaults.max_minis
MAX_MICROS = TEMPLATES["lucid_pro"].defaults.max_micros   # 40 = the same 4 minis of exposure

# --- the grid ----------------------------------------------------------------

#: The initial stop. ``ruler`` is 1.0x the s30 developing median (``STOP_MULT``);
#: the rest are the fixed tick distances the operating plan and the ticket use.
#: ``t40`` is the control for the ruler, not another width to try: the ruler's
#: median reading is 40 ticks, so without a fixed arm at the same width its wins
#: cannot be told apart from simply being that wide.
STOP_BASES = [("ruler", None), ("t35", 35), ("t40", 40), ("t50", 50), ("t75", 75)]

#: The target, in one of three currencies. ``r`` is a multiple of whichever stop
#: is in force, ``ticks`` is an absolute distance, and ``usd`` is a **dollar**
#: target — the tick distance that pays $N at the size actually on, so it narrows
#: as size grows rather than paying a multiple of it.
TARGETS = [
    ("none", None),
    ("1r", ("r", 1.0)), ("1.5r", ("r", 1.5)), ("2r", ("r", 2.0)), ("3r", ("r", 3.0)),
    ("t100", ("ticks", 100)), ("t120", ("ticks", 120)),
    ("usd300", ("usd", 300.0)), ("usd600", ("usd", 600.0)),
]

#: Trail in ``TrailCfg`` terms, distances in R. ``beOnly`` takes the first rung
#: and no other — a breakeven stop, not a trail.
TRAILS = [
    ("none", None),
    ("be1r", dict(distR=1.0, stepR=0.0, beTicks=BE_TICKS, beOnly=True)),
    ("trail1r", dict(distR=1.0, stepR=0.0, beTicks=BE_TICKS, beOnly=False)),
    ("trail.5r", dict(distR=0.5, stepR=0.5, beTicks=BE_TICKS, beOnly=False)),
]

#: The shapes that are shipped presets, for reading the table. The 1R one was C
#: when this ran and is D since 1.33R went in above it; the sweep prices shapes,
#: not letters, so the row is the same row under either name.
SHIPPED = {"none/trail1r": "A · trending", "1.5r/be1r": "B · 1.5R", "1r/none": "D · 1R"}

#: How many contracts an entry gets. ``1nq`` is the operating plan's flat mini;
#: the ``risk`` arms hold the dollar risk per entry constant instead, which is
#: the ticket's own sizer (``riskSizer.presetsFor``) and the only way a ruler stop
#: does not float the risk with volatility.
#: The ``mnq`` arms are the same idea at **micro** granularity, which is the only
#: place fixed-dollar risk can actually act on a $50K account (§7): a $250 budget
#: buys one mini at almost every stop width, but 4-8 micros. It is also the only
#: way a wide stop is takeable at all — 1 NQ at the 114-tick stop the ruler arms on
#: a wild day is **$570 of risk against a $2,000 max loss**, four losers from dead.
SIZINGS = ["1nq", "risk150", "risk250", "risk400",
           "mnq100", "mnq150", "mnq250", "mnq400"]
RISK_USD = {"risk150": 150.0, "risk250": 250.0, "risk400": 400.0,
            "mnq100": 100.0, "mnq150": 150.0, "mnq250": 250.0, "mnq400": 400.0}

MICRO_RATIO = 10                                  # contracts.ts:30
MICRO_POINT_VALUE = POINT_VALUE / MICRO_RATIO     # $2 a point
MICRO_TICK_USD = TICK_USD / MICRO_RATIO           # $0.50 a tick
#: Brokers do not discount a micro to a tenth; $0.50 a side is the measured floor
#: (`MICRO_COMMISSION_FLOOR`, contracts.ts:69 and journal/live/broker.py).
MICRO_COMM = max(0.5, CFG["commission"] / MICRO_RATIO)

# --- the edge dial -----------------------------------------------------------
# A **directional oracle**, and it is a dial, not a signal: with probability ``p``
# the entry takes the side the tape is actually on ``horizon`` minutes later. p=0.5
# is the coin flip this study started from.
#
# The horizon is drawn with the side, once per (day, seed, cell), *before* the arm
# loop — so every bracket trades the identical entry list and a difference between
# two arms is still the bracket. It cannot be chosen per bracket: an oracle tuned to
# "does it reach target before stop" would hand every arm its own edge and flatter
# wide targets by construction.
#
# It is not neutral either, and that is why it is swept rather than fixed. A 15-min
# oracle cannot help a position a 0.5R trail scratches in ninety seconds, so the
# horizon encodes how long the edge persists and favours brackets whose holding
# period matches it. Calibration (docs/research/bracket-survival.md §13): on the
# plan's own t50/t120, p=0.65 buys about +$15..$39 a trade, so the reviewed book's
# +$53/trade — 95% CI −$8..+$115 — sits near p≈0.68-0.70.
EDGE_PS = [0.50, 0.60, 0.65, 0.70]
HORIZONS_MIN = [1, 2, 5, 15, 60]

#: The arms carried into the edge sweep. The zero-edge run showed the rest were
#: near-duplicates; these keep every family that ranked or illustrated something,
#: including the tight trail as the known-worst control.
FOCUS_STOPS = ["ruler", "t40", "t50", "t75"]
FOCUS_TARGETS = ["none", "1r", "1.5r", "3r", "t120"]
FOCUS_TRAILS = ["none", "be1r", "trail1r"]
FOCUS_EXTRA = ["t35/1r/trail.5r", "t50/1.5r/trail.5r", "t75/1.5r/trail.5r"]

# --- the time-of-day dial ----------------------------------------------------
# The one axis §13 called the biggest hole: every result before this drew entries
# uniformly across the whole session, and three separate things say that is the
# wrong shape. The book is bursty, the ruler falls 71t -> 27t through the day
# (``vol-ruler-day-shape``), and ``drift-fade-poc-price-action`` found the first
# hour carries the net.
#
# Entries are **pace-matched**, not count-matched: a window gets its share of the
# 16-a-day rate, so ``h1`` takes ~2 entries and ``pm`` ~10. That is the honest
# version of "only trade this window" — trading one hour a day really is fewer
# trades — but it means ``all`` vs ``h1`` mixes *when* with *how often*, and the
# study has already shown how strongly trade count alone moves the pass column.
# So the three **one-hour** windows are the actual experiment: same pace, same
# count, same warmup, different hour. Any gap between those three is the hour.
WINDOWS = [
    ("all",   ENTRY_OPEN_SOD,     CLOSE_SOD),        # the control, 09:35-16:00
    ("h1",    ENTRY_OPEN_SOD,     ENTRY_OPEN_SOD + 3600),   # 09:35-10:35
    ("mid",   12 * 3600,          13 * 3600),        # 12:00-13:00
    ("lastH", 15 * 3600,          CLOSE_SOD),        # 15:00-16:00
    ("am",    ENTRY_OPEN_SOD,     12 * 3600),        # 09:35-12:00
    ("pm",    12 * 3600,          CLOSE_SOD),        # 12:00-16:00
    # A session that stops N minutes after the bell. Appended, never inserted:
    # the window's index is in the draw seed, so moving one re-deals the rest.
    ("o30",   ENTRY_OPEN_SOD,     BELL_SOD + 30 * 60),   # 09:35-10:00
    ("o60",   ENTRY_OPEN_SOD,     BELL_SOD + 60 * 60),   # 09:35-10:30
    ("o90",   ENTRY_OPEN_SOD,     BELL_SOD + 90 * 60),   # 09:35-11:00
]
WINDOW_SPAN = {k: (a, b) for k, a, b in WINDOWS}
WINDOW_IX = {k: i for i, (k, _, _) in enumerate(WINDOWS)}
FULL_SPAN_S = CLOSE_SOD - ENTRY_OPEN_SOD


#: ``--entries``: a fixed attempt count for every window instead of its pace
#: share — "I take 5-10 trades in my session", however long the session is.
ENTRIES: int | None = None


def entries_for(window: str) -> int:
    """The window's share of the measured daily pace, at least one."""
    if ENTRIES:
        return ENTRIES
    a, b = WINDOW_SPAN[window]
    return max(1, round(ENTRIES_PER_DAY * (b - a) / FULL_SPAN_S))


DAY_STOPS = ["dll", "self500"]
#: A day profit goal, taken on the **marked** path: touch it — unrealised profit
#: counts — and the day is done. ``0`` is no goal at all.
DAY_GOALS = [0.0, 500.0, 1000.0]
MARKS = ["mtm", "closed"]
ACCOUNTS = ["lucid_pro", "lucid_daily"]


def build_arms() -> list[dict]:
    """Every bracket arm. No target and no trail would never exit; it is dropped.

    Sizing is normally a pure post-multiplier — the engine has no market impact,
    so a trade at 3 contracts is three times the same trade — and those arms carry
    ``sizing=None`` and get expanded across ``SIZINGS`` when the accounts are
    walked. A **dollar** target is the exception: its tick distance depends on how
    many contracts are on, so it has to be priced once per sizing here.
    """
    out = []
    for sb, sticks in STOP_BASES:
        for tk, target in TARGETS:
            for lk, trail in TRAILS:
                if target is None and trail is None:
                    continue
                shape = f"{tk}/{lk}"
                sizings = SIZINGS if (target and target[0] == "usd") else [None]
                for sz in sizings:
                    key = f"{sb}/{shape}" + (f"@{sz}" if sz else "")
                    out.append(dict(key=key, bracket=key, stop_basis=sb, stop_ticks=sticks,
                                    target=target, trail=trail, shape=shape,
                                    sizing=sz, shipped=SHIPPED.get(shape),
                                    edge_p=0.5, horizon=HORIZONS_MIN[0], window="all"))
    return out


def _focus_base():
    keep = {f"{s}/{t}/{r}" for s in FOCUS_STOPS for t in FOCUS_TARGETS for r in FOCUS_TRAILS}
    keep |= set(FOCUS_EXTRA)
    return [a for a in build_arms() if a["key"] in keep and not a["sizing"]]


def focus_arms(ps=None, hs=None) -> list[dict]:
    """The focused set, crossed with every edge cell.

    An arm's identity gains ``(edge_p, horizon)`` because the entry list differs
    per cell — the same bracket under two different edges is two different runs.
    ``ps``/``hs`` narrow the cells, which is how a targeted probe (one edge level
    against the zero-edge control) is run without paying for the whole grid.
    """
    out = []
    for p in (ps or EDGE_PS):
        for H in (hs or HORIZONS_MIN):
            for a in _focus_base():
                b = dict(a)
                b.update(edge_p=p, horizon=H,
                         key=f"{a['key']}|p{int(p * 100)}|h{H}")
                out.append(b)
    return out


def window_arms(ps=None, hs=None, wins=None) -> list[dict]:
    """The focused set crossed with edge cells **and** entry windows.

    The window joins ``(edge_p, horizon)`` in the arm's identity for the same
    reason they are already there: it changes the entry list, so the same bracket
    in two windows is two runs, and only arms sharing all three were dealt the
    same market.
    """
    out = []
    for w in (wins or [k for k, _, _ in WINDOWS]):
        for a in focus_arms(ps, hs):
            b = dict(a)
            b.update(window=w, key=f"{a['key']}|w{w}")
            out.append(b)
    return out


ARMS = build_arms()
ARM_KEYS = [a["key"] for a in ARMS]

# --- the tape ----------------------------------------------------------------


def load_rth(symbol: str, day: date_cls):
    """The RTH tape in the browser's clock — ``load_tape``'s convention, RTH only.

    This sim is strictly intraday: an entry is drawn inside the session and a
    position still open at the bell is marked to market there, so the overnight
    and post segments the full tape glues on would be dead weight.
    """
    frame = tickmod.cached_rth(symbol, day)
    if frame is None or frame.empty:
        raise FileNotFoundError(f"no cached RTH ticks for {symbol} on {day}")
    zone = DISPLAY_TZS[DEFAULT_DISPLAY_TZ]
    local = frame["ts_utc"].dt.tz_convert(zone).dt.tz_localize(None)
    t = local.values.astype("datetime64[ms]").astype("int64").astype(np.float64)
    return t, frame["price"].to_numpy(dtype="float64")


def sessions() -> list[tuple[str, date_cls]]:
    """Every cached RTH day as (symbol, day), oldest first, one contract a day."""
    seen: dict[date_cls, str] = {}
    for p in sorted(tickmod.TICK_CACHE_DIR.glob("*_day.parquet")):
        sym, iso, _ = p.name.split("_", 2)
        if sym.startswith(ROOT_SYMBOL):
            seen[date_cls.fromisoformat(iso)] = sym
    return [(seen[d], d) for d in sorted(seen)]


# --- the vol ruler -----------------------------------------------------------


def ruler_series(t, px):
    """The ``s30`` developing median bar range at every print, in ticks.

    A port of ``PresetRuler.read`` for the one bucket this study uses: whole
    30-second bars from the 09:30 bell, a bar belonging to the window by its
    **first** print, the forming bar never inside a median, and no reading at all
    under three closed bars. ``NaN`` where the ticket would have nothing to show —
    there is no fallback leg, by design (``volRuler.ts:273-296``).
    """
    out = np.full(len(px), np.nan)
    sod = (t / 1000.0) % 86400.0
    idx = np.flatnonzero((sod >= BELL_SOD) & (sod < CLOSE_SOD))
    if len(idx) < MIN_BARS + 1:
        return out
    bucket = np.floor(sod[idx] / BUCKET_S).astype(np.int64)
    starts = idx[np.r_[True, bucket[1:] != bucket[:-1]]]
    if len(starts) < MIN_BARS + 1:
        return out
    ends = np.r_[starts[1:], idx[-1] + 1]
    rng = (np.maximum.reduceat(px, starts) - np.minimum.reduceat(px, starts)) / TICK

    order: list[float] = []
    for i in range(len(starts) - 1):
        bisect.insort(order, float(rng[i]))
        if len(order) < MIN_BARS:
            continue
        n = len(order)
        med = order[n // 2] if n % 2 else 0.5 * (order[n // 2 - 1] + order[n // 2])
        # In force through the *next* bar: the bar that just closed is in the
        # median, the one now forming is not.
        out[starts[i + 1]:ends[i + 1]] = med
    return out


# --- the walk ----------------------------------------------------------------
# A vectorised re-statement of ``run_sim``'s inner loop for the one shape this
# study needs: one market order, its bracket, its trail. Every *price* decision is
# the engine's own arithmetic; only the stepping is different, so a whole trade
# resolves in a handful of array ops instead of a Python iteration per print.

CHUNK = 8192


def walk_trade(px, i0, side, entry_px, stop_px, target_px, trail):
    """Resolve one open position. Returns (exit_idx, exit_price, reason).

    Mirrors ``run_sim``'s ordering: the bracket is tested *before* the trail
    advances on the same print, so the stop in force at print *i* is the one the
    high-water mark through *i-1* had earned. Stop beats target on a tie, because
    ``bracket_hit`` tests it first.
    """
    d = 1.0 if side == "long" else -1.0
    gap = CFG["queueTicks"] * TICK
    n = len(px)
    hwm_d = entry_px * d          # the high-water mark, in the trade's own direction
    lo = i0
    step = back = origin = 0.0
    if trail is not None and trail["dist"] > 0:
        step = trail["step"] if trail["step"] > 0 else trail["dist"]
        back = max(0.0, trail["dist"] - trail["be"])
        origin = entry_px + d * trail["be"]

    while lo < n:
        hi = min(n, lo + CHUNK)
        seg = px[lo:hi]
        run = np.maximum.accumulate(seg * d)
        np.maximum(run, hwm_d, out=run)
        prev = np.empty(len(seg))
        prev[0] = hwm_d          # the mark the *previous* print had earned
        prev[1:] = run[:-1]

        stop = np.full(len(seg), stop_px)
        if trail is not None and trail["dist"] > 0:
            k = np.floor(((prev * d - origin) * d - back) / step)
            lvl = np.full(len(seg), origin) if trail["beOnly"] else origin + d * k * step
            # ``tighten`` never loosens, and the ladder only ever rises.
            stop = np.where(k >= 0, np.maximum(lvl * d, stop_px * d) * d, stop_px)

        if side == "long":
            hit_stop = seg <= stop
            hit_tgt = (seg >= target_px + gap) if target_px is not None else False
        else:
            hit_stop = seg >= stop
            hit_tgt = (seg <= target_px - gap) if target_px is not None else False
        any_hit = hit_stop if target_px is None else (hit_stop | hit_tgt)

        if any_hit.any():
            j = int(np.argmax(any_hit))
            i = lo + j
            if hit_stop[j]:
                # ``stop_fill``: book the print it jumped through, then cross.
                at = min(seg[j], stop[j]) if side == "long" else max(seg[j], stop[j])
                why = "trail" if stop[j] != stop_px else "stop"
                return i, cross(at, side == "short", CFG), why
            return i, target_px, "target"

        hwm_d = float(run[-1])
        lo = hi
    return n - 1, cross(px[-1], side == "short", CFG), "open"


def prune(pairs):
    """Drop peak/trough pairs a later pair already dominates. Exact, not a sample.

    The floor only ever rises, so a pair whose trough is matched or beaten by a
    *later* pair can never be the one that catches the account: the later pair has
    a floor at least as high under a trough at least as deep. What survives is a
    handful of numbers instead of a few thousand.
    """
    keep, best = [], np.inf
    for h, lo in reversed(pairs):
        if lo < best:
            keep.append((h, lo))
            best = lo
    return keep[::-1]


def excursion(e):
    """One path's pruned (record high, deepest point after it) pairs, **flattened**.

    Returned as a flat ``(h0, lo0, h1, lo1, …)`` tuple rather than a list of pairs
    purely for size: a 599-session pass holds millions of these at once, and the
    flat tuple is the difference between fitting in memory and not.
    """
    rm = np.maximum.accumulate(e)
    starts = np.flatnonzero(np.r_[True, rm[1:] > rm[:-1]])
    lows = np.minimum.reduceat(e, starts)
    out = []
    best = np.inf
    for h, lo in zip(reversed(rm[starts].tolist()), reversed(lows.tolist())):
        if lo < best:
            out.append((h, lo))
            best = lo
    return tuple(v for pair in reversed(out) for v in pair)


# --- one day -----------------------------------------------------------------


def window_ms(t, window: str):
    """The window's (first, last) tape stamp, or ``None`` if the day has no prints
    in it. Resolved against seconds-of-day, which is monotone inside an RTH tape."""
    a, b = WINDOW_SPAN[window]
    sod = (t / 1000.0) % 86400.0
    lo = int(np.searchsorted(sod, a, side="left"))
    hi = int(np.searchsorted(sod, b, side="left"))
    return None if hi - lo < 2 else (float(t[lo]), float(t[hi - 1]))


def draw(rng, t, px=None, p=0.5, horizon_min=5, span=None, n=ENTRIES_PER_DAY):
    """Entry times and sides — uniform through ``span``, at the measured pace.

    With ``p == 0.5`` the side is a coin flip and ``px`` is never read, which is the
    zero-edge baseline the study is built on. Above it the side is the one the tape
    is on ``horizon_min`` later, taken with probability ``p`` — see ``EDGE_PS``.

    ``span`` is the entry window as tape stamps (``window_ms``) and ``n`` its
    pace-matched entry count; the default pair is the whole tape at the full rate.
    Only the *entry* is confined — a position opened inside the window runs to its
    bracket wherever that lands, including past the window's end, because closing a
    trade early at a wall clock is a different rule and not the one under test.
    """
    t0, t1 = span if span is not None else (float(t[0]), float(t[-1]))
    ms = np.sort(rng.uniform(t0, t1, n))
    coin = rng.random(n)
    if px is None or p == 0.5:
        return [(m, "long" if c < 0.5 else "short") for m, c in zip(ms.tolist(), coin.tolist())]
    H = horizon_min * 60_000
    out = []
    for m, c in zip(ms.tolist(), coin.tolist()):
        i = int(np.searchsorted(t, m, side="right")) - 1
        j = min(len(px) - 1, int(np.searchsorted(t, m + H, side="right")) - 1)
        right = "long" if px[j] > px[max(i, 0)] else "short"
        wrong = "short" if right == "long" else "long"
        out.append((m, right if c < p else wrong))
    return out


def legs_in_ticks(arm, stop_ticks, size):
    """The arm's bracket as the ticket would hold it — **whole ticks**.

    ``SimPrefs`` keeps every leg as an integer tick count and resolves it to a
    price once, at placement, so a target has to land on the tick grid before it
    becomes a level. Skipping that rounding is what made a 1.5R target on an odd
    stop disagree with the engine by an eighth of a point. A dollar target divides
    by ``size`` for the same reason the plan quotes $600 rather than 120 ticks:
    what is being aimed at is the money, not the distance.
    """
    tgt, target = 0, arm["target"]
    if target is not None:
        kind, v = target
        if kind == "r":
            tgt = round(v * stop_ticks)
        elif kind == "ticks":
            tgt = int(v)
        else:
            tgt = max(1, round(v / (TICK_USD * size)))
    trail = None
    if arm["trail"] is not None:
        trail = dict(dist=max(1, round(arm["trail"]["distR"] * stop_ticks)),
                     step=round(arm["trail"]["stepR"] * stop_ticks),
                     be=arm["trail"]["beTicks"], beOnly=arm["trail"]["beOnly"])
    return tgt, trail


def bracket_for(arm, entry_px, side, stop_ticks, size):
    d = 1.0 if side == "long" else -1.0
    tgt, trail = legs_in_ticks(arm, stop_ticks, size)
    stop_px = entry_px - d * stop_ticks * TICK
    target_px = entry_px + d * tgt * TICK if tgt > 0 else None
    if trail is not None:
        trail = dict(dist=trail["dist"] * TICK, step=trail["step"] * TICK,
                     be=trail["be"] * TICK, beOnly=trail["beOnly"])
    return stop_px, target_px, trail


def run_day(t, px, ruler, entries, arm, *, taken=None):
    """Every trade one arm takes on one day. Sequential — an entry lands only flat.

    ``taken`` collects the indices into ``entries`` that actually opened a
    position, which is what lets ``validate`` hand the engine the same question:
    an entry drawn while a trade is still running is not a second order, it is a
    gesture that never happened.
    """
    lag = CFG["latencyMs"]
    n = len(px)
    trades = []
    free_at = -1
    for k, (ms, side) in enumerate(entries):
        gi = int(np.searchsorted(t, ms, side="right")) - 1       # the gesture
        i = int(np.searchsorted(t, ms + lag, side="right"))      # where it landed
        if i <= free_at or i >= n or gi < 0:
            continue
        if arm["stop_ticks"] is not None:
            stop_ticks = arm["stop_ticks"]
        else:
            r = ruler[gi]
            if not np.isfinite(r):
                continue          # no reading, no trade — the ticket refuses to arm
            # ``stop_mult`` puts the stop at a *fraction* of the ruler while the
            # target stays the ruler's full width — stated as ``("r", 1/mult)``,
            # because R is the stop. Half a ruler with a 2R target and three
            # quarters with a 1.33R target are the same bracket said two ways.
            stop_ticks = max(1, int(round(float(r) * arm.get("stop_mult", 1.0))))

        size = contracts(arm["sizing"], stop_ticks, MAX_MINIS)
        entry_px = cross(px[i - 1], side == "long", CFG)
        stop_px, target_px, trail = bracket_for(arm, entry_px, side, stop_ticks, size)
        ex_i, ex_px, why = walk_trade(px, i, side, entry_px, stop_px, target_px, trail)
        free_at = ex_i
        d = 1.0 if side == "long" else -1.0
        pts = (ex_px - entry_px) * d
        path = np.r_[(px[i:ex_i + 1] - entry_px) * d, pts]
        # A tuple, not a dict: this is the object the pass holds millions of.
        trades.append((float(pts), int(stop_ticks), excursion(path), why))
        if taken is not None:
            taken.append((k, stop_ticks, size))
    return trades


#: The arms the current pass is walking. Set once per worker rather than shipped
#: with every job — see ``main``'s batching, which is what keeps a 599-day pass
#: inside the box's memory.
_BATCH: list[dict] = ARMS


def _set_batch(batch, bucket_s, entries):
    global _BATCH, BUCKET_S, ENTRIES
    _BATCH, BUCKET_S, ENTRIES = batch, bucket_s, entries


def day_job(job):
    sym, day, seeds = job
    try:
        t, px = load_rth(sym, day)
    except Exception as exc:                     # a half-written cache is not a result
        return dict(day=day.isoformat(), error=str(exc))
    if len(px) < 5_000:
        return dict(day=day.isoformat(), error=f"thin tape ({len(px):,} prints)")
    ruler = ruler_series(t, px)
    cells = sorted({(a["edge_p"], a["horizon"], a.get("window", "all")) for a in _BATCH})
    spans = {w: window_ms(t, w) for _, _, w in cells}
    out = {a["key"]: [] for a in _BATCH}
    for s in range(seeds):
        for p, H, w in cells:
            if spans[w] is None:                 # no prints in the window that day
                continue
            # One draw per (day, seed, edge cell, window), shared by every arm in
            # it: two rows differ by their bracket, never by the market they were
            # dealt. The window is in the seed so a day's `h1` draw is not a
            # prefix of its `all` draw — they are independent samples of it.
            # WINDOW_IX, not hash(w): str hashing is salted per process, so a
            # hashed seed would differ between workers and between runs.
            rng = np.random.default_rng(
                [day.toordinal(), s, int(p * 100), H, WINDOW_IX[w]])
            entries = draw(rng, t, px, p, H, spans[w], entries_for(w))
            for arm in _BATCH:
                if (arm["edge_p"], arm["horizon"], arm.get("window", "all")) == (p, H, w):
                    out[arm["key"]].append(run_day(t, px, ruler, entries, arm))
    return dict(day=day.isoformat(), symbol=sym, arms=out)


# --- sizing, the daily stop, and the day's equity path -----------------------


def contracts(sizing: str | None, stop_ticks: int, max_minis: int) -> int:
    """How many minis one entry gets. ``riskSizer.presetsFor``'s arithmetic —
    the per-contract cost is net of both commissions, not just the stop.

    Minis only. ``position`` is what the equity path uses; this survives because
    a **dollar** target has to know the contract count to price its legs, and
    those arms are mini-denominated.
    """
    if not sizing or sizing == "1nq" or sizing.startswith("mnq"):
        return 1
    per = stop_ticks * TICK_USD + 2 * CFG["commission"]
    return max(1, min(max_minis, int(RISK_USD[sizing] // per)))


def position(sizing: str | None, stop_ticks: int) -> tuple[float, float]:
    """One entry as **(dollars per point on, round-turn fees)**.

    The equity path only ever needs those two numbers, and stating it this way is
    what lets minis and micros share one code path: "the same trade in a smaller
    contract" is a tenth of the money per point and its own commission, nothing
    else (``contracts.ts:83``).

    The micro is **not** a tenth of the mini's cost. Brokers floor it — $0.50 a
    side against the mini's $3.50 — so ten micros pay $5.00 where one mini pays
    $3.50. **Micro granularity costs ~1.43x the fees per unit of exposure**, and
    that is the price of being able to size at all.
    """
    if not sizing or sizing == "1nq":
        return POINT_VALUE, 2 * CFG["commission"]
    if sizing.startswith("mnq"):
        per = stop_ticks * MICRO_TICK_USD + 2 * MICRO_COMM
        m = max(1, min(MAX_MICROS, int(RISK_USD[sizing] // per)))
        return m * MICRO_POINT_VALUE, 2 * MICRO_COMM * m
    n = contracts(sizing, stop_ticks, MAX_MINIS)
    return n * POINT_VALUE, 2 * CFG["commission"] * n


def day_record(trades, sizing, day_stop_usd, day_goal, mark, max_minis,
               hard_stop=False):
    """One day's (close, pruned peak/trough skeleton) under one reading.

    ``day_stop_usd`` truncates exactly as the app does when it disarms the day: no
    entry is taken once realised P&L has reached the stop. ``day_goal`` is the
    other end of the same idea and it reads the **marked** path — touch the goal
    with unrealised profit and the day is done, flat, at that level. ``mark``
    decides whether the path carries the open position at all (``mtm``, which is
    what LucidDaily's peak follows) or only the closed balance.

    ``hard_stop`` makes the daily stop symmetric with the goal: instead of only
    refusing the *next* entry, it cuts the open position the moment **marked**
    equity touches the stop. The default is off because it is not what the app
    does — the app disarms between trades, so a trade opened at −$490 realised
    still takes its whole stop, which is why the measured worst day is −$790
    rather than −$500.
    """
    pairs, realized, gpeak = [], 0.0, 0.0
    for tr in trades:
        # A trade may carry its own sizing key as a 5th field, which is how a
        # study that sizes per *entry* rather than per arm (``grade_tiers``)
        # reuses this one equity path. A plain 4-tuple takes the arm's ``sizing``
        # and behaves exactly as before.
        pts, stop_ticks, path, _why = tr[:4]
        if realized <= -day_stop_usd:
            break
        pv, fees = position(tr[4] if len(tr) > 4 else sizing, stop_ticks)
        net = pts * pv - fees
        if mark == "mtm":
            # The round turn is charged at entry, so an open trade never looks
            # richer than it is (``intraday_dd.day_path``).
            steps = [(realized + path[j] * pv - fees,
                      realized + path[j + 1] * pv - fees)
                     for j in range(0, len(path), 2)]
        else:
            steps = [(realized + net, realized + net)]
        done = False
        for h, lo in steps:
            # ``prune`` and the intraday floor both read a peak that only rises,
            # so the day's running high has to be carried across trades here.
            gpeak = max(gpeak, h)
            if day_goal and gpeak >= day_goal:
                # The rise to this record high crossed the goal, so the trough
                # that would have followed it never happens: flat, and done.
                pairs.append((day_goal, day_goal))
                realized, done = day_goal, True
                break
            if hard_stop and lo <= -day_stop_usd:
                # Out at market on the touch. Leaving at the level exactly would
                # be free, and it is not: crossing the spread costs the same tick
                # every other exit here pays.
                out = -day_stop_usd - CFG["slipTicks"] * TICK * pv
                pairs.append((gpeak, out))
                realized, done = out, True
                break
            pairs.append((gpeak, lo))
        if done:
            break
        realized += net
    return realized, prune(pairs) if pairs else [(0.0, 0.0)]


# --- the two floors ----------------------------------------------------------


def evaluate(pool, numbers, intraday: bool, picks):
    """One evaluation: sample days until the target, the floor, or the cap on days.

    Both shapes breach on equity touching the floor at any moment — Lucid counts
    intraday spikes. They differ only in when the floor *moves*: at a day close
    under ``eod``, and at every new running high under ``intraday``, where the
    high includes an open position. That second clause is the whole cost of
    LucidDaily, and it is why a day that ran up and gave it back can kill an
    account that finished the day green.
    """
    eq = peak = numbers.start
    floor = min(peak, numbers.trail_cap) - numbers.max_loss
    target = numbers.start + numbers.profit_target
    for n_days, pick in enumerate(picks, 1):
        close, skel = pool[pick]
        if intraday:
            for h, lo in skel:
                peak = max(peak, eq + h)
                floor = min(peak, numbers.trail_cap) - numbers.max_loss
                if eq + lo <= floor:
                    return "bust", n_days
        elif eq + min(lo for _, lo in skel) <= floor:
            return "bust", n_days
        eq += close
        peak = max(peak, eq)
        floor = min(peak, numbers.trail_cap) - numbers.max_loss
        if eq <= floor:
            return "bust", n_days
        if eq >= target:
            return "pass", n_days
    return "timeout", len(picks)


# --- report ------------------------------------------------------------------


def chain_arm(job):
    """Every (sizing, day stop, goal, mark, account) reading of one arm.

    Split out as the parallel unit because the arms are independent and the pools
    are what cost: one is built per reading, over every day-sample the sim
    produced, before a single evaluation is drawn from it.
    """
    arm, per_day, marks = job
    sizings = [arm["sizing"]] if arm["sizing"] else SIZINGS
    out = []
    # What actually took the trades off. Untouched by sizing or the daily stop, so
    # it is counted once — it is the mechanism behind whatever the table shows.
    why: dict[str, int] = {}
    stops: list[int] = []
    n_samples = 0
    for day in per_day:
        for seed in day:
            n_samples += 1
            for _pts, st, _path, w in seed:
                why[w] = why.get(w, 0) + 1
                stops.append(st)
    exits = {k: round(100 * v / max(1, sum(why.values())), 1) for k, v in why.items()}
    med_stop = int(np.median(stops)) if stops else 0
    # How many trades a day-sample actually held. A window is pace-matched, so
    # this differs by design and is the confound in every `all` vs window row:
    # report it beside the pass rate rather than letting it hide inside one.
    trades_day = round(len(stops) / max(1, n_samples), 2)
    for sizing in sizings:
        for dstop in DAY_STOPS:
            for goal in DAY_GOALS:
                for mark in marks:
                    # A pool depends on the account only through the daily loss
                    # limit, so it is built once per distinct stop and shared.
                    pools = {}
                    for acct in ACCOUNTS:
                        nums = TEMPLATES[acct].defaults
                        stop_usd = nums.day_loss if dstop == "dll" else SELF_STOP
                        if stop_usd not in pools:
                            pools[stop_usd] = [
                                day_record(tr, sizing, stop_usd, goal, mark, MAX_MINIS)
                                for day in per_day for tr in day]
                        pool = pools[stop_usd]
                        if not pool:
                            continue
                        # The same day draws for both accounts and every arm, so a
                        # difference between two rows is the rule, not the sample.
                        rng = np.random.default_rng([7, len(pool)])
                        picks = rng.integers(len(pool), size=(BOOTSTRAPS, MAX_DAYS))
                        res = [evaluate(pool, nums, TEMPLATES[acct].trailing == "intraday",
                                        picks[i]) for i in range(BOOTSTRAPS)]
                        fates = [f for f, _ in res]
                        closes = np.array([c for c, _ in pool])
                        out.append(dict(
                            arm=arm.get("bracket", arm["key"]), shape=arm["shape"],
                            stop_basis=arm["stop_basis"], shipped=arm["shipped"],
                            edge_p=arm.get("edge_p", 0.5), horizon=arm.get("horizon", 0),
                            window=arm.get("window", "all"),
                            sizing=sizing, day_stop=dstop,
                            day_goal=goal, mark=mark, account=acct,
                            passed=round(100 * fates.count("pass") / BOOTSTRAPS, 1),
                            bust=round(100 * fates.count("bust") / BOOTSTRAPS, 1),
                            timeout=round(100 * fates.count("timeout") / BOOTSTRAPS, 1),
                            med_days=float(np.median([d for _, d in res])),
                            day_net=round(float(closes.mean()), 2),
                            day_p10=round(float(np.percentile(closes, 10)), 2),
                            exits=exits, med_stop=med_stop, trades_day=trades_day,
                            n_days=len(pool)))
    return out


def main():
    global ARMS, OUT, SIZINGS, MAX_DAYS, BUCKET_S, ENTRIES
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--days", type=int, default=0, help="cap the session count (a smoke run)")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    ap.add_argument("--validate", type=int, default=0, metavar="N",
                    help="grade the walker against run_flat on N days, then stop")
    ap.add_argument("--marks", choices=["mtm", "both"], default="mtm",
                    help="'both' also prices the closed-balance-only reading")
    ap.add_argument("--batch", type=int, default=32,
                    help="arms walked per pass; the peak memory knob")
    ap.add_argument("--page", action="store_true",
                    help="re-inject the last result into the page, then stop")
    ap.add_argument("--edge", action="store_true",
                    help="run the focused arm set across the edge x horizon cells")
    ap.add_argument("--edge-p", default=None, help="restrict edge levels, e.g. 0.5,0.7")
    ap.add_argument("--edge-h", default=None, help="restrict horizons, e.g. 2")
    ap.add_argument("--keep-sizings", action="store_true",
                    help="carry the fixed-dollar sizing axis through the edge run")
    ap.add_argument("--windows", default=None, metavar="LIST",
                    help="cross the focused set with entry windows, e.g. "
                         "all,h1,mid,lastH ('*' for every one). Implies --edge.")
    ap.add_argument("--max-days", type=int, default=MAX_DAYS,
                    help="evaluation day cap; raise it when a window trades so "
                         "little that 60 days censors the result")
    ap.add_argument("--out", default=None, help="override the result path")
    ap.add_argument("--bucket", type=int, default=BUCKET_S, metavar="S",
                    help="the ruler's bar in seconds (the ticket offers 30 and 15); "
                         "only the `ruler` arms move, the fixed-tick ones are a check")
    ap.add_argument("--entries", type=int, default=None, metavar="N",
                    help="entry attempts per day in every window, instead of the "
                         "window's share of the 16-a-day pace")
    args = ap.parse_args()

    MAX_DAYS, BUCKET_S, ENTRIES = args.max_days, args.bucket, args.entries
    if args.windows:
        args.edge = True
    if args.edge:
        ps = [float(x) for x in args.edge_p.split(",")] if args.edge_p else None
        hs = [int(x) for x in args.edge_h.split(",")] if args.edge_h else None
        if args.windows:
            wins = ([k for k, _, _ in WINDOWS] if args.windows.strip() == "*"
                    else [w.strip() for w in args.windows.split(",")])
            bad = [w for w in wins if w not in WINDOW_SPAN]
            if bad:
                raise SystemExit(f"unknown window(s): {', '.join(bad)}")
            ARMS = window_arms(ps, hs, wins)
        else:
            ARMS = focus_arms(ps, hs)
        # Collapsed by default because at zero edge the sizer buys nothing (§7).
        # That reasoning does NOT carry to a positive edge — more size harvests
        # more edge, not just more commission — so ``--keep-sizings`` exists and
        # the edge-run sizing question is answered with it, not assumed.
        if not args.keep_sizings:
            SIZINGS = ["1nq"]
    if args.out:
        OUT = pathlib.Path(args.out)

    if args.validate:
        raise SystemExit(0 if validate(args.validate) else 1)
    if args.page:
        inject_page()
        raise SystemExit(f"wrote {PAGE} from {OUT}")

    marks = MARKS if args.marks == "both" else ["mtm"]
    days = sessions()
    if args.days:
        days = days[-args.days:]
    print(f"{len(days)} sessions, {len(ARMS)} arms, {args.seeds} seeds "
          f"-> {len(days) * len(ARMS) * args.seeds:,} arm-days")

    # Every arm's trades for every session will not fit in memory at once — the
    # tapes are cheap, the skeletons are not — so the arms are walked in batches
    # and only the finished grid rows survive a batch. The draws are seeded off
    # the day, so batching changes nothing but the peak resident size.
    jobs = [(s, d, args.seeds) for s, d in days]
    grid, usable = [], 0
    for b0 in range(0, len(ARMS), args.batch):
        batch = ARMS[b0:b0 + args.batch]
        print(f"arms {b0 + 1}-{b0 + len(batch)} of {len(ARMS)}")
        rows = []
        with mp.Pool(args.workers, initializer=_set_batch, initargs=(batch, BUCKET_S, ENTRIES)) as pool:
            for i, r in enumerate(pool.imap(day_job, jobs, chunksize=4), 1):
                if r.get("error"):
                    if b0 == 0:
                        print(f"  !! {r['day']}: {r['error']}")
                else:
                    rows.append(r)
                if i % 200 == 0:
                    print(f"    {i}/{len(days)} sessions")
        usable = len(rows)
        with mp.Pool(args.workers) as pool:
            chain_jobs = [(a, [d["arms"][a["key"]] for d in rows], marks) for a in batch]
            for part in pool.imap_unordered(chain_arm, chain_jobs):
                grid.extend(part)
        del rows
        print(f"    chained; {len(grid):,} grid rows so far")
    rows_n = usable

    payload = dict(seeds=args.seeds, sessions=rows_n, bucket_s=BUCKET_S, entries_override=ENTRIES, bootstraps=BOOTSTRAPS,
                   max_days=MAX_DAYS, entries_per_day=ENTRIES_PER_DAY,
                   marks=marks, sizings=SIZINGS, day_stops=DAY_STOPS,
                   day_goals=DAY_GOALS, self_stop=SELF_STOP, risk_usd=RISK_USD,
                   edge_ps=sorted({a.get("edge_p", 0.5) for a in ARMS}),
                   horizons=sorted({a.get("horizon", 0) for a in ARMS}),
                   windows={w: dict(start=WINDOW_SPAN[w][0], end=WINDOW_SPAN[w][1],
                                    entries=entries_for(w))
                            for w in sorted({a.get("window", "all") for a in ARMS})},
                   entry_open_sod=ENTRY_OPEN_SOD,
                   accounts={k: dict(label=TEMPLATES[k].label,
                                     trailing=TEMPLATES[k].trailing,
                                     **TEMPLATES[k].defaults.as_dict()) for k in ACCOUNTS},
                   fill=dict(commission=CFG["commission"], slipTicks=CFG["slipTicks"],
                             queueTicks=CFG["queueTicks"], latencyMs=CFG["latencyMs"]),
                   grid=grid)
    OUT.write_text(json.dumps(payload))
    if not args.out:          # a run written elsewhere must not clobber the page
        inject_page()
        print(f"wrote {OUT} and {PAGE} ({len(grid):,} rows)")
    else:
        print(f"wrote {OUT} ({len(grid):,} rows) — page left alone")


def compact(payload: dict) -> dict:
    """The same grid, shaped for the page instead of for reading.

    Seven of each row's fields describe its *arm*, not its row, and there are
    thousands of rows — writing them out per row is most of a 2.5 MB file for a
    page that is otherwise 26 KB. So the arms are hoisted into their own table and
    each row becomes a short array of indices and numbers. The page expands it back
    on load; nothing downstream sees the difference.
    """
    keys, arms = {}, []
    for r in payload["grid"]:
        if r["arm"] not in keys:
            keys[r["arm"]] = len(arms)
            arms.append([r["arm"], r["shape"], r["stop_basis"], r["shipped"],
                         r["med_stop"], r["exits"], r["n_days"]])
    dims = {k: sorted({str(r.get(k, "all")) for r in payload["grid"]})
            for k in ("sizing", "day_stop", "day_goal", "mark", "account", "window")}
    idx = {k: {v: i for i, v in enumerate(vs)} for k, vs in dims.items()}
    rows = [[keys[r["arm"]], idx["sizing"][str(r["sizing"])],
             idx["day_stop"][str(r["day_stop"])], idx["day_goal"][str(r["day_goal"])],
             idx["mark"][str(r["mark"])], idx["account"][str(r["account"])],
             r["passed"], r["bust"], round(r["med_days"], 1),
             round(r["day_net"]), round(r["day_p10"]),
             # Row-level, not hoisted: the arm table is keyed by *bracket*, which
             # is not unique once one bracket is run in several windows.
             idx["window"][str(r.get("window", "all"))], r.get("trades_day", 0)]
            for r in payload["grid"]]
    out = {k: v for k, v in payload.items() if k != "grid"}
    out.update(armTable=arms, dims=dims, rows=rows)
    return out


def inject_page() -> None:
    """Write the finished grid into the page's inline data block."""
    head, tail = PAGE_MARK
    html = PAGE.read_text()
    i = html.index(head) + len(head)
    j = html.index(tail, i)
    blob = json.dumps(compact(json.loads(OUT.read_text())), separators=(",", ":"))
    PAGE.write_text(html[:i] + blob + html[j:])
    print(f"  page data {len(blob) / 1e3:.0f} KB (from {OUT.stat().st_size / 1e3:.0f} KB)")


# --- the gate ----------------------------------------------------------------


def validate(n_days: int) -> bool:
    """Grade the vectorised walk against ``run_flat`` — the engine the app runs.

    The walk above is a second statement of ``run_sim``'s control flow, so it is
    worth exactly what it can prove. This builds the same random entries as a real
    order log, runs the engine over it, and requires the two to agree trade for
    trade on price and reason. Anything less and the study does not run, the way
    ``pick_cfg`` refuses a sitting no fill model reproduces.
    """
    days = sessions()[-n_days:]
    bad = checked = 0
    for sym, day in days:
        try:
            t, px = load_rth(sym, day)
        except Exception as exc:
            print(f"  -- {day}: {exc}")
            continue
        ruler = ruler_series(t, px)
        # Both entry shapes: the full span and the narrowest window, so the
        # windowed draw is gated against the engine too and not just the walk.
        plans = []
        for w in ("all", "h1"):
            span = window_ms(t, w)
            if span is None:
                continue
            rng = np.random.default_rng([day.toordinal(), 0, WINDOW_IX[w]])
            plans.append(draw(rng, t, span=span, n=entries_for(w)))
        for entries, arm in ((e, a) for e in plans for a in ARMS):
            taken: list[tuple[int, int]] = []
            mine = run_day(t, px, ruler, entries, arm, taken=taken)
            if not mine:
                continue
            # The same trades as an order log the engine will accept: only the
            # gestures that actually opened a position, with the bracket in whole
            # ticks, exactly as the ticket would have placed it.
            orders = []
            for k, st, size in taken:
                ms, side = entries[k]
                tgt, trail = legs_in_ticks(arm, st, size)
                if trail is not None:
                    trail = dict(dist=trail["dist"] * TICK, step=trail["step"] * TICK,
                                 be=trail["be"] * TICK, beOnly=trail["beOnly"])
                orders.append(dict(id=str(k), type="market", side=side, size=1, ms=ms,
                                   idx=0, price=None, stop=None, target=None,
                                   stopTicks=st, targetTicks=tgt, trail=trail,
                                   edits=[], cancelMs=None, micro=False))
            st = run_flat(t, px, dict(orders=orders, closes=[], brackets=[]),
                          float(t[-1]), CFG)
            theirs = st["trades"]
            checked += 1
            if len(theirs) != len(mine):
                print(f"  !! {day} {arm['key']}: {len(mine)} trades vs engine {len(theirs)}")
                bad += 1
                continue
            for a, b in zip(mine, theirs):
                if abs(a[0] - b["pts"]) > 1e-9:
                    print(f"  !! {day} {arm['key']}: {a[0]} vs engine {b['pts']}")
                    bad += 1
                    break
    print(f"validate: {checked - bad}/{checked} arm-days reproduce run_flat exactly")
    return bad == 0


if __name__ == "__main__":
    main()
