"""Market structure as a READABLE EVENT STREAM — swing pivots -> BOS/CHoCH.

The prior market-structure study (market-structure-winloss.md) encoded swing
*state* at a trade's entry (HH/HL trend, vs-last-swing distances) and found raw
structure *breaks* null for the upper-band-bounce win/loss. What it never built
is the thing family-#1 of the "structure as data" note is really about: an
explicit **bias state machine** that turns the chart into a stream of typed
events — pivot confirmations, BOS (continuation breaks) and CHoCH (character
flips) — each with a timestamp, a price, and a forward outcome.

This script builds that layer on top of the SAME non-repainting primitive the
prior study used (`causal_zigzag`, copied verbatim below so the two studies
agree bit-for-bit), runs it over every cached RTH session, and scores each
event as if it were a trade in its signalled direction (interactions.py
method: forward MFE / MAE / net over a fixed window). Output:

    structure_events.parquet   one row per event, all sessions
    structure_sessions.parquet one row per session (densities, bias time-share)
    printed worked examples + the honest signal check

Nothing here is a strategy or an A/B. It is a representation + a first,
skeptical look at whether the events carry any forward information at all.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
from journal.sim.ticks import cached_rth  # noqa: E402
from journal.sim.regime import minute_bars  # noqa: E402

TICK = 0.25
SWING_PTS = 5.0        # zigzag reversal threshold (zz20 in the prior study)
FWD_BARS = 20          # forward window for event outcome scoring (~20 min)
SWEEP_PTS = [5.0, 10.0, 20.0, 40.0]  # "the threshold is the model" sweep
OUT = Path("data/research/market-structure")


# --- non-repainting swing primitive (verbatim from extract_structure.py) ----

def causal_zigzag(high, low, thr):
    """A pivot is usable at bar t iff confirm_idx <= t. No lookahead."""
    n = len(high)
    piv = []
    direction = 0
    max_i, min_i = 0, 0
    for i in range(n):
        if high[i] >= high[max_i]:
            max_i = i
        if low[i] <= low[min_i]:
            min_i = i
        if direction >= 0 and high[max_i] - low[i] >= thr:
            piv.append((max_i, high[max_i], "H", i))
            direction = -1
            min_i = i
        elif direction <= 0 and high[i] - low[min_i] >= thr:
            piv.append((min_i, low[min_i], "L", i))
            direction = 1
            max_i = i
    return piv


# --- the BOS/CHoCH bias state machine ---------------------------------------

def structure_events(bars: pd.DataFrame, thr_pts=SWING_PTS):
    """Turn OHLC bars into a typed event stream.

    Concepts, made operational:
      * pivot label: each confirmed swing vs the prior same-kind swing -> HH/HL
        (highs) or LH/LL (lows). Two bits encode the trend picture.
      * BOS  : close beyond the active swing in the CURRENT bias direction.
      * CHoCH: first close beyond the OPPOSING swing -> flips the bias.
    """
    piv = causal_zigzag(bars["high"].to_numpy(), bars["low"].to_numpy(),
                        thr_pts)
    by_confirm: dict[int, list] = {}
    for p in piv:
        by_confirm.setdefault(p[3], []).append(p)

    close = bars["close"].to_numpy()
    ts = bars["ts_utc"].to_numpy()
    n = len(bars)

    last_high = last_low = None   # most recent confirmed pivot of each kind
    ref_high = ref_low = None     # active (unbroken) break references
    bias = "na"
    events = []

    for t in range(n):
        for p in by_confirm.get(t, []):
            price = p[1]
            if p[2] == "H":
                label = "HH" if (last_high and price > last_high) else \
                        "LH" if last_high is not None else "H0"
                last_high, ref_high = price, price
            else:
                label = "HL" if (last_low and price > last_low) else \
                        "LL" if last_low is not None else "L0"
                last_low, ref_low = price, price
            events.append((ts[p[0]], p[0], f"pivot_{p[2]}", label, price, bias))

        c = close[t]
        if ref_high is not None and c > ref_high:
            ev = "CHoCH_up" if bias == "down" else "BOS_up"
            bias = "up"
            events.append((ts[t], t, ev, "", ref_high, bias))
            ref_high = None
        elif ref_low is not None and c < ref_low:
            ev = "CHoCH_down" if bias == "up" else "BOS_down"
            bias = "down"
            events.append((ts[t], t, ev, "", ref_low, bias))
            ref_low = None

    ev = pd.DataFrame(events, columns=["ts", "bar", "type", "label",
                                       "level", "bias_after"])
    return ev, _score_forward(ev, bars)


def _score_forward(ev: pd.DataFrame, bars: pd.DataFrame, k=FWD_BARS):
    """Each break event scored as a trade in its signalled direction: forward
    MFE / MAE / net over the next k bars (points). Pivot rows get NaN."""
    close = bars["close"].to_numpy()
    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    n = len(bars)
    mfe = np.full(len(ev), np.nan)
    mae = np.full(len(ev), np.nan)
    net = np.full(len(ev), np.nan)
    for i, (_, r) in enumerate(ev.iterrows()):
        if not (r["type"].startswith("BOS") or r["type"].startswith("CHoCH")):
            continue
        t = int(r["bar"])
        j = min(t + k, n - 1)
        if j <= t:
            continue
        c = close[t]
        d = 1.0 if r["type"].endswith("_up") else -1.0
        win_h, win_l = high[t + 1:j + 1], low[t + 1:j + 1]
        if d > 0:
            mfe[i] = win_h.max() - c
            mae[i] = c - win_l.min()
        else:
            mfe[i] = c - win_l.min()
            mae[i] = win_h.max() - c
        net[i] = d * (close[j] - c)
    ev = ev.copy()
    ev["fwd_mfe"], ev["fwd_mae"], ev["fwd_net"] = mfe, mae, net
    return ev


# --- driver -----------------------------------------------------------------

def sessions():
    """(symbol, date) for every cached RTH parquet, chronological."""
    out = []
    for p in sorted(Path("data/cache/ticks").glob("*_rth.parquet")):
        sym, d, _ = p.stem.split("_")
        out.append((sym, date.fromisoformat(d)))
    out.sort(key=lambda x: x[1])
    return out


def main():
    all_ev, sess_rows, base_long, base_abs = [], [], [], []
    sweep = {p: [] for p in SWEEP_PTS}  # thr -> list of break sub-frames
    for sym, d in sessions():
        t = cached_rth(sym, d)
        if t is None or len(t) < 2000:
            continue
        bars = minute_bars(t, "1min")
        if len(bars) < 30:
            continue
        for p in SWEEP_PTS:
            _, evp = structure_events(bars, thr_pts=p)
            br = evp[evp["type"].str.contains("BOS|CHoCH")]
            sweep[p].append(br)
        _, ev = structure_events(bars)  # (raw, forward-scored) -> keep scored
        # drift baseline: k-bar forward net of an always-long entry at every bar
        c = bars["close"].to_numpy()
        k = FWD_BARS
        fwd = c[k:] - c[:-k]
        base_long.append(fwd)
        base_abs.append(np.abs(fwd))
        ev.insert(0, "session", d.isoformat())
        ev.insert(1, "sym", sym)
        all_ev.append(ev)

        breaks = ev[ev["type"].str.contains("BOS|CHoCH")]
        chochs = ev[ev["type"].str.startswith("CHoCH")]
        sess_rows.append({
            "session": d.isoformat(), "sym": sym, "bars": len(bars),
            "n_pivots": int(ev["type"].str.startswith("pivot").sum()),
            "n_bos": int(ev["type"].str.startswith("BOS").sum()),
            "n_choch": len(chochs),
            "final_bias": ev["bias_after"].iloc[-1] if len(ev) else "na",
            "break_net_pts": float(breaks["fwd_net"].mean()) if len(breaks) else np.nan,
        })

    events = pd.concat(all_ev, ignore_index=True)
    sess = pd.DataFrame(sess_rows)
    events.to_parquet(OUT / "structure_events.parquet")
    sess.to_parquet(OUT / "structure_sessions.parquet")

    print(f"sessions={len(sess)}  events={len(events)}  "
          f"breaks={int(events['type'].str.contains('BOS|CHoCH').sum())}\n")

    # ---- honest signal check: does the event type mark a directional move? --
    br = events[events["type"].str.contains("BOS|CHoCH")].copy()
    br["kind"] = br["type"].str.replace("_up", "").str.replace("_down", "")
    g = br.groupby("kind").agg(
        n=("fwd_net", "size"),
        net_pts=("fwd_net", "mean"),
        mfe=("fwd_mfe", "mean"),
        mae=("fwd_mae", "mean"),
        win_rate=("fwd_net", lambda s: float((s > 0).mean())),
    ).round(2)
    print("=== forward outcome by event type (signalled direction, "
          f"{FWD_BARS}-bar window, points) ===")
    print(g.to_string())

    # baseline the events must beat: k-bar forward move at an arbitrary bar
    bl = np.concatenate(base_long)
    ba = np.concatenate(base_abs)
    print(f"\n=== drift baseline ({FWD_BARS}-bar fwd net at every bar, "
          f"n={len(bl):,}) ===")
    print(f"  always-long mean net = {bl.mean():+.2f} pts   "
          f"mean |move| = {ba.mean():.2f} pts   "
          f"(an event must clear the |move| bar, not the ~0 drift)")

    # ---- the threshold IS the model: does coarser structure sharpen events? -
    print("\n=== swing-threshold sweep (breaks pooled, "
          f"{FWD_BARS}-bar fwd, points) ===")
    rows = []
    for p in SWEEP_PTS:
        b = pd.concat(sweep[p], ignore_index=True)
        ch = b[b["type"].str.startswith("CHoCH")]
        rows.append({
            "thr_pts": p, "breaks/sess": round(len(b) / len(sess), 1),
            "net": round(b["fwd_net"].mean(), 2),
            "win%": round(float((b["fwd_net"] > 0).mean()) * 100, 1),
            "mfe": round(b["fwd_mfe"].mean(), 1),
            "mae": round(b["fwd_mae"].mean(), 1),
            "choch_net": round(ch["fwd_net"].mean(), 2),
            "choch_win%": round(float((ch["fwd_net"] > 0).mean()) * 100, 1),
        })
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n=== per-session densities (describe) ===")
    print(sess[["n_pivots", "n_bos", "n_choch"]].describe().round(1).to_string())

    # ---- worked examples: three sessions' event logs ----
    for s in ["2025-02-03", "2025-02-04", "2025-02-05"]:
        _print_example(events, s)


def _print_example(events, s):
    ev = events[events["session"] == s]
    if ev.empty:
        return
    show = ev.copy()
    show["time"] = pd.to_datetime(show["ts"]).dt.tz_convert(
        "America/New_York").dt.strftime("%H:%M")
    cols = ["time", "type", "label", "level", "bias_after",
            "fwd_net", "fwd_mfe", "fwd_mae"]
    print(f"\n=== worked example — {ev['sym'].iloc[0]} {s} "
          f"({len(ev)} events) ===")
    print(show[cols].round(1).to_string(index=False))


if __name__ == "__main__":
    main()
