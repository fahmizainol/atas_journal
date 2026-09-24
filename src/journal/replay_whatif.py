"""Re-price a stored sitting under a different bracket, or the other side of it.

A replay or drill sitting records one set of exit settings — the stop, target and
trail that were on the ticket that day. This module answers what the *same button
presses* would have paid under a different one, by replaying the recorded order log
against the recorded tape with the exits swapped out.

It is a port of the browser fill engine (``frontend/src/lib/replaySim.ts``), and it
earns the right to answer by reproducing the sitting it is about to counterfactual:
``pick_cfg`` searches fill-model configurations and keeps whichever replays
``trades.json`` to the exact dollar. If none does, the grid is refused rather than
approximated — a sitting frozen under an older ``engine_version`` is not re-priced
quietly.

**What a row is not.** Entries were conditioned on what actually happened, so a
wide-stop row reads as "the same clicks under a different bracket", never as "what I
would have done". It is honest as a readout and wrong as a base rate.

**The reversed rows.** Three rows take the *other side* of every entry — same moment,
same size — with the placed stop and target reflected across the fill, so the reversed
trade risks the same number of ticks the original did. A resting order flips its type
along with its side: a buy limit at P becomes a sell stop at P, because that is the
order which triggers where the original triggered, on the same move, in the other
direction. Keeping it a limit would make it wait for a *rise* instead, which is a
different entry at a different time and not the question being asked. These rows are
*less* of a base rate than the bracket rows, not more: entry times chosen by watching
the tape are not the times a contrarian would have chosen.

Two columns, and the difference between them is the price of trading by hand:

  - **as clicked** — the log's manual flattens (``log.closes``) are honoured.
  - **set and forget** — they are dropped; the bracket, the trail, or the
    end-of-sitting mark-to-market decides.

Recorded bracket *drags* (``log.brackets``) are kept on the as-played row alone. A
drag writes an absolute level, so on any other row it would overwrite that row's own
bracket and re-anchor its trail ladder — the row would quietly collapse back toward
what was actually traded. The as-played row keeps them because that row *is* the
stored sitting: its as-clicked cell is the validation anchor.

Three traps this file knows about so you don't rediscover them:

  - **glued-tape idx**: ``OrderRec.idx`` counts from the start of the *glued* tape
    (context days prepended by replay resume), so it overflows a single day's ticks.
    Indices are re-derived from timestamps instead.
  - **tape clock**: the browser tape's ms are the display-zone wall clock read as
    epoch-ms, not UTC. ``load_tape`` converts to match.
  - **absolute levels**: the log stores stop and target as prices, not distances. A
    stop/target override is therefore applied at the fill (``open_position``), never
    by rewriting the log.

The research scripts in ``data/research/replay-trail/`` import this module; it is the
only copy of the engine on the Python side.
"""

from __future__ import annotations

import copy
from datetime import date as date_cls

import numpy as np
import pandas as pd

from . import replays
from .config import DEFAULT_DISPLAY_TZ, DISPLAY_TZS, contract_spec
from .live.broker import MICRO_COMMISSION_FLOOR
from .sim import ticks as tickmod

#: A micro is a tenth of its mini (``frontend/src/lib/contracts.ts``). Orders carry
#: ``micro``, so a sitting traded in MNQ prices off the same NQ tape.
MICRO_RATIO = 10.0


# --- loading -----------------------------------------------------------------


def load_tape(symbol: str, day: str | date_cls, tz: str | None):
    """The session tape as the browser saw it: (epoch-ms, price), on + rth + post.

    The same three reads ``/simulator/session`` makes, in the same order and from
    the same two stores — a day that was *recorded* off Rithmic has no entry in the
    Databento parquet cache, and reading that cache directly (as the first draft of
    this port did) silently loses those sittings.
    """
    d = day if isinstance(day, date_cls) else date_cls.fromisoformat(day)
    rth = tickmod.cached_rth(symbol, d)
    if rth is None:
        raise FileNotFoundError(f"no cached RTH ticks for {symbol} on {d}")
    parts = [
        f
        for f in (tickmod.cached_overnight(symbol, d), rth, tickmod.cached_post(symbol, d))
        if f is not None and not f.empty
    ]
    frame = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]

    zone = DISPLAY_TZS.get(tz or DEFAULT_DISPLAY_TZ, DISPLAY_TZS[DEFAULT_DISPLAY_TZ])
    local = frame["ts_utc"].dt.tz_convert(zone).dt.tz_localize(None)
    t = local.values.astype("datetime64[ms]").astype("int64").astype(np.float64)
    return t, frame["price"].to_numpy(dtype="float64")


def fill_cfg(prefs: dict, symbol: str) -> dict:
    """A fill-model config from an attempt's prefs, priced for ``symbol``."""
    spec = contract_spec(symbol)
    return dict(
        commission=prefs.get("commission", 0),
        slipTicks=prefs.get("slipTicks", 0),
        queueTicks=prefs.get("queueTicks", 0),
        latencyMs=prefs.get("latencyMs", 0),
        tick_size=float(spec["tick_size"]),
        point_value=float(spec["point_value"]),
    )


# --- engine port (mirrors replaySim.ts) --------------------------------------


def money(cfg: dict, micro: bool) -> tuple[float, float]:
    """What a fill is worth and what it costs, in the contract it was taken in.

    Two of the four fill-model numbers move when the order is a micro and two do
    not (``contracts.ts:asMicro``): the money per point is a tenth and the
    commission is billed at the micro rate, while the tick grid and the two
    tick-denominated costs are identical — a micro's book is a tick wide the same
    way its mini's is. So a fill resolves to the same *price* either way.

    A zero commission passes through untouched: the fill model can be switched off
    entirely, and a free fill that started charging fifty cents the moment you
    picked the micro would be the toggle quietly overruling the choice above it.
    """
    pv, com = cfg["point_value"], cfg["commission"]
    if not micro:
        return pv, com
    return pv / MICRO_RATIO, (max(MICRO_COMMISSION_FLOOR, com / MICRO_RATIO) if com > 0 else 0.0)


