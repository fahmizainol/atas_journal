"""Structure x S/R x order flow — tape features at price-action events.

Both parents are individually null: structure breaks carry no forward edge at
any swing scale (market-structure-events.md) and touch-fading has no edge at any
horizon (interactions v9). Big-lot participation is the one live entry-time tape
signal (bigtrade study). The untested cross: does order flow AT a structure
break or an S/R touch predict THAT event's outcome — follow-through vs fail,
accept vs reject?

Anchor classes (every anchor located by tick timestamp / minute_bars end_idx —
never by raw index base; see the drift-fade lookahead post-mortem):

  break : BOS/CHoCH close-break bars from the study state machine (imported so
          the two studies agree bit-for-bit), thr 5 / 10 / 20 pts
  touch : S/R level touches on 1-min bars — static session refs (Open, ONH,
          ONL, pdHigh, pdLow, pdClose) + retests of confirmed 10-pt swing
          pivots while unbroken. Interactions-bench constants throughout.
  null  : time-of-day-matched random bars, direction = sign of the trailing
          3-bar move — the "flow predicts short drift anywhere" control that
          every event class must beat before the cross means anything.

Features per anchor — trailing 60s / 300s tick windows ending at the anchor
bar's LAST tick (the break/touch bar's close; outcome starts at t+1, so the
window is strictly pre-outcome). Canonical aggressor sign: A = buy (ask-lift),
B = sell (bid-hit), buy-positive CVD = A - B. `*_al` variants are signed by the
event direction so + = flow agrees with the signalled move.

  w{60,300}_volrate / _cvdpv / _cvdpv_al / _big_part / _bigcvd_al / _maxsz
  absorp    : approach-side lots per tick of net progress over the last 60s —
              effort vs result (high = hammering without progress)
  exh_ratio : approach-side rate last 20s vs prior 40s (<1 = drying up)
  sess_cvdpv(_al) : whole-session imbalance up to the anchor

Outcome: forward net / MFE / MAE over bars t+1 .. t+FWD (20) in the event's
direction (break dir / approach dir / momentum dir). Touches additionally get
the interactions accept/reject/chop call (fade vs continuation excursions).

Writes sof_events.parquet (one row per anchor, all classes, all sessions).
"""
from __future__ import annotations

import sys
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "data/research/market-structure")
from journal.sim import ticks as tickmod  # noqa: E402
from journal.sim.regime import minute_bars  # noqa: E402
from structure_events import causal_zigzag, structure_events  # noqa: E402

TICK = 0.25
FWD = 20                    # forward race window, 1-min bars
THRS = [5.0, 10.0, 20.0]    # break event sweep (40pt too sparse to test flow on)
PIVOT_THR = 10.0            # swing scale whose confirmed pivots act as S/R
TOUCH_TOL = 2.0             # interactions TOUCH_TOL_PTS
TOUCH_GAP = 3               # interactions TOUCH_GAP_BARS
REJECT_MIN = 3.0            # interactions REJECT_MIN_PTS
ACCEPT_MARGIN = 2.0         # interactions ACCEPT_MARGIN_PTS
AWAY_PTS = 3.0              # a (re)test only counts after price has left the zone
OPEN_WARMUP = 15            # interactions LEVEL_WARMUP_MIN, for the Open level
MIN_BAR = 5                 # skip anchors with <5 bars of session history
N_NULL = 30
WINS = (60, 300)
OUT = Path("data/research/structure-orderflow")


def sessions():
    """(symbol, date) per calendar day, front contract on roll overlaps."""
    by_date: dict[date, list[str]] = {}
    for p in sorted(Path("data/cache/ticks").glob("*_rth.parquet")):
        sym, ds, _ = p.stem.split("_")
        by_date.setdefault(date.fromisoformat(ds), []).append(sym)
    out = []
    for d in sorted(by_date):
        pick = tickmod.contract_for_cached("NQ", d)
        out.append((pick if pick in by_date[d] else by_date[d][0], d))
    return out


