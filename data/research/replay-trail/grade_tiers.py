"""What is a *pre-trade* conviction tier worth?

The journal grades trades A-D **in review**, after the outcome is known
(``trade-grading-built``: "grading is review-only", the user's own call). So a
hindsight grade can never set the bracket at the ticket — sizing up on the A's
would be reading the answer. This measures the thing that *could* be built
instead: a tier assigned **before** the fill, of stated accuracy.

The knob is ``s``, the spread of the oracle across four equally-likely tiers:

    p_tier = mean_p + s * (+1.5, +0.5, -0.5, -1.5)

which holds the **mean p fixed**. That is the whole point of the design: at
``s = 0`` every tier is the flat baseline, so any gain a policy shows is the
value of *knowing which trade is which* — never the value of a better signal.
``s = 0.06`` is A=0.74 / D=0.56 against a 0.65 mean, a strong but not absurd
separation.

Tiers are drawn **uniform 1/4**, not on the journal's observed 3/21/47/24 split.
A hindsight distribution is not the ex-ante one: the A's are rare in review
because few trades *worked*, which says nothing about how often a conviction
call would fire.

The policies are the ways a tier could pay for itself, and they are deliberately
mean-preserving so the comparison stays honest:

``flat``          the null. Every tier identical. **Must** equal the flat-p
                  baseline at ``s = 0`` -- ``--check`` asserts it against the
                  gated engine's own ``run_day``.
``size``          risk weighted 1.6/1.2/0.8/0.4 x $240 -- mean risk per trade
                  unchanged, so this is re-allocation, not leverage.
``target``        R weighted 1.50/1.33/1.00/0.75 -- the *width* lever, which
                  doc SS16.5c says is the right one (a tighter stop only buys
                  more micros and a bigger commission bill).
``skip_d``        decline the D's. The corner solution: 12 trades a day instead
                  of 16, and the surviving mean p **rises**.
``skip_d_size``   decline the D's and redeploy their risk across the rest
                  ($320 each), so the same dollars are exposed as ``flat``.

Everything else is the study's standing frame: $240 of micro risk, stop at the
vol ruler, -$500 day stop, no goal, LucidDaily 50K funded month, keep-$1k.
"""
import argparse, collections, multiprocessing as mp, pathlib, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bracket_survival as B
import funded_payouts as FP

TIERS = ("A", "B", "C", "D")
#: Mean-preserving under a uniform 1/4 tier draw: the weights sum to zero.
Z = (1.5, 0.5, -0.5, -1.5)

MEAN_PS = [0.60, 0.65]
SPREADS = [0.00, 0.02, 0.04, 0.06]
H, SEEDS = 2, 4
BASE_RISK, STOP, GOAL = 240.0, 500.0, 0.0
MONTHS, TRADING_DAYS = 4_000, 21

#: The ``mnq`` prefix is load-bearing, not cosmetic: ``B.position`` routes on it,
#: and a key without it prices the trade in **minis** -- ten times the exposure,
#: silently.
MULTS = (0.4, 0.8, 1.0, 1.2, 1.6, 16 / 12)
SZ = {m: f"mnq{round(BASE_RISK * m)}" for m in MULTS}
for m in MULTS:
    B.RISK_USD[SZ[m]] = float(round(BASE_RISK * m))


def tier_ps(mean_p, s):
    return [min(0.95, max(0.50, mean_p + s * z)) for z in Z]


def leg(target_r, sizing):
    """One tier's bracket. The stop is always the ruler -- only R and size move."""
    return dict(key="", stop_basis="ruler", stop_ticks=None, stop_mult=1.0,
                target=("r", round(target_r, 4)), trail=None, sizing=None,
                money=sizing)


#: policy -> per-tier (target R, sizing key) or None to decline the tier.
POLICIES = {
    "flat":        [leg(1.0, SZ[1.0])] * 4,
    "size":        [leg(1.0, SZ[1.6]), leg(1.0, SZ[1.2]),
                    leg(1.0, SZ[0.8]), leg(1.0, SZ[0.4])],
    "target":      [leg(1.50, SZ[1.0]), leg(4 / 3, SZ[1.0]),
                    leg(1.00, SZ[1.0]), leg(0.75, SZ[1.0])],
    "skip_d":      [leg(1.0, SZ[1.0])] * 3 + [None],
    "skip_d_size": [leg(1.0, SZ[16 / 12])] * 3 + [None],
}