def cross(px, buying, cfg):
    s = cfg["slipTicks"]
    return px + (1 if buying else -1) * s * cfg["tick_size"] if s > 0 else px


def price_at_ms(t, px, ms):
    i = np.searchsorted(t, ms, side="right") - 1
    return px[max(i, 0)]


def trail_stop(p):
    tc = p["trail"]
    if not tc or tc["dist"] <= 0:
        return None
    d = 1 if p["side"] == "long" else -1
    step = tc["step"] if tc["step"] > 0 else tc["dist"]
    back = max(0.0, tc["dist"] - tc["be"])
    origin = p["ladder"] if p["ladder"] is not None else p["entryPrice"] + d * tc["be"]
    k = int(np.floor(((p["hwm"] - origin) * d - back) / step))
    if k < 0:
        return None
    return origin if tc["beOnly"] else origin + d * k * step


def tighten(p, lvl):
    d = 1 if p["side"] == "long" else -1
    if p["stop"] is not None and (lvl - p["stop"]) * d <= 0:
        return
    p["stop"] = lvl
    p["trailArmed"] = True


def bracket_hit(p, px, gap):
    if p["side"] == "long":
        if p["stop"] is not None and px <= p["stop"]:
            return "stop"
        if p["target"] is not None and px >= p["target"] + gap:
            return "target"
    else:
        if p["stop"] is not None and px >= p["stop"]:
            return "stop"
        if p["target"] is not None and px <= p["target"] - gap:
            return "target"
    return None


def stop_fill(p, px, cfg):
    at = min(px, p["stop"]) if p["side"] == "long" else max(px, p["stop"])
    return cross(at, p["side"] == "short", cfg)


def order_state_at(o, ms):
    s = o
    for e in o.get("edits") or []:
        if e["ms"] > ms:
            break
        s = e
    return s


def open_position(o, legs, ms, idx, price, size, cfg, scen):
    """Open a position, applying the scenario's bracket at the fill.

    The log stores stop and target as *absolute prices*, so an override cannot be
    written into the log ahead of time — it only becomes a price once the fill price
    is known. ``targetR`` is a multiple of whichever stop is in force, so overriding
    the stop moves the target with it.

    A **market** order also carries the bracket the ticket actually stated, in
    ticks (``stopTicks``/``targetTicks``), and for those the distance wins: the
    prices on the record were struck from the mark at the gesture, and the fill
    lands ``latencyMs`` later having paid the spread. This mirrors
    ``replaySim.openPosition`` exactly, which is the only thing that keeps this
    port able to reproduce a sitting — see ``run_flat``'s refusal. Absent on
    every order logged before the distances existed, and those still resolve off
    the prices.
    """
    ts = cfg["tick_size"]
    d = 1 if o["side"] == "long" else -1

    # On a reversed row ``o["side"]`` is already the other side — ``apply_scenario``
    # rewrote the log — so the overrides below need nothing. The levels the log
    # *placed* still point the old way, and are reflected across the fill instead.
    def placed(lvl):
        return lvl if lvl is None or not scen.get("flip") else 2 * price - lvl

    by_ticks = o.get("type") == "market" and (
        o.get("stopTicks") is not None or o.get("targetTicks") is not None
    )
    if by_ticks:
        # Struck from the fill, so a flip needs no reflection: these are
        # distances either side of the price that opened the position, and
        # ``o["side"]`` is already the reversed one.
        st, tg = o.get("stopTicks") or 0, o.get("targetTicks") or 0
        legs = {
            "stop": price - d * st * ts if st > 0 else None,
            "target": price + d * tg * ts if tg > 0 else None,
        }
        placed = lambda lvl: lvl  # noqa: E731 — already relative to this fill

    stop = placed(legs.get("stop"))
    if scen.get("stop") is not None:
        stop = price - d * scen["stop"] * ts

    target = placed(legs.get("target"))
    if scen.get("target") is not None:
        target = price + d * scen["target"] * ts
    elif scen.get("targetR") is not None:
        target = price + d * scen["targetR"] * abs(price - stop) if stop is not None else target

    tr = o.get("trail")
    return dict(side=o["side"], size=size, entryPrice=price, fillMs=ms, fillIdx=idx,
                stop=stop, target=target,
                trail=tr if tr and tr["dist"] > 0 else None,
                hwm=price, ladder=None, trailArmed=False,
                riskPts=abs(price - stop) if stop is not None else None,
                targetPts=abs(price - target) if target is not None else None,
                micro=bool(o.get("micro")))


#: How deep a drawdown has to be before the path records it, in dollars. The same
#: quantum the autosave signature uses, and the *only* approximation in the path:
#: a floor breach is missed only where the account had under $25 of room and the
#: equity recovered without a deeper dip. Everything else below is exact.
PATH_QUANTUM_USD = 25.0


