"""Accounts as consumables: what does a year of buying, busting and rebuying pay?

Every table in §15 ranks configurations by *survival*, which is the right objective
only if an account is precious. It is not — it is a **$60-130 consumable**, and the
question this file answers is the one that follows:

    given a fixed budget for buying accounts, what maximises take-home cash?

That inverts several §15 conclusions, because a bust is no longer a terminal event.
It is a fee. The campaign walks the whole pipeline —

    buy an evaluation → pass it (or bust) → funded → payouts → bust or graduate
    → buy another, if the budget allows

— over a 12-month horizon, and reports **net cash per month after fees**.

Two structural notes that matter more than any parameter:

*Resets are not modelled, deliberately.* The public discount code takes 40% off a
new purchase and never applies to a reset, so a fresh discounted account is the
cheaper path at every size. Buying fresh is what the fee table prices.

*Accounts are run one at a time.* Running N in parallel with the same strategy
multiplies cash and variance by N with **no diversification** — the trades are
identical, so the accounts bust together. That makes the per-account numbers here
the thing to optimise, and the budget the thing that sets N.
"""
from __future__ import annotations

import argparse
import collections
import json
import multiprocessing as mp
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bracket_survival as B  # noqa: E402
import funded_payouts as FP  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "funded_campaign.json"

# --- what an account costs ---------------------------------------------------

#: **List** prices, sampled late July 2026, with the discount applied separately by
#: ``--discount`` so a 30%/40% sale is a dial rather than a baked-in assumption.
#: Provenance differs by row and is worth knowing: the two Pro figures came off
#: checkout screenshots and are solid; the Flex and Daily 50K rows are **inferred**
#: from published reset fees (a reset runs ~60% of list), so they are the shakiest.
#: The captured Daily prices are the **intraday**-eval configuration, which is the
#: *cheaper* side of that toggle; EOD costs more and was not captured. The default
#: run still models an EOD eval (``INTRADAY_EVAL = False``), so the default is
#: **cheap price paired with the easy eval** and is optimistic for Daily by exactly
#: the amount ``--intraday-eval`` measures. Fees are reported as their own column
#: throughout: they turn out to be small against take-home, which is itself a finding.
LIST = {
    # VERIFIED 2026-08-25 off the live LucidPro pricing carousel (user-supplied DOM).
    (25_000, "lucid_pro"): 123.0, (50_000, "lucid_pro"): 192.0,
    (100_000, "lucid_pro"): 307.0, (150_000, "lucid_pro"): 410.0,
    # VERIFIED the same way. Daily's price depends on two toggles (eval drawdown,
    # DLL on/off), so these are the *configured* prices the capture showed:
    # 25K intraday + DLL off, 50K intraday + DLL on.
    (25_000, "lucid_daily"): 115.0, (50_000, "lucid_daily"): 156.0,
    (100_000, "lucid_daily"): 314.0, (150_000, "lucid_daily"): 436.0,
    # VERIFIED. Flex is cheapest at 25K; Daily is cheapest at 50K and above.
    (25_000, "lucid_flex"): 89.0, (50_000, "lucid_flex"): 146.0,
    (100_000, "lucid_flex"): 293.0, (150_000, "lucid_flex"): 407.0,
}
#: Checkout price with the sale *and* the coupon, where it is known exactly. The
#: two-step discount (sale, then a coupon at checkout) lands at ~40-43% off list,
#: which is why the flat ``--discount 0.40`` was close in aggregate even where the
#: list price was wrong.
VERIFIED = {(25_000, "lucid_pro"): 70.60, (50_000, "lucid_pro"): 115.40,
            (100_000, "lucid_pro"): 180.40, (150_000, "lucid_pro"): 245.50,
            (25_000, "lucid_daily"): 75.00, (50_000, "lucid_daily"): 76.60,
            (100_000, "lucid_daily"): 157.40, (150_000, "lucid_daily"): 221.60,
            (25_000, "lucid_flex"): 50.30, (50_000, "lucid_flex"): 90.20,
            (100_000, "lucid_flex"): 170.60, (150_000, "lucid_flex"): 250.40}