def draw_tiered(rng, rng_tier, t, px, ps, horizon_min, span, n):
    """``B.draw`` with a per-entry tier and that tier's own oracle.

    The two generators are separate on purpose: the tier draw must not shift the
    side draw, so at ``s = 0`` (every ``ps`` equal) this returns bit-identical
    sides to ``B.draw`` -- which is the null the checker leans on.
    """
    t0, t1 = span
    ms = np.sort(rng.uniform(t0, t1, n))
    coin = rng.random(n)
    tiers = rng_tier.integers(0, len(TIERS), n)
    Hms = horizon_min * 60_000
    out = []
    for m, c, g in zip(ms.tolist(), coin.tolist(), tiers.tolist()):
        i = int(np.searchsorted(t, m, side="right")) - 1
        j = min(len(px) - 1, int(np.searchsorted(t, m + Hms, side="right")) - 1)
        right = "long" if px[j] > px[max(i, 0)] else "short"
        wrong = "short" if right == "long" else "long"
        out.append((m, right if c < ps[g] else wrong, g))
    return out


def run_day_tiered(t, px, ruler, entries, legs):
    """``B.run_day`` where the bracket is chosen per entry, by the entry's tier.

    A declined tier (``legs[g] is None``) is a gesture that never happened -- it
    leaves the account flat, so the next entry can land. That is the difference
    between skipping a trade and losing one.

    Trades carry their sizing key as a 5th field; ``B.day_record`` reads it.
    """
    lag = B.CFG["latencyMs"]
    n = len(px)
    trades, free_at = [], -1
    for ms, side, g in entries:
        arm = legs[g]
        if arm is None:
            continue
        gi = int(np.searchsorted(t, ms, side="right")) - 1
        i = int(np.searchsorted(t, ms + lag, side="right"))
        if i <= free_at or i >= n or gi < 0:
            continue
        r = ruler[gi]
        if not np.isfinite(r):
            continue
        stop_ticks = max(1, int(round(float(r))))
        entry_px = B.cross(px[i - 1], side == "long", B.CFG)
        stop_px, target_px, trail = B.bracket_for(arm, entry_px, side, stop_ticks, 1)
        ex_i, ex_px, why = B.walk_trade(px, i, side, entry_px, stop_px, target_px, trail)
        free_at = ex_i
        d = 1.0 if side == "long" else -1.0
        pts = (ex_px - entry_px) * d
        path = np.r_[(px[i:ex_i + 1] - entry_px) * d, pts]
        trades.append((float(pts), int(stop_ticks), B.excursion(path), why,
                       arm["money"], g))
    return trades


def _day(sym, day):
    t, px = B.load_rth(sym, day)
    if len(px) < 5_000:
        raise ValueError("thin")
    span = B.window_ms(t, "all")
    if span is None:
        raise ValueError("no window")
    return t, px, B.ruler_series(t, px), span


def job(sd):
    sym, day = sd
    try:
        t, px, ruler, span = _day(sym, day)
    except Exception:
        return None
    out = collections.defaultdict(list)
    for s in range(SEEDS):
        for mean_p in MEAN_PS:
            for sp in SPREADS:
                cell = (mean_p, sp)
                rng = np.random.default_rng(
                    [day.toordinal(), s, int(mean_p * 100), H, 55])
                rng_tier = np.random.default_rng(
                    [day.toordinal(), s, int(mean_p * 100), int(sp * 1000), 77])
                e = draw_tiered(rng, rng_tier, t, px, tier_ps(mean_p, sp), H,
                                span, B.ENTRIES_PER_DAY)
                for name, legs in POLICIES.items():
                    out[(name, *cell)].append(run_day_tiered(t, px, ruler, e, legs))
    return dict(out)


