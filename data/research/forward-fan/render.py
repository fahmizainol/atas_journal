"""Forward-fan demo, stage 2: walk one session and draw its neighbours' futures.

For every RTH minute of the walked day, from the pool ``build_states.py``
wrote:

  candidates = other sessions, same time of day (+/- W min), same ruler (+/- band)
  matched    = the k geometrically nearest candidates, ONE PER DAY
  control    = k random candidates from the SAME set, one per day

Both fans are drawn side by side. That is the whole honesty device: a tilt
in the matched fan is only worth believing when the control fan lacks it.
One neighbour per day, because adjacent minutes of one session are the same
moment twice — 40 neighbours must mean 40 days.

    .venv/bin/python data/research/forward-fan/render.py --day 2026-03-25

Writes ``docs/research/forward-fan-<day>.html`` (Lab -> Research).
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
STATES = HERE / "states.parquet"
TEMPLATE = HERE / "_template.html"
DOCS = ROOT / "docs" / "research"

from build_states import LEVELS, SIGMAS, ARRIVAL, FWD_MIN  # noqa: E402

POSITION = [f"f_{k}" for k, _ in LEVELS] + [f"s_{m}" for m, _ in SIGMAS]
#: Two feature sets, both rendered, toggled in the viewer: where price IS,
#: and where it is plus how it ARRIVED.
FEATURE_SETS = {"P": POSITION, "A": POSITION + [f"a_{a}" for a in ARRIVAL]}
FWD = [f"fwd_{h}" for h in range(1, FWD_MIN + 1)]


def build_fans(tgt: pd.DataFrame, pool: pd.DataFrame, feats: list[str], k: int,
               tod_window: int, ruler_band: float, seed: int) -> dict[int, dict]:
    pool = pool.dropna(subset=feats).reset_index(drop=True)
    mu = pool[feats].mean().to_numpy()
    sd = pool[feats].std().replace(0, 1).to_numpy()
    Z = ((pool[feats].to_numpy() - mu) / sd).astype(np.float32)
    p_tod = pool["tod"].to_numpy()
    p_lr = np.log(pool["ruler"].to_numpy())
    p_day = pool["day"].to_numpy()
    p_fwd = pool[FWD].to_numpy()
    band = np.log1p(ruler_band)
    rng = np.random.default_rng(seed)

    fans: dict[int, dict] = {}
    for _, r in tgt.iterrows():
        x = r[feats].to_numpy(dtype=float)
        if not np.all(np.isfinite(x)):
            continue
        cand = np.flatnonzero((np.abs(p_tod - r["tod"]) <= tod_window)
                              & (np.abs(p_lr - np.log(r["ruler"])) <= band))
        if cand.size == 0:
            continue
        z = ((x - mu) / sd).astype(np.float32)
        d = np.sqrt(((Z[cand] - z) ** 2).sum(axis=1))
        # Nearest minute per day, then the k nearest days.
        order = cand[np.argsort(d)]
        seen, matched = set(), []
        for j, dist in zip(order, np.sort(d)):
            if p_day[j] in seen:
                continue
            seen.add(p_day[j])
            matched.append((int(j), float(dist)))
            if len(matched) == k:
                break
        # Control: one random minute per day, k random days, same candidate set.
        by_day: dict[str, list[int]] = {}
        for j in cand:
            by_day.setdefault(p_day[j], []).append(int(j))
        days = list(by_day)
        rng.shuffle(days)
        control = [int(rng.choice(by_day[dd])) for dd in days[:k]]
        fans[int(r["tod"])] = {
            "m": [np.rint(p_fwd[j]).astype(int).tolist() for j, _ in matched],
            "c": [np.rint(p_fwd[j]).astype(int).tolist() for j in control],
            "nm": [[p_day[j], hhmm(p_tod[j]), round(dist, 2)] for j, dist in matched],
            "nc": [[p_day[j], hhmm(p_tod[j])] for j in control],
            "ncand": int(cand.size),
            "ndays": len(by_day),
        }
    return fans


def summarize(label: str, fans: dict[int, dict], k: int) -> None:
    def band_w(paths, h, lo=10, hi=90):
        a = np.array(paths)[:, h - 1]
        return float(np.percentile(a, hi) - np.percentile(a, lo)) if len(a) else np.nan

    def up_frac(paths, h):
        a = np.array(paths)[:, h - 1]
        return float((a > 0).mean()) if len(a) else np.nan

    rows = [{"w5m": band_w(f["m"], 5), "w5c": band_w(f["c"], 5),
             "w15m": band_w(f["m"], 15), "w15c": band_w(f["c"], 15),
             "t5m": abs(up_frac(f["m"], 5) - .5), "t5c": abs(up_frac(f["c"], 5) - .5)}
            for f in fans.values() if len(f["m"]) >= 10 and len(f["c"]) >= 10]
    s = pd.DataFrame(rows)
    if s.empty:
        return
    print(f"  [{label}] {len(fans)} minutes")
    print(f"    10-90 band @+5m   matched {s.w5m.median():6.1f}t  control {s.w5c.median():6.1f}t")
    print(f"    10-90 band @+15m  matched {s.w15m.median():6.1f}t  control {s.w15c.median():6.1f}t")
    print(f"    |up frac - 0.5| @+5m  matched {s.t5m.mean():.3f}  control {s.t5c.mean():.3f}"
          f"   (noise floor at k={k}: ~{0.5 * np.sqrt(0.25 / k) * 1.6:.3f})")


def hhmm(tod: int) -> str:
    m = 9 * 60 + 30 + int(tod)
    return f"{m // 60:02d}:{m % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-03-25")
    ap.add_argument("--k", type=int, default=40)
    ap.add_argument("--tod-window", type=int, default=20, help="minutes")
    ap.add_argument("--ruler-band", type=float, default=0.25, help="fraction")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    day = date.fromisoformat(args.day)

    df = pd.read_parquet(STATES)
    tgt = df[df["day"] == day.isoformat()].sort_values("tod").reset_index(drop=True)
    if tgt.empty:
        raise SystemExit(f"{day} is not in the pool ({df['day'].nunique()} sessions)")

    pool = df[(df["day"] != day.isoformat()) & df["has_on"]].dropna(subset=FWD)
    fans = {name: build_fans(tgt, pool, feats, args.k, args.tod_window, args.ruler_band, args.seed)
            for name, feats in FEATURE_SETS.items()}

    data = {
        "day": day.isoformat(),
        "symbol": str(tgt["symbol"].iat[0]),
        "k": args.k, "tod_window": args.tod_window, "ruler_band": args.ruler_band,
        "pool_days": int(pool["day"].nunique()),
        "pool_span": [str(pool["day"].min()), str(pool["day"].max())],
        "fwd_min": FWD_MIN,
        "tick": 0.25,
        "bars": [[int(t), o, h, l, c] for t, o, h, l, c in
                 tgt[["tod", "open", "high", "low", "close"]].itertuples(index=False)],
        "ruler": tgt["ruler"].round(1).tolist(),
        "levels": {label: tgt[f"lvl_{k}"].round(2).tolist() for k, label in LEVELS},
        "feat": {label: tgt[f"f_{k}"].round(2).tolist() for k, label in LEVELS},
        "arrival": {a: tgt[f"a_{a}"].round(2).where(tgt[f"a_{a}"].notna(), None).tolist()
                    for a in ARRIVAL},
        "fans": fans,
    }

    # A small numeric readout beside the picture: are the matched fans any
    # narrower or more one-sided than the control fans?
    print(f"{day}  {data['symbol']}  pool {data['pool_days']} sessions")
    for name, f in fans.items():
        summarize({"P": "position", "A": "position+arrival"}[name], f, args.k)

    html = TEMPLATE.read_text()
    html = html.replace("/*__DATA__*/", "const DATA = " + json.dumps(data, separators=(",", ":")) + ";")
    html = html.replace("__DAY__", day.isoformat())
    out = DOCS / f"forward-fan-{day.isoformat()}.html"
    out.write_text(html)
    print(f"  wrote {out.relative_to(ROOT)}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
