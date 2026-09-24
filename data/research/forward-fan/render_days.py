"""Day twins, stage 2: which past days drew the same picture?

Two anchors, one machine:

  --anchor globex   the OVERNIGHT as a curve (18:00 -> --at, default 09:30):
                    price, Globex VWAP, its band. Forward = the RTH.
  --anchor ny       the RTH SESSION as a curve (09:30 -> --at, default 16:00):
                    price, NY VWAP, its band. At 16:00 there is no forward —
                    the answer is simply "these sessions played like this one";
                    --at 11:00 matches the session so far and shows the rest.

Each window is normalised by ITS OWN range — what the eye does when the chart
auto-fits — and sampled on a 5-min grid:

    p = (close - open) / R        where price went
    v = (vwap  - open) / R        where the mean went
    w = (u1 - vwap) / R           how the band widened
    z = (close - vwap) / (u1 - vwap)   where price lived inside the band

Twins = the N days with the smallest weighted block distance over those
curves. Control = N random days. Both are drawn the same way, so a resemblance
in what the twins did is only worth believing if the random days lack it.

    .venv/bin/python data/research/forward-fan/render_days.py --day 2026-03-25 --anchor ny

Writes ``docs/research/<slug>-<day>.html`` (Lab -> Research).
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
TEMPLATE = HERE / "_days_template.html"
DOCS = ROOT / "docs" / "research"
TICK = 0.25
STEP = 5                 # feature grid, minutes
MIN_COVERAGE = 0.9       # of the window must have prints

def minute_of(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return ((h - 18) % 24) * 60 + m     # minutes since 18:00 the evening before


ANCHORS = {
    "globex": dict(start=minute_of("18:00"), v="gxv", u="gxu", l="gxl", label="Globex VWAP",
                   poc="gxpoc", vah="gxvah", val="gxval", vp_label="Globex VP",
                   start_label="18:00", default_at="09:30", slug="overnight-twins",
                   title="Overnight twins", noun="overnight"),
    "ny":     dict(start=minute_of("09:30"), v="nyv", u="nyu", l="nyl", label="NY VWAP",
                   poc="nypoc", vah="nyvah", val="nyval", vp_label="NY VP",
                   start_label="09:30", default_at="16:00", slug="ny-twins",
                   title="NY session twins", noun="session",
                   # Display-only lead-in. The overnight is drawn ahead of the bell
                   # for context and takes NO part in the match: `shape()` still
                   # reads from `start`, so the distances are unchanged.
                   pre=minute_of("18:00"), pre_label="18:00", pre_vwap="Globex VWAP",
                   pre_v="gxv", pre_u="gxu", pre_l="gxl"),
}

#: Blocks of the shape vector and how much each counts. Each block is scaled by
#: its pool-wide spread and averaged over its own points before weighting, so
#: a 138-point curve and a 1-point amplitude are comparable and the band
#: position (which swings +/-3) cannot swamp the range-normalised curves
#: (which live in [0, 1]). Price and VWAP paths are what the eye compares;
#: band width and band position are secondary; amplitude is a mild pull so a
#: 400t window is not called the twin of an 1,800t one because the shapes rhyme.
#: The VP blocks mirror the VWAP ones exactly: a centre line (POC vs VWAP), a
#: half-width (value area vs sigma band), and where price sits inside it. So
#: "vwap+vp" weighs the profile the same as the mean — half the shape each.
BLOCKS = {"p": 2.0, "v": 2.0, "w": 1.0, "z": 1.0,
          "pc": 2.0, "va": 1.0, "zv": 1.0, "amp": 0.5}
#: "vp" mirrors "vwap" block for block — a centre line, a half-width, and where
#: price sits inside it — so the two are a fair A/B on which reference the eye
#: should be matching, rather than one being a richer description than the other.
SETS = {
    "vwap":    ("p", "v", "w", "z", "amp"),
    "vp":      ("p", "pc", "va", "zv", "amp"),
    "vwap+vp": ("p", "v", "w", "z", "pc", "va", "zv", "amp"),
}


def shape(g: pd.DataFrame, A: dict, upto: int) -> dict[str, np.ndarray] | None:
    """The window as named curve blocks over [start, upto) on the STEP grid,
    or None if it is too thin to describe."""
    w = g.iloc[A["start"]:upto]
    if len(w) < 2 * STEP or w["high"].notna().mean() < MIN_COVERAGE or w[A["v"]].isna().mean() > 0.1:
        return None
    open_ = float(w["close"].iloc[0])
    R = float(np.nanmax(w["high"]) - np.nanmin(w["low"]))
    if not np.isfinite(R) or R <= 0:
        return None
    # The VWAP is NaN for the first minute (nothing has closed strictly before
    # the anchor + 1) and for the odd gap; a 0.1% hole is filled, not fatal.
    fillers = {k: w[A[k]].ffill().bfill() for k in ("v", "u", "poc", "vah", "val")}
    s = w.assign(**{A[k]: col for k, col in fillers.items()}).iloc[::STEP]
    c, v, u = s["close"].to_numpy(), s[A["v"]].to_numpy(), s[A["u"]].to_numpy()
    poc, vah, val = s[A["poc"]].to_numpy(), s[A["vah"]].to_numpy(), s[A["val"]].to_numpy()
    half = u - v
    z = np.where(half > 0, (c - v) / np.where(half > 0, half, 1), 0.0)
    vhalf = (vah - val) / 2.0
    zv = np.where(vhalf > 0, (c - poc) / np.where(vhalf > 0, vhalf, 1), 0.0)
    out = {"p": (c - open_) / R, "v": (v - open_) / R, "w": half / R,
           "z": np.clip(z, -3, 3),
           "pc": (poc - open_) / R, "va": vhalf / R, "zv": np.clip(zv, -3, 3),
           "amp": np.array([np.log(R / open_)])}
    return out if all(np.all(np.isfinite(b)) for b in out.values()) else None


WARP_BAND_MIN = 45      # Sakoe-Chiba band: a move may land this far early or late


def block_scales(feats: list[dict[str, np.ndarray]], keys) -> dict[str, float]:
    return {k: (np.array([f[k] for f in feats]).std(axis=0).mean() or 1.0) for k in keys}


def distances(x: dict[str, np.ndarray], feats: list[dict[str, np.ndarray]], keys) -> np.ndarray:
    """Clock-locked: weighted block RMS distance from ``x`` to every entry of
    ``feats``. Minute 120 of one day is compared to minute 120 of the other."""
    sd = block_scales(feats, keys)
    d2 = np.zeros(len(feats))
    for k in keys:
        F = np.array([f[k] for f in feats])
        d2 += BLOCKS[k] * (((F - x[k]) / sd[k]) ** 2).mean(axis=1)
    return np.sqrt(d2 / sum(BLOCKS[k] for k in keys))


def dtw(x: dict[str, np.ndarray], y: dict[str, np.ndarray], sd: dict[str, float],
        band: int, curves) -> tuple[float, list[tuple[int, int]]]:
    """Dynamic time warping over the curve blocks, band-limited.

    The time axis may stretch and squeeze so a flush at 11:00 on one day lines
    up with the flush at 11:35 on the other; the cost is measured between the
    SHAPES. The band keeps it honest — the open cannot be matched to lunch.
    Returns the path-mean weighted cost (comparable to ``distances`` at zero
    warp) and the alignment path as (i_x, j_y) grid pairs.
    """
    n, m = len(x["p"]), len(y["p"])
    C = np.zeros((n, m))
    for name in curves:
        C += BLOCKS[name] * ((x[name][:, None] - y[name][None, :]) / sd[name]) ** 2
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    step = np.zeros((n + 1, m + 1), dtype=np.int8)   # 0 diag, 1 up (i-1), 2 left (j-1)
    for i in range(1, n + 1):
        lo, hi = max(1, i - band), min(m, i + band)
        Di, Dp, Ci = D[i], D[i - 1], C[i - 1]
        for j in range(lo, hi + 1):
            a, b, c = Dp[j - 1], Dp[j], Di[j - 1]
            if a <= b and a <= c:
                Di[j], step[i, j] = a + Ci[j - 1], 0
            elif b <= c:
                Di[j], step[i, j] = b + Ci[j - 1], 1
            else:
                Di[j], step[i, j] = c + Ci[j - 1], 2
    path, i, j = [], n, m
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        s = step[i, j]
        if s == 0:
            i, j = i - 1, j - 1
        elif s == 1:
            i -= 1
        else:
            j -= 1
    path.reverse()
    return float(D[n, m] / len(path)), path


def warped_distances(x: dict[str, np.ndarray], feats: list[dict[str, np.ndarray]],
                     band_steps: int, keys) -> tuple[np.ndarray, list[list[tuple[int, int]]]]:
    """DTW distance to every entry of ``feats``, same weighting and amplitude
    term as ``distances`` so the two are on one scale."""
    sd = block_scales(feats, keys)
    curves = [k for k in keys if k != "amp"]
    wsum = sum(BLOCKS[k] for k in keys)
    out, paths = np.zeros(len(feats)), []
    for i, f in enumerate(feats):
        cost, path = dtw(x, f, sd, band_steps, curves)
        amp = BLOCKS["amp"] * (((f["amp"] - x["amp"]) / sd["amp"]) ** 2).mean()
        out[i] = np.sqrt((cost + amp) / wsum)
        paths.append(path)
    return out, paths


def lag_and_map(path: list[tuple[int, int]], n_min: int) -> tuple[float, list[int]]:
    """From an alignment path: how many minutes the twin runs late (+) or
    early (-) on average, and a per-minute map twin-minute -> target-minute
    so the twin can be redrawn on the target's clock."""
    p = np.array(path)
    lag = float((p[:, 1] - p[:, 0]).mean() * STEP)
    # For each twin grid point j, the mean target grid point i it aligned to.
    j_to_i = {}
    for i, j in path:
        j_to_i.setdefault(j, []).append(i)
    js = np.array(sorted(j_to_i))
    is_ = np.array([np.mean(j_to_i[j]) for j in js])
    tmap = np.interp(np.arange(n_min) / STEP, js, is_) * STEP
    return lag, np.rint(tmap).astype(int).tolist()