def check(n_days=25):
    """At ``s = 0`` the tiered walker must reproduce the gated engine exactly.

    Same draw, same bracket, same trades -- otherwise every number below is this
    module's arithmetic rather than the study's.
    """
    arm = dict(key="base", stop_basis="ruler", stop_ticks=None, stop_mult=1.0,
               target=("r", 1.0), trail=None, sizing=None)
    ok = bad = 0
    for sym, day in B.sessions()[:n_days]:
        try:
            t, px, ruler, span = _day(sym, day)
        except Exception:
            continue
        for mean_p in MEAN_PS:
            rng = np.random.default_rng([day.toordinal(), 0, int(mean_p * 100), H, 55])
            rng_tier = np.random.default_rng([day.toordinal(), 0, int(mean_p * 100), 0, 77])
            e = draw_tiered(rng, rng_tier, t, px, tier_ps(mean_p, 0.0), H,
                            span, B.ENTRIES_PER_DAY)
            ref_rng = np.random.default_rng([day.toordinal(), 0, int(mean_p * 100), H, 55])
            ref_e = B.draw(ref_rng, t, px, mean_p, H, span, B.ENTRIES_PER_DAY)
            assert [(m, s) for m, s, _ in e] == ref_e, f"draw diverged {day}"
            mine = [x[:4] for x in run_day_tiered(t, px, ruler, e, POLICIES["flat"])]
            ref = B.run_day(t, px, ruler, ref_e, arm)
            same = len(mine) == len(ref) and all(
                a[0] == b[0] and a[1] == b[1] and a[3] == b[3]
                for a, b in zip(mine, ref))
            ok, bad = ok + same, bad + (not same)
    print(f"check: {ok}/{ok + bad} day-cells reproduce the gated run_day exactly")
    return bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        raise SystemExit(0 if check() else 1)

    days = B.sessions()
    trades = collections.defaultdict(list)
    with mp.Pool(max(1, mp.cpu_count() - 2)) as pool:
        for r in pool.imap(job, days, chunksize=4):
            if r:
                for k, v in r.items():
                    trades[k].extend(v)
    FP.configure(50_000)
    FP.TRADING_DAYS = TRADING_DAYS
    FP.CAL = FP.calendar_days(TRADING_DAYS)

    print(f"{len(days)} sessions x {SEEDS} draws, mean p held fixed, "
          f"${STOP:.0f} day stop, no goal, LucidDaily 50K keep-$1k\n")

    print("THE MECHANISM  (policy=flat -- what the tier separates, before any policy acts)")
    print(f"  {'mean p':>7} {'s':>5} " + " ".join(f"{g:>16}" for g in TIERS))
    for mean_p in MEAN_PS:
        for sp in SPREADS:
            flat = [x for d in trades[("flat", mean_p, sp)] for x in d]
            cells = []
            for g in range(4):
                sub = [x for x in flat if x[5] == g]
                net = np.array([x[0] * B.position(x[4], x[1])[0]
                                - B.position(x[4], x[1])[1] for x in sub])
                cells.append(f"{100*(net>0).mean():5.1f}% {net.mean():+7.2f}"
                             if len(net) else " " * 14)
            ps = tier_ps(mean_p, sp)
            print(f"  {mean_p:>7} {sp:>5.2f} " + " ".join(f"{c:>16}" for c in cells)
                  + f"   p={'/'.join(f'{x:.2f}' for x in ps)}")

    print("\nPER TRADE")
    print(f"  {'policy':13} {'mean p':>7} {'s':>5} {'trades/day':>11} {'win%':>7} "
          f"{'mean$':>8} {'day mean':>9} {'green%':>7}")
    for name in POLICIES:
        for mean_p in MEAN_PS:
            for sp in SPREADS:
                tr = trades[(name, mean_p, sp)]
                flat = [x for d in tr for x in d]
                net = np.array([x[0] * B.position(x[4], x[1])[0]
                                - B.position(x[4], x[1])[1] for x in flat])
                pl = [B.day_record(d, SZ[1.0], STOP, GOAL, "mtm", B.MAX_MINIS)
                      for d in tr]
                dd = np.array([c for c, _ in pl])
                print(f"  {name:13} {mean_p:>7} {sp:>5.2f} {len(flat)/len(tr):11.1f} "
                      f"{100*(net>0).mean():6.1f}% {net.mean():8.2f} "
                      f"{dd.mean():9.0f} {100*(dd>0).mean():6.1f}%")

    print("\nA FUNDED MONTH  (LucidDaily 50K)")
    print(f"  {'policy':13} {'mean p':>7} {'s':>5} {'payouts':>8} {'take-home':>10} "
          f"{'dead':>7} {'p10':>9}")
    for name in POLICIES:
        for mean_p in MEAN_PS:
            for sp in SPREADS:
                tr = trades[(name, mean_p, sp)]
                pl = [B.day_record(d, SZ[1.0], STOP, GOAL, "mtm", B.MAX_MINIS)
                      for d in tr]
                rng = np.random.default_rng([31, len(pl)])
                picks = rng.integers(len(pl), size=(MONTHS, TRADING_DAYS))
                res = [FP.month(pl, picks[i], "lucid_daily", FP.POLICIES["keep"])
                       for i in range(MONTHS)]
                n = np.array([r[1] for r in res], float)
                g = np.array([r[2] for r in res], float) * FP.SPLIT
                bust = collections.Counter(r[0] for r in res)["bust"]
                print(f"  {name:13} {mean_p:>7} {sp:>5.2f} {n.mean():8.2f} "
                      f"{g.mean():10,.0f} {100*bust/MONTHS:6.1f}% "
                      f"{np.percentile(g, 10):9,.0f}")


if __name__ == "__main__":
    main()