#: Resets, verified the same way. **A reset now costs the same as a fresh account**
#: ($70.00 vs $70.60 at 25K, $115.00 vs $115.40 at 50K), so the earlier claim that
#: buying fresh is strictly cheaper no longer holds — they are interchangeable.
RESET = {(25_000, "lucid_pro"): 70.0, (50_000, "lucid_pro"): 115.0,
         (100_000, "lucid_pro"): 180.0, (150_000, "lucid_pro"): 245.0,
         # On Daily a fresh account is *cheaper* than a reset at every size
         # ($76.60 vs $90.00 at 50K), so the buy-fresh rule survives there.
         (25_000, "lucid_daily"): 85.0, (50_000, "lucid_daily"): 90.0,
         (100_000, "lucid_daily"): 185.0, (150_000, "lucid_daily"): 260.0,
         # Flex: reset == fresh, as on Pro.
         (25_000, "lucid_flex"): 50.0, (50_000, "lucid_flex"): 90.0,
         (100_000, "lucid_flex"): 170.0, (150_000, "lucid_flex"): 250.0}
DISCOUNT = 0.40         # the public code; sales run 30-40%
INTRADAY_EVAL = False   # LucidDaily's cheaper eval drawdown; see `campaign`
FEES: dict[tuple[int, str], float] = {}

#: Evaluation rules. Pro has no consistency rule and can pass in a day; Flex and
#: Daily both carry a **50% consistency check in the evaluation only**, which is
#: the gate a +day-goal config passes trivially and a swing-for-the-fences one does
#: not. Daily's eval drawdown is a paid choice (EOD costs more); the EOD version is
#: modelled, because paying ~$20 to not be trailed intraday during the eval is the
#: obvious buy.
EVAL = {
    "lucid_pro": dict(consistency=None, min_days=1),
    # The card shows 50% eval consistency for Flex. `min_days` is this file's own
    # guess; the pricing card states no minimum trading days for any product.
    "lucid_flex": dict(consistency=0.50, min_days=2),
    "lucid_daily": dict(consistency=0.50, min_days=1),
}
#: 6% of the account at 50K/100K/150K. **The 25K is $1,250, not $1,500** — verified
#: on the live pricing page, and a 5% bar rather than 6%. An earlier run assumed 6%
#: and so made the 25K evaluation harder than it is. Daily's targets are unverified.
EVAL_TARGET = {(25_000, "lucid_pro"): 1_250.0, (50_000, "lucid_pro"): 3_000.0,
               (25_000, "lucid_flex"): 1_250.0, (50_000, "lucid_flex"): 3_000.0,
               (25_000, "lucid_daily"): 1_250.0, (50_000, "lucid_daily"): 3_000.0}

MONTH_DAYS = 21
HORIZON_MONTHS = 12
BUDGET_PER_MONTH = 250.0
RUNS = 1_000

#: The aggression dial, as a fraction of the account's max loss risked per trade.
#: 7.5% is §15.5's optimum; 20% is roughly 1 NQ on a 50K.
RISK_PCTS = [0.05, 0.075, 0.12, 0.20]
#: The day goal, in multiples of the account's scale ($500 on a 50K). `0` = none,
#: which is the aggressive choice: it removes the cap on a good day.
GOAL_MULTS = [0.0, 1.0, 2.0]
POLICIES = ["asap", "full", "keep"]
SIZES = [25_000, 50_000]
ACCOUNTS = ["lucid_pro", "lucid_flex", "lucid_daily"]

PS = [0.55, 0.60, 0.65, 0.70]
HORIZON = 2
SEEDS = 4
ARM = dict(key="ruler/1r/none", stop_basis="ruler", stop_ticks=None,
           target=("r", 1.0), trail=None, shape="1r/none", sizing=None)