class _Path:
    """The sitting's equity path, kept as the drawdowns an account can die in.

    Storing every turning point is both bigger than it needs to be and less than
    it sounds: an intraday floor rises *only* at a new running maximum, and the
    room left is at its smallest at the lowest point before the next maximum. So
    the whole of what a floor walk can learn from a path is the sequence

        [(running peak, the deepest trough after it), …]

    with the peaks increasing. A rally with no dip in it collapses to a single
    entry, because every intermediate high leaves exactly the same room (the
    equity and the floor rise together) and none of them can ever bind. That is
    a reduction, not a sample: ``replay_account.walk`` computes the same death
    from these pairs that it would from every print.

    Sitting-relative, like ``replay_account.excursion``'s three figures: the
    account's equity can move under a sitting, and an absolute figure would be
    wrong the moment it did. ``peak >= 0 >= trough`` always, because t=0 is a
    point on the path — a sitting that only ever lost has ``peak == 0``, not its
    best trade. Mirrors ``replaySim.ts``'s ``SimState.peakUsd`` exactly, which is
    what makes the as-played row checkable against the browser's own reading.
    """

    def __init__(self) -> None:
        self.peak = 0.0
        self.trough = 0.0
        self._low = 0.0                     # the deepest point since ``peak``
        self._dd: list[list[float]] = []

    def mark(self, eq: float) -> None:
        if eq < self.trough:
            self.trough = eq
        if eq > self.peak:
            # A new high closes the drawdown that preceded it. Shallow ones are
            # dropped: they cannot cost a life that $25 of room would not have
            # survived, and keeping them turns a rally into thousands of rows.
            if self.peak - self._low >= PATH_QUANTUM_USD:
                self._dd.append([self.peak, self._low])
            self.peak = self._low = eq
            return
        if eq < self._low:
            self._low = eq

    def done(self) -> list[list[float]]:
        """The finished path: every drawdown, deepest-point last, in order."""
        out = [*self._dd, [self.peak, self._low]]
        return [[round(hi, 2), round(lo, 2)] for hi, lo in out]


def open_value(p, price: float, cfg: dict) -> float:
    """What an open position is worth right now, in dollars. Zero when flat.

    Gross of the exit's commission, which has not been paid and may never be —
    ``replaySim.ts``'s ``openValue`` marks the same way, and the path is only
    comparable to the browser's stored ``peak_usd``/``trough_usd`` if this one
    does too.
    """
    if not p:
        return 0.0
    d = 1 if p["side"] == "long" else -1
    pv, _ = money(cfg, p.get("micro", False))
    return (price - p["entryPrice"]) * d * pv * p["size"]


def mark(st, price: float, cfg: dict) -> None:
    """Mark the sitting's equity path at one price. Called per print."""
    st["path"].mark(st["realized"] + open_value(st["open"], price, cfg))


def reduce(st, size, ms, price, reason, cfg):
    p = st["open"]
    d = 1 if p["side"] == "long" else -1
    pts = (price - p["entryPrice"]) * d
    pv, com = money(cfg, p.get("micro", False))
    fees = 2 * com * size
    # ``riskPts`` and ``targetPts`` are the distances the two legs stood at when the
    # position opened, and a drag never overwrites either — so these are the *initial*
    # bracket, which is what a what-if row is characterised by.
    ts = cfg["tick_size"]
    risk, tgt = p.get("riskPts"), p.get("targetPts")
    st["trades"].append(dict(side=p["side"], size=size, entryPrice=p["entryPrice"],
                             entryMs=p["fillMs"], exitMs=ms, exitPrice=price,
                             reason=reason, pts=pts, micro=p.get("micro", False),
                             stopTicks=None if risk is None else round(risk / ts),
                             targetTicks=None if tgt is None else round(tgt / ts),
                             pnl=pts * pv * size - fees, fees=fees))
    p["size"] -= size
    if p["size"] <= 0:
        st["open"] = None
    # The booked half of the equity path, and the excursion re-marked at the
    # price it booked at. Marking here as well as per print is what puts a close
    # that happened *between* two prints on the path: without it a trade that
    # stopped out at its worst and never printed there again would leave a
    # trough the account could not see. Both sites mirror ``replaySim.ts``.
    st["realized"] += st["trades"][-1]["pnl"]
    mark(st, price, cfg)


def apply_fill(st, o, legs, ms, idx, price):
    cfg, scen = st["cfg"], st["scen"]
    p = st["open"]
    if not p:
        st["open"] = open_position(o, legs, ms, idx, price, o["size"], cfg, scen)
        return
    if p["side"] == o["side"]:
        p["entryPrice"] = (p["entryPrice"] * p["size"] + price * o["size"]) / (p["size"] + o["size"])
        p["size"] += o["size"]
        p["fillIdx"] = idx
        return
    closed = min(o["size"], p["size"])
    reduce(st, closed, ms, price, "reduce", cfg)
    rest = o["size"] - closed
    if rest > 0:
        st["open"] = open_position(o, legs, ms, idx, price, rest, cfg, scen)


