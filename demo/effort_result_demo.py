"""Effort vs result at a level — is it a new reading, or the absorption band renamed?

The proposal was a panel line saying, at a level you are watching: *since price
arrived, buyers have spent 2,400 net contracts and gained one tick*. Heavy
one-sided aggression that buys no ground is the classic absorption read, and the
idea was to put it beside the level-approach rows.

Before it gets pixels there is a cheaper question, and it is this page's first
job. **The chart already draws effort ÷ result.** `replayEngine.ts:1305` scores
every 15-second window at `volume / max(range, tick)` — lots per point traversed
— against a developing session median, and paints the hot ones as absorption
bands. So the honest question is not "does effort vs result mean anything", it is
"does a *level-anchored* window say anything the *clock* window does not".

Three questions, in the order that can kill the feature soonest:

  Q1  DISTINGUISHABILITY — of the contact episodes this reading would flag, how
      many already have an absorption band drawn over them? If most do, the
      panel line is a re-skin, and the right change is four lines adding the
      signed delta to the band's tooltip.

  Q2  NORMALISATION — absorption's baseline is a median over *fixed-length*
      windows. A since-contact window is variable-length, so its concentration
      may not be comparable to that median at all: four minutes at a level and
      fifteen seconds at a level need not share a distribution. Fit the scaling
      and find out whether a rate or a duration-matched baseline is mandatory.

  Q3  SEPARATION — after an episode of heavy one-sided delta that gained no
      ground, does price actually go against the aggressor? Reported against the
      base rate of all contact episodes, with a split-half, because the prior
      here is bad: absorption scored AUC 0.43-0.59 at every underwater anchor in
      the loser order-flow study and 0.53 at the low in the big-trade study.

A contact episode is a level-anchored window with hysteresis: it opens when
price comes within CONTACT_TICKS of a level and closes when price leaves by
EXIT_TICKS, so a level being chopped across does not print a new episode per
tick. Both edges are swept in the page, because neither is pinned by anything.

Reads only the existing tick cache (data/cache/ticks/*.parquet) — never fetches,
so it costs nothing at Databento.

Writes ``docs/research/effort-vs-result.html``, which the Lab's Research tab
lists and serves.

    uv run python demo/effort_result_demo.py          # last 40 sessions
    uv run python demo/effort_result_demo.py 60       # last 60
"""

from __future__ import annotations

import bisect
import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from journal.sim import ticks as tickmod  # noqa: E402
from journal.sim.bars import tick_bars  # noqa: E402
from journal.sim.profile import developing_profile, levels_in_force  # noqa: E402
from journal.sim.vwap import vwap_bands  # noqa: E402

# --- Config ---------------------------------------------------------------
INSTRUMENT = "NQ"
TICK = 0.25
N_SESSIONS = 40

# Buy aggressor is 'B' — verified empirically in the big-trade study
# (corr(B-signed delta, price change) = +0.6..+0.8). `interactions.py` signs
# these the other way round; `api/sim_charts._cvd` and this file agree with the
# chart. `size` is uint32 and MUST be cast before differencing or it wraps.
BUY, SELL = "B", "A"

# A contact opens inside this and closes outside that. The gap is the whole
# point: without hysteresis a level being chopped across prints an episode per
# tick. Neither number is measured, so both are swept — and the sweep turned out
# to matter more than expected, because it decides how long a "contact" even is.
CONTACT_TICKS = 4
EXIT_TICKS = 12
# (contact, exit) in ticks — NQ ticks are 0.25, so 4 ticks is a point. The last
# three hold the contact edge at 4 and widen only the exit, which is the direct
# test of "let the wick stay inside one episode": at 40 ticks (10 points) an
# excursion has to be enormous before it ends the contact.
SWEEP = [(2, 8), (4, 12), (4, 24), (8, 40), (4, 40), (4, 80)]
RECLAIM_S = 300          # come back within this or it is not a reclaim
MIN_EPISODE_S = 2.0      # a graze is not a contact
MAX_EPISODE_S = 3600.0   # nothing on RTH should reach this; it is a guard

FORWARD_MIN = 5          # the forward window Q3 scores over

# The chart's own absorption tuning, so Q1 compares against what is actually
# drawn rather than a plausible-looking stand-in. Mirrors DEFAULT_EVENT_TUNING
# in frontend/src/lib/replayEngine.ts:336 — if that changes, this is stale.
ABS_WIN_MS = 15_000
ABS_MULT = 3.0
ABS_MIN_WINDOWS = 20
ABS_MERGE = True