def job(sd):
    """One session → its trades under the single best arm, per edge level."""
    sym, day = sd
    try:
        t, px = B.load_rth(sym, day)
    except Exception:
        return None
    if len(px) < 5_000:
        return None
    ruler = B.ruler_series(t, px)
    span = B.window_ms(t, "all")
    if span is None:
        return None
    out = collections.defaultdict(list)
    for s in range(SEEDS):
        for p in PS:
            rng = np.random.default_rng([day.toordinal(), s, int(p * 100), HORIZON, 55])
            entries = B.draw(rng, t, px, p, HORIZON, span, B.ENTRIES_PER_DAY)
            out[p].append(B.run_day(t, px, ruler, entries, ARM))
    return dict(out)


# --- one account's life ------------------------------------------------------


def evaluate_phase(pool, picks, i, budget_days, size, acct, intraday):
    """Trade an evaluation. Returns (passed, days_used)."""
    nums_max_loss = FP.SIZES[size]["max_loss"]
    buffer = FP.SIZES[size]["buffer"]
    target = size + EVAL_TARGET[(size, acct)]
    rule = EVAL[acct]
    eq = peak = float(size)
    floor = min(peak, buffer) - nums_max_loss
    best = 0.0
    used = 0
    while used < budget_days:
        close, skel = pool[picks[(i + used) % len(picks)]]
        used += 1
        if intraday:
            for h, lo in skel:
                peak = max(peak, eq + h)
                floor = min(peak, buffer) - nums_max_loss
                if eq + lo <= floor:
                    return False, used
        elif eq + min(lo for _, lo in skel) <= floor:
            return False, used
        eq += close
        peak = max(peak, eq)
        floor = min(peak, buffer) - nums_max_loss
        if eq <= floor:
            return False, used
        best = max(best, close)
        if eq >= target and used >= rule["min_days"]:
            # The eval consistency rule, where there is one: a single outsized day
            # cannot be more than half the total. Failing it does not fail the
            # account — it just means you keep trading until the rest catches up.
            profit = eq - size
            if rule["consistency"] is None or best <= rule["consistency"] * profit:
                return True, used
    return False, used