def run_sim(t, px, log, clock, cfg, scen=None):
    scen = scen or {}
    orders = copy.deepcopy(log["orders"])
    closes = log.get("closes") or []
    brackets = log.get("brackets") or []
    # glued-tape idx trap: re-derive indices from timestamps
    for o in orders:
        o["idx"] = int(np.searchsorted(t, o["ms"], side="left"))
    gap = cfg["queueTicks"] * cfg["tick_size"]
    # How long a gesture spends in flight (replaySim.ts `stepSim`). Absent or
    # zero, every expression below collapses to the order's own stamp and cursor
    # — so a log recorded before the lag model reproduces exactly as it did.
    lag = cfg.get("latencyMs", 0) or 0
    st = dict(open=None, trades=[], working=[], oi=0, ci=0, bi=0, cfg=cfg, scen=scen,
              realized=0.0, path=_Path())

    def landed(ms, idx):
        """The tape as it stood when a gesture stamped `ms` arrived."""
        if lag <= 0:
            return ms, idx
        return ms + lag, int(np.searchsorted(t, ms + lag, side="right"))

    def admin(ms):
        # Which gestures have *reached the market* by now.
        at = ms - lag
        while st["oi"] < len(orders) and orders[st["oi"]]["ms"] <= at:
            o = orders[st["oi"]]
            st["oi"] += 1
            ms0, idx0 = landed(o["ms"], o["idx"])
            if o["type"] == "market":
                oco = st["open"] is None
                apply_fill(st, o, o, ms0, idx0,
                           cross(price_at_ms(t, px, ms0), o["side"] == "long", cfg))
                if oco:
                    st["working"] = [w for w in st["working"] if not w["oco"]]
            else:
                st["working"].append(dict(o=o, oco=st["open"] is None, idx=idx0))
        if st["working"]:
            st["working"] = [w for w in st["working"]
                             if w["o"].get("cancelMs") is None or w["o"]["cancelMs"] > at]
        while st["bi"] < len(brackets) and brackets[st["bi"]]["ms"] <= at:
            b = brackets[st["bi"]]
            st["bi"] += 1
            if st["open"]:
                p = st["open"]
                s, tg = b.get("stop"), b.get("target")
                # A drag onto a leg that was never placed *is* that leg's initial
                # distance; a drag onto one that was placed is not, and must not
                # overwrite it.
                if p["riskPts"] is None and s is not None:
                    p["riskPts"] = abs(p["entryPrice"] - s)
                if p["targetPts"] is None and tg is not None:
                    p["targetPts"] = abs(p["entryPrice"] - tg)
                p["stop"] = s
                p["target"] = tg
                if s is not None and p["trail"]:
                    d = 1 if p["side"] == "long" else -1
                    p["ladder"] = s
                    cap = s + d * max(0.0, p["trail"]["dist"] - p["trail"]["be"])
                    if (cap - p["hwm"]) * d < 0:
                        p["hwm"] = cap
                    p["trailArmed"] = False
        while st["ci"] < len(closes) and closes[st["ci"]]["ms"] <= at:
            c = closes[st["ci"]]
            st["ci"] += 1
            # Flattening by hand pays for the spread — and the lag — like any
            # other market order.
            ms0, _ = landed(c["ms"], 0)
            if st["open"]:
                reduce(st, st["open"]["size"], ms0,
                       cross(price_at_ms(t, px, ms0), st["open"]["side"] == "short", cfg),
                       "manual", cfg)

    start = int(np.searchsorted(t, orders[0]["ms"], side="left")) if orders else len(t)
    for i in range(max(0, start), len(t)):
        ms = t[i]
        if ms > clock:
            break
        if st["oi"] < len(orders) or st["ci"] < len(closes) or st["bi"] < len(brackets) or st["working"]:
            admin(ms)
        p_ = px[i]
        if st["open"] and i >= st["open"]["fillIdx"]:
            p = st["open"]
            hit = bracket_hit(p, p_, gap)
            if hit:
                why = "trail" if hit == "stop" and p["trailArmed"] else hit
                reduce(st, p["size"], ms,
                       stop_fill(p, p_, cfg) if hit == "stop" else p["target"], why, cfg)
            elif p["trail"]:
                d = 1 if p["side"] == "long" else -1
                if (p_ - p["hwm"]) * d > 0:
                    p["hwm"] = p_
                lvl = trail_stop(p)
                if lvl is not None:
                    tighten(p, lvl)
        if st["working"]:
            for w in list(st["working"]):
                if i < w["idx"] or w not in st["working"]:
                    continue
                # The levels as they had reached the market, not the screen: a
                # drag is a modify on the wire, so the old level stands until
                # the new one lands.
                s = order_state_at(w["o"], ms - lag)
                if s.get("price") is None:
                    continue
                is_stop = w["o"]["type"] == "stop"
                wants_up = (w["o"]["side"] == "long") if is_stop else (w["o"]["side"] == "short")
                at = s["price"] if is_stop else (s["price"] + gap if wants_up else s["price"] - gap)
                if (p_ >= at) if wants_up else (p_ <= at):
                    fill = (cross(max(p_, s["price"]) if wants_up else min(p_, s["price"]),
                                  wants_up, cfg) if is_stop else s["price"])
                    apply_fill(st, w["o"], s, ms, i + 1, fill)
                    st["working"] = [x for x in st["working"]
                                     if x is not w and not (w["oco"] and x["oco"])]
        # The sitting's equity path, marked last on the tick so everything that
        # happened at this price has happened: the bracket has been read and any
        # resting order has filled. Marking before them would let one print both
        # set a new high-water and be the print that stopped you out of it — the
        # same ordering argument the trail's ``hwm`` makes above.
        mark(st, p_, cfg)
    admin(clock)
    return st


def mark_to_market(st, t, px, clock, cfg):
    """Book a position still open at the end clock, at the closing price.

    The browser does *not* do this — a sitting that ends holding something leaves
    it out of ``trades.json`` entirely, which is why validation must not do it
    either. The grid does, on every row including the anchor, because otherwise the
    rows stop being comparable: a trail that exits the last position books its
    result while a no-trail row holding the same position books nothing, and the
    row with no exit wins by not being asked.
    """
    if st["open"]:
        p = st["open"]
        reduce(st, p["size"], clock,
               cross(price_at_ms(t, px, clock), p["side"] == "short", cfg), "open", cfg)
    return st


def run_flat(t, px, log, clock, cfg, scen=None):
    """``run_sim`` plus the end-of-sitting mark-to-market."""
    return mark_to_market(run_sim(t, px, log, clock, cfg, scen), t, px, clock, cfg)


