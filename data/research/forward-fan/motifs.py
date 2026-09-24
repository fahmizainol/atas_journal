"""Motif search: the same move, whenever it happened.

The day-twins pages ask "which sessions played like this one". This asks a
smaller and more useful question: take a 45-minute stretch of the chart and
find every time that shape occurred ANYWHERE in the pool — any day, any time
of day — then show what followed each one.

The unit is a window, not a session, so a rejection off the VAH at 10:15 can
match one at 14:40. Each window is normalised by its OWN range and its own
opening price, so shape is compared, not level or size.

Every query also draws a CONTROL set: the same number of random windows. With
~40,000 windows in the bank something will always look similar, so the only
question that means anything is whether the matches' continuations agree more
than random ones' do.

    .venv/bin/python data/research/forward-fan/motifs.py --day 2026-03-25 [--anchor ny]

Writes ``docs/research/motifs-<day>.html`` (Lab -> Research).
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DAYS = HERE / "days.parquet"
TEMPLATE = HERE / "_motifs_template.html"
DOCS = ROOT / "docs" / "research"

TICK = 0.25
GRID = 5            # matching grid, minutes
WINDOW_MIN = 45     # the move
FWD_MIN = 60        # what followed
QUERY_STEP = 15     # query positions through the target day
BANK_STEP = 5       # how densely the searchable bank is sampled

from render_days import ANCHORS, BLOCKS, minute_of  # noqa: E402

#: Motif windows carry the VWAP family only — named explicitly rather than
#: derived from BLOCKS, which also holds the day matcher's VP blocks.
CURVES = ("p", "v", "w", "z")


def window_feats(g: pd.DataFrame, A: dict, s: int, w: int) -> np.ndarray | None:
    """One window as a flat feature vector: price, VWAP, band width and band
    position, each on the GRID and normalised by the window's own range."""
    win = g.iloc[s:s + w]
    if win["high"].isna().mean() > 0.05 or win[A["v"]].isna().mean() > 0.1:
        return None
    R = float(np.nanmax(win["high"]) - np.nanmin(win["low"]))
    if not np.isfinite(R) or R <= 0:
        return None
    open_ = float(win["close"].iloc[0])
    f = win.assign(**{A["v"]: win[A["v"]].ffill().bfill(),
                      A["u"]: win[A["u"]].ffill().bfill()}).iloc[::GRID]
    c, v, u = f["close"].to_numpy(), f[A["v"]].to_numpy(), f[A["u"]].to_numpy()
    half = u - v
    z = np.where(half > 0, (c - v) / np.where(half > 0, half, 1), 0.0)
    out = np.concatenate([(c - open_) / R, (v - open_) / R, half / R, np.clip(z, -3, 3)])
    return out.astype(np.float32) if np.all(np.isfinite(out)) else None


