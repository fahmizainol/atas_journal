"""Probe for the vol-conditioned sizing study: what do the stored sittings
actually contain, and what does the vol ruler read at each fill?

Answers three questions before any arm is designed:
  1. how many sittings validate under the ported engine (and at what size /
     contract, since the sizing ladder is denominated per contract);
  2. what the vol ruler's two causal references — ATR(14) and the developing
     median since 10:00 ET — read at the moment of each entry, on both the
     30s clock bars and the 500-tick bars the user trades off;
  3. how far apart those references are, because their disagreement is the
     only thing a three-row recommender adds over a one-number one.

Nothing here re-runs a counterfactual. It exists so the arms are designed
against the data rather than against an assumption about it.

Usage:
    .venv/bin/python data/research/replay-trail/vol_probe.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
from multiprocessing import Pool

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, pathlib.Path(__file__).parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


w = _load("whatif")
xw = _load("exit_whatif")

TICK = 0.25
ATR_PERIOD = 14
WIN_START = 10 * 3600  # the vol ruler's settled window, ET seconds-of-day
WIN_END = 16 * 3600
MIN_BARS = 3


# --- bars ---------------------------------------------------------------------


def clock_bars(t, px, secs):
    """OHLC over fixed clock buckets. Returns (open_ms, high, low, close)."""
    key = (t // (secs * 1000)).astype(np.int64)
    edge = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate(([0], edge))
    ends = np.concatenate((edge, [len(t)]))
    return _ohlc(t, px, starts, ends)


def tick_bars(t, px, n):
    """OHLC over fixed print counts — the 500-tick bar, vol-clocked by
    construction: a fast tape makes more bars, not bigger ones."""
    starts = np.arange(0, len(t), n)
    ends = np.minimum(starts + n, len(t))
    return _ohlc(t, px, starts, ends)


def _ohlc(t, px, starts, ends):
    hi = np.array([px[a:b].max() for a, b in zip(starts, ends)])
    lo = np.array([px[a:b].min() for a, b in zip(starts, ends)])
    cl = px[ends - 1]
    return t[starts], hi, lo, cl, t[ends - 1]


# --- the vol ruler's two causal references, per bar ---------------------------


def atr_ticks(hi, lo, cl):
    """Wilder ATR(14) in ticks, seeded on the simple mean like lib/volRuler."""
    pc = np.concatenate(([cl[0]], cl[:-1]))
    tr = np.maximum(hi, pc) - np.minimum(lo, pc)
    out = np.empty(len(tr))
    val = 0.0
    seed = 0.0
    for i, x in enumerate(tr):
        if i < ATR_PERIOD:
            seed += x
            val = seed / (i + 1)
        else:
            val += (x - val) / ATR_PERIOD
        out[i] = val
    return out / TICK


def dev_median_ticks(t0, hi, lo):
    """Expanding median of bar ranges inside 10:00-16:00 ET, in ticks. NaN
    until MIN_BARS have accumulated, and NaN outside the window."""
    tod = ((t0 / 1000) % 86400)
    rng = (hi - lo) / TICK
    out = np.full(len(hi), np.nan)
    acc = []
    for i in range(len(hi)):
        if not (WIN_START <= tod[i] < WIN_END):
            out[i] = out[i - 1] if i else np.nan
            continue
        acc.append(rng[i])
        if len(acc) >= MIN_BARS:
            out[i] = float(np.median(acc))
    return out


def refs_at(close_ms, atr, dev, ms):
    """The two references as of the last CLOSED bar at `ms` — causal by
    construction, which is what makes this legal inside a blind replay."""
    i = int(np.searchsorted(close_ms, ms, side="right")) - 1
    if i < 0:
        return np.nan, np.nan
    return float(atr[i]), float(dev[i])


# --- per sitting ---------------------------------------------------------------


def run_one(aid):
    try:
        a, log, recorded, summ = w.load_attempt(aid)
        t, px = w.load_tape(a["symbol"], a["date"], a["tz"])
        clock = a["clock_ms"]
        cfg, _ = xw.pick_cfg(a, t, px, log, recorded, clock)
        if cfg is None:
            return dict(aid=aid, valid=False, error="no cfg validates")

        b30 = clock_bars(t, px, 30)
        b500 = tick_bars(t, px, 500)
        vols = {}
        for name, (t0, hi, lo, cl, tc) in (("s30", b30), ("k500", b500)):
            vols[name] = (tc, atr_ticks(hi, lo, cl), dev_median_ticks(t0, hi, lo))

        fills = []
        for tr in recorded:
            row = dict(ms=tr["entryMs"], size=tr["size"], pnl=tr["pnl"],
                       reason=tr["reason"], pts=tr.get("pts"))
            for name, (tc, atr, dev) in vols.items():
                row[f"atr_{name}"], row[f"dev_{name}"] = refs_at(tc, atr, dev, tr["entryMs"])
            fills.append(row)

        return dict(aid=aid, valid=True, date=a["date"], symbol=a["symbol"],
                    cfg=cfg, n_ticks=len(t),
                    prefs=dict(stop=a["prefs"]["stopTicks"], tgt=a["prefs"]["targetTicks"],
                               trail=a["prefs"]["trailTicks"], step=a["prefs"]["trailStepTicks"],
                               size=a["prefs"].get("size")),
                    stored=w.summarize(recorded), fills=fills)
    except Exception as e:
        return dict(aid=aid, valid=False, error=repr(e))


def main():
    aids = []
    for d in sorted((ROOT / "data/replays").glob("*/*/")):
        if not (d / "log.json").exists() or not (d / "summary.json").exists():
            continue
        if json.loads((d / "summary.json").read_text())["trades"] == 0:
            continue
        aids.append(d.name)
    print(f"{len(aids)} sittings with trades")

    with Pool(8) as pool:
        res = list(pool.imap_unordered(run_one, aids))
    ok = sorted([r for r in res if r["valid"]], key=lambda r: r["date"])
    bad = [r for r in res if not r["valid"]]
    print(f"{len(ok)} validate, {len(bad)} do not")
    for r in bad[:12]:
        print(f"  !! {r['aid']}: {r['error']}")

    syms = {}
    sizes = {}
    stops = {}
    for r in ok:
        syms[r["symbol"][:3]] = syms.get(r["symbol"][:3], 0) + 1
        stops[r["prefs"]["stop"]] = stops.get(r["prefs"]["stop"], 0) + 1
        for f in r["fills"]:
            sizes[f["size"]] = sizes.get(f["size"], 0) + 1
    print(f"\nroots: {syms}")
    print(f"placed stop (prefs): {dict(sorted(stops.items()))}")
    print(f"trade sizes: {dict(sorted(sizes.items()))}")

    fills = [f for r in ok for f in r["fills"]]
    print(f"\n{len(fills)} trades across {len(ok)} sittings")
    for col in ("atr_s30", "dev_s30", "atr_k500", "dev_k500"):
        v = np.array([f[col] for f in fills], dtype=float)
        v = v[np.isfinite(v)]
        if not len(v):
            print(f"  {col:<10} — no finite values")
            continue
        q = np.percentile(v, [10, 25, 50, 75, 90])
        print(f"  {col:<10} n={len(v):<5} p10={q[0]:6.1f} p25={q[1]:6.1f} "
              f"med={q[2]:6.1f} p75={q[3]:6.1f} p90={q[4]:6.1f}  max={v.max():.0f}")

    # How far apart the references are — the disagreement a 3-row recommender
    # would be showing, and whether it is big enough to be worth a row.
    for a, b in (("atr_s30", "dev_s30"), ("atr_k500", "dev_k500"), ("atr_s30", "atr_k500")):
        x = np.array([f[a] for f in fills], dtype=float)
        y = np.array([f[b] for f in fills], dtype=float)
        m = np.isfinite(x) & np.isfinite(y) & (y > 0)
        if m.sum() < 10:
            continue
        ratio = x[m] / y[m]
        q = np.percentile(ratio, [10, 50, 90])
        print(f"  {a} / {b}: med {q[1]:.2f}  p10 {q[0]:.2f}  p90 {q[2]:.2f}  "
              f"(rho {np.corrcoef(x[m], y[m])[0, 1]:.2f})")

    # Does the vol read at entry say anything at all about the trade? A
    # first look, not a test — if this is flat, the arms are a formality.
    pnl = np.array([f["pnl"] for f in fills], dtype=float)
    for col in ("atr_s30", "dev_s30", "atr_k500", "dev_k500"):
        v = np.array([f[col] for f in fills], dtype=float)
        m = np.isfinite(v)
        if m.sum() < 20:
            continue
        cuts = np.percentile(v[m], [33, 67])
        lo = pnl[m][v[m] <= cuts[0]]
        mid = pnl[m][(v[m] > cuts[0]) & (v[m] <= cuts[1])]
        hi = pnl[m][v[m] > cuts[1]]
        print(f"  {col:<10} terciles  quiet n={len(lo):3} net={lo.sum():8.0f} "
              f"avg={lo.mean():7.1f} | mid n={len(mid):3} net={mid.sum():8.0f} "
              f"avg={mid.mean():7.1f} | hot n={len(hi):3} net={hi.sum():8.0f} avg={hi.mean():7.1f}")

    out = ROOT / "data/research/replay-trail/vol_probe.json"
    out.write_text(json.dumps(ok, default=float))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
