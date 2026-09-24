"""A funded month: how many payouts, and what does one actually pay?

Everything before this priced the **evaluation** — reach +$3,000 before the floor
reaches you, once. Funded is a different game with a different shape, and the
difference is the *buffer*:

    buffer = start + max loss + $100 = $52,100 on a 50K

You cannot withdraw a cent below it, and the trailing floor stops following at
``buffer - max loss = $50,100``. Those two numbers are the same event, which is
the tidy part of Lucid's design: **by the moment you are first allowed to take a
payout, your floor has already locked**. So a funded life is two phases —

  1. climb $50,000 → $52,100 with a floor still trailing under you. All of the
     death risk lives here, and it is the evaluation again in miniature.
  2. above the buffer the floor is nailed to $50,100 forever, you hold a fixed
     $2,000 cushion, and the game becomes *how often can I clear $500 above the
     buffer without giving the cushion back*.

The two accounts then diverge completely in how they let you take it:

  LucidPro    3 calendar days between requests, ≥$500 profit in the cycle, the
              40% consistency rule, and a **capped ladder**: $2,000, then $2,500
              four times. Five payouts and the sim is over (live review pool).
  LucidDaily  every day, no consistency rule, no cap — everything above the
              buffer — but the floor trails *intraday*, and a single $8,000 day
              ends the sim by promoting you.

Sources: Lucid's help-center LucidDaily payouts page (pasted by the user, and the
authority for the daily side), cross-checked against two independent write-ups
for the Pro ladder and the 3-day gap. Anything the help centre does not state is
marked ``ASSUMPTION`` at its use.

Everything about a *day* — the fills, the bracket, the daily stop, the goal, the
equity skeleton — is delegated to ``bracket_survival``, which is gated arm-for-arm
against the app's own ``run_flat``. This file adds only the funded floor and the
payout machinery on top of it.
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

OUT = pathlib.Path(__file__).resolve().parent / "funded_payouts.json"

# --- the funded rulebook -----------------------------------------------------

#: Every rule that moves with account size. Lucid scales these almost perfectly
#: linearly — max loss is 4% of the account on both, the buffer is max loss + $100,
#: the eval target is 6% — which makes the two exceptions the whole story of the
#: 25K. See ``configure``.
SIZES = {
    25_000: dict(max_loss=1_000.0, buffer=26_100.0, pro_cycle_min=250.0,
                 pro_caps=[1_000.0, 1_500.0, 1_500.0, 1_500.0, 1_500.0],
                 flex_min_day=100.0, flex_cap=1_000.0, daily_max_day=6_000.0,
                 max_minis=2, max_micros=20),
    50_000: dict(max_loss=2_000.0, buffer=52_100.0, pro_cycle_min=500.0,
                 pro_caps=[2_000.0, 2_500.0, 2_500.0, 2_500.0, 2_500.0],
                 flex_min_day=150.0, flex_cap=2_000.0, daily_max_day=8_000.0,
                 max_minis=4, max_micros=40),
}

START = 50_000.0
MAX_LOSS = 2_000.0
BUFFER = 52_100.0                                        # the trail cap, too
LOCKED_FLOOR = BUFFER - MAX_LOSS                         # 50,100, once peak clears it

#: **This is the one that does not scale.** $500 is the minimum request on every
#: account size. On a 50K that is 25% of the $2,000 cushion; on a 25K it is **50%
#: of a $1,000 cushion**. Commission does not scale either. Everything else about
#: the 25K is half of the 50K, so those two are where its disadvantage lives.
MIN_PAYOUT = 500.0
SPLIT = 0.90                # 90/10 in the trader's favour

PRO_GAP_DAYS = 3            # calendar days since the last payout, or since funding
PRO_CYCLE_MIN = 500.0       # 50K's minimum profit goal per cycle
PRO_CONSISTENCY = 0.40      # best single day ≤ 40% of cumulative cycle profit
PRO_CAPS = [2_000.0, 2_500.0, 2_500.0, 2_500.0, 2_500.0]   # payout 1, then 2-5
PRO_MAX_PAYOUTS = len(PRO_CAPS)                            # then: live review pool

DAILY_MAX_DAY_PROFIT = 8_000.0   # a 50K day this big is an automatic move to live

# LucidFlex. It is *not* "Pro with a longer cycle" — it has no calendar gap at all.
# Five differences, and the last one is the trap:
#   1. no buffer, so profit is withdrawable long before $52,100;
#   2. the gate is **five qualifying days** (≥ $150 each on a 50K) plus $1 net,
#      instead of Pro's 3-calendar-day wait and $500 cycle goal;
#   3. no consistency rule at all (Pro's 40% is the tightest of the three);
#   4. the cap is **50% of cycle profit, ceiling $2,000, and it never escalates** —
#      Pro's ladder climbs to $2,500, so Flex's lifetime sim ceiling is $10,000
#      gross against Pro's $12,000, and it takes $20,000 of profit to reach it;
#   5. **requesting a payout snaps the max loss limit to $50,100 immediately.**
#      That is Flex's buffer, charged as a floor jump rather than a withdrawal
#      block — and it is far more dangerous, because you can trigger it while the
#      balance is still low. Cash out at $51,000 and the floor leaps from $49,000
#      to $50,100 under a $50,500 balance: $400 of room where there was $2,000.
FLEX_MIN_DAY = 150.0        # what makes a day "qualifying" on a 50K
FLEX_QUAL_DAYS = 5          # of them, per cycle
FLEX_PAYOUT_FRAC = 0.50     # of cycle profit
FLEX_CAP = 2_000.0          # flat — unlike Pro's, it does not climb

#: The funded contract ladder Flex gates you behind (2 minis / 20 micros until
#: $1,000 of sim profit, 3/30, then 4/40 at $2,000+). Every sizing this study runs
#: is at or under 2 minis — `mnq250` is 11 micros at a 40-tick stop — so the ladder
#: never binds here and is not modelled. It would bite hard at `mnq400` or 1 NQ + 1.
FLEX_SCALE_NOTE = "2 minis/20 micros under $1k profit; never binds at these sizings"

TRADING_DAYS = 21           # a month
MONTHS = 4_000              # bootstrap draws

#: The operating config the study landed on, and the axes worth seeing beside it.
#: All three are **scaled with the account** by ``configure``: a −$500 daily stop
#: against a 25K's $1,000 max loss is two bad days from dead, so comparing a 25K to
#: a 50K at the same absolute risk would measure the sizing mistake, not the account.
SIZINGS = ["1nq", "mnq150", "mnq250"]
DAY_STOP = B.SELF_STOP      # the −$500 self stop beats both accounts' DLLs (§12)
DAY_GOAL = 500.0            # +$500 and the day is done
MARK = "mtm"                # the path carries the open position, which is what
                            # LucidDaily's floor follows and what an EOD account
                            # still breaches on intraday

PS = [0.50, 0.65, 0.70]
HORIZON = 2
SEEDS = 4

SHAPES = [("1r/none", ("r", 1.0), None),
          ("1.5r/be1r", ("r", 1.5), dict(distR=1.0, stepR=0.0, beTicks=B.BE_TICKS, beOnly=True)),
          ("none/trail1r", None, dict(distR=1.0, stepR=0.0, beTicks=B.BE_TICKS, beOnly=False))]
STOPS = [("t40", 40), ("ruler", None)]
ARMS = [dict(key=f"{sk}/{shk}", stop_basis=sk, stop_ticks=st,
             target=tg, trail=tr, shape=shk, sizing=None)
        for sk, st in STOPS for shk, tg, tr in SHAPES]

#: When to actually press the button. This is a real decision and it is worth
#: more than most of the bracket knobs:
#:
#:   ``asap``   take the minimum the moment you are eligible.
#:   ``full``   (Pro) wait until the whole cap is available. The ladder is only
#:              five rungs long, so a $500 rung *wastes* one.
#:   ``keep1k`` leave $1,000 of your own money above the buffer as cushion. The
#:              floor is locked either way, so this is the one lever that changes
#:              how much room you have to survive the next drawdown.
POLICIES = {
    "asap":   dict(keep=0.0, wait_full=False),
    "full":   dict(keep=0.0, wait_full=True),
    "keep1k": dict(keep=1_000.0, wait_full=False),
}

ACCOUNTS = ["lucid_pro", "lucid_flex", "lucid_daily"]


def configure(size: int) -> float:
    """Point every funded constant at one account size. Returns the scale factor.

    Everything Lucid publishes scales linearly off the account, so the *rules* are
    a lookup. What has to be scaled by hand is **our** operating config — the daily
    stop, the day goal and the risk budget — because holding those fixed while the
    max loss halves would price a sizing error rather than an account.

    The two things that stay put are the $500 minimum payout and the commission.
    Those are the entire structural difference between the sizes.
    """
    global START, MAX_LOSS, BUFFER, LOCKED_FLOOR, PRO_CYCLE_MIN, PRO_CAPS
    global FLEX_MIN_DAY, FLEX_CAP, DAILY_MAX_DAY_PROFIT, DAY_STOP, DAY_GOAL
    global SIZINGS, POLICIES
    cfg = SIZES[size]
    scale = size / 50_000
    START, MAX_LOSS, BUFFER = float(size), cfg["max_loss"], cfg["buffer"]
    LOCKED_FLOOR = BUFFER - MAX_LOSS
    PRO_CYCLE_MIN, PRO_CAPS = cfg["pro_cycle_min"], cfg["pro_caps"]
    FLEX_MIN_DAY, FLEX_CAP = cfg["flex_min_day"], cfg["flex_cap"]
    DAILY_MAX_DAY_PROFIT = cfg["daily_max_day"]
    DAY_STOP, DAY_GOAL = B.SELF_STOP * scale, 500.0 * scale
    # The micro cap is what `position` clips against, and it halves with the account.
    B.MAX_MICROS, B.MAX_MINIS = cfg["max_micros"], cfg["max_minis"]
    # Risk budgets scale too. `position` looks them up by name in bracket_survival,
    # so the scaled ones are registered there.
    SIZINGS = ["1nq"] + [f"mnq{int(round(b * scale))}" for b in (150, 250)]
    for name, base in zip(SIZINGS[1:], (150.0, 250.0)):
        B.RISK_USD[name] = base * scale
    # Key stays "keep" at every size so the two runs line up column for column;
    # the amount it holds back is 2% of the account ($1,000 on a 50K, $500 on a 25K).
    POLICIES = {"asap": dict(keep=0.0, wait_full=False),
                "full": dict(keep=0.0, wait_full=True),
                "keep": dict(keep=1_000.0 * scale, wait_full=False)}
    return scale


def calendar_days(n: int) -> list[int]:
    """Trading-day index → calendar-day index. Mon-Fri, starting on a Monday.

    Pro's gate is in *calendar* days and weekends count, so a Friday payout is
    eligible again on Monday. Getting this wrong would flatter Pro by about a
    third of a payout a month.
    """
    out, cal = [], 0
    for _ in range(n):
        out.append(cal)
        cal += 3 if cal % 7 == 4 else 1
    return out


CAL = calendar_days(TRADING_DAYS)


# --- pool -------------------------------------------------------------------


def job(sd):
    """One session → its trades, per arm and edge level, for every seed."""
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
            # The same seeding rule as the sweep, with the window slot spent on a
            # marker of its own so this study's draws are not the sweep's.
            rng = np.random.default_rng([day.toordinal(), s, int(p * 100), HORIZON, 77])
            entries = B.draw(rng, t, px, p, HORIZON, span, B.ENTRIES_PER_DAY)
            for arm in ARMS:
                out[(arm["key"], p)].append(B.run_day(t, px, ruler, entries, arm))
    return dict(out)


# --- the funded walk ---------------------------------------------------------


def payout_due(acct, eq, cycle_net, cycle_best, qual_days, days_since, n_pay, pol):
    """What you may withdraw right now, or 0.0. Each account's gates, in order."""
    if cycle_net <= 0:                      # "positive net profit since last payout"
        return 0.0

    if acct == "lucid_flex":
        # No buffer — but a payout snaps the floor to $50,100, so withdrawing into
        # a breach is the one thing the account will not let you do. `keep` is
        # measured against that post-snap floor rather than against a buffer.
        avail = eq - LOCKED_FLOOR - pol["keep"]
        if avail < MIN_PAYOUT or qual_days < FLEX_QUAL_DAYS:
            return 0.0
        want = min(FLEX_PAYOUT_FRAC * cycle_net, FLEX_CAP)
        if pol["wait_full"] and want < FLEX_CAP:
            return 0.0
        amt = min(want, avail)
        return amt if amt >= MIN_PAYOUT else 0.0

    avail = eq - BUFFER - pol["keep"]
    if avail < MIN_PAYOUT:
        return 0.0
    if acct == "lucid_daily":
        return avail                        # no cap, no cycle, no consistency rule
    if days_since < PRO_GAP_DAYS:
        return 0.0
    if cycle_net < PRO_CYCLE_MIN:
        return 0.0
    if cycle_best > PRO_CONSISTENCY * cycle_net:
        return 0.0                          # the 40% rule: keep trading, or wait
    cap = PRO_CAPS[n_pay]
    if pol["wait_full"] and avail < cap:
        return 0.0
    return min(avail, cap)