BAR_TICKS = 500          # the engine's bar size; the developing profile's clock

OUT_DIR = Path(__file__).resolve().parent
HTML_OUT = ROOT / "docs" / "research" / "effort-vs-result.html"


# --- Session assembly -----------------------------------------------------
def cached_sessions(limit: int) -> list[tuple[date, str]]:
    """The `limit` most recent (day, contract) pairs with ticks on disk."""
    days: dict[date, str] = {}
    for p in sorted((tickmod.CACHE_DIR / "ticks").glob("*_day.parquet")):
        sym, day_s, _ = p.stem.split("_")
        days[datetime.strptime(day_s, "%Y-%m-%d").date()] = sym
    return [(d, days[d]) for d in sorted(days)[-limit:]]


# --- The chart's absorption detector, ported -------------------------------
def absorption_events(ms: np.ndarray, price: np.ndarray, size: np.ndarray,
                      is_buy: np.ndarray, tick: float) -> list[dict]:
    """Publish the absorption bands the chart would have drawn on this tape.

    A transcription of `applyAbsorb` / `closeAbsorbWindow`
    (frontend/src/lib/replayEngine.ts:1243-1370), including the parts that are
    easy to drop and change the answer:

      * the median is taken over the windows that have **already closed**,
        including the one being scored — it develops, so a window is measured
        against the day as it was known at the time, not the finished day;
      * adjacent hot windows merge into one band, but only while the *merged*
        block still clears the floor, since a merge widens the range as well as
        adding volume;
      * a cold window breaks the run.

    Only the final window of a session differs: the chart closes a window when a
    tick in the next one lands, so the last never publishes. It is closed here,
    which can add at most one band in the session's last 15 seconds.
    """
    if len(ms) == 0:
        return []
    key = (ms // ABS_WIN_MS).astype("int64")
    # Window boundaries, vectorised — the per-tick accumulation in the TS is a
    # loop only because it runs live.
    edges = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate(([0], edges))
    stops = np.concatenate((edges, [len(ms)]))

    vol = np.add.reduceat(size, starts)
    buy = np.add.reduceat(np.where(is_buy, size, 0.0), starts)
    lo = np.minimum.reduceat(price, starts)
    hi = np.maximum.reduceat(price, starts)
    conc = vol / np.maximum(hi - lo, tick)

    events: list[dict] = []
    pool: list[float] = []          # closed concentrations, kept sorted
    pos = -1                        # index of the band adjacent windows merge into
    prev_key = -2
    buy_acc = 0.0

    for w in range(len(starts)):
        c = float(conc[w])
        bisect.insort(pool, c)
        if len(pool) < ABS_MIN_WINDOWS:
            continue
        n = len(pool)
        med = pool[n // 2] if n % 2 else (pool[n // 2 - 1] + pool[n // 2]) / 2
        floor = ABS_MULT * med
        if not floor > 0:
            continue
        k = int(key[starts[w]])
        if c < floor:
            pos = -1                # a cold window ends the run
            continue
        if ABS_MERGE and pos >= 0 and prev_key == k - 1:
            ev = events[pos]
            b_lo, b_hi = min(ev["lo"], lo[w]), max(ev["hi"], hi[w])
            lots = ev["lots"] + vol[w]
            merged = lots / max(b_hi - b_lo, tick)
            if merged >= floor:
                buy_acc += buy[w]
                ev.update(to=float(ms[stops[w] - 1]), lo=float(b_lo), hi=float(b_hi),
                          lots=float(lots), st=float(merged / floor),
                          buy=bool(buy_acc >= lots / 2), n=ev["n"] + 1)
                prev_key = k
                continue
        buy_acc = float(buy[w])
        pos = len(events)
        prev_key = k
        events.append({
            "from": float(ms[starts[w]]), "to": float(ms[stops[w] - 1]),
            "lo": float(lo[w]), "hi": float(hi[w]), "lots": float(vol[w]),
            "st": float(c / floor), "buy": bool(buy[w] >= vol[w] / 2), "n": 1,
        })
    # The invariant the TS states out loud — a merge is only taken while the
    # merged block still clears the floor, "otherwise a strength floored at 1.0
    # would be a lie". Cheap to check and the one thing that would silently
    # break if the merge branch were edited.
    assert all(e["st"] >= 1.0 for e in events), "published a band below its own floor"
    return events


# --- Levels ----------------------------------------------------------------
def level_series(rth: pd.DataFrame, prior: dict[str, float] | None) -> dict[str, np.ndarray]:
    """Per-tick value of every level family this page watches.

    Static and moving families both, on purpose: the whole question is whether a
    level-anchored window says something a clock window doesn't, and a moving
    level is where the two most plausibly diverge. Every array is causal —
    `levels_in_force` hands a tick the last bar to have closed *strictly before*
    it, so nothing here can see its own future.
    """
    n = len(rth)
    out: dict[str, np.ndarray] = {}

    bands = vwap_bands(rth)
    out["vwap"] = bands["mid"].to_numpy()
    out["vwap+1σ"] = bands["upper1"].to_numpy()
    out["vwap−1σ"] = bands["lower1"].to_numpy()

    bars = tick_bars(rth, BAR_TICKS)
    prof = developing_profile(rth, bars, TICK)
    for edge, name in (("poc", "POC"), ("vah", "VAH"), ("val", "VAL")):
        out[name] = levels_in_force(prof, bars, n, edge)

    if prior:
        for name, px in prior.items():
            out[f"y{name}"] = np.full(n, px)
    return out


def session_close_profile(rth: pd.DataFrame) -> dict[str, float]:
    """The session's finished POC/VAH/VAL — tomorrow's static levels."""
    bars = tick_bars(rth, BAR_TICKS)
    prof = developing_profile(rth, bars, TICK)
    if len(prof) == 0 or not np.isfinite(prof.poc[-1]):
        return {}
    return {"POC": float(prof.poc[-1]), "VAH": float(prof.vah[-1]), "VAL": float(prof.val[-1])}


# --- Contact episodes ------------------------------------------------------
def episodes(level: np.ndarray, price: np.ndarray, contact_tol: float,
             exit_tol: float) -> list[tuple[int, int]]:
    """(start, end) tick index pairs for each contact with `level`.

    A three-state hysteresis run over the tape: inside `contact_tol` is "at the
    level", outside `exit_tol` is "away", and the band between them holds
    whatever state was last decided — which is what stops a level being chopped
    across from printing an episode per tick.

    Written as a forward-fill rather than a loop: the undecided ticks take the
    last decided state, and episodes are then the runs of "at".
    """
    d = np.abs(price - level)
    state = np.where(d <= contact_tol, 1, np.where(d > exit_tol, -1, 0)).astype("int8")
    decided = state != 0
    if not decided.any():
        return []
    # Forward-fill: index of the most recent decided tick at or before each tick.
    src = np.where(decided, np.arange(len(state)), -1)
    np.maximum.accumulate(src, out=src)
    held = np.where(src >= 0, state[np.clip(src, 0, None)], -1)
    # NaN level (no profile yet, or a level that does not exist here) is "away".
    held = np.where(np.isnan(level), -1, held)

    on = held == 1
    edges = np.diff(on.astype("int8"))
    starts = list(np.flatnonzero(edges == 1) + 1)
    stops = list(np.flatnonzero(edges == -1))
    if on[0]:
        starts.insert(0, 0)
    if on[-1]:
        stops.append(len(on) - 1)
    return list(zip(starts, stops))


def episode_rows(day: date, rth: pd.DataFrame, levels: dict[str, np.ndarray],
                 bands: list[dict], contact_tol: float, exit_tol: float) -> list[dict]:
    """Every contact episode on this session, with its effort and its result."""
    price = rth["price"].to_numpy(dtype="float64")
    size = rth["size"].to_numpy(dtype="float64")
    ms = rth["ts_utc"].to_numpy(dtype="datetime64[ms]").astype("int64")
    is_buy = (rth["side"].to_numpy() == BUY)
    signed = np.where(is_buy, size, np.where(rth["side"].to_numpy() == SELL, -size, 0.0))

    cum_v = np.concatenate(([0.0], np.cumsum(size)))
    cum_d = np.concatenate(([0.0], np.cumsum(signed)))
    fwd_ms = FORWARD_MIN * 60_000

    # Bands as sorted intervals, so the overlap test is a scan not a product.
    b_from = np.array([b["from"] for b in bands]) if bands else np.zeros(0)
    b_to = np.array([b["to"] for b in bands]) if bands else np.zeros(0)
    b_lo = np.array([b["lo"] for b in bands]) if bands else np.zeros(0)
    b_hi = np.array([b["hi"] for b in bands]) if bands else np.zeros(0)

    rows: list[dict] = []
    for name, level in levels.items():
        for a, b in episodes(level, price, contact_tol, exit_tol):
            dur = (ms[b] - ms[a]) / 1000.0
            if dur < MIN_EPISODE_S or dur > MAX_EPISODE_S:
                continue
            vol = cum_v[b + 1] - cum_v[a]
            delta = cum_d[b + 1] - cum_d[a]
            if vol <= 0:
                continue
            lo, hi = float(price[a:b + 1].min()), float(price[a:b + 1].max())
            rng_ticks = (hi - lo) / TICK
            disp = (price[b] - price[a]) / TICK
            side = 1.0 if delta > 0 else (-1.0 if delta < 0 else 0.0)

            # Forward move from the episode's end, signed *against* the
            # aggressor: positive means the side that was spending got it wrong,
            # which is what an absorption read claims to see coming.
            j = int(np.searchsorted(ms, ms[b] + fwd_ms))
            fwd = np.nan if j >= len(price) else -(price[j] - price[b]) / TICK * side

            # Does the chart already draw a band over this? Time *and* price must
            # intersect — a band 40 points away at the same moment is not this.
            covered = bool(bands) and bool(np.any(
                (b_from <= ms[b]) & (b_to >= ms[a]) & (b_lo <= hi) & (b_hi >= lo)
            ))

            rows.append({
                "day": day.isoformat(), "level": name,
                "t0": int(ms[a]), "t1": int(ms[b]), "dur": round(dur, 1),
                "px": round(float(price[a]), 2), "lo": lo, "hi": hi,
                "vol": float(vol), "delta": float(delta),
                "range": round(rng_ticks, 1), "disp": round(float(disp), 1),
                # Effort in the aggressor's own direction, and the ground it
                # bought. `result` is negative when the spending side lost ground.
                "effort": abs(float(delta)),
                "result": round(float(disp * side), 1),
                "conc": round(float(vol / max(rng_ticks, 1.0)), 1),
                "rate": round(float(abs(delta) / max(dur, 1.0)), 2),
                "fwd": None if not np.isfinite(fwd) else round(float(fwd), 1),
                "covered": covered,
            })
    return rows


# --- Sweep and reclaim -----------------------------------------------------
def reclaim_rows(day: date, rth: pd.DataFrame, levels: dict[str, np.ndarray],
                 contact_tol: float, exit_tol: float) -> list[dict]:
    """Price left the level past the exit, then came back inside contact.

    This exists because the episode definition has an obvious hole and it is
    better to measure it than to argue about it. A wick through a level and back
    — the most readable thing that happens at a level, and the thing a
    discretionary trader would most want scored — becomes *two* episodes under
    the hysteresis, split at the moment of the excursion, and the reversal falls
    in the gap between them. Neither episode contains it.

    So the pattern is scored on its own terms: the excursion depth, the time
    away, and the delta spent while price was out there, signed so that positive
    means *the reclaim was right*.
    """
    price = rth["price"].to_numpy(dtype="float64")
    ms = rth["ts_utc"].to_numpy(dtype="datetime64[ms]").astype("int64")
    size = rth["size"].to_numpy(dtype="float64")
    signed = np.where(rth["side"].to_numpy() == BUY, size, -size)
    cum_d = np.concatenate(([0.0], np.cumsum(signed)))
    fwd_ms = FORWARD_MIN * 60_000

    out: list[dict] = []
    for name, lv in levels.items():
        eps = episodes(lv, price, contact_tol, exit_tol)
        for k in range(len(eps) - 1):
            _, b0 = eps[k]
            a1, _ = eps[k + 1]
            gap = (ms[a1] - ms[b0]) / 1000.0
            if not 0 < gap <= RECLAIM_S:
                continue
            level_px = lv[b0]
            if not np.isfinite(level_px):
                continue
            seg = price[b0:a1 + 1]
            up = (seg.max() - level_px) / TICK
            dn = (level_px - seg.min()) / TICK
            through = max(up, dn)
            if through < EXIT_TICKS:
                continue
            # Wicked up -> the reclaim is a short read, and vice versa.
            side = -1.0 if up >= dn else 1.0
            j = int(np.searchsorted(ms, ms[a1] + fwd_ms))
            if j >= len(price):
                continue
            out.append({
                "day": day.isoformat(), "level": name,
                "through": round(float(through), 1), "gap": round(gap, 1),
                "fwd": round(float((price[j] - price[a1]) / TICK * side), 1),
                "delta_for": float((cum_d[a1 + 1] - cum_d[b0]) * side),
            })
    return out


def summarise_reclaim(rows: list[dict]) -> dict:
    """Was the reclaim right, and does the tape during the excursion know?"""
    df = pd.DataFrame(rows)
    if df.empty:
        return {}
    days = sorted(df["day"].unique())
    h1 = set(days[: len(days) // 2])

    def cut(g: pd.DataFrame) -> dict:
        return {
            "n": int(len(g)), "median": round(float(g["fwd"].median()), 1),
            "share": round(float((g["fwd"] > 0).mean()), 3),
            "h1": round(float((g.loc[g["day"].isin(h1), "fwd"] > 0).mean()), 3),
            "h2": round(float((g.loc[~g["day"].isin(h1), "fwd"] > 0).mean()), 3),
            "tape_auc": round(auc((g["delta_for"] > 0).to_numpy(), g["fwd"].to_numpy()), 3),
        }

    out = {"all": cut(df),
           "through_p50": round(float(df["through"].median()), 1),
           "gap_p50": round(float(df["gap"].median()), 1)}
    # The unfiltered set is dominated by two-second flickers. These are the ones
    # that would actually print a wick you could see on a chart.
    out["visible"] = [
        {"gap": g, "through": t, **cut(sub)}
        for g, t in ((10, 20), (30, 20), (30, 40), (60, 40))
        if len(sub := df[(df["gap"] >= g) & (df["through"] >= t)]) >= 50
    ]
    return out


# --- Read-outs -------------------------------------------------------------
def auc(flag: np.ndarray, score: np.ndarray) -> float:
    """Rank AUC of `score` separating `flag` from the rest (ties averaged)."""
    ok = np.isfinite(score)
    flag, score = flag[ok], score[ok]
    n1, n0 = int(flag.sum()), int((~flag).sum())
    if not n1 or not n0:
        return float("nan")
    r = pd.Series(score).rank().to_numpy()
    return float((r[flag].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def summarise(rows: list[dict]) -> dict:
    """The three questions, answered off one episode table."""
    df = pd.DataFrame(rows)
    if df.empty:
        return {}
    out: dict = {"n": int(len(df)), "sessions": int(df["day"].nunique())}

    # How long a contact actually lasts. This is not bookkeeping: the whole
    # proposal reads "since price arrived, N contracts have been spent", and
    # that sentence assumes price stays put long enough for N to mean something.
    out["dur_p"] = {p: round(float(df["dur"].quantile(p / 100)), 1)
                    for p in (10, 25, 50, 75, 90, 99)}
    out["dur_max"] = round(float(df["dur"].max()), 1)
    out["frac_under_win"] = round(float((df["dur"] < ABS_WIN_MS / 1000).mean()), 3)

    # The flag the panel line would draw attention to: heavy one-sided effort
    # (top tercile) that bought no ground. Deliberately a within-corpus
    # threshold, not an absolute one — the same reason absorption is scored
    # against a median rather than a fixed lot count.
    eff_cut = float(df["effort"].quantile(2 / 3))
    df["notable"] = (df["effort"] >= eff_cut) & (df["result"] <= 0)
    out["effort_cut"] = round(eff_cut, 1)
    out["n_notable"] = int(df["notable"].sum())

    # Q1 — how much of this is already on the chart.
    out["covered_all"] = round(float(df["covered"].mean()), 3)
    out["covered_notable"] = round(float(df.loc[df["notable"], "covered"].mean()), 3)
    out["notable_uncovered"] = int((df["notable"] & ~df["covered"]).sum())

    # Q2 — does concentration scale with how long the contact lasted? If it
    # does, a fixed-window median cannot baseline a variable-length window.
    m = (df["dur"] > 0) & (df["conc"] > 0)
    lx, ly = np.log(df.loc[m, "dur"]), np.log(df.loc[m, "conc"])
    out["dur_exp"] = round(float(np.polyfit(lx, ly, 1)[0]), 3)
    out["dur_r"] = round(float(np.corrcoef(lx, ly)[0, 1]), 3)
    # Where the scaling comes from, rather than a claim about it. The answer is
    # worse than diffusion: the hysteresis *defines* the range — an episode
    # cannot be wider than the exit threshold, or it would have ended — so the
    # denominator of `conc` is close to a constant, and concentration at a
    # level-anchored window is volume wearing a disguise. Volume grows with
    # duration, so concentration reports how long price stayed. Which the clock
    # already said.
    for col in ("vol", "effort", "range"):
        mm = m & (df[col] > 0)
        out[f"exp_{col}"] = round(
            float(np.polyfit(np.log(df.loc[mm, "dur"]), np.log(df.loc[mm, col]), 1)[0]), 3)
    q = pd.qcut(df.loc[m, "dur"], 4, duplicates="drop")
    out["dur_buckets"] = [
        {"dur": f"{iv.left:.0f}–{iv.right:.0f}s", "conc": round(float(g["conc"].median()), 1),
         "rate": round(float(g["rate"].median()), 2), "n": int(len(g))}
        for iv, g in df.loc[m].groupby(q, observed=True)
    ]
    # The same fit on the rate, which is the candidate fix.
    lr = np.log(df.loc[m & (df["rate"] > 0), "rate"])
    lxr = np.log(df.loc[m & (df["rate"] > 0), "dur"])
    out["rate_exp"] = round(float(np.polyfit(lxr, lr, 1)[0]), 3)

    # Q3 — does the flag say anything about what happens next?
    f = df[df["fwd"].notna()].copy()
    out["fwd_n"] = int(len(f))
    out["fwd_all"] = round(float(f["fwd"].median()), 2)
    out["fwd_notable"] = round(float(f.loc[f["notable"], "fwd"].median()), 2)
    out["fwd_auc"] = round(auc(f["notable"].to_numpy(), f["fwd"].to_numpy()), 3)
    # The test that decides whether the *order-flow* half earns its place. The
    # flag has two terms, and one of them is pure price: "the aggressor lost
    # ground" is a displacement fact that needs no tape at all. So hold that
    # term fixed — take only the episodes where ground was lost — and ask
    # whether knowing how much size was spent adds anything on top.
    lost = f[f["result"] <= 0]
    out["lost_n"] = int(len(lost))
    out["fwd_lost"] = round(float(lost["fwd"].median()), 2) if len(lost) else None
    if len(lost) > 20:
        hi_eff = (lost["effort"] >= eff_cut).to_numpy()
        out["fwd_lost_hi"] = round(float(lost.loc[hi_eff, "fwd"].median()), 2)
        out["fwd_lost_lo"] = round(float(lost.loc[~hi_eff, "fwd"].median()), 2)
        out["auc_marginal"] = round(auc(hi_eff, lost["fwd"].to_numpy()), 3)

    # Positive control. A null is only worth reporting if the harness could have
    # found something, so score a term that is *not* order flow: the episode's
    # own displacement. Price structure at these horizons is known to exist —
    # `pre60_runup` reached AUC 0.582 in the loser order-flow study — so a
    # reading of exactly 0.500 here would mean the forward join is broken rather
    # than the signal absent.
    out["ctrl_auc"] = round(auc((f["result"] <= 0).to_numpy(), f["fwd"].to_numpy()), 3)
    out["ctrl_r"] = round(float(np.corrcoef(f["result"], f["fwd"])[0, 1]), 3)
    out["ctrl_auc_dur"] = round(auc((f["dur"] >= f["dur"].median()).to_numpy(),
                                    f["fwd"].to_numpy()), 3)

    days = sorted(f["day"].unique())
    h1, h2 = set(days[: len(days) // 2]), set(days[len(days) // 2:])
    for tag, half in (("h1", h1), ("h2", h2)):
        g = f[f["day"].isin(half)]
        out[f"fwd_auc_{tag}"] = round(auc(g["notable"].to_numpy(), g["fwd"].to_numpy()), 3)
        out[f"fwd_notable_{tag}"] = round(float(g.loc[g["notable"], "fwd"].median()), 2)

    # Per-family, because a moving level and a static one are not the same claim.
    out["by_level"] = [
        {"level": name, "n": int(len(g)),
         "covered": round(float(g["covered"].mean()), 3),
         "notable": int(g["notable"].sum()),
         "fwd": round(float(g.loc[g["notable"], "fwd"].median()), 2)
         if g["notable"].any() and g.loc[g["notable"], "fwd"].notna().any() else None}
        for name, g in df.groupby("level", observed=True)
    ]
    return out


# --- Main ------------------------------------------------------------------
def main() -> None:
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    n = int(argv[0]) if argv and argv[0].isdigit() else N_SESSIONS
    pairs = cached_sessions(n)
    if not pairs:
        print("no cached sessions")
        return

    rows: list[dict] = []
    reclaims: list[dict] = []
    swept: dict[str, list[dict]] = {f"{c}/{e}": [] for c, e in SWEEP}
    prior: dict[str, float] | None = None
    per_session: list[dict] = []
    for day, sym in pairs:
        rth = tickmod.cached_rth(sym, day)
        if rth is None or rth.empty:
            continue
        rth = rth.sort_values("ts_utc").reset_index(drop=True)
        rth["size"] = rth["size"].astype("int64")   # uint32 wraps on differencing

        ms = rth["ts_utc"].to_numpy(dtype="datetime64[ms]").astype("int64")
        price = rth["price"].to_numpy(dtype="float64")
        size = rth["size"].to_numpy(dtype="float64")
        is_buy = rth["side"].to_numpy() == BUY

        bands = absorption_events(ms, price, size, is_buy, TICK)
        levels = level_series(rth, prior)
        for c, e in SWEEP:
            got = episode_rows(day, rth, levels, bands, c * TICK, e * TICK)
            swept[f"{c}/{e}"].extend(got)
            if (c, e) == (CONTACT_TICKS, EXIT_TICKS):
                rows.extend(got)
        reclaims.extend(reclaim_rows(day, rth, levels,
                                     CONTACT_TICKS * TICK, EXIT_TICKS * TICK))

        # Q1 needs a denominator. A band covers a slice of the session, and if
        # that slice is tiny then "the episodes don't overlap the bands" is true
        # of any episode set at all — including a randomly placed one.
        span = max(ms[-1] - ms[0], 1)
        lit = sum(b["to"] - b["from"] + ABS_WIN_MS for b in bands) / span
        per_session.append({"day": day.isoformat(), "sym": sym, "ticks": int(len(rth)),
                            "bands": len(bands), "lit": round(lit, 4),
                            "episodes": len(rows)})
        prior = session_close_profile(rth)
        print(f"  {day}  {sym}  {len(rth):>7,} ticks  {len(bands):>3} bands "
              f"({lit:>5.2%} of the session lit)", flush=True)

    s = summarise(rows)
    if not s:
        print("no episodes")
        return
    s["lit"] = round(float(np.mean([p["lit"] for p in per_session])), 4)
    s["reclaim"] = summarise_reclaim(reclaims)
    s["sweep"] = [
        {"cfg": k, **{f: v for f, v in summarise(r).items()
                      if f in ("n", "dur_p", "dur_max", "frac_under_win", "covered_all",
                               "covered_notable", "fwd_all", "fwd_notable", "fwd_auc",
                               "fwd_auc_h1", "fwd_auc_h2", "dur_exp", "rate_exp",
                               "exp_range", "auc_marginal", "fwd_lost_hi",
                               "fwd_lost_lo")}}
        for k, r in swept.items() if r
    ]

    print(f"\n{s['n']:,} contact episodes over {s['sessions']} sessions "
          f"at {CONTACT_TICKS}/{EXIT_TICKS} ticks")
    print(f"\nQ0  how long is a contact")
    print("    " + "  ".join(f"p{p} {v:.0f}s" for p, v in s["dur_p"].items())
          + f"   max {s['dur_max']:.0f}s")
    print(f"    {s['frac_under_win']:.1%} are shorter than absorption's own "
          f"{ABS_WIN_MS // 1000}s window")
    print(f"\nQ1  already drawn as an absorption band")
    print(f"    all episodes      {s['covered_all']:.1%}")
    print(f"    flagged episodes  {s['covered_notable']:.1%}  "
          f"({s['notable_uncovered']:,} of {s['n_notable']:,} would be new)")
    print(f"    but bands only light {s['lit']:.2%} of the session — the "
          f"denominator for both")
    print(f"\nQ2  concentration vs contact duration")
    print(f"    log-log slope     {s['dur_exp']:+.3f}  (r {s['dur_r']:+.3f})")
    print(f"    same on the rate  {s['rate_exp']:+.3f}")
    print(f"    from  volume^{s['exp_vol']:+.2f}  delta^{s['exp_effort']:+.2f}  "
          f"range^{s['exp_range']:+.2f}  — the range is the exit threshold, not the tape")
    for b in s["dur_buckets"]:
        print(f"    {b['dur']:>12}  conc {b['conc']:>8.1f}  rate {b['rate']:>7.2f}  n {b['n']:,}")
    print(f"\nQ3  forward {FORWARD_MIN}min, signed against the aggressor (ticks)")
    print(f"    all contacts      {s['fwd_all']:+.2f}   n {s['fwd_n']:,}")
    print(f"    flagged           {s['fwd_notable']:+.2f}   AUC {s['fwd_auc']:.3f}")
    print(f"    split-half        h1 {s['fwd_notable_h1']:+.2f} / AUC {s['fwd_auc_h1']:.3f}"
          f"    h2 {s['fwd_notable_h2']:+.2f} / AUC {s['fwd_auc_h2']:.3f}")
    if "auc_marginal" in s:
        print(f"\n    the order-flow half on its own — episodes that lost ground "
              f"({s['lost_n']:,}), split by effort")
        print(f"    heavy spend       {s['fwd_lost_hi']:+.2f}")
        print(f"    light spend       {s['fwd_lost_lo']:+.2f}   AUC "
              f"{s['auc_marginal']:.3f}  ← the tape's whole contribution")
    print(f"\n    positive control — non-tape terms on the same forward join")
    print(f"    lost ground       AUC {s['ctrl_auc']:.3f}   (r(result,fwd) {s['ctrl_r']:+.3f})")
    print(f"    long contact      AUC {s['ctrl_auc_dur']:.3f}")

    r = s.get("reclaim") or {}
    if r:
        print(f"\nQ4  sweep and reclaim — the pattern the episode split cuts in half")
        print(f"    {r['all']['n']:,} events, p50 {r['through_p50']:.0f}t through / "
              f"{r['gap_p50']:.0f}s away")
        print(f"    {'filter':>22}  {'n':>6}  {'median':>7}  {'right':>6}  "
              f"{'h1/h2':>13}  {'tape':>5}")
        for w in [{"gap": 0, "through": 0, **r["all"]}] + r["visible"]:
            tag = ("everything" if not w["gap"]
                   else f">={w['gap']}s away, >={w['through']}t through")
            print(f"    {tag:>22}  {w['n']:>6,}  {w['median']:>+6.1f}t  "
                  f"{w['share']:>5.1%}  {w['h1']:>5.1%}/{w['h2']:<6.1%} "
                  f"{w['tape_auc']:>5.3f}")

    print(f"\nsweep — the two thresholds nothing pins")
    print(f"    {'cfg':>7}  {'n':>6}  {'p50':>5}  {'<15s':>5}  {'rng^':>6}  "
          f"{'fwd flag':>9}  {'AUC':>6}  {'h1/h2':>13}  {'marginal':>8}")
    for w in s["sweep"]:
        print(f"    {w['cfg']:>7}  {w['n']:>6,}  {w['dur_p'][50]:>4.0f}s  "
              f"{w['frac_under_win']:>4.0%}  {w['exp_range']:>+6.2f}  "
              f"{w['fwd_notable']:>+8.1f}  {w['fwd_auc']:>6.3f}  "
              f"{w['fwd_auc_h1']:>6.3f}/{w['fwd_auc_h2']:<6.3f}  "
              f"{w.get('auc_marginal', float('nan')):>8.3f}")

    write_page(s, per_session)
    print(f"\nwrote {HTML_OUT.relative_to(ROOT)}")


def write_page(s: dict, per_session: list[dict]) -> None:
    """Write the read-out.

    The episode table is deliberately *not* shipped. The repo's other demo pages
    carry their raw rows so a threshold can be dragged live, but the two
    thresholds that matter here are swept server-side and the answer is flat at
    every setting — an interactive explorer over a null is 1.9MB of polish on a
    corpse. Re-run the script to change anything.
    """
    template = (OUT_DIR / "_effort_result_template.html").read_text()
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(
        template
        .replace("__SUMMARY_JSON__", json.dumps(s))
        .replace("__SESSIONS_JSON__", json.dumps(per_session))
        .replace("__CONTACT_TICKS__", str(CONTACT_TICKS))
        .replace("__EXIT_TICKS__", str(EXIT_TICKS))
        .replace("__FORWARD_MIN__", str(FORWARD_MIN))
    )


if __name__ == "__main__":
    main()