class _Flow:
    """Prefix-summed tick arrays + the per-anchor feature writer."""

    def __init__(self, t: pd.DataFrame):
        self.tsns = t["ts_utc"].astype("int64").to_numpy()
        self.px = t["price"].to_numpy("float64")
        sz = t["size"].to_numpy("int64")
        side = t["side"].to_numpy("U1")
        A = np.where(side == "A", sz, 0)
        B = np.where(side == "B", sz, 0)
        sd = A - B  # buy-positive, canonical
        self.sz = sz

        def c0(a):
            return np.concatenate(([0], np.cumsum(a, dtype="int64")))

        self.csz, self.csd = c0(sz), c0(sd)
        self.cbig = c0(np.where(sz >= 10, sz, 0))
        self.cbsd = c0(np.where(sz >= 10, sd, 0))
        self.cA, self.cB = c0(A), c0(B)

    def _i(self, ns: int) -> int:
        return int(np.searchsorted(self.tsns, ns, side="left"))

    def features(self, rec: dict, ai: int, dr: float) -> None:
        """Trailing-window tape features at anchor tick ``ai`` (inclusive),
        direction-aligned by ``dr`` (+1 up / -1 down)."""
        a_ts = int(self.tsns[ai])
        i1 = ai + 1
        sess_vol = int(self.csz[i1])
        sess_cvdpv = (float(self.csd[i1]) / sess_vol) if sess_vol else 0.0
        rec["sess_cvdpv"] = sess_cvdpv
        rec["sess_cvdpv_al"] = dr * sess_cvdpv

        for W in WINS:
            i0 = self._i(a_ts - W * 1_000_000_000)
            vol = int(self.csz[i1] - self.csz[i0])
            p = f"w{W}"
            rec[f"{p}_volrate"] = vol / W
            cvdpv = (float(self.csd[i1] - self.csd[i0]) / vol) if vol else 0.0
            rec[f"{p}_cvdpv"] = cvdpv
            rec[f"{p}_cvdpv_al"] = dr * cvdpv
            rec[f"{p}_big_part"] = (int(self.cbig[i1] - self.cbig[i0]) / vol) if vol else 0.0
            rec[f"{p}_bigcvd_al"] = dr * (float(self.cbsd[i1] - self.cbsd[i0]) / vol) if vol else 0.0
            rec[f"{p}_maxsz"] = int(self.sz[i0:i1].max()) if i1 > i0 else 0

        # approach-side absorption + exhaustion over the last 60s
        appr = self.cA if dr > 0 else self.cB
        i60 = self._i(a_ts - 60_000_000_000)
        i20 = self._i(a_ts - 20_000_000_000)
        avol = float(appr[i1] - appr[i60])
        w = self.px[i60:i1]
        if len(w):
            prog = (self.px[ai] - w.min()) / TICK if dr > 0 else (w.max() - self.px[ai]) / TICK
            prog = max(prog, 0.0)
            rec["absorp"] = avol / (1.0 + prog)
            rec["prog_ticks"] = prog          # price-only component of absorp
            rec["appr_vol60"] = avol          # flow-only component of absorp
        else:
            rec["absorp"] = 0.0
            rec["prog_ticks"] = 0.0
            rec["appr_vol60"] = 0.0
        r20 = float(appr[i1] - appr[i20]) / 20.0
        r40 = float(appr[i20] - appr[i60]) / 40.0
        rec["exh_ratio"] = r20 / (r40 + 1e-9)


def _race(high, low, close, bar: int, dr: float):
    """Forward net/MFE/MAE over bars bar+1..bar+FWD in direction dr."""
    n = len(close)
    j = min(bar + FWD, n - 1)
    if j <= bar:
        return None
    c = close[bar]
    h, l = high[bar + 1:j + 1], low[bar + 1:j + 1]
    if dr > 0:
        mfe, mae = h.max() - c, c - l.min()
    else:
        mfe, mae = c - l.min(), h.max() - c
    return dr * (close[j] - c), float(mfe), float(mae)


def _touch_outcome(high, low, close, i: int, level: float, from_below: bool):
    """The interactions accept/reject/chop call, touch bar excluded."""
    lo = low[i + 1:i + 1 + FWD]
    hi = high[i + 1:i + 1 + FWD]
    cl = close[i + 1:i + 1 + FWD]
    if len(cl) == 0:
        return None
    if from_below:  # level overhead: reject fades down, continuation breaks up
        fade, cont = level - lo, hi - level
        end_beyond = cl[-1] > level + ACCEPT_MARGIN
    else:
        fade, cont = hi - level, level - lo
        end_beyond = cl[-1] < level - ACCEPT_MARGIN
    fmax = max(0.0, float(fade.max()))
    cmax = max(0.0, float(cont.max()))
    if end_beyond and cmax > fmax:
        oc = "accept"
    elif fmax >= REJECT_MIN:
        oc = "reject"
    else:
        oc = "chop"
    return oc, fmax, cmax


def _scan_touches(high, low, close, level: float, i_start: int, i_end: int):
    """Touch bars of a static level over [i_start, i_end): near within
    TOUCH_TOL, re-armed only after price has been AWAY_PTS away, consecutive
    straddles merged (TOUCH_GAP)."""
    hits = []
    armed = False
    last = -10**9
    for i in range(max(i_start, 1), min(i_end, len(close))):
        near = (low[i] - TOUCH_TOL) <= level <= (high[i] + TOUCH_TOL)
        if not near:
            if abs(close[i] - level) >= AWAY_PTS:
                armed = True
            continue
        if armed and i - last > TOUCH_GAP:
            hits.append((i, close[i - 1] < level))
            armed = False
        last = i
    return hits


def _et_minute(bars: pd.DataFrame) -> np.ndarray:
    et = bars["ts_utc"].dt.tz_convert("America/New_York")
    return (et.dt.hour * 60 + et.dt.minute).to_numpy()


