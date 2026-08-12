# LucidPro 50K — survivability knobs from the real day book

> ⚠ **The exit-policy section of this document is superseded (2026-08-10).** Its tick-level
> re-simulation never checked that the tape agreed with the journal. Re-run with a 2-point
> agreement gate at entry, **122 of the 296 prop entries fail** — 47 have no tick file, and 75
> are off-tape by 60–90 points with an inconsistent offset (44 of those overnight). So every
> figure here that came off that re-sim (+$18,801 on target-120, +$34,661 on the trail, and the
> stop-width table) is partly simulated against prices that did not trade, and **must not be
> quoted until it is re-run**. The account mechanics, the rule table and the daily-stop logic
> are unaffected. Current numbers, and the switch from 5 MNQ to 1 NQ that followed:
> [`lucidpro-operating-plan.md`](lucidpro-operating-plan.md).

- **Date:** 2026-08-09
- **What this is:** the LucidPro 50K rule set, and a bootstrap of **the actual prop day book**
  (19 account-days, `journal.db.pre-mode-folders.bak`) through those exact mechanics — to
  pick guardrail levels for a real account rather than generic ones. Budget constraint:
  **2 × 50K evaluations per month, maximum.**
- **> VERDICT: one knob dominates — a self-imposed daily stop at ~$400, far tighter than
  Lucid's own $1,200.** It moves the evaluation pass rate from **56% → 82%**, and
  P(at least one pass in two attempts) from **81% → 96%**. Trade **1 contract, not 2.**
  And the optimal behaviour **inverts between eval and funded** — see §5.
- Companion to [`manual-trade-behaviour-audit.md`](manual-trade-behaviour-audit.md), which
  supplies the behavioural findings the levels are set from.

---

## 1. The rules (LucidPro 50K)

| | Evaluation | Funded |
|---|---|---|
| Start / target | $50,000 / **+$3,000** | — |
| Max Loss Limit | **$2,000, end-of-day trailing** | same |
| Trail stops at | Initial Trail Balance **$52,100**, then the floor locks at **$50,100** | same |
| Daily Loss Limit | **$1,200**, *soft* breach — locked out for the session, account survives | $1,200 until balance > $52,100, then **LucidScale** = 60% of peak EOD profit |
| Max size | 4 minis / 40 micros | same |
| Minimum trading days | none — a one-day pass is possible | — |
| Consistency | **sources conflict** — see caveat | **40%**: largest single day ≤ 40% of cycle profit |
| Payout | — | 3-day cycle, min $500, first cap $2,000 then $2,500 |

**⚠ Verify before relying on it:** one source states a 40% consistency rule applies to the
LucidPro *evaluation*, another states there is none in eval and it is funded-only. The
simulation below models eval **without** consistency and funded **with** it. If eval does
carry 40%, the eval numbers shift toward the funded column and the green-lock in §5
becomes necessary immediately.

### The structural fact everything follows from

> **The daily loss limit is 60% of the entire max loss limit.** $1,200 of $2,000.

Two sessions at Lucid's own limit and the account is gone. Their DLL is not a risk control
for you — it is the width of the hole they permit before the trailing floor catches you.
Any survivable plan needs a personal stop **well inside** it.

---

## 2. The day book being bootstrapped

The 19 real prop account-days, sorted:

```
-2037  -2006  -1840  -1305  -1080  -1047  -59  222  587  632
  678    863   1024   1212   1365   1587  1635  3253  4618
```

median **+$632**, mean **+$437**, 63% green. **Six of nineteen days are −$1,000 or worse,
and three sit at roughly −$2,000 — a full MLL in a single session.** That shape, against a
$2,000 trailing drawdown, is the whole problem.

---

## 3. Evaluation — sweeping the self-imposed daily stop (1 contract)