def month(pool, picks, acct, pol):
    """One funded month. Returns fate, payout count, and gross withdrawn."""
    intraday = acct == "lucid_daily"
    eq = peak = START
    snapped = False                              # Flex: has a payout locked the floor?

    def floor_at(pk):
        f = min(pk, BUFFER) - MAX_LOSS
        return max(f, LOCKED_FLOOR) if snapped else f

    floor = floor_at(peak)                       # 48,000 on day one
    n_pay, gross, first = 0, 0.0, None
    cycle_net = cycle_best = 0.0
    qual_days = 0
    last_cal = 0                                 # funding is calendar day 0

    for i, pick in enumerate(picks):
        close, skel = pool[pick]
        # The floor, exactly as the evaluation walks it: both shapes breach on
        # equity *touching* it at any moment, and they differ only in when it moves.
        if intraday:
            for h, lo in skel:
                peak = max(peak, eq + h)
                floor = floor_at(peak)
                if eq + lo <= floor:
                    return "bust", n_pay, gross, first, i + 1
        elif eq + min(lo for _, lo in skel) <= floor:
            return "bust", n_pay, gross, first, i + 1
        eq += close
        peak = max(peak, eq)
        floor = floor_at(peak)
        if eq <= floor:
            return "bust", n_pay, gross, first, i + 1

        if intraday and close >= DAILY_MAX_DAY_PROFIT:
            return "live", n_pay, gross, first, i + 1

        cycle_net += close
        cycle_best = max(cycle_best, close)      # a losing day cannot raise the best
        if close >= FLEX_MIN_DAY:
            qual_days += 1

        # ASSUMPTION: the request goes in at the end of the session it qualifies
        # on, and the money leaves the account the same day. The help centre says
        # funds are deducted "within a few minutes" of approval, so same-day is
        # the honest reading; it is also the conservative one, because the cushion
        # is gone before the next day's trading rather than after it.
        amt = payout_due(acct, eq, cycle_net, cycle_best, qual_days,
                         CAL[i] - last_cal, n_pay, pol)
        if amt >= MIN_PAYOUT:
            eq -= amt                            # the floor does NOT reset on a withdrawal
            gross += amt
            n_pay += 1
            first = first if first is not None else i + 1
            cycle_net = cycle_best = 0.0
            qual_days = 0                        # "reset after every approved payout"
            last_cal = CAL[i]
            if acct == "lucid_flex":
                # The Flex penalty: the floor jumps to $50,100 the moment you ask.
                snapped = True
                floor = floor_at(peak)
                if eq <= floor:
                    return "bust", n_pay, gross, first, i + 1
            if acct in ("lucid_pro", "lucid_flex") and n_pay >= PRO_MAX_PAYOUTS:
                return "live", n_pay, gross, first, i + 1

    return "alive", n_pay, gross, first, TRADING_DAYS


