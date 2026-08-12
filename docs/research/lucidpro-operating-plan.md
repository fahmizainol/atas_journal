# LucidPro 50K — operating plan

Derived from [`manual-trade-behaviour-audit.md`](manual-trade-behaviour-audit.md) (2,538 of my
own round trips) and [`lucidpro-50k-survivability.md`](lucidpro-50k-survivability.md) (a
bootstrap of the real 19-day prop book through Lucid's account mechanics). Every number here is
fitted to **this** book, not to generic prop advice.

The rules below are enforced in the app — see *Enforcement* at the end. A rule that can be
overridden by clicking twice delivers the outcome of the rule that was overridden, so where the
data supports it the app refuses rather than warns.

## The account

| | Evaluation | Funded |
|---|---|---|
| Start / target | $50,000 / **+$3,000** | — |
| Max Loss Limit | **$2,000, end-of-day trailing** | same |
| Trail stops at | $52,100 → floor locks at $50,100 | same |
| Lucid's daily limit | $1,200 soft (session lockout) | $1,200 → LucidScale above $52,100 |
| Consistency | **none — a one-day pass is allowed** | **40%** of cycle profit |
| Min trading days | none | — |
| Payout | — | 3-day cycle, min $500, cap $2,000 then $2,500 |

> **The one fact to keep in mind:** Lucid's daily limit is **60% of the entire drawdown**. Two
> sessions at their limit and the account is gone. Their DLL is not a risk control — mine is.

## The setup

| | Evaluation | Funded |
|---|---|---|
| Instrument | **NQ (1 mini)** | NQ |
| Size | **1** | 1 → 2 only above $52,100 |
| Stop | **50 ticks** = **$250** | same |
| Target | **120 ticks** = **$600** | same |
| Never a target below | **100 ticks** | 100 ticks |
| Max risk per entry | **$250** | $250 |
| Daily stop | **−$500** → done for the day | −$500 |
| Slow-down trigger | **−$300** | −$300 |
| Daily profit lock | **none** | **+$1,000** → done for the day |

Sizing is **flat**. Never scale on how the day is going — every variant of that was tested and
every one lost. The only thing that changes size is **stop distance**: if a setup needs an
80-tick stop, it does not get taken at this size, because the risk ceiling is a dollar amount.

### Why 1 mini and not 5 micros

The first version of this plan said 5 MNQ, on the reasoning that the $2,000 MLL is fixed in
dollars so smaller absolute size survives longer. Two things overturned it.

**Commission.** Lucid charges **$1.00 per round turn on MNQ** (measured off a real fill: gross
−$2.50, net −$3.50 on one contract) against **$7.00** on NQ. That sounds like micros win, but
per unit of exposure it is the other way round — the mini gives up **1.4 ticks** a round turn and
five micros give up **2.0**.

**The re-simulation, once it was gated on the tape** (see *A correction* below): at 50/120 the
edge is **+$39/trade at 1 NQ** and **+$18/trade at 5 MNQ**. Pass probability comes out a tie —
90.8% against 91.0% — and 1 mini gets there in a **median 5 sessions against 9**. Ten micros,
which is the same exposure as one mini, lands at 89.5%: exposure is what matters, and at equal
exposure the mini is the cheaper way to hold it.

There is a third reason that has nothing to do with money. The chart, the tick cache, the shadow
strategies and the signals are all **NQ**. An order goes out on whatever contract the feed is on,
so a plan written in MNQ while the chart runs NQ is a live hazard — the same 5 on the same
50-tick stop is $125 of micros or $1,250 of minis. Trading 1 NQ makes the whole stack one
contract. (The `max_risk_usd` guardrail exists to catch this regardless.)

## The day in numbers

- One loss: **−$250**. One win: **+$600**. That is **2.4 : 1**, break-even at a 29% win rate.
- Measured win rate on a 120-tick target: **~35%**. Expectancy ≈ **+$39/trade**, ~**$660/day**.
- **Two losses = −$500 = the day is over.** One win covers two losses and a third of a third.
- $3,000 target ÷ $660 ≈ **4–5 sessions**.
- The $2,000 drawdown is **8 losses**. At 5 micros it was 16. This is what the extra speed costs.

## The three thresholds

| when | what happens |
|---|---|
| **−$300 on the day** | Slow down. Minimum **~2 minutes between entries** from here. No exceptions. |
| **−$500 on the day** | **Stop. Flat, disarmed, done** — the app closes the position itself. Not "one more to get back to −$300". |
| **+$1,000 (funded only)** | Stop. A bigger day poisons the 40% consistency rule. In evaluation, keep going. |

At 1 mini the daily stop lands on a number that was measured directly rather than derived: the
behavioural audit found expectancy flips sign **below −$500 on the day**, and it measured that on
this same 1-mini book. So the stop is not an approximation of the tilt threshold — it *is* the
tilt threshold, and it is two losses. (At 5 micros the same behavioural state arrives at −$250,
which is why that version of the plan needed a separate slow-down band to do the work.)

Below −$500 I am a **−$40/trade, 38%** trader; above it, **+$45/trade, 51%**. Same person, same
day. The threshold is not about willpower — it is where the measured expectancy flips sign, and
the −$300 slow-down exists to make the last stretch before it deliberate.

The daily stop is **not independent of the exit policy**. A 120-tick target wins only ~35% of the
time, so consecutive losses before a winner are routine and too tight a stop truncates the days
that pay. The gated sweep at 1 NQ: 90.8% pass at $250, **74.9% at $500**, 62.5% at $750, 53.8% at
$1,000.

**$250 is not the recommendation despite topping that sweep**, and the reason is worth keeping.
A $250 stop is one loss, and on this 12-day sample it works by cutting 8 of the 12 days after
their first trade and letting the other 4 run — which is ex-post day-picking, not a rule. $500 is
the level with a mechanism behind it: two losses, and the measured tilt threshold.

## Pre-session checklist

1. Symbol is **NQ**, size **1**, and the ticket says so. Check the *contract*, not just the
   quantity — the order goes out on whatever the chart is on.
2. Every entry goes out **with a bracket** — stop 50, target 120. No naked entries.
3. Yesterday's account balance noted, so today's trailing floor is known.
4. One line written down: what is being traded today and where the level is.

## During the session

**The one habit that costs money: trades that resolve inside 30 seconds.** They are about **half
of all entries**, they win **26%** of the time, and they cost the prop book **−$15,616** while
everything else made **+$23,918**. They are full stop-outs when wrong and 11-tick scraps when
right.

That cannot be fixed with an exit rule — 85% of the damage is the stop firing, not an early
manual exit. It is an **entry** problem, and the practical tell is pace:

| | green days | red days |
|---|---|---|
| trades taken | 15.8 | **15.3** — identical |
| gap between entries | **136s** | **76s** |
| share resolving <30s | 48% | **71%** |

**Volume is not the problem. Speed is.** On a red day the same number of trades gets taken in
half the time. Entering inside two minutes of the last one is the tell — before the P&L shows
it.

And the single most useful number in the whole study:

> A bad start that gets **slowed down** on: **−$147/day.**
> A bad start that gets **sped up** on: **−$803/day.**
> Same hole. The difference is entirely what happens next.

## Things not to do — already tested on this book

Each of these looked like an obvious rule and **is refuted by the data**. Do not reintroduce
them on instinct:

- **Don't wait after a loss.** Re-entering quickly is fine (+$10 to +$61/trade). A cooldown
  timer is folklore. (What is *not* folklore is the −$300 slow-down — that is indexed on the
  hole, not on the last outcome.)
- **Don't ban adding to a position.** Measured per leg, the adds are mildly *profitable*.
- **Don't count losing streaks.** No consecutive-loss effect exists once lookahead is removed.
  Count dollars, not losses in a row — three small losses leave you fine, one −$700 trade does
  not.
- **Don't cap the trade count.** Not supported on the funded book.
- **Don't take a small target.** Every target ≤80 ticks is net-negative; a 50-tick target loses
  even against a 100-tick stop. The problem is absolute distance, not the ratio.
- **Don't size up because a one-day pass is allowed.** It is — at 4 minis, two winners clear
  $3,000. It also drops the pass probability from ~98% to roughly 40–65%. There are two attempts
  a month, not ten.
- **Don't trail the stop.** It made more in backtest but only in one trending stretch
  (H1 +$29,493 / H2 −$1,362). The fixed target replicates across split halves; the trail does
  not.

## Direction note

Prop shorts lost money in **both** months (−$22.50 and −$23.50/trade) — including a stretch
where they won 65% of the time — while longs made +$10,831. Not a hard rule, and not enforced,
but shorts are being taken at a measured disadvantage.

## Stage differences

**Evaluation** — no consistency rule, no minimum days. The only job is reaching $53,000 before
the trailing floor catches up. Let winning days run; a profit cap actively *hurts* here (54.5%
vs 56.9% pass).

**Funded** — the 40% rule inverts it. A $1,500 day needs $3,750 of cycle profit to be payable; a
$1,000 day needs $2,500, which is about the payout cap. **Stop at +$1,000.** Also: stay above
$52,100 to request a payout, and the daily limit becomes LucidScale (60% of peak EOD profit).

## After each session

Log three numbers — they are the ones that predict the next day: **median gap between entries**,
**share of trades that resolved under 30 seconds**, and **whether the day traded below −$300**.
P&L is the outcome; these are the behaviour.

## A correction to the numbers this replaced

The first version of this plan, and the exit-policy section of
`lucidpro-50k-survivability.md`, were built on a re-simulation that did **not check the tape
agreed with the journal**. Re-run with that gate — the recorded entry price has to have actually
traded at the recorded instant, within 2 points — **122 of 296 entries fail it**:

- **47** have no tick file at all (2026-06-16, 07-01, 07-02);
- **75** are off-tape by 60–90 points with an *inconsistent* offset, so it is not a calendar
  spread and not a roll mislabel. **44 of those 75 are overnight fills.**

What survives is **174 entries over 12 sessions, 171 of them RTH**. Every figure in this document
comes from the gated book. The ungated totals in the survivability doc (+$18,801 on target-120,
+$34,661 on the trail) are simulated partly against prices that did not trade, and that section
needs re-running before any of it is quoted again.

A consequence worth stating plainly: this is now an **RTH plan**, because the overnight book is
what the gate mostly removed and there is no tape to check it against.

## Enforcement

The levels above are enforced server-side in `journal.live.routing` and `journal.live.broker`,
on the two paths that can reach an exchange. Three properties are deliberate:

- **The check runs at submit time, not only at review time.** A review token is minted before
  the order is sent and can be spent later, so a preview-only check would be bypassable by
  staging an order while still allowed and sending it after the day locked.
- **Guardrails never stand between me and the exit.** An order on the closing side that is no
  bigger than the position held skips the whole layer, including the shape rules. A discipline
  rule that could refuse a scale-out would, at the worst possible moment, be a rule that keeps
  me in a trade.
- **The stop is measured on equity, and it acts.** Realised plus what the open position is
  currently down — because the account's own drawdown does not wait for a loss to be booked. At
  the line the app cancels the working orders, exits the position and disarms, rather than only
  refusing the next entry. A rule that counted closed trades alone would sit silent through an
  $800 open loss and then refuse the order that was never the problem. `auto_flatten` turns the
  acting half off; the day still locks.
- **The daily lock latches.** Once −$500 is crossed the day stays over, even if a later winner
  brings the running total back above the line. That is the whole content of "not one more to
  get back to −$300".
- **Risk is checked in dollars, not contracts.** `max_risk_usd` refuses any entry whose
  stop × size × the contract's own dollars-per-tick exceeds $250. A quantity ceiling cannot do
  this job: the same 5 on the same 50-tick stop is $125 of MNQ or $1,250 of NQ, and the order
  takes its symbol from whatever the feed is on rather than from anything the ticket says.

The master switch is `LIVE_GUARDRAILS` in `.env`, and it defaults to **on**: unset means
enforced, and only an explicit `0`/`false`/`no`/`off` disables. That is the opposite polarity to
`LIVE_ROUTING` (where unset means *cannot trade*), and deliberately — both defaults fail toward
not losing money. It lives in the environment rather than in the UI because that is the feature:
turning the guardrails off should require leaving the chart, not a toggle reachable at 09:31
with a red P&L. Individual levels are app settings; setting any one to `0` disables that rule
alone.

Turning it off is loud. The chart's top bar carries a red chip whenever the layer is off, and
the order panel says so in full. A safety layer that is silently off is worse than one that was
never built, because it gets traded as though it were there.

**Honest caveat:** none of this is un-bypassable by whoever owns the machine. What it buys is
friction and visibility, not impossibility.

## What this plan assumes

- The prop edge is real: +$437/day over 19 days, across 6 accounts of which 4 lost money.
- Bad days do not cluster more than the model assumes — they probably do, which means the real
  bust rate is *higher* than 2% and the small size matters more, not less.
- Expected outcome on two attempts: **~94% at least one pass** at a $500 daily stop, ~4 days per
  attempt. That is 74.9% on a single attempt.
- **And the sample is thin.** 12 sessions survive the tape gate. Dropping the single best day
  (2026-05-27, +$5,618) takes the pass rate from 90.8% to 77.3% at a $250 stop, so one day in
  twelve is worth thirteen points. The split halves are positive both ways but lopsided —
  $65/trade in the first, $14 in the second. Treat the pass rate as an upper bound.