def funded_phase(pool, picks, i, budget_days, acct, pol, cal0):
    """Trade a funded account until it dies, graduates, or the horizon ends.

    A near-copy of ``funded_payouts.month`` with the fixed horizon replaced by a
    budget of remaining days, so the campaign can keep the clock across accounts.
    """
    intraday = acct == "lucid_daily"
    eq = peak = FP.START
    snapped = False

    def floor_at(pk):
        f = min(pk, FP.BUFFER) - FP.MAX_LOSS
        return max(f, FP.LOCKED_FLOOR) if snapped else f

    floor = floor_at(peak)
    n_pay, gross = 0, 0.0
    cycle_net = cycle_best = 0.0
    qual_days = 0
    last_cal = cal0
    used = 0
    while used < budget_days:
        close, skel = pool[picks[(i + used) % len(picks)]]
        cal = cal0 + used + (used // 5) * 2      # Mon-Fri → calendar days
        used += 1
        if intraday:
            for h, lo in skel:
                peak = max(peak, eq + h)
                floor = floor_at(peak)
                if eq + lo <= floor:
                    return "bust", n_pay, gross, used
        elif eq + min(lo for _, lo in skel) <= floor:
            return "bust", n_pay, gross, used
        eq += close
        peak = max(peak, eq)
        floor = floor_at(peak)
        if eq <= floor:
            return "bust", n_pay, gross, used
        if intraday and close >= FP.DAILY_MAX_DAY_PROFIT:
            return "live", n_pay, gross, used
        cycle_net += close
        cycle_best = max(cycle_best, close)
        if close >= FP.FLEX_MIN_DAY:
            qual_days += 1
        amt = FP.payout_due(acct, eq, cycle_net, cycle_best, qual_days,
                            cal - last_cal, n_pay, pol)
        if amt >= FP.MIN_PAYOUT:
            eq -= amt
            gross += amt
            n_pay += 1
            cycle_net = cycle_best = 0.0
            qual_days = 0
            last_cal = cal
            if acct == "lucid_flex":
                snapped = True
                floor = floor_at(peak)
                if eq <= floor:
                    return "bust", n_pay, gross, used
            if acct in ("lucid_pro", "lucid_flex") and n_pay >= FP.PRO_MAX_PAYOUTS:
                return "live", n_pay, gross, used
    return "open", n_pay, gross, used


def campaign(pool, picks, size, acct, pol, horizon_days):
    """Buy, trade, bust, rebuy — for a year. Returns cash, fees and body count."""
    fee = FEES[(size, acct)]
    # LucidDaily alone lets you *buy* the evaluation's drawdown type. It does NOT
    # carry into funded — funded is always intraday — so it is a pure one-time
    # purchase of pass probability, and intraday is the cheaper side. `INTRADAY_EVAL`
    # switches which one is being priced. Pro and Flex are EOD-only.
    intraday_eval = INTRADAY_EVAL and acct == "lucid_daily"
    day = 0
    budget = BUDGET_PER_MONTH             # month one's allowance, up front
    cash = fees = 0.0
    bought = busted = graduated = 0
    eval_fails = 0
    i = 0
    while day < horizon_days:
        if budget < fee:
            # Idle until the next month's allowance lands. This is the only place
            # the budget binds, and it is what stops a reckless config compounding.
            step = min(horizon_days - day, MONTH_DAYS - (day % MONTH_DAYS))
            day += step
            budget += BUDGET_PER_MONTH * step / MONTH_DAYS
            continue
        budget -= fee
        fees += fee
        bought += 1
        ok, used = evaluate_phase(pool, picks, i, horizon_days - day, size, acct,
                                  intraday_eval)
        i += used
        day += used
        budget += BUDGET_PER_MONTH * used / MONTH_DAYS
        if not ok:
            eval_fails += 1
            continue
        fate, _n, gross, used = funded_phase(pool, picks, i, horizon_days - day,
                                             acct, FP.POLICIES[pol], 0)
        i += used
        day += used
        budget += BUDGET_PER_MONTH * used / MONTH_DAYS
        cash += gross * FP.SPLIT
        busted += fate == "bust"
        graduated += fate == "live"
    return dict(cash=cash, fees=fees, bought=bought, busted=busted,
                graduated=graduated, eval_fails=eval_fails)


def run_cell(pool, size, acct, pol, horizon_days):
    rng = np.random.default_rng([23, len(pool), len(acct), int(size)])
    picks = rng.integers(len(pool), size=(RUNS, horizon_days + 40))
    res = [campaign(pool, picks[k], size, acct, pol, horizon_days) for k in range(RUNS)]
    net = np.array([r["cash"] - r["fees"] for r in res])
    return dict(
        net_month=round(float(net.mean()) / HORIZON_MONTHS, 0),
        cash_month=round(float(np.mean([r["cash"] for r in res])) / HORIZON_MONTHS, 0),
        fees_month=round(float(np.mean([r["fees"] for r in res])) / HORIZON_MONTHS, 0),
        accounts=round(float(np.mean([r["bought"] for r in res])), 1),
        eval_fail_pct=round(100 * float(np.mean([r["eval_fails"] for r in res]))
                            / max(1e-9, float(np.mean([r["bought"] for r in res]))), 0),
        graduated=round(float(np.mean([r["graduated"] for r in res])), 2),
        p_loss=round(100 * float((net < 0).mean()), 1),
        p10=round(float(np.percentile(net, 10)), 0),
        p90=round(float(np.percentile(net, 90)), 0),
    )


def main():
    global FEES, RUNS, BUDGET_PER_MONTH, INTRADAY_EVAL, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-sessions", type=int, default=0)
    ap.add_argument("--discount", type=float, default=DISCOUNT,
                    help="0.40 = the public code; try 0.0 for full price")
    ap.add_argument("--budget", type=float, default=BUDGET_PER_MONTH)
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--intraday-eval", action="store_true",
                    help="LucidDaily's cheaper eval drawdown (funded is intraday either way)")
    a = ap.parse_args()

    RUNS = a.runs
    BUDGET_PER_MONTH = a.budget
    INTRADAY_EVAL = a.intraday_eval
    if INTRADAY_EVAL:
        OUT = OUT.with_name("funded_campaign_intraday_eval.json")
    FEES = {k: VERIFIED.get(k, round(v * (1 - a.discount), 2)) for k, v in LIST.items()}
    if a.discount != DISCOUNT:                      # an explicit sweep overrides them
        FEES = {k: round(v * (1 - a.discount), 2) for k, v in LIST.items()}
    print(f"fees at {a.discount:.0%} off: " + ", ".join(
        f"{k[1].split('_')[1]} {k[0]//1000}K ${v:.0f}" for k, v in sorted(FEES.items())))

    days = B.sessions()
    if a.max_sessions:
        days = days[-a.max_sessions:]
    horizon = MONTH_DAYS * HORIZON_MONTHS
    print(f"{len(days)} sessions x {SEEDS} seeds x {len(PS)} edges, arm {ARM['key']}")
    print(f"campaign: {HORIZON_MONTHS} months ({horizon} trading days), "
          f"${BUDGET_PER_MONTH:,.0f}/month for accounts, {RUNS:,} runs a cell")

    trades = collections.defaultdict(list)
    with mp.Pool(max(1, mp.cpu_count() - 2)) as pool:
        for n, r in enumerate(pool.imap(job, days, chunksize=4), 1):
            if r:
                for k, v in r.items():
                    trades[k].extend(v)
            if n % 200 == 0:
                print(f"  {n}/{len(days)}")

    rows = []
    for size in SIZES:
        scale = FP.configure(size)
        max_loss = FP.SIZES[size]["max_loss"]
        for pct in RISK_PCTS:
            budget = round(max_loss * pct)
            name = f"mnq{budget}"
            B.RISK_USD[name] = float(budget)
            for gm in GOAL_MULTS:
                goal = 500.0 * scale * gm
                for p in PS:
                    pl = [B.day_record(tr, name, B.SELF_STOP * scale, goal, "mtm",
                                       B.MAX_MINIS)
                          for tr in trades[p]]
                    for acct in ACCOUNTS:
                        for pol in POLICIES:
                            if acct == "lucid_daily" and pol == "full":
                                continue
                            rows.append(dict(
                                size=size, risk_pct=pct, risk_usd=budget,
                                goal=goal, edge_p=p, account=acct, policy=pol,
                                day_net=round(float(np.mean([c for c, _ in pl])), 2),
                                **run_cell(pl, size, acct, pol, horizon)))
            print(f"  {size//1000}K risk {pct:.1%} done")
    OUT.write_text(json.dumps(rows, indent=1))
    print(f"\n{len(rows)} rows → {OUT}")
    report(rows)