def run_cell(pool, acct, pol):
    rng = np.random.default_rng([11, len(pool), len(acct)])
    picks = rng.integers(len(pool), size=(MONTHS, TRADING_DAYS))
    res = [month(pool, picks[i], acct, POLICIES[pol]) for i in range(MONTHS)]
    n = np.array([r[1] for r in res], float)
    g = np.array([r[2] for r in res], float)
    fates = collections.Counter(r[0] for r in res)
    firsts = [r[3] for r in res if r[3] is not None]
    return dict(
        payouts=round(float(n.mean()), 2),
        take_home=round(float(g.mean() * SPLIT), 0),
        p_any=round(100 * float((n > 0).mean()), 1),
        p_bust=round(100 * fates["bust"] / MONTHS, 1),
        p_live=round(100 * fates["live"] / MONTHS, 1),
        med_first=float(np.median(firsts)) if firsts else None,
        p90_take=round(float(np.percentile(g, 90) * SPLIT), 0),
        p10_take=round(float(np.percentile(g, 10) * SPLIT), 0),
    )


def main():
    global ARMS, TRADING_DAYS, CAL, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-sessions", type=int, default=0)
    ap.add_argument("--days", type=int, default=TRADING_DAYS,
                    help="trading days in the horizon (21 = a month, 63 = a quarter)")
    ap.add_argument("--size", type=int, default=50_000, choices=sorted(SIZES),
                    help="account size; scales the rules AND the operating config")
    ap.add_argument("--quick", action="store_true", help="one arm, for a smoke")
    a = ap.parse_args()

    scale = configure(a.size)
    TRADING_DAYS = a.days
    CAL = calendar_days(TRADING_DAYS)
    tag = "" if a.size == 50_000 else f"_{a.size // 1000}k"
    tag += "" if TRADING_DAYS == 21 else f"_{TRADING_DAYS}d"
    if tag:
        OUT = OUT.with_name(f"funded_payouts{tag}.json")
    if a.quick:
        ARMS = [x for x in ARMS if x["key"] == "t40/1r/none"]

    days = B.sessions()
    if a.max_sessions:
        days = days[-a.max_sessions:]
    print(f"{len(days)} sessions x {SEEDS} seeds x {len(PS)} edges x {len(ARMS)} arms")
    print(f"funded {a.size // 1000}K (scale {scale:g}): max loss ${MAX_LOSS:,.0f}, "
          f"buffer ${BUFFER:,.0f}, locked floor ${LOCKED_FLOOR:,.0f}, "
          f"caps {B.MAX_MINIS} minis / {B.MAX_MICROS} micros")
    print(f"  day stop −${DAY_STOP:,.0f}, goal +${DAY_GOAL:,.0f}, sizings {SIZINGS}, "
          f"keep-policy holds ${POLICIES['keep']['keep']:,.0f}")
    print(f"  MIN PAYOUT ${MIN_PAYOUT:,.0f} = {MIN_PAYOUT / MAX_LOSS:.0%} of the cushion "
          f"(does not scale), {TRADING_DAYS} trading days over {CAL[-1] + 1} calendar")

    trades = collections.defaultdict(list)
    # `imap`, not `imap_unordered`: the day-records land in the pool in completion
    # order otherwise, so the bootstrap draws a different *sequence* of the same
    # multiset on every run. That is worth about ±0.03 payouts and ±$35 of
    # take-home — small, but it makes a published table unreproducible.
    with mp.Pool(max(1, mp.cpu_count() - 2)) as pool:
        for i, r in enumerate(pool.imap(job, days, chunksize=4), 1):
            if r:
                for k, v in r.items():
                    trades[k].extend(v)
            if i % 200 == 0:
                print(f"  {i}/{len(days)}")

    rows = []
    for (armk, p), per_day in sorted(trades.items()):
        for sizing in SIZINGS:
            pl = [B.day_record(tr, sizing, DAY_STOP, DAY_GOAL, MARK, B.MAX_MINIS)
                  for tr in per_day]
            for acct in ACCOUNTS:
                for polk in POLICIES:
                    if acct == "lucid_daily" and polk == "full":
                        continue            # no ladder to fill
                    rows.append(dict(arm=armk, edge_p=p, sizing=sizing, account=acct,
                                     policy=polk, n_days=len(pl),
                                     day_net=round(float(np.mean([c for c, _ in pl])), 2),
                                     **run_cell(pl, acct, polk)))
    OUT.write_text(json.dumps(rows, indent=1))
    print(f"\n{len(rows)} rows → {OUT}")
    report(rows)