def summarize(trades) -> dict:
    pnl = [tr["pnl"] for tr in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    reasons: dict[str, int] = {}
    for tr in trades:
        reasons[tr["reason"]] = reasons.get(tr["reason"], 0) + 1
    return dict(n=len(trades), net=round(sum(pnl)),
                # The booked path, in the order it happened. ``net`` cannot give
                # this and the account needs it: a sitting that dived through the
                # floor and traded back settles above it, and only the running
                # sum says so (``replay_account.walk``).
                pnls=[round(x, 2) for x in pnl],
                wr=round(100 * len(wins) / len(pnl)) if pnl else 0,
                avg_win=round(np.mean(wins)) if wins else 0,
                avg_loss=round(np.mean(losses)) if losses else 0,
                stop_ticks=tick_span(trades, "stopTicks"),
                target_ticks=tick_span(trades, "targetTicks"),
                reasons=reasons)


def path_of(st) -> dict:
    """The equity excursions a finished sim produced, for the account to walk.

    Separate from ``summarize`` because that one is pure over a trade list and is
    also run on the *stored* trades, which came from a browser and have no sim
    state behind them. Only a run of the engine can answer this.
    """
    p = st["path"]
    return {"peak_usd": round(p.peak, 2), "trough_usd": round(p.trough, 2),
            "equity_path": p.done()}


def tick_span(trades, key: str) -> dict | None:
    """The initial bracket leg these trades ran, in ticks: ``{lo, med, hi}`` or ``None``.

    A range rather than a number because most sittings do not run one bracket. The
    ticket's stop is sized off the volatility ruler, so it moves with every fill — two
    thirds of the finished corpus varies inside a single sitting, one drill from 44 to
    109 ticks. The target moves too, for a different reason: the log stores it as an
    absolute price, so how far it sits from the entry depends on where the fill landed.
    A row that overrides a leg collapses that leg to a single value, and that difference
    is the point of showing this at all.
    """
    v = sorted(tr[key] for tr in trades if tr.get(key) is not None)
    if not v:
        return None
    return {"lo": v[0], "med": int(round(float(np.median(v)))), "hi": v[-1]}


# --- the order behind one stored fill ----------------------------------------
#
# Not a re-pricing: a *reading*. Given a journal row that a sitting mirrored, say
# how that position was opened and where its bracket sat — the two things the
# mirror drops on the way to the journal (`live.booking.journal_row` keeps the
# exit reason in a comment and nothing else). Lab → Recall's back card asks for
# it, and asks on a flip, so this reads the two small JSON files and never opens
# a tape. What it cannot do without one is re-derive anything: every number here
# is either stored or struck from the stored fill price by `open_position`, which
# is the same rule the engine opened the position under.

def _fill_of(trades: list[dict], *, side: str, size: float, entry: float,
             exit_px: float, duration_s: float | None) -> dict | None:
    """The sitting's own record of one journaled trade, matched on what survives.

    Matched on prices, side and size rather than on the clock, because the two
    clocks are not the same one: the journal stamps a *wall* time in Eastern
    while the log stamps the display-zone epoch the tape ran on, and reconciling
    them needs the attempt's zone. Duration survives both, so it is what breaks
    a tie — and a sitting with two identical round trips of identical length is
    a sitting where either answer is the same answer.
    """
    hits = [
        t for t in trades
        if t.get("side") == side
        and abs(float(t.get("size") or 0) - size) < 1e-9
        and abs(float(t.get("entryPrice") or 0) - entry) < 1e-4
        and abs(float(t.get("exitPrice") or 0) - exit_px) < 1e-4
    ]
    if not hits:
        return None
    if len(hits) == 1 or duration_s is None:
        return hits[0]
    return min(hits, key=lambda t: abs(
        (float(t.get("exitMs") or 0) - float(t.get("entryMs") or 0)) / 1000.0 - duration_s))


def _opening_order(log: dict, trade: dict) -> dict | None:
    """The order whose fill opened ``trade``, or None if the log has no candidate.

    Nothing joins the two — the log has no trade id and the trade has no order id
    — so this is the narrowest honest reconstruction: the last order of the
    trade's own side *and type* placed at or before the fill, having never been
    cancelled. ``openType`` is the engine's own word for which of the three
    opened the position, so it does most of the work; where two resting orders of
    one type sat on one side, the one whose level is the fill price wins, a limit
    filling at its limit by construction.
    """
    ms = float(trade.get("entryMs") or 0)
    cands = [
        o for o in (log.get("orders") or [])
        if o.get("side") == trade.get("side")
        and o.get("type") == trade.get("openType")
        and o.get("cancelMs") is None
        and float(o.get("ms") or 0) <= ms
    ]
    if not cands:
        return None
    if trade.get("openType") != "market":
        px = float(trade.get("entryPrice") or 0)
        at_price = [
            o for o in cands
            if (lvl := order_state_at(o, ms).get("price")) is not None
            and abs(float(lvl) - px) < 1e-4
        ]
        cands = at_price or cands
    return max(cands, key=lambda o: float(o.get("ms") or 0))


def order_behind_fill(attempt_id: str, *, side: str, size: float,
                      entry_price: float, exit_price: float,
                      duration_s: float | None = None) -> dict | None:
    """How one mirrored trade was opened, and the bracket it opened with.

    None when the sitting is not on disk or holds no trade that matches — the
    caller has a journal row either way, and a bracket guessed off the wrong fill
    would be worse than no bracket at all.

    The bracket reported is the one the position **opened** with, which is the
    risk that was actually accepted — not necessarily the levels the exit fired
    against. A drag moves the position's stop or target afterwards and the trail
    moves the stop on its own; either can put an exit somewhere the opening
    bracket does not explain, so ``moved`` and ``trail_pts`` are what stop the
    two numbers from reading as a contradiction. Checked against every stored
    sitting: of 1213 trades, the opening levels account for every stop and target
    exit except the 41 taken after a drag, and for those ``moved`` is true.

    ``rest_ms`` is a *duration*, not a stamp: how long the entry sat at
    ``rest_price`` before it filled. That keeps the zone problem out of the
    payload entirely — a reader that knows when the fill was knows when the order
    came to rest, in whatever clock it already holds.
    """
    try:
        rec = replays.read(attempt_id)
    except (FileNotFoundError, ValueError):
        return None
    trade = _fill_of(rec.get("trades") or [], side=side, size=size,
                     entry=entry_price, exit_px=exit_price, duration_s=duration_s)
    if trade is None:
        return None

    log = rec.get("log") or {}
    out = {
        "open_type": trade.get("openType") or "market",
        "exit_reason": trade.get("reason") or "manual",
        "rest_price": None,
        "rest_ms": None,
        "stop": None,
        "target": None,
        "trail_pts": None,
        "trail_be_only": False,
        "moved": any(
            float(trade.get("entryMs") or 0) <= float(b.get("ms") or 0)
            <= float(trade.get("exitMs") or 0)
            for b in (log.get("brackets") or [])
        ),
    }
    o = _opening_order(log, trade)
    if o is None:
        return out

    fill_ms = float(trade.get("entryMs") or 0)
    state = order_state_at(o, fill_ms)
    price = float(trade.get("entryPrice") or 0)
    # The bracket the *position* opened with, through the engine's own rule
    # rather than off the record's levels: a market order's ticket said ticks,
    # and those are struck from the fill. `open_position` reads nothing from cfg
    # but the tick size, and an empty scenario overrides nothing.
    pos = open_position(o, state, fill_ms, 0, price, float(trade.get("size") or 0),
                        {"tick_size": contract_spec(rec.get("symbol") or "")["tick_size"]},
                        {})
    out["stop"] = pos["stop"]
    out["target"] = pos["target"]
    if pos["trail"]:
        out["trail_pts"] = pos["trail"]["dist"]
        out["trail_be_only"] = bool(pos["trail"].get("beOnly"))
    if o.get("type") != "market":
        out["rest_price"] = state.get("price")
        out["rest_ms"] = int(max(
            0.0, fill_ms - float(state.get("ms") or o.get("ms") or fill_ms)))
    return out


# --- scenarios ---------------------------------------------------------------
#
# A scenario spec is one dict, and it is also exactly the shape a user-added row
# arrives in from the browser:
#
#   {"stop": int | None,       ticks from entry; None = the stop that was placed
#    "target": int | None,     ticks from entry
#    "targetR": float | None,  or: a multiple of whichever stop is in force
#    "trail": "as-placed" | None | {"dist": int, "step": int, "beOnly": bool},
#    "flip": bool}             take the other side of every entry, legs mirrored
#
# The two columns are not part of the spec — every row is priced both ways.

#: The default breakeven offset for a row that adds a trail to an order which had
#: none: three ticks, the ticket's own default.
DEFAULT_BE_TICKS = 3


def _trail_row(dist: int, step: int = 0, be_only: bool = False) -> dict:
    return {"stop": None, "target": None, "targetR": None,
            "trail": {"dist": dist, "step": step, "beOnly": be_only}}


def _r_row(r: float, trail=None) -> dict:
    return {"stop": None, "target": None, "targetR": r, "trail": trail}


def _sl_row(stop: int, trail=None) -> dict:
    return {"stop": stop, "target": None, "targetR": None, "trail": trail}


#: The ladder every sitting is priced against. The thirteen trail regimes are the
#: grid of ``docs/research/replay-exit-whatif.md`` (t25 the winner, ≥75t bad, steps
#: noise); the four R rows are §5 of the same study, where 1R set-and-forget and
#: 1R+BE25 were the best cells of the whole thing. The six fixed-stop rows are the
#: one exit the study never varied — see the comment on them below.
PRESETS: list[dict] = [
    {"key": "as-played", "label": "As played", "spec":
        {"stop": None, "target": None, "targetR": None, "trail": "as-placed"}},
    {"key": "no-trail", "label": "No trail", "spec":
        {"stop": None, "target": None, "targetR": None, "trail": None}},
    {"key": "be25", "label": "BE at 25t", "spec": _trail_row(25, 0, True)},
    {"key": "be50", "label": "BE at 50t", "spec": _trail_row(50, 0, True)},
    {"key": "t25", "label": "Trail 25t", "spec": _trail_row(25)},
    {"key": "t35", "label": "Trail 35t", "spec": _trail_row(35)},
    {"key": "t50", "label": "Trail 50t", "spec": _trail_row(50)},
    {"key": "t75", "label": "Trail 75t", "spec": _trail_row(75)},
    {"key": "t100", "label": "Trail 100t", "spec": _trail_row(100)},
    {"key": "t50s10", "label": "Trail 50t / 10t steps", "spec": _trail_row(50, 10)},
    {"key": "t50s25", "label": "Trail 50t / 25t steps", "spec": _trail_row(50, 25)},
    {"key": "t75s25", "label": "Trail 75t / 25t steps", "spec": _trail_row(75, 25)},
    {"key": "t100s25", "label": "Trail 100t / 25t steps", "spec": _trail_row(100, 25)},
    {"key": "r1", "label": "Target 1R, no trail", "spec": _r_row(1.0)},
    {"key": "r15", "label": "Target 1.5R, no trail", "spec": _r_row(1.5)},
    {"key": "r2", "label": "Target 2R, no trail", "spec": _r_row(2.0)},
    {"key": "r1be25", "label": "Target 1R + BE at 25t", "spec":
        _r_row(1.0, {"dist": 25, "step": 0, "beOnly": True})},
    # The stop held still. Every row above inherits whatever stop the ticket sized
    # per fill, which is rarely the same twice; these six ask what a *consistent*
    # one would have paid — the leak the vol-sizing study points at. The target is
    # left as it was placed, so the only thing that moved is the stop (and, on the
    # second three, the trail that the trail grid picked as its winner).
    {"key": "sl30", "label": "SL 30t, no trail", "spec": _sl_row(30)},
    {"key": "sl50", "label": "SL 50t, no trail", "spec": _sl_row(50)},
    {"key": "sl75", "label": "SL 75t, no trail", "spec": _sl_row(75)},
    {"key": "sl30t25", "label": "SL 30t + trail 25t", "spec":
        _sl_row(30, {"dist": 25, "step": 0, "beOnly": False})},
    {"key": "sl50t25", "label": "SL 50t + trail 25t", "spec":
        _sl_row(50, {"dist": 25, "step": 0, "beOnly": False})},
    {"key": "sl75t25", "label": "SL 75t + trail 25t", "spec":
        _sl_row(75, {"dist": 25, "step": 0, "beOnly": False})},
    # The other side. Three rows and not one because the trail is path-dependent:
    # a reversed trade is not the negative of the one that was taken, and how much
    # it is not is the whole reason to price it. As placed, with the bracket alone,
    # and under the trail the exit study picked as its winner.
    {"key": "rev", "label": "Reversed, exits as placed", "spec":
        {"stop": None, "target": None, "targetR": None, "trail": "as-placed",
         "flip": True}},
    {"key": "rev-nt", "label": "Reversed, no trail", "spec":
        {"stop": None, "target": None, "targetR": None, "trail": None, "flip": True}},
    {"key": "rev-t25", "label": "Reversed, trail 25t", "spec":
        {**_trail_row(25), "flip": True}},
]

#: What a *cached* grid was produced by (``replays.read_whatif``). Bump it by hand
#: whenever ``PRESETS`` changes or the fill model's arithmetic does — a stored
#: grid stamped with an older number is treated as missing rather than as usable,
#: because a row keyed ``t25`` that used to mean something else is worse than no
#: row at all. It is *not* the attempt's ``engine_version``: that says what wrote
#: the sitting, this says what re-priced it, and either can move alone.
GRID_VERSION = 1


def apply_scenario(log: dict, spec: dict, tick_size: float, *,
                   drop_closes: bool = False, drop_drags: bool = False) -> dict:
    """The log this row replays: side and trail rewritten, hand gestures kept or dropped.

    Only the side, the order type and the trail live in the log (the trail is
    per-order and stored in price units). Stop, target and R are applied at the fill
    instead — see ``open_position``, which also mirrors the placed legs.
    """
    lg = copy.deepcopy(log)
    if spec.get("flip"):
        for o in lg["orders"]:
            o["side"] = "short" if o["side"] == "long" else "long"
            # A resting order flips type with its side so that it still triggers
            # where it did: a buy limit at P waits for a fall to P, and so does a
            # sell stop at P. See the module docstring.
            if o["type"] in ("limit", "stop"):
                o["type"] = "stop" if o["type"] == "limit" else "limit"
    trail = spec.get("trail", "as-placed")
    if trail != "as-placed":
        for o in lg["orders"]:
            if trail is None:
                o["trail"] = None
                continue
            old = o.get("trail")
            be = (old["be"] if old and old.get("dist", 0) > 0
                  else DEFAULT_BE_TICKS * tick_size)
            o["trail"] = dict(dist=trail["dist"] * tick_size,
                              step=trail["step"] * tick_size,
                              be=be, beOnly=bool(trail["beOnly"]))
    if drop_closes:
        lg["closes"] = []
    if drop_drags:
        lg["brackets"] = []
    return lg


def scen_of(spec: dict) -> dict:
    """The half of a spec that is applied at the fill rather than in the log."""
    return {"stop": spec.get("stop"), "target": spec.get("target"),
            "targetR": spec.get("targetR"), "flip": bool(spec.get("flip"))}


# --- validation --------------------------------------------------------------


#: Fill models to try when reproducing a stored sitting, most-likely first. An
#: attempt's ``prefs`` are a creation-time snapshot but its trades were re-derived
#: under whatever fill model was current when the browser last replayed them — the
#: commission 7→3.5 correction moved every older sitting, and ``latencyMs`` arrived
#: after the earliest ones. Engine-v1 sittings priced nothing at all and validate at
#: zero, which is inert.
_FALLBACK_CFGS = [
    dict(commission=3.5, slipTicks=1, queueTicks=1, latencyMs=250),
    dict(commission=3.5, slipTicks=1, queueTicks=1, latencyMs=0),
    dict(commission=0, slipTicks=0, queueTicks=0, latencyMs=0),
    dict(commission=7, slipTicks=1, queueTicks=1, latencyMs=0),
]


def pick_cfg(attempt: dict, t, px, log, recorded, clock, symbol: str | None = None):
    """The fill model that replays this sitting to the dollar, or ``None``.

    Whether the end-of-sitting position is booked is part of what gets searched.
    Finishing a sitting flattens what it was holding into ``trades.json``; leaving
    one open — abandoned, or reviewed mid-position — does not. Both conventions are
    in the corpus, so trying only one disqualifies real sittings for a reason that
    has nothing to do with the fill model.

    Returns ``(cfg, state)`` on success. The state is left in whichever convention
    matched, so ``state["open"]`` afterwards means *this record does not include the
    position it ended on* — the caller marks it to market and says so.
    """
    sym = symbol or attempt["symbol"]
    spec = contract_spec(sym)
    money = dict(tick_size=float(spec["tick_size"]), point_value=float(spec["point_value"]))

    cands = []
    prefs = attempt.get("prefs") or {}
    if "commission" in prefs:
        for lag in (prefs.get("latencyMs", 0), 0):
            cands.append(dict(commission=prefs["commission"], slipTicks=prefs["slipTicks"],
                              queueTicks=prefs["queueTicks"], latencyMs=lag))
    cands += _FALLBACK_CFGS

    want = (summarize(recorded)["n"], summarize(recorded)["net"])
    for cand in cands:
        cfg = {**cand, **money}
        st = run_sim(t, px, copy.deepcopy(log), clock, cfg)
        got = summarize(st["trades"])
        if (got["n"], got["net"]) == want:
            return cfg, st
        if st["open"]:
            flat = mark_to_market(copy.deepcopy(st), t, px, clock, cfg)
            got = summarize(flat["trades"])
            if (got["n"], got["net"]) == want:
                return cfg, flat
    return None, None


# --- the grid ----------------------------------------------------------------


def _cache_key(spec: dict, log: dict, drop_closes: bool, drop_drags: bool):
    """Identity of a simulation, with the flags that can't matter normalised away.

    A sitting with no manual closes prices the same in both columns; one with no
    drags prices the same whether or not they are dropped. Folding those cases
    together halves the work on the sittings where it is free to.
    """
    trail = spec.get("trail", "as-placed")
    trail_key = trail if trail in (None, "as-placed") else (
        trail["dist"], trail["step"], bool(trail["beOnly"]))
    return (spec.get("stop"), spec.get("target"), spec.get("targetR"), trail_key,
            bool(spec.get("flip")),
            bool(drop_closes) if log.get("closes") else None,
            bool(drop_drags) if log.get("brackets") else None)


def grid(attempt_id: str, custom: list[dict] | None = None) -> dict:
    """Every preset row (plus any custom ones) priced in both columns.

    Refuses rather than approximates: if no fill model reproduces the stored
    sitting, the answer is ``valid: False`` and no rows at all.
    """
    rec = replays.read(attempt_id)
    log = rec.get("log") or {"orders": [], "closes": [], "brackets": []}
    recorded = rec.get("trades") or []
    symbol = rec["symbol"]
    tick_size = float(contract_spec(symbol)["tick_size"])
    stored = summarize(recorded)

    if not log.get("orders"):
        return {"valid": False, "reason": "This sitting has no order log to replay.",
                "stored": {"n": stored["n"], "net": stored["net"]},
                "cfg": None, "open_marked": False, "tape_drift": False, "rows": []}

    try:
        t, px = load_tape(symbol, rec["date"], rec.get("tz"))
    except FileNotFoundError:
        return {"valid": False,
                "reason": (f"The tick tape for {symbol} on {rec['date']} is not in the "
                           "cache any more, so this sitting cannot be re-run."),
                "stored": {"n": stored["n"], "net": stored["net"]},
                "cfg": None, "open_marked": False, "tape_drift": False, "rows": []}
    clock = rec["clock_ms"]
    cfg, anchor = pick_cfg(rec, t, px, log, recorded, clock, symbol)
    if cfg is None:
        return {
            "valid": False,
            "reason": ("No fill model reproduces this sitting's stored trades, so its "
                       "what-ifs would not be comparable to it. Sittings recorded under "
                       "an older engine version read this way."),
            "stored": {"n": stored["n"], "net": stored["net"]},
            "cfg": None,
            "open_marked": False,
            "tape_drift": len(t) != (rec.get("tape") or {}).get("n", len(t)),
            "rows": [],
        }

    # The sitting ended holding something. Every row prices it at the close, so the
    # As played row will read that much away from the stored net — the UI says so.
    open_marked = anchor["open"] is not None
    cache: dict = {}
    as_played_spec = PRESETS[0]["spec"]
    played = mark_to_market(anchor, t, px, clock, cfg)
    cache[_cache_key(as_played_spec, log, False, False)] = {
        **summarize(played["trades"]), **path_of(played)}

    def run(spec, *, drop_closes, drop_drags):
        key = _cache_key(spec, log, drop_closes, drop_drags)
        if key not in cache:
            lg = apply_scenario(log, spec, tick_size,
                                drop_closes=drop_closes, drop_drags=drop_drags)
            st = run_flat(t, px, lg, clock, cfg, scen_of(spec))
            cache[key] = {**summarize(st["trades"]), **path_of(st)}
        return cache[key]

    rows = []
    for i, row in enumerate([*PRESETS, *(custom or [])]):
        spec = row["spec"]
        as_played = i == 0
        rows.append({
            "key": row.get("key") or f"custom-{i}",
            "label": row.get("label") or describe(spec),
            "spec": spec,
            "custom": i >= len(PRESETS),
            # Drags are the hand on the bracket. They belong to the sitting that
            # was actually traded and to no other row — see the module docstring.
            "drags_kept": as_played,
            "clicked": run(spec, drop_closes=False, drop_drags=not as_played),
            "forget": run(spec, drop_closes=True, drop_drags=True),
        })

    return {
        "valid": True,
        "reason": None,
        "stored": {"n": stored["n"], "net": stored["net"]},
        "cfg": cfg,
        "open_marked": open_marked,
        "tape_drift": len(t) != (rec.get("tape") or {}).get("n", len(t)),
        "rows": rows,
    }


# --- the cached grid ---------------------------------------------------------


def cached(attempt_id: str, rec: dict | None = None) -> dict | None:
    """This sitting's stored grid, if one was priced under the current rules."""
    rec = rec if rec is not None else replays.read(attempt_id)
    return replays.read_whatif(
        attempt_id,
        engine_version=int(rec.get("engine_version") or 0),
        grid_version=GRID_VERSION,
    )


def price(attempt_id: str, *, force: bool = False) -> dict:
    """Measure this sitting's grid and store it. Returns the stored payload.

    Idempotent by fingerprint: a sitting already priced under this engine and
    this ladder is returned as it stands unless ``force``. Storing the *refusal*
    matters as much as storing the rows — see ``replays.write_whatif``.

    The presets only. Custom rows are built in a browser and live in its own
    storage (``lib/whatIfPrefs.ts``), so they are priced on demand by the day
    view and are not part of any account's answer.
    """
    rec = replays.read(attempt_id)
    if not force:
        got = cached(attempt_id, rec)
        if got is not None:
            return got
    out = grid(attempt_id)
    return replays.write_whatif(attempt_id, {
        "engine_version": int(rec.get("engine_version") or 0),
        "grid_version": GRID_VERSION,
        "priced_at": replays._iso(replays._utc_now()),
        "valid": bool(out.get("valid")),
        "reason": out.get("reason"),
        "cfg": out.get("cfg"),
        "open_marked": bool(out.get("open_marked")),
        "stored": out.get("stored"),
        "rows": out.get("rows") or [],
    })


def describe(spec: dict) -> str:
    """A label for a row the user built, in the terms they built it in."""
    bits = ["Reversed"] if spec.get("flip") else []
    if spec.get("stop") is not None:
        bits.append(f"SL {spec['stop']}t")
    if spec.get("target") is not None:
        bits.append(f"TP {spec['target']}t")
    elif spec.get("targetR") is not None:
        bits.append(f"TP {spec['targetR']:g}R")
    trail = spec.get("trail", "as-placed")
    if trail is None:
        bits.append("no trail")
    elif trail != "as-placed":
        step = f" / {trail['step']}t steps" if trail.get("step") else ""
        bits.append(f"BE at {trail['dist']}t" if trail.get("beOnly")
                    else f"trail {trail['dist']}t{step}")
    return ", ".join(bits) or "As placed"