| my daily stop | pass % | bust % | median days | **P(pass in 2 attempts)** |
|---:|---:|---:|---:|---:|
| **$300** | **81.2** | 18.4 | 6.7 | **96.5** |
| **$400** | **81.8** | 17.9 | 6.3 | **96.7** |
| $500 | 70.9 | 29.0 | 5.7 | 91.5 |
| $600 | 71.2 | 28.7 | 5.4 | 91.7 |
| $750 | 67.6 | 32.4 | 5.3 | 89.5 |
| $900 | 53.6 | 46.4 | 4.2 | 78.5 |
| **$1,200 (Lucid's own)** | **56.4** | **43.6** | 4.0 | **81.0** |

Monotone from $1,200 down to $400, then flat. Tightening the daily stop from Lucid's
$1,200 to $400 buys **+25 points of pass rate** and costs ~2 extra trading days.

The *rate governor* from the behaviour audit (stop taking trades once $N down, rather than
hard-stopping the session) performs identically — 80.1% at $300, 70.2% at $500 — so either
implementation works. The hard stop is simpler to enforce server-side.

### Why: you die early, before any buffer exists

| | busts | median bust day | busts by day 3 | median high-water first |
|---|---:|---:|---:|---:|
| Lucid DLL only ($1,200) | 2,602 | **day 3** | **52%** | +$402 |
| self-imposed $500 stop | 1,766 | day 6 | 13% | +$276 |

Over half of all busts happen in the first three sessions, with the account never having
been more than ~$400 up. The failure mode is **not** giving back a big lead to the ratchet —
it is two bad sessions before a cushion exists. That is exactly what a tight daily stop
prevents, and it is why the level matters more than any other knob.

---

## 4. Size — 1 contract beats 2

| size | best stop | pass % | median days |
|---|---:|---:|---:|
| **1 contract** | $400 | **81.8** | 6.3 |
| 2 contracts | $800 | 64.8 | 3.2 |
| 2 contracts | $1,800 | 43.0 | 2.0 |

Two contracts halves the time to target and costs **17 points of pass rate**. With a
two-attempt budget and no minimum trading days, speed is worth nothing. Trade one.

---

## 5. Funded inverts the plan — cap your winning days

Once funded, the **40% consistency rule** makes a big day actively harmful: a $1,500 day
requires $3,750 of cycle profit before it is payable. Adding a green-lock (stop the session
once $N ahead):

| daily stop | green lock | pass % | bust % | days |
|---:|---:|---:|---:|---:|
| $400 | none | 64.7 | 19.0 | 14.6 |
| $400 | $750 | 76.5 | 21.2 | 10.5 |
| **$400** | **$1,000** | **81.7** | **16.8** | 9.6 |
| $400 | $1,500 | 67.8 | 23.1 | 12.7 |
| $1,200 | none | 41.8 | 51.0 | 9.1 |
| $1,200 | $1,000 | 53.1 | 46.9 | 6.0 |

$1,000 is the optimum and it is derivable, not fitted: at 40% consistency a $1,000 day needs
$2,500 of cycle profit to clear — which is also roughly the payout cap. A $1,500 day needs
$3,750 and strands the cycle.

**In evaluation the same green-lock is harmful** — 47.6% at a $750 lock and 54.5% at $1,000,
against 56.9% with no lock at all. Eval has no consistency rule and no minimum days, so the
only thing that matters is reaching $53,000 before the trail grinds you; capping winning
days just extends exposure. Two stages, opposite instruction.

---

## 5b. Per-trade risk — and why it should NOT be dynamic

Per-trade shape on the real book: median losing trade **40 ticks = $200 on one mini**,
p90 loss 67 ticks = $335. Median 14 trades and **6 losses per day**. Against a $400 daily
stop, one mini gives you **two losses and the session is over** — which is why size and
stop have to be chosen together.

Sweeping both (eval, 1 contract = 1 mini, 10 micros = 1 mini):

| size | risk/trade | daily stop | losses to stop | pass % | bust % | days | 2 tries |
|---|---:|---:|---:|---:|---:|---:|---:|
| **5 micros** | **$100** | **$200** | 2.0 | **95.0** | 3.9 | 13.4 | **99.7** |
| 5 micros | $100 | $300 | 3.0 | 89.5 | 9.7 | 13.0 | 98.9 |
| 7 micros | $140 | $300 | 2.1 | 87.3 | 12.6 | 9.3 | 98.4 |
| 3 micros | $60 | $200 | 3.3 | 87.9 | 2.2 | 21.7 | 98.5 |
| 1 mini | $200 | $400 | 2.0 | 81.5 | 18.5 | 6.3 | 96.6 |
| 1 mini | $200 | $200 | 1.0 | 49.9 | 49.9 | 10.9 | 74.9 |
| 1 mini | $200 | $900 | 4.5 | 52.5 | 47.5 | 4.2 | 77.4 |
| 2 minis | $400 | $400 | 1.0 | 40.2 | 59.8 | 4.6 | 64.2 |

Two things fall out. **The ratio wants to be 2–3 losses to the daily stop** — at 1.0 the
stop fires before any normal day can develop (49.9%), at 4.5 it stops protecting you
(52.5%). And **at equal ratio, smaller absolute size always wins**, because the $2,000
drawdown is fixed in dollars: 5 micros/$200 and 1 mini/$400 are both "2 losses", but they
pass 95.0% vs 81.5%. The floor is patience — 3 micros needs 21.7 days and starts timing
out.

### Dynamic sizing on running P&L: tested, and it loses

| rule | pass % | bust % | days |
|---|---:|---:|---:|
| **flat 1 mini, stop −400** | **81.8** | 18.2 | 6.4 |
| → 0.5× at −150 | 75.1 | 24.9 | 7.1 |
| → 0.25× at −200 | 80.9 | 19.1 | 7.1 |
| → 0× at −150 | 34.7 | 65.2 | 11.4 |
| ×1.5 once +300 | 67.4 | 32.6 | 5.5 |
| ×2 once +300 | 64.0 | 36.0 | 5.0 |
| ×1.5 once +800 | 81.4 | 18.6 | 6.0 |
| → 0.5× at −200 **and** ×1.5 at +500 | 79.8 | 20.2 | 6.5 |
| flat 7 micros, stop −300 | **87.7** | 12.2 | 9.3 |

**Nothing beats flat.** The best de-escalation ties it; every escalation loses; the
combination loses. Two reasons:

1. **The daily stop already carries the state-dependence.** The behaviour audit found
   expectancy goes *negative* below −$500 on the day. The correct response to negative
   expectancy is **zero** size, not half — half-size in a losing state is still losing,
   just slower, and it costs you the days you need to reach target.
2. **Pressing when green fights the ratchet.** A bigger winning day raises the EOD
   high-water mark, which raises the trailing floor by the same amount — then one ordinary
   red day is fatal. Escalation buys a higher floor, not a bigger cushion.

**The one dynamic that IS right: size inversely to stop distance, so dollar risk is
constant.** A setup needing an 80-tick stop gets 2–3 micros, not 5. That is risk
normalisation, not P&L-reactive sizing, and it is what keeps the "2–3 losses to the stop"
ratio true trade by trade.

### The framing that generalises

> **On a prop account your capital is the drawdown, not the balance.** $2,000, not $50,000.

Risk ~**5% of the MLL** per trade ($100), set the daily stop at ~**10–15% of the MLL**
($200–300), and you get the 2–3 losses ratio automatically on any account size.

## 5c. Profit target and trailing stop — re-run tick-by-tick on the real entries

Method: take the **249 prop entries** that have a cached tick tape (13 of 16 trade-days),
hold entry price/direction/time fixed — those are what you actually chose — and simulate
every exit policy forward on the tape to a 2-hour cap. Costs $7/contract round turn.

**Actual on these 249 trades: +$8,111, $32.6/trade, 49.4% win, average win 58 ticks.**

### Fixed target (stop held at your median 40 ticks)

| target | net | per trade | win% | vs actual |
|---:|---:|---:|---:|---:|
| 20 tk | **−4,599** | −18.5 | 62.2 | −12,710 |
| 30 tk | **−3,849** | −15.5 | 54.6 | −11,960 |
| 40 tk | **−1,599** | −6.4 | 49.8 | −9,710 |
| 60 tk | +3,801 | 15.3 | 44.2 | −4,310 |
| 80 tk | +5,601 | 22.5 | 38.2 | −2,510 |
| **120 tk** | **+18,801** | **75.5** | 34.9 | **+10,690** |
| 160 tk | +13,801 | 55.4 | 26.1 | +5,690 |

**Any target at or under ~80 ticks loses money outright.** At a 40-tick stop, a 40-tick
target is 1:1 against a coin-flip win rate — costs alone sink it. The optimum is **~120
ticks, three times the stop.** The stop×target grid agrees: every cell with a target ≤40
is negative regardless of stop width.

### Trailing stop

| policy | net | per trade | win% | avg win |
|---|---:|---:|---:|---:|
| trail 40 from entry | +32,406 | 130.1 | 41.8 | — |
| trail 60 from entry | +28,131 | 113.0 | 38.6 | 117 tk |
| **trail 20, armed at +60** | **+35,146** | **141.1** | 44.2 | — |
| trail 30, armed at +60 | +34,661 | 139.2 | 44.2 | 117 tk |
| target 120 (best fixed) | +18,801 | 75.5 | 34.9 | 123 tk |
| ACTUAL | +8,111 | 32.6 | 49.4 | **58 tk** |

A trail beats every fixed target, and *arming* it after the trade has moved +60 beats
trailing from entry. The gap between 58-tick average wins and 117-tick average wins is the
sub-30-second finding from the behaviour audit, priced: roughly **$27k left on the table
across 249 trades.**

### ⚠ But it does not replicate across halves

| policy | H1 (n=129) | H2 (n=120) |
|---|---:|---:|
| target 120 | +16,883 (+130.9/trade) | +1,918 (+16.0) |
| trail 60 | +29,493 (+228.6) | **−1,362 (−11.3)** |
| trail 30 armed 60 | +34,033 (+263.8) | +453 (+3.8) |
| ACTUAL | +7,038 | +1,073 |

The entire advantage sits in the first half — late May / early June, the stretch where the
month split already showed longs at +$161/trade. **Letting winners run is a bet on trend
persistence, and this sample bought that bet in one favourable regime.** Treat the +$35k
as an upper bound, not an expectation.

### Under the account constraint, the gain is modest

Feeding each policy's per-trade P&L back through the LucidPro sim (5 micros, −$300 stop):

| policy | pass % | bust % | days |
|---|---:|---:|---:|
| ACTUAL | 89.0 | 10.8 | 11.6 |
| **target 120** | **95.4** | 4.5 | 8.6 |
| **trail 60** | **94.9** | 5.1 | 6.7 |
| trail 30 armed 60 | 93.5 | 6.4 | 6.6 |
| target 80 | **66.0** | 33.6 | 10.7 |

Quadrupling gross P&L buys **+6 points of pass rate** — because size and the daily stop
already do the heavy lifting. But note target 80 *destroys* the account (66%): a target
that cuts winners while the stop stays wide inverts the payoff ratio.

### The funded trap: fat days break the consistency rule

| policy | worst day | best day | median day | std |
|---|---:|---:|---:|---:|
| ACTUAL | −2,006 | 3,253 | 632 | 1,651 |
| target 120 | −2,070 | 12,285 | 358 | 3,236 |
| trail 30 armed 60 | −3,146 | **21,800** | 237 | **6,297** |

Trailing produces a *lower median day and a much fatter tail* — lottery-ticket
distribution. In evaluation that is fine. **Once funded, a single $21,800 day would need
$54,500 of cycle profit to satisfy the 40% consistency rule**, stranding the payout
entirely. This is the same eval/funded inversion as §5: in eval let it run, once funded cap
the day.

### Verdict

- **Never use a target under ~100 ticks.** Below 80 they are all net-negative; that is the
  single most actionable number in this document.
- **Eval: trail 30 ticks, armed once +60 in profit**, initial stop 40. Or a flat 120-tick
  target if you prefer a set-and-forget bracket — nearly the same pass rate, less babysitting.
- **Funded: 120-tick target**, plus the $1,000 daily profit lock. Do not let the trail run
  into a consistency-breaking day.
- Consistent with your own prior work: [`atr-trailing-stop-study`] killed *ATR-scaled*
  trails and concluded the geometry is absolute — a fixed-tick trail is exactly that, and
  is not contradicted by it.

**What this simulation ignores:** holding a winner for 2 hours blocks the entries you
actually took next, and the sim keeps all 249 entries regardless — so the trail figures are
optimistic. It also charges only the $7 round turn, not the 1-tick spread + queue from
`fill-model-verification.md`, which every extra stop-out would pay.

## 5d. Stop width 50 and 100 ticks at 1 mini — and two revisions

### Per-trade: a 50-tick stop beats 40, and 100 is too wide for targets

Net $ over the 249 entries, stop × target:

| target ↓ / stop → | 40 tk | 50 tk | 100 tk |
|---:|---:|---:|---:|
| 50 tk | **−549** | **−549** | **−1,299** |
| 60 tk | 3,801 | 4,351 | 3,301 |
| 80 tk | 5,601 | 6,101 | 201 |
| 100 tk | 11,201 | 12,701 | 4,701 |
| **120 tk** | 18,801 | **21,501** | 13,801 |
| 150 tk | 13,301 | 14,951 | 15,951 |

**A 50-tick target is net-negative at every stop width** — including at a 100-tick stop,
where it is 0.5R. That extends the earlier floor: the problem is not the ratio, it is the
absolute distance. 120 ticks stays the optimum, now at a 50-tick stop.

### The wider stop is what makes the edge replicate

| policy | H1 (n=129) | H2 (n=120) |
|---|---:|---:|
| s40 / target 120 | +130.9/tr | +16.0/tr |
| **s50 / target 120** | **+108.0/tr** | **+63.1/tr** |
| **s100 / target 120** | **+47.9/tr** | **+63.5/tr** |
| s50 / trail 30 @60 | +255.6/tr | +16.3/tr |
| s100 / trail 30 @60 | +227.3/tr | +47.6/tr |
| ACTUAL | +54.6/tr | +8.9/tr |

This is the most robust thing found all session: **widening the stop from 40 to 50–100
ticks turns a lopsided H1-only result into one that holds in both halves** — the s100
version is actually *stronger* in H2. The 40-tick stop was being knocked out by noise in
the less trendy second half. Trails stay H1-concentrated at every stop width, which is
further reason to prefer a fixed target over a trail.

### Account-level: 100 ticks is unusable, and 1 mini still loses to micros

Pass % on the LucidPro eval:

| policy | 1 mini @ best daily stop | 5 micros @ best daily stop |
|---|---:|---:|
| s40 / target 120 | 89.2 ($750) | **98.1 ($500)** |
| **s50 / target 120** | 89.3 ($1,500) | **97.7 ($500)** |
| s50 / trail 30 @60 | 73.8 ($750) | 94.7 ($300) |
| s100 / target 150 | 57.9 ($300) | 74.3 ($1,000) |
| s100 / target 120 | 49.7 ($300) | 66.4 ($1,500) |
| ACTUAL | 85.8 ($300) | 89.1 ($300) |

**A 100-tick stop is $500 of risk against a $2,000 max loss limit — four losses to a dead
account — and it fails at every size and every daily stop.** Its per-trade robustness is
real but irrelevant: this account is too small to hold that geometry. **1 mini caps out
around 89% whatever the policy**, against 98% at 5 micros.

### ⚠ Two revisions to earlier recommendations

1. **Stop 40 → 50 ticks.** Same account performance (97.7 vs 98.1), better per-trade net
   (+21,501 vs +18,801), and materially better split-half behaviour. Take the robustness.
2. **Daily stop $300 → $500.** This one is a genuine correction. The $300 figure was fitted
   against *your actual exits* — a 49% win rate taking small wins. A 120-tick target wins
   only ~40% of the time, so **you routinely take three or four losses before a winner
   lands**, and a $300 daily stop truncates exactly the days that would have paid. At
   s50/target120 the daily stop sweep runs 88.9 ($300) → **97.7 ($500)** → 97.3 ($750) →
   93.7 ($1,000).

> **The daily stop is not independent of the exit policy.** Change the win rate and the
> right daily stop moves with it. Rule of thumb from this grid: the daily stop wants to be
> **~4× per-trade risk** for a low-win-rate/high-payoff policy, against ~2–3× for the
> high-win-rate/small-win style you were actually trading.

## 6. The knob set

| knob | evaluation | funded | source |
|---|---|---|---|
| `risk_per_trade` | **$125** (5 MNQ at a 50-tick stop) | same | §5b, §5d |
| `daily_loss_stop` (hard disarm) | **$500** — ~4 losses deep | **$500** | §5d — moves with the exit policy |
| `daily_profit_lock` | **off** | **$1,000** | §5 — inverts between stages |
| `max_qty` | **5 micros** (10 micros = 1 mini) | 5, → 10 only above $52,100 | §5b |
| size vs stop distance | inverse — hold $100 constant | same | §5b |
| size vs running P&L | **flat, never dynamic** | same | §5b — every variant lost |
| rate governor (min gap between entries once $500 down) | on | on | behaviour audit §2e |
| `require_bracket` | on | on | behaviour audit |
| `stop_ticks` | **50** (clamp [40, 60]) | same | §5d — 100 is unusable on a $2,000 MLL |
| `min_target_ticks` | **100** — refuse anything tighter | **100** | §5c/§5d — every target ≤80 is net-negative, 50 is negative at any stop |
| exit policy | **fixed 120-tick target** | **fixed 120-tick target** | §5d — trails never replicate across halves |
| one-click | off | off | existing `set_tag` behaviour |

Expected outcome on the two-attempt budget:

| plan | P(at least one pass) | P(both fail) | days/attempt |
|---|---:|---:|---:|
| no self-imposed stop, 1 mini | 81.1% | 18.9% | 4.0 |
| $400 daily stop, 1 mini | 96.6% | 3.4% | 6.3 |
| **$300 daily stop, 5 micros** | **98.9%** | **1.1%** | 13.0 |
| $200 daily stop, 5 micros | 99.7% | 0.3% | 13.4 |

The micro plan trades **~7 extra days per attempt for ~2/3 of the remaining bust risk.**
With ~21 trading days in a month and busts landing early (median day 3–6), two sequential
attempts still fit — but only just, so treat 13 days as the reason to take the first
attempt seriously rather than as spare room.

---

## 7. What this simulation does not know

Read these before trusting the point estimates:

1. **It resamples 19 days independently.** The behaviour audit found that being down money
   changes how you trade — so bad days plausibly *cluster*, and IID resampling cannot
   produce that. **Real bust rates are therefore higher than shown**, and the tight-stop
   recommendation gets *stronger*, not weaker, since its whole job is breaking that chain.
2. **It assumes your prop edge is real.** That book is +$437/day over 19 days on 6 accounts,
   4 of which were net losers. If the true mean is nearer zero, every pass rate here falls.
3. **19 days is a thin bootstrap.** It can only resample day-types you have already had; a
   worse day than −$2,037 never appears.
4. **The daily stop is modelled as perfectly obeyed.** That is the entire point of putting
   it in `build_intent` server-side rather than in your head — but a rule that gets
   overridden delivers the $1,200 row, not the $400 row.
5. **The eval consistency rule is unresolved** (§1). If it applies, use the funded column.