# --- report ------------------------------------------------------------------


def report(rows):
    def sel(**kw):
        return [r for r in rows if all(r[k] == v for k, v in kw.items())]

    print("\n" + "=" * 100)
    print(f"A FUNDED RUN — {TRADING_DAYS} trading days, ${START:,.0f}, "
          f"−${DAY_STOP:,.0f} self stop, +${DAY_GOAL:,.0f} day goal")
    print("=" * 100)

    for p in PS:
        print(f"\n  edge p = {p}")
        print(f"    {'account':12} {'policy':8} {'sizing':8} "
              f"{'payouts':>8} {'take-home':>10} {'p(any)':>7} {'p(bust)':>8} "
              f"{'p(live)':>8} {'1st day':>8}")
        for acct in ACCOUNTS:
            for polk in POLICIES:
                for sizing in SIZINGS:
                    rs = sel(edge_p=p, account=acct, policy=polk, sizing=sizing)
                    if not rs:
                        continue
                    m = {k: float(np.mean([r[k] for r in rs]))
                         for k in ("payouts", "take_home", "p_any", "p_bust", "p_live")}
                    fs = [r["med_first"] for r in rs if r["med_first"]]
                    print(f"    {acct:12} {polk:8} {sizing:8} "
                          f"{m['payouts']:8.2f} {m['take_home']:10,.0f} "
                          f"{m['p_any']:6.1f}% {m['p_bust']:7.1f}% {m['p_live']:7.1f}% "
                          f"{np.mean(fs) if fs else 0:8.1f}")


if __name__ == "__main__":
    main()