def report(rows):
    for p in PS:
        rs = sorted([r for r in rows if r["edge_p"] == p],
                    key=lambda r: -r["net_month"])
        print("\n" + "=" * 108)
        print(f"p = {p} — ranked by NET take-home a month, after account fees")
        print("=" * 108)
        print(f"  {'size':5} {'acct':11} {'pol':5} {'risk':>6} {'goal':>6} "
              f"{'day$':>7} {'net/mo':>8} {'fees/mo':>8} {'accts/yr':>9} "
              f"{'evalfail':>9} {'grad':>5} {'p(loss)':>8} {'p10':>9}")
        for r in rs[:14]:
            print(f"  {r['size']//1000:4}K {r['account']:11} {r['policy']:5} "
                  f"{r['risk_pct']:6.1%} {r['goal']:6.0f} {r['day_net']:7.0f} "
                  f"{r['net_month']:8,.0f} {r['fees_month']:8,.0f} "
                  f"{r['accounts']:9.1f} {r['eval_fail_pct']:8.0f}% "
                  f"{r['graduated']:5.2f} {r['p_loss']:7.1f}% {r['p10']:9,.0f}")
        best = max(rs, key=lambda r: r["net_month"])
        print(f"    -> optimum risk at p={p}: {best['risk_pct']:.1%} of max loss, "
              f"goal ${best['goal']:,.0f}, {best['account']} {best['size']//1000}K")


if __name__ == "__main__":
    main()