def series(g: pd.DataFrame, A: dict) -> dict:
    r = lambda col: [None if not np.isfinite(x) else round(float(x), 2) for x in g[col].to_numpy()]
    out = {"close": r("close"), "v": r(A["v"]), "u": r(A["u"]), "l": r(A["l"]),
           "poc": r(A["poc"]), "vah": r(A["vah"]), "val": r(A["val"])}
    if "pre_v" in A:        # the lead-in's own VWAP family — drawn, never matched
        out |= {"gv": r(A["pre_v"]), "gu": r(A["pre_u"]), "gl": r(A["pre_l"])}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-03-25")
    ap.add_argument("--anchor", choices=list(ANCHORS), default="globex")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--at", default=None, help="HH:MM ET; window end (default per anchor)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    A = ANCHORS[args.anchor]
    at = args.at or A["default_at"]
    day = date.fromisoformat(args.day)
    upto = minute_of(at)
    if upto <= A["start"]:
        raise SystemExit(f"--at {at} is before the {args.anchor} anchor opens ({A['start_label']})")

    df = pd.read_parquet(DAYS)
    if "src" not in df.columns:
        df["src"] = "databento"
    by = {d: g.sort_values("i").reset_index(drop=True) for d, g in df.groupby("day")}
    src_of = {d: str(g["src"].iloc[0]) for d, g in by.items()}
    if day.isoformat() not in by:
        raise SystemExit(f"{day} not in pool ({len(by)} sessions)")
    tgt = by[day.isoformat()]
    M = len(tgt)
    x = shape(tgt, A, upto)
    if x is None:
        raise SystemExit(f"{day}: {A['noun']} too thin to describe")

    # The reference pool is the Databento corpus ONLY. A recorded day can be the
    # target this page describes, but never a twin it is described against —
    # live-shadow-plan decision 3, which is permanent.
    feats, names = [], []
    for d, g in by.items():
        if d == day.isoformat() or src_of[d] != "databento":
            continue
        f = shape(g, A, upto)
        if f is not None:
            feats.append(f)
            names.append(d)
    n_min = upto - A["start"]
    rng = np.random.default_rng(args.seed)
    ctrl = [names[j] for j in rng.choice(len(names), args.n, replace=False)]

    # The curves are a property of the DAY; the distance and the DTW alignment
    # are properties of the (feature set, time mode) slot that selected it. Twins
    # overlap heavily across slots, so packing the series once per distinct day
    # and referencing it keeps the page from growing with every knob.
    packed: dict[str, dict] = {}

    def day_rec(d: str) -> dict:
        if d not in packed:
            g = by[d]
            w = g.iloc[A["start"]:upto]
            R = float(np.nanmax(w["high"]) - np.nanmin(w["low"]))
            c_at = float(g["close"].iloc[upto - 1])
            fwd = [None if not np.isfinite(v) else int(round((v - c_at) / TICK))
                   for v in g["close"].iloc[upto:].to_numpy()]
            packed[d] = {"day": d, "R": round(R / TICK),
                         "symbol": str(g["symbol"].iloc[0]), **series(g, A), "fwd": fwd}
        return packed[d]

    def ref(d: str, dd: float | None, path=None) -> dict:
        day_rec(d)
        out = {"day": d, "dist": None if dd is None else round(dd, 3)}
        if path is not None:
            out["lag"], out["tmap"] = lag_and_map(path, n_min)
            out["lag"] = round(out["lag"])
        return out

    def top(ds):
        return [int(j) for j in np.argsort(ds)[:args.n]]

    pctl = lambda ds: [round(float(np.percentile(ds, p)), 3) for p in (1, 5, 25, 50)]
    match = {}
    for sname, keys in SETS.items():
        dist = distances(x, feats, keys)
        wdist, paths = warped_distances(x, feats, WARP_BAND_MIN // STEP, keys)
        match[sname] = {
            "clock": {"twins": [ref(names[j], float(dist[j])) for j in top(dist)],
                      "dist_pctl": pctl(dist)},
            "warp": {"twins": [ref(names[j], float(wdist[j]), paths[j]) for j in top(wdist)],
                     "dist_pctl": pctl(wdist), "band_min": WARP_BAND_MIN},
        }
    data = {
        "day": day.isoformat(), "anchor": args.anchor, "vwap_label": A["label"], "vp_label": A["vp_label"],
        "noun": A["noun"], "start": A["start"], "start_label": A["start_label"],
        "draw_start": A.get("pre", A["start"]), "pre_label": A.get("pre_label"),
        "pre_vwap_label": A.get("pre_vwap"),
        "at": at, "upto": upto, "n": args.n, "pool": len(names), "tick": TICK, "step": STEP,
        "target_src": src_of[day.isoformat()],
        "target": ref(day.isoformat(), None),
        "match": match,
        "control": [ref(d, None) for d in ctrl],
        "days": packed,          # day -> curves; target/twins/control reference it
    }

    print(f"{day} {args.anchor} at {at}: target src={src_of[day.isoformat()]}, "
          f"pool {len(names)} databento days, N={args.n}")
    for sname, modes in match.items():
        for mode, mm in modes.items():
            tw = mm["twins"]
            extra = (f"  lags {[t['lag'] for t in tw]} min" if mode == "warp" else "")
            print(f"  [{sname:7} {mode:5}] top {[t['dist'] for t in tw][:3]} "
                  f"... pool pctl = {mm['dist_pctl']}{extra}")
        ov = len({t["day"] for t in modes["clock"]["twins"]} & {t["day"] for t in modes["warp"]["twins"]})
        print(f"  [{sname:7}] clock/warp share {ov}/{args.n} twins")
    snames = list(SETS)
    for mode in ("clock", "warp"):
        for i, a in enumerate(snames):
            for b in snames[i + 1:]:
                ov = len({t["day"] for t in match[a][mode]["twins"]}
                         & {t["day"] for t in match[b][mode]["twins"]})
                print(f"  [{mode:5}] {a} vs {b} share {ov}/{args.n} twins")

    # Numeric read, only when there is a forward: do the twins' continuations
    # agree with each other more than random days' do?
    if M - upto >= 30:
        def agree(items):
            P = np.array([[v if v is not None else np.nan for v in packed[it["day"]]["fwd"]]
                          for it in items], dtype=float)
            P = P[:, ~np.isnan(P).any(axis=0)]
            if P.shape[1] < 30 or len(P) < 3:
                return np.nan, np.nan, np.nan
            C = np.corrcoef(P)
            iu = np.triu_indices(len(P), 1)
            h = min(60, P.shape[1] - 1)
            w = float(np.percentile(P[:, h], 90) - np.percentile(P[:, h], 10))
            return float(C[iu].mean()), w, float((P[:, h] > 0).mean())
        a_c, w_c, u_c = agree(data["control"])
        for sname, modes in match.items():
            for mode, mm in modes.items():
                a_t, w_t, u_t = agree(mm["twins"])
                print(f"  [{sname:7} {mode:5}] fwd corr {a_t:+.3f} (ctl {a_c:+.3f})  "
                      f"+60m 10-90 {w_t:5.0f}t (ctl {w_c:4.0f}t)  +60m up {u_t:.2f} (ctl {u_c:.2f})")

    html = TEMPLATE.read_text()
    html = html.replace("/*__DATA__*/", "const DATA = " + json.dumps(data, separators=(",", ":")) + ";")
    html = html.replace("__DAY__", day.isoformat()).replace("__TITLE__", A["title"])
    out = DOCS / f"{A['slug']}-{day.isoformat()}.html"
    out.write_text(html)
    print(f"  wrote {out.relative_to(ROOT)}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