def main():
    rows = []
    prev_ref = None  # (sym, date, high, low, close) of the previous session
    n_sess = 0
    for sym, d in sessions():
        t = tickmod.cached_rth(sym, d)
        if t is None or len(t) < 2000:
            prev_ref = None
            continue
        bars = minute_bars(t, "1min")
        if len(bars) < 60:
            prev_ref = None
            continue
        n_sess += 1
        high = bars["high"].to_numpy()
        low = bars["low"].to_numpy()
        close = bars["close"].to_numpy()
        end_idx = bars["end_idx"].to_numpy()
        etm = _et_minute(bars)
        fl = _Flow(t)
        n = len(bars)

        def emit(cls, kind, thr, bar, dr, level, extra=None):
            if bar < MIN_BAR:
                return
            r = _race(high, low, close, int(bar), dr)
            if r is None:
                return
            rec = {
                "session": d.isoformat(), "sym": sym, "cls": cls, "kind": kind,
                "thr": thr, "bar": int(bar), "et_min": int(etm[bar]),
                "am": bool(etm[bar] < 12 * 60), "d": dr,
                "level": level,
                # price-only controls: where the anchor bar CLOSED relative to
                # the level, in the event direction (+ = beyond). The artifact
                # screen — any tape feature must add information beyond these.
                "close_al": dr * (close[bar] - level),
                "prevclose_al": dr * (close[bar - 1] - level),
                "bar_ret_al": dr * (close[bar] - close[bar - 1]),
                "fwd_net": r[0], "fwd_mfe": r[1], "fwd_mae": r[2],
            }
            if extra:
                rec.update(extra)
            fl.features(rec, int(end_idx[bar]), dr)
            rows.append(rec)

        # --- structure breaks, thr sweep (bit-for-bit the study machine) ---
        for thr in THRS:
            _, ev = structure_events(bars, thr_pts=thr)
            br = ev[ev["type"].str.contains("BOS|CHoCH")]
            for _, e in br.iterrows():
                dr = 1.0 if e["type"].endswith("_up") else -1.0
                emit("break", e["type"].split("_")[0], thr, e["bar"], dr,
                     float(e["level"]))

        # --- S/R level touches: static refs + confirmed-pivot retests ---
        levels = []  # (kind, family, level, i_start, i_end)
        levels.append(("Open", "static", float(t["price"].iloc[0]), OPEN_WARMUP, n))
        on = tickmod.cached_overnight(sym, d)
        if on is not None and not on.empty:
            levels.append(("ONH", "static", float(on["price"].max()), 0, n))
            levels.append(("ONL", "static", float(on["price"].min()), 0, n))
        if prev_ref is not None and prev_ref[0] == sym and (d - prev_ref[1]).days <= 4:
            levels.append(("pdHigh", "static", prev_ref[2], 0, n))
            levels.append(("pdLow", "static", prev_ref[3], 0, n))
            levels.append(("pdClose", "static", prev_ref[4], 0, n))

        piv = causal_zigzag(high, low, PIVOT_THR)
        for p_idx, price, pk, confirm in piv:
            # active from confirmation until the level is close-broken
            after = np.arange(confirm + 1, n)
            if pk == "H":
                broken = after[close[confirm + 1:] > price]
            else:
                broken = after[close[confirm + 1:] < price]
            i_end = int(broken[0]) if len(broken) else n
            levels.append((f"pivot_{pk}", "pivot", float(price), confirm + 1, i_end))

        for kind, family, level, i0, i1 in levels:
            for i, from_below in _scan_touches(high, low, close, level, i0, i1):
                oc = _touch_outcome(high, low, close, i, level, from_below)
                if oc is None:
                    continue
                dr = 1.0 if from_below else -1.0  # approach = continuation dir
                emit("touch", kind, np.nan, i, dr, level, extra={
                    "family": family, "from_below": from_below,
                    "outcome": oc[0], "fade": oc[1], "cont": oc[2],
                    "accept": oc[0] == "accept", "reject": oc[0] == "reject",
                })

        # --- time-matched random null anchors, momentum-sign direction ---
        rng = np.random.default_rng(zlib.crc32(f"{sym}{d}".encode()))
        lo_b, hi_b = 30, n - FWD - 1
        if hi_b > lo_b:
            for b in rng.choice(np.arange(lo_b, hi_b), size=min(N_NULL, hi_b - lo_b),
                                replace=False):
                mom = close[b] - close[b - 3]
                if mom == 0.0:
                    continue
                emit("null", "null", np.nan, int(b), float(np.sign(mom)),
                     float(close[b]))

        prev_ref = (sym, d, float(high.max()), float(low.min()), float(close[-1]))

    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT / "sof_events.parquet")
    print(f"sessions={n_sess}  rows={len(df)}")
    print(df.groupby("cls")["fwd_net"].agg(["size", "mean"]).round(3).to_string())
    print("\nbreaks by thr:")
    b = df[df["cls"] == "break"]
    print(b.groupby(["thr", "kind"])["fwd_net"].agg(["size", "mean"]).round(2).to_string())
    print("\ntouches by kind:")
    tt = df[df["cls"] == "touch"]
    print(tt.groupby("kind").agg(nobs=("fwd_net", "size"),
                                 accept=("accept", "mean"),
                                 reject=("reject", "mean")).round(3).to_string())


if __name__ == "__main__":
    main()
