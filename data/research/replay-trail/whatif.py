"""Replay-sitting what-if harness: re-run a stored order log under different
trail settings, plus tape-texture metrics for the traded window.

Ports the browser fill engine (frontend/src/lib/replaySim.ts) closely enough
to reproduce a stored sitting TO THE DOLLAR — validated against summary.json
before any grid is trusted. See docs/research/replay-trail-whatif.md for the
study this was built for.

Two traps this file knows about so you don't rediscover them:

  - glued-tape idx: OrderRec.idx counts from the start of the *glued* tape
    (context days prepended by replay resume), so it overflows the single-day
    parquet. Indices are re-derived from timestamps instead.
  - tape clock: the browser tape's ms are the display-zone (ET) wall clock
    read as epoch-ms, not UTC. The parquet's ts_utc is converted to match.

Usage:
    .venv/bin/python data/research/replay-trail/whatif.py [attempt_id ...]

No arguments runs the three sittings of the 2026-08-10 study.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[3]
TICK = 0.25
POINT_VALUE = 20.0  # $/point, NQ

STUDY_ATTEMPTS = [
    "2025-03-13_NQH5_20260810T050644Z",
    "2025-12-04_NQZ5_20260810T055423Z",
    "2026-02-10_NQH6_20260810T063621Z",
]

# (dist_ticks, step_ticks); step 0 = rungs a full dist apart (the UI default)
GRID = [(25, 0), (25, 5), (35, 0), (35, 5), (50, 0), (50, 5), (50, 10),
        (60, 0), (75, 0), (75, 10), (100, 0)]


# --- loading -----------------------------------------------------------------


def load_attempt(aid: str):
    d = ROOT / "data/replays" / aid[:10] / aid
    attempt = json.loads((d / "attempt.json").read_text())
    log = json.loads((d / "log.json").read_text())
    trades = json.loads((d / "trades.json").read_text())
    summary = json.loads((d / "summary.json").read_text())
    return attempt, log, trades, summary


def load_tape(symbol: str, date: str, tz: str):
    df = pd.read_parquet(ROOT / f"data/cache/ticks/{symbol}_{date}_day.parquet")
    zone = "America/New_York" if tz in ("New York", "America/New_York") else tz
    wall = df["ts_utc"].dt.tz_convert(zone).dt.tz_localize(None)
    t = (wall.astype("int64").to_numpy() // 1_000_000).astype(np.float64)
    return t, df["price"].to_numpy()


# --- engine port (mirrors replaySim.ts) --------------------------------------


def cross(px, buying, cfg):
    s = cfg["slipTicks"]
    return px + (1 if buying else -1) * s * TICK if s > 0 else px


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


def open_position(o, legs, ms, idx, price, size):
    stop = legs.get("stop")
    risk = abs(price - stop) if stop is not None else None
    tr = o.get("trail")
    return dict(side=o["side"], size=size, entryPrice=price, fillMs=ms, fillIdx=idx,
                stop=stop, target=legs.get("target"),
                trail=tr if tr and tr["dist"] > 0 else None,
                hwm=price, ladder=None, trailArmed=False, riskPts=risk)


def reduce(st, size, ms, price, reason, cfg):
    p = st["open"]
    d = 1 if p["side"] == "long" else -1
    pts = (price - p["entryPrice"]) * d
    fees = 2 * cfg["commission"] * size
    st["trades"].append(dict(side=p["side"], size=size, entryPrice=p["entryPrice"],
                             entryMs=p["fillMs"], exitMs=ms, exitPrice=price,
                             reason=reason, pts=pts,
                             pnl=pts * POINT_VALUE * size - fees, fees=fees))
    p["size"] -= size
    if p["size"] <= 0:
        st["open"] = None


def apply_fill(st, o, legs, ms, idx, price):
    p = st["open"]
    if not p:
        st["open"] = open_position(o, legs, ms, idx, price, o["size"])
        return
    if p["side"] == o["side"]:
        p["entryPrice"] = (p["entryPrice"] * p["size"] + price * o["size"]) / (p["size"] + o["size"])
        p["size"] += o["size"]
        p["fillIdx"] = idx
        return
    closed = min(o["size"], p["size"])
    reduce(st, closed, ms, price, "reduce", st["cfg"])
    rest = o["size"] - closed
    if rest > 0:
        st["open"] = open_position(o, legs, ms, idx, price, rest)


def run_sim(t, px, log, clock, cfg):
    orders = copy.deepcopy(log["orders"])
    closes = log.get("closes") or []
    brackets = log.get("brackets") or []
    # glued-tape idx trap: re-derive indices from timestamps
    for o in orders:
        o["idx"] = int(np.searchsorted(t, o["ms"], side="left"))
    gap = cfg["queueTicks"] * TICK
    st = dict(open=None, trades=[], working=[], oi=0, ci=0, bi=0, cfg=cfg)

    def admin(ms):
        while st["oi"] < len(orders) and orders[st["oi"]]["ms"] <= ms:
            o = orders[st["oi"]]
            st["oi"] += 1
            if o["type"] == "market":
                oco = st["open"] is None
                apply_fill(st, o, o, o["ms"], o["idx"],
                           cross(price_at_ms(t, px, o["ms"]), o["side"] == "long", cfg))
                if oco:
                    st["working"] = [w for w in st["working"] if not w["oco"]]
            else:
                st["working"].append(dict(o=o, oco=st["open"] is None))
        if st["working"]:
            st["working"] = [w for w in st["working"]
                             if w["o"].get("cancelMs") is None or w["o"]["cancelMs"] > ms]
        while st["bi"] < len(brackets) and brackets[st["bi"]]["ms"] <= ms:
            b = brackets[st["bi"]]
            st["bi"] += 1
            if st["open"]:
                p = st["open"]
                s = b.get("stop")
                if p["riskPts"] is None and s is not None:
                    p["riskPts"] = abs(p["entryPrice"] - s)
                p["stop"] = s
                p["target"] = b.get("target")
                if s is not None and p["trail"]:
                    d = 1 if p["side"] == "long" else -1
                    p["ladder"] = s
                    cap = s + d * max(0.0, p["trail"]["dist"] - p["trail"]["be"])
                    if (cap - p["hwm"]) * d < 0:
                        p["hwm"] = cap
                    p["trailArmed"] = False
        while st["ci"] < len(closes) and closes[st["ci"]]["ms"] <= ms:
            c = closes[st["ci"]]
            st["ci"] += 1
            if st["open"]:
                reduce(st, st["open"]["size"], c["ms"],
                       cross(price_at_ms(t, px, c["ms"]), st["open"]["side"] == "short", cfg),
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
                if i < w["o"]["idx"] or w not in st["working"]:
                    continue
                s = order_state_at(w["o"], ms)
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
    admin(clock)
    return st


# --- tape texture ------------------------------------------------------------


def zigzag_legs(p, thr_ticks):
    """Leg lengths (ticks) of a zigzag with reversal threshold thr_ticks."""
    thr = thr_ticks * TICK
    legs = []
    anchor = p[0]
    extreme = p[0]
    direction = 0
    for x in p[1:]:
        if direction == 0:
            if abs(x - anchor) >= thr:
                direction = 1 if x > anchor else -1
                extreme = x
        elif direction == 1:
            if x > extreme:
                extreme = x
            elif extreme - x >= thr:
                legs.append((extreme - anchor) / TICK)
                anchor, extreme, direction = extreme, x, -1
        else:
            if x < extreme:
                extreme = x
            elif x - extreme >= thr:
                legs.append((anchor - extreme) / TICK)
                anchor, extreme, direction = extreme, x, 1
    return np.array(legs)


def texture(t, px, w0, w1):
    m = (t >= w0) & (t <= w1)
    pw = px[m]
    minutes = (w1 - w0) / 60000
    path = np.abs(np.diff(pw)).sum() / TICK
    net = abs(pw[-1] - pw[0]) / TICK
    legs = zigzag_legs(pw, 25)
    return dict(minutes=minutes, box=(pw.max() - pw.min()) / TICK,
                path_per_min=path / minutes, drift_per_min=net / minutes,
                churn=path / max(net, 1.0),
                swings_per_min=len(legs) / minutes,
                leg_med=float(np.median(legs)) if len(legs) else float("nan"))


def mfe_ticks(t, px, trades, horizon_ms=180_000):
    out = []
    for tr in trades:
        i0 = np.searchsorted(t, tr["entryMs"])
        i1 = np.searchsorted(t, tr["entryMs"] + horizon_ms)
        seg = px[i0:i1]
        if not len(seg):
            continue
        sgn = 1 if tr["side"] == "long" else -1
        out.append((sgn * (seg - tr["entryPrice"])).max() / TICK)
    return np.array(out)


# --- report ------------------------------------------------------------------


def summarize(trades):
    pnl = [tr["pnl"] for tr in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    reasons = {}
    for tr in trades:
        reasons[tr["reason"]] = reasons.get(tr["reason"], 0) + 1
    return dict(n=len(trades), net=round(sum(pnl)),
                wr=round(100 * len(wins) / len(pnl)) if pnl else 0,
                avg_win=round(np.mean(wins)) if wins else 0,
                avg_loss=round(np.mean(losses)) if losses else 0,
                reasons=reasons)


def main(attempt_ids):
    for aid in attempt_ids:
        a, log, recorded, summ = load_attempt(aid)
        t, px = load_tape(a["symbol"], a["date"], a["tz"])
        if len(t) != a["tape"]["n"]:
            print(f"!! {aid}: tape drifted ({len(t)} vs {a['tape']['n']}) — "
                  f"fills may not reproduce")
        cfg = dict(commission=a["prefs"]["commission"],
                   slipTicks=a["prefs"]["slipTicks"], queueTicks=a["prefs"]["queueTicks"])
        clock = a["clock_ms"]

        print("=" * 96)
        print(f"{aid}  prefs: trail={a['prefs']['trailTicks']}t "
              f"step={a['prefs']['trailStepTicks']}t target={a['prefs']['targetTicks']}t "
              f"stop={a['prefs']['stopTicks']}t")

        mine = summarize(run_sim(t, px, log, clock, cfg)["trades"])
        theirs = summarize([{**tr, "pnl": tr["pnl"], "reason": tr["reason"]} for tr in recorded])
        ok = "OK" if (mine["n"], mine["net"]) == (theirs["n"], theirs["net"]) else "MISMATCH"
        print(f"  validate [{ok}]  port n={mine['n']} net={mine['net']}  "
              f"stored n={theirs['n']} net={theirs['net']}")
        if ok != "OK":
            print("  !! port disagrees with the browser engine — do not trust the grid")
            continue

        tx = texture(t, px, summ["first_fill_ms"], summ["last_exit_ms"])
        mfe = mfe_ticks(t, px, recorded)
        print(f"  window {tx['minutes']:.0f}m | box {tx['box']:.0f}t | "
              f"path {tx['path_per_min']:,.0f} t/min | drift {tx['drift_per_min']:.0f} t/min | "
              f"churn {tx['churn']:.0f}x | 25t-swings {tx['swings_per_min']:.1f}/min "
              f"(median leg {tx['leg_med']:.0f}t) | MFE-3min med {np.median(mfe):.0f}t")

        print(f"  {'dist':>5} {'step':>5} {'n':>3} {'net$':>6} {'wr%':>4} {'avgW':>6} {'avgL':>6}  exits")
        for dist, step in GRID:
            lg = copy.deepcopy(log)
            for o in lg["orders"]:
                if o.get("trail"):
                    o["trail"]["dist"] = dist * TICK
                    o["trail"]["step"] = step * TICK
            s = summarize(run_sim(t, px, lg, clock, cfg)["trades"])
            print(f"  {dist:>4}t {step:>4}t {s['n']:>3} {s['net']:>6} {s['wr']:>4} "
                  f"{s['avg_win']:>6} {s['avg_loss']:>6}  {s['reasons']}")


if __name__ == "__main__":
    main(sys.argv[1:] or STUDY_ATTEMPTS)