def build_bank(by: dict, A: dict, end: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every window in the pool, on BANK_STEP centres. Returns (feats, day index,
    window start)."""
    feats, dayi, starts, days = [], [], [], sorted(by)
    for k, d in enumerate(days):
        g = by[d]
        for s in range(A["start"], end - WINDOW_MIN + 1, BANK_STEP):
            f = window_feats(g, A, s, WINDOW_MIN)
            if f is not None:
                feats.append(f)
                dayi.append(k)
                starts.append(s)
    return np.array(feats), np.array(dayi), np.array(starts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-03-25")
    ap.add_argument("--anchor", choices=list(ANCHORS), default="ny")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    A = ANCHORS[args.anchor]
    day = date.fromisoformat(args.day)
    end = minute_of(A["default_at"])

    df = pd.read_parquet(DAYS)
    by = {d: g.sort_values("i").reset_index(drop=True) for d, g in df.groupby("day")}
    if day.isoformat() not in by:
        raise SystemExit(f"{day} not in pool ({len(by)} sessions)")
    days = sorted(by)
    tgt_k = days.index(day.isoformat())

    # The bank is the same for every target day, and costs ~60s to walk, so it
    # is cached beside the pool and keyed by everything that shapes it.
    cache = HERE / f"bank_{args.anchor}_{WINDOW_MIN}_{BANK_STEP}_{GRID}_{len(by)}.npz"
    if cache.exists():
        z = np.load(cache)
        F, dayi, starts = z["F"], z["dayi"], z["starts"]
    else:
        F, dayi, starts = build_bank(by, A, end)
        np.savez_compressed(cache, F=F, dayi=dayi, starts=starts)
    # One scale per curve block, so the +/-3 band position cannot swamp the
    # range-normalised curves — same discipline as the day matcher.
    npts = F.shape[1] // len(CURVES)
    sd = np.concatenate([np.full(npts, F[:, i * npts:(i + 1) * npts].std(axis=0).mean() or 1.0)
                         for i in range(len(CURVES))]).astype(np.float32)
    wt = np.concatenate([np.full(npts, BLOCKS[b]) for b in CURVES]).astype(np.float32)
    Z = (F / sd) * np.sqrt(wt / wt.sum())
    other = dayi != tgt_k
    print(f"bank: {len(F):,} windows over {len(by)} days ({WINDOW_MIN}m, {npts} grid pts/curve)")

    def draw(g: pd.DataFrame, s: int) -> dict:
        """One window plus what followed, in ticks off the window's open."""
        seg = g.iloc[s:s + WINDOW_MIN + FWD_MIN]
        open_ = float(g["close"].iloc[s])
        def r(col):
            return [None if not np.isfinite(x) else int(round((x - open_) / TICK))
                    for x in seg[col].to_numpy()]
        return {"c": r("close"), "v": r(A["v"]), "u": r(A["u"]), "l": r(A["l"])}

    def hhmm(s: int) -> str:
        m = (18 * 60 + s) % 1440
        return f"{m // 60:02d}:{m % 60:02d}"

    rng = np.random.default_rng(args.seed)
    tgt = by[day.isoformat()]
    positions = []
    for s in range(A["start"], end - WINDOW_MIN + 1, QUERY_STEP):
        q = window_feats(tgt, A, s, WINDOW_MIN)
        if q is None:
            continue
        qz = (q / sd) * np.sqrt(wt / wt.sum())
        d = np.sqrt(((Z - qz) ** 2).sum(axis=1))
        d[~other] = np.inf                     # never match the day to itself
        # Best window per day, then the n nearest days.
        seen, hits = set(), []
        for j in np.argsort(d):
            if not np.isfinite(d[j]):
                break
            if dayi[j] in seen:
                continue
            seen.add(dayi[j])
            hits.append(int(j))
            if len(hits) == args.n:
                break
        pool_idx = np.flatnonzero(other)
        ctl = rng.choice(pool_idx, args.n, replace=False)
        pack = lambda j, withd: {
            "day": days[dayi[j]], "tod": hhmm(int(starts[j])),
            **({"dist": round(float(d[j]), 3)} if withd else {}),
            **draw(by[days[dayi[j]]], int(starts[j]))}
        positions.append({
            "s": s, "label": hhmm(s),
            "m": [pack(j, True) for j in hits],
            "ctl": [pack(int(j), False) for j in ctl],
            "self": draw(tgt, s),
        })

    data = {
        "day": day.isoformat(), "symbol": str(tgt["symbol"].iloc[0]), "anchor": args.anchor,
        "vwap_label": A["label"], "vp_label": A["vp_label"], "start_label": A["start_label"],
        "end_label": A["default_at"], "start": A["start"], "end": end,
        "window": WINDOW_MIN, "fwd": FWD_MIN, "tick": TICK, "n": args.n,
        "bank": int(len(F)), "pool": len(by),
        "session": {k: [None if not np.isfinite(x) else round(float(x), 2)
                        for x in tgt[col].to_numpy()[A["start"]:end]]
                    for k, col in (("close", "close"), ("v", A["v"]), ("u", A["u"]),
                                   ("l", A["l"]), ("poc", A["poc"]), ("vah", A["vah"]), ("val", A["val"]))},
        "positions": positions,
    }

    # Do the matched windows' continuations agree more than random ones?
    # A window near the close has a short forward — it is dropped at that
    # horizon, never padded, or a truncated tail would read as a flat market.
    def read(items, h):
        i = WINDOW_MIN - 1 + h
        ok = [it for it in items if len(it["c"]) > i and it["c"][i] is not None
              and it["c"][WINDOW_MIN - 1] is not None]
        return np.array([it["c"][i] - it["c"][WINDOW_MIN - 1] for it in ok], dtype=float)
    for h in (15, 30, 60):
        mw, cw, mu, cu = [], [], [], []
        for p in positions:
            m, c = read(p["m"], h), read(p["ctl"], h)
            if len(m) >= 4 and len(c) >= 4:
                mw.append(np.percentile(m, 90) - np.percentile(m, 10))
                cw.append(np.percentile(c, 90) - np.percentile(c, 10))
                mu.append(abs((m > 0).mean() - .5))
                cu.append(abs((c > 0).mean() - .5))
        print(f"  +{h:2d}m  10-90 width  matched {np.median(mw):6.0f}t  control {np.median(cw):6.0f}t"
              f"   |up-.5| matched {np.mean(mu):.3f}  control {np.mean(cu):.3f}")
    dd = [p["m"][0]["dist"] for p in positions]
    print(f"  nearest-match distance across {len(positions)} positions: "
          f"min {min(dd):.3f}  median {np.median(dd):.3f}  max {max(dd):.3f}")

    html = TEMPLATE.read_text()
    html = html.replace("/*__DATA__*/", "const DATA = " + json.dumps(data, separators=(",", ":")) + ";")
    html = html.replace("__DAY__", day.isoformat())
    out = DOCS / f"motifs-{day.isoformat()}.html"
    out.write_text(html)
    print(f"  wrote {out.relative_to(ROOT)}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
