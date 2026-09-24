# Which bracket survives the floor — LucidPro's EOD trail against LucidDaily's intraday one

**Date:** 2026-08-25
**Data:** 599 cached NQ RTH sessions, 2024-03-04 → 2026-06-30, × 4 random draws =
2,396 day-samples per arm. Entries are drawn **at random** through the session at
the pace the behaviour audit measured (16 a day, uniform, side a coin flip), so no
entry signal is involved anywhere.
**Script:** `data/research/replay-trail/bracket_survival.py`, driving the fill
primitives in `src/journal/replay_whatif.py`. The funded stage (§15) is
`funded_payouts.py`, which delegates every day back to the same gated walker.
Interactive: [`bracket-survival-visual.html`](bracket-survival-visual.html).
**Question:** which bracket geometry survives a prop drawdown floor, and how much
worse is LucidDaily's intraday floor — which follows the running equity peak
*including an open position* — than LucidPro's end-of-day one?
**Verdict:** at zero edge nothing survives either account: the mean day is
**−$159** and **92% of evaluations bust**. What the sweep does measure is real and
edge-independent. **LucidDaily costs a mean 2.0 points of pass rate and up to 9.2**,
and the geometries it punishes hardest are exactly the ones that carry large
*unrealised* profit — wide stops and breakeven-only trails. **A day profit goal is
the specific antidote: +$500 halves the mean penalty to 0.8 points and caps the
worst case from 9.2 to 2.8.** A trail is worth ~1 point more under the intraday
floor than under the end-of-day one. **Fixed-dollar sizing does not help at mini granularity** — it can only
act on the narrow stops at all (§7) — but **at micro granularity it is the largest
effect in the study**: `mnq150` passes +16 points better than 1 NQ *and* earns more
(§14.3), because 1 NQ is simply oversized for a $50K account.
**§12 adds a directional edge and the bracket ranking inverts end to end** — at
zero edge the least-trading bracket wins, with edge the most-harvesting one does.
**§15 goes past the evaluation to the stage that pays**, across all three funded
rulebooks: a funded month is worth 1–3 payouts and $1,350–1,700 at p = 0.70 and
nothing at all at a coin flip, and the *payout policy* — Pro's five-rung ladder,
Daily's cushion above the buffer, Flex's floor snap — is worth more than any
bracket knob in this document. **LucidFlex pays soonest and most reliably (day 7.3,
90% of months) and earns the least over a quarter while dying three times as
often**, because its missing buffer is charged as a floor jump instead.

---

## 1. What is being measured, and what is not

Entries are random. That is the design, not a limitation — it is the only way to
price a bracket without the answer being a claim about a setup. At zero edge the
mean day must come out at roughly minus the costs, and it does (−$159 at 1 NQ with
16 entries). Every number below is therefore about **geometry against a floor**,
never about whether a bracket makes money.

The consequence to keep hold of: a "pass" here is reaching +$3,000 before the
floor catches you, while bleeding costs the whole way. Pass rate correlates
**+0.89 with the arm's mean day** — so the ranking is mostly *how little an arm
bleeds*, and bleed is mostly *how many round turns it pays*. It is not a quality
ranking of brackets. Read the **floor cost** column, not the pass column.

Bust and pass sum to exactly 100.0 across every arm: no evaluation hit the 60-day
cap, so nothing is censored.

## 2. The walker is gated against the app's own engine

The trade walk is vectorised rather than tick-stepped, which makes it a second
statement of `run_sim`'s control flow. So it is not trusted on its own:
`--validate N` rebuilds the same random entries as a real order log, runs
`replay_whatif.run_flat` over it, and requires agreement trade for trade.

> **708 of 708 arm-days reproduce `run_flat` exactly.**

That gate earned its keep immediately — it caught two bugs that would have silently
skewed every number here:

- **Brackets are placed in whole ticks.** `SimPrefs` holds each leg as an integer
  tick count and resolves it to a price once, at placement. A 1.5R target on an odd
  stop has to round before it becomes a level; not rounding put the port an eighth
  of a point off the engine.
- **An entry drawn while a trade is still open is not a second order.** It is a
  gesture that never happened. Feeding those to the engine had it pyramiding.

## 3. What the intraday floor actually charges for

Both accounts hold $50,000, a $2,000 max loss limit and a $52,100 trail cap. The
only difference is *when the floor moves*: at a day close under LucidPro, and at
every new running high under LucidDaily — where the high includes a position you
still have on. The discriminating case, unit-checked directly:

| the day | LucidPro | LucidDaily |
|---|---|---|
| runs **+$1,500 unrealised**, closes **−$600** | alive | **dead** |
| only ever goes down, closes −$600 | alive | alive |
| runs +$1,500 unrealised, closes flat | alive | alive |

A day that only falls costs nothing extra — which is why peak-minus-close
overstates the penalty. What LucidDaily charges for is specifically **profit you
were up and gave back**.

At the baseline reading (1 NQ, −$500 self stop, no goal), across all 140 brackets:

| | pass pts |
|---|---:|
| mean floor cost | **2.0** |
| median | 1.4 |
| worst bracket | **9.2** |
| best bracket | 0.0 |

The arms punished hardest are exactly the ones that sit on unrealised profit:

| bracket | LucidPro | LucidDaily | floor cost | exits |
|---|---:|---:|---:|---|
| `ruler/none/be1r` | 22.0 | 12.8 | **9.2** | trail 45% · stop 51% |
| `t50/none/be1r` | 20.0 | 10.8 | **9.2** | trail 44% · stop 51% |
| `t75/none/be1r` | 21.8 | 13.5 | 8.3 | trail 42% · stop 50% |
| `t75/3r/none` | 21.0 | 14.2 | 6.8 | stop 73% · target 23% |
| `t50/1r/none` — preset **C** | 6.8 | 6.0 | 0.8 | — |

The pattern is mechanical: **no target + breakeven-only stop** is the shape that
lets a position run to a large unrealised high and then hand it back. That is the
top of the LucidPro column *and* the top of the floor-cost column at the same time.

## 4. The day profit goal is the LucidDaily antidote

Modelled as asked: the goal reads the **marked** path, so unrealised profit
touching it ends the day — flat, at that level. Over all 140 brackets:

| day goal | mean floor cost | worst | mean day |
|---|---:|---:|---:|
| none | 2.00 | 9.2 | −$159 |
| **+$500** | **0.82** | **2.8** | **−$85** |
| +$1,000 | 1.17 | 4.5 | −$129 |

**A +$500 goal roughly halves the mean penalty and cuts the worst case by
two-thirds.** The mechanism is direct: the floor is at its highest and most
dangerous exactly at the peak, and the goal takes you flat there, so the giveback
LucidDaily bills for never happens.

It moves LucidDaily's best configuration a long way:

| day goal | best for LucidDaily | LucidDaily | LucidPro | mean day |
|---|---|---:|---:|---:|
| none | `t75/3r/none` | 14.2 | 21.0 | −$59 |
| **+$500** | `t75/3r/none` | **23.2** | 24.8 | **−$17** |
| +$1,000 | `t75/3r/none` | 19.5 | 23.0 | −$24 |

$500 beats $1,000 on both counts. That is the same shape as the funded green-lock
in [`lucidpro-50k-survivability.md`](lucidpro-50k-survivability.md) §5, arrived at
from a completely different direction — there it was the 40% consistency rule, here
it is the intraday floor.

## 5. Stop width is a commission trade, and it cuts both ways

| stop | best · Pro | best · Daily | mean day | mean floor cost |
|---|---:|---:|---:|---:|
| 35 ticks | 20.0 | 11.8 | −$192 | **0.8** |
| vol ruler (median 40t) | 22.0 | 12.8 | −$169 | 1.9 |
| 50 ticks | 20.0 | 10.8 | −$145 | 1.6 |
| 75 ticks | 21.8 | **14.2** | **−$128** | **3.7** |

Wider stops bleed less — fewer round turns for the same number of entries — but are
punished more by the intraday floor, because a wide stop is what lets a position
accumulate unrealised profit to give back. **t35 has the lowest floor cost and the
worst bleed; t75 the reverse.** The vol ruler sits in the middle of both.

Worth knowing about the ruler itself (measured over 24 sampled sessions): the `s30`
developing median reads a **median 71 ticks at 09:31:30 and 27 by the close**,
across-day median 42. So "1.0× the vol ruler" is not one stop — it is a very wide
one at the open and a tight one late, and only 4 of 24 sessions settled above the
plan's fixed 50.

## 6. Trails hurt — but they hurt LucidDaily less

Against the same stop and target with no trail (n = 32 pairs each):

| trail | Δ pass · LucidPro | Δ pass · LucidDaily | relative |
|---|---:|---:|---:|
| breakeven at 1R | −1.7 | −0.7 | **+1.0** |
| trail 1R | −1.8 | −0.8 | **+1.0** |
| trail 0.5R, 0.5R step | −5.3 | −3.7 | **+1.6** |

Every trail costs pass rate in absolute terms — it converts winners into scratches
while the stop-outs stay. Note this **runs opposite to
[`replay-exit-whatif.md`](replay-exit-whatif.md)**, which found on the real book
that tightening exits recovered $6.3k and no-trail was worst. The two are not in
conflict: that study re-priced *my* entries, whose losers were the problem a tight
trail cuts, and it scored dollars; this one prices *random* entries and scores
reaching a target before a floor, where cutting winners short is what hurts. A
trail is worth more the worse the entries are. But the
relative column is consistently positive and grows with how aggressive the trail
is: **a trail is worth about a point more under the intraday floor than under the
end-of-day one.** That is the predicted mechanism, and it is real but second-order
next to the day goal.

## 7. Fixed-dollar sizing: tested where it applies, and no help

| sizing | best · Pro | best · Daily | mean floor cost | mean day |
|---|---:|---:|---:|---:|
| 1 NQ flat | 22.0 | 14.2 | 2.0 | −$159 |
| risk $150 | 22.5 | 14.2 | 2.0 | −$159 |
| risk $250 | 22.2 | 14.2 | 1.9 | −$163 |
| risk $400 | 21.8 | 14.2 | 2.6 | −$193 |

Best-LucidDaily is **14.2 for all four**, and the reason is that the sizer only ever
leaves one contract on the narrow stops:

| sizing | arms where size ≠ 1 | stop widths involved |
|---|---:|---|
| risk $150 | **2** of 140 | 35t |
| risk $250 | **26** of 140 | 40–41t (the ruler) |
| risk $400 | **67** of 140 | 35t, 40–41t |

Every `t50` and `t75` arm stays at one contract, because $400 ÷ (50t × $5 + $7) < 2.
So the axis is **inert for wide stops and live for narrow ones** — and where it is
live, it is a wash bought with bleed:

| arm (risk $400 vs 1 NQ) | pass | mean day |
|---|---|---|
| `ruler/1r/none` | 5.2 → **7.5** | −$171 → **−$224** |
| `ruler/1.5r/be1r` | 8.8 → **11.5** | −$167 → **−$215** |
| `ruler/1.5r/none` | 10.5 → 11.0 | −$148 → −$191 |

More contracts buy variance — slightly better odds of a lucky run at +$3,000 — and
strictly more commission. `risk250` mostly moves the wrong way outright (8.8 → 8.0
on `ruler/1.5r/be1r`, −$167 → −$188). Best-overall never improves.

The sizer's arithmetic is what bounds all of it:

| stop | cost a contract | risk $150 | risk $250 | risk $400 |
|---:|---:|---:|---:|---:|
| 27t (ruler, late session) | $142 | 1 | 1 | **2** |
| 35t | $182 | 1 | 1 | **2** |
| 42t (ruler, across-day median) | $217 | 1 | 1 | 1 |
| 50t | $257 | 1 | 1 | 1 |
| 75t | $382 | 1 | 1 | 1 |

> **$250 of risk at a 42-tick stop *is* one mini.** The dollar-risk sizer and the
> flat 1 NQ arm are, on this grid, mostly the same arm — which is why the only rows
> that differ at all are the `ruler` ones, where the reading drops near 27 ticks
> late in the session and `risk400` can find a second contract.

> **Where the sizer can act, it does not buy survival; where it might matter most —
> the wide stops that won §9 — it cannot act at all.** On a $50K account with a
> $2,000 max loss limit, any budget large enough to move a 50-tick stop off one
> contract would risk $500+ an entry, i.e. four losses to a dead account.

The untested part is therefore **micros**, where a $0.50 tick gives ten times the
granularity and the whole grid could size properly. That is an open question, not a
null — but it is narrower than "sizing is untested".

The self-imposed −$500 daily stop is worth **$75 a day** against letting the
account's own DLL run (−$159 vs −$234) but changes neither pass rate nor floor cost.

## 8. Dollar and fixed-tick targets

Dollar targets (`usd300`, `usd600`) divide by the size actually on, so they narrow
as size grows. At 1 NQ, `usd600` is $600 ÷ $5 = 120 ticks and therefore an exact
alias of `t120` — and it comes out exact: **max |difference| in pass rate across all
16 pairs is 0.0**, on both accounts. That is a clean internal check on the leg
arithmetic, since the two reach the same level by different code paths.

A dollar target only becomes distinctive when the size varies, and per §7 that
happens on the narrow-stop arms alone. There it does separate from the tick
equivalent, and slightly for the worse (mean **−0.12** pass points): aiming at a
fixed dollar figure with more contracts on means a nearer target and a worse payoff
ratio. **On the wide stops that won §9 it is exactly the tick target**, by
construction.

## 9. The winner, per account

Searching all 3,360 configurations per account, **both land on the same one**:

| | LucidPro | LucidDaily |
|---|---|---|
| bracket | `t75/3r/none` — 75t stop, 3R target (225t), no trail | same |
| settings | 1 NQ · −$500 daily stop · **+$500 day goal** | same |
| pass | **24.8%** (next distinct bracket 23.2) | **23.2%** (next 20.8) |
| bust | 75.2% | 76.8% |
| mean day | **−$17** | −$17 |
| median to resolve | 9 days | 8 days |
| exits | stop 73% · target 23% · open 4% | same |

It wins because **it trades least**: on a sample session it took 10 of the 16 drawn
entries against 14–15 for the tighter arms, because a 75-tick stop and a 225-tick
target hold a position long enough that later entries never land flat. $70 of
commission on the day instead of $105. Given ρ = +0.89 between pass and mean day,
fewest round turns wins — a cost result, not an edge result.

**The runners-up are where the accounts diverge.** LucidPro's second-best distinct
bracket is `t75/none/be1r` at 23.2% while bleeding **−$102 a day**: the end-of-day
floor does not care that the shape rides a large unrealised gain and gives it back,
so an expensive ride-it-out geometry survives anyway. LucidDaily's top eight are
almost all **defined-target, no-trail** shapes (`3r`, `2r`, `1.5r`, `t120`,
`usd600`), and that same `none/be1r` arm falls from 23.2 to 20.0. Directionally:
**LucidPro tolerates letting it run; LucidDaily wants a target named and taken.**

Keep it in proportion — 75% of these evaluations still bust. This is the least-bad
geometry on a book with no edge, not a way to pass.

## 10. The trades themselves — win rate and the cost drag

Win rate is not account-dependent: the floors decide when you die, not how a trade
resolves. Per trade, net of the $7 round turn and the spread, over 150 sessions × 2
draws at 1 NQ, on the raw stream (no daily stop, no goal):

| bracket | trades | win% | avg win | avg loss | break-even win% | short by | exp/trade |
|---|---:|---:|---:|---:|---:|---:|---:|
| `t75/3r/none` ← §9 winner | 2,597 | **26.6** | $1,016 | −$383 | 27.4 | −0.8 | −$11.4 |
| `t75/2r/none` | 2,947 | 34.0 | $708 | −$383 | 35.1 | −1.1 | −$12.2 |
| `t75/t120/none` | 3,130 | 38.2 | $577 | −$383 | 39.9 | −1.7 | −$16.3 |
| `t75/1.5r/none` | 3,196 | 39.9 | $540 | −$383 | 41.5 | −1.6 | −$14.6 |
| `t75/none/be1r` | 2,072 | 45.8 | $399 | −$354 | 47.0 | −1.2 | **−$9.4** |
| `ruler/none/trail1r` — **A** | 4,209 | 46.1 | $211 | −$207 | 49.5 | −3.4 | −$13.9 |
| `ruler/1.5r/be1r` — **B** | 4,376 | 46.8 | $208 | −$209 | 50.1 | −3.3 | −$14.0 |
| `ruler/1r/none` — **C** | 4,430 | 48.0 | $200 | −$217 | 52.0 | −4.0 | −$17.1 |
| `t50/1r/none` | 4,069 | 48.9 | $242 | −$261 | 51.9 | −3.0 | −$15.3 |
| `t35/1r/trail.5r` | 4,486 | **56.9** | $86 | −$158 | 64.8 | **−7.9** | **−$19.0** |

The win rates are what the geometry dictates — 1R ≈ 48%, 1.5R ≈ 40%, 2R ≈ 34%,
3R ≈ 27% — which is the coin flip behaving, and a useful check that nothing in the
walk is inventing edge. Every arm is negative; no bracket rescues a zero-edge entry.

Two things this shows that the pass table cannot:

- **The "short by" column is the cost drag, and it shrinks as trades get bigger.**
  `t75/3r/none` misses break-even by 0.8 points, because $7 against a $1,016 average
  win is nothing; `ruler/1r/none` misses by 4.0 and the tight trail by **7.9**. Same
  costs, smaller trades, far bigger bite. It is §9's mechanism seen per trade.
- **The highest win rate is the worst arm.** `t35/1r/trail.5r` wins 56.9% and loses
  the most money — $86 average wins against $158 average losses, because a tight
  trail converts winners into scratches while every stop-out stays full size.
  **Win rate on its own is worthless here.**

These `/day` figures are more negative than §9's, which apply the −$500 daily stop
and the +$500 goal: `t75/3r/none` is −$99 a day raw against −$17 truncated.

## 11. Caveats

- **The goal is modelled optimistically.** A day that touches the goal books at
  exactly the goal, i.e. it assumes you get out at that level. The unmodelled exit
  is ~1 tick of spread plus commission — about $12 a contract against a $500 book,
  so ~2%. It does not move the finding, but the goal arms' `$/day` is flattered by
  roughly that much.
- **Days are resampled i.i.d.**, so serial correlation between real sessions is gone
  by construction. Same frame as the survivability bootstrap; it will understate
  streaks in both directions.
- **The mechanism is edge-independent; the magnitudes are not.** A real entry edge
  changes the shape of the day, and with it how much unrealised profit is carried.
  The direction of every comparison here should hold; the absolute pass rates would
  not.
- **`none/*` arms are only 12 of 140** at the baseline reading, so the "no target"
  comparison in §3 rests on a small set.
- **Only the `s30` ruler bucket was run.** That is production's default
  (`volRuler.ts:127`), but the ticket also toggles **500-tick and 15-second**
  buckets and neither is priced here. A volume clock in particular would read the
  open differently from the wall clock, which is where §5 found the ruler most
  extreme — so the `ruler` rows are a statement about the 30-second reading, not
  about the vol ruler in general.
- Nothing here has been re-run on a second half of the corpus. Treat the ordering as
  established and the exact figures as one sample.

## 12. Addendum — introducing an edge, and the ranking inverts

**Run:** `--edge`, → `bracket_survival_edge.json`. 45 brackets × `p ∈ {0.50, 0.60,
0.65, 0.70}` × horizon ∈ {1, 2, 5, 15, 60} min, 599 sessions × 2 draws, 10,800 rows.

The entry side is chosen by a **directional oracle**: with probability `p` it takes
the side the tape is on `H` minutes later. It is a dial, not a signal. The side is
drawn once per (day, seed, cell) *before* the arm loop, so every bracket in a cell
trades an identical entry list. The horizon is **swept rather than fixed**, because
it is not neutral — a 15-minute view cannot help a position a 0.5R trail scratches
in ninety seconds — so it encodes how long the edge persists.

**Calibration against the reviewed book.** Only the 93 trades carrying a review
grade were used (the *grade itself* never filters — it is hindsight-only by design,
A/B are 100% winners and D is 0%, so selecting on it would be circular). Those 93
look like a fair sample of the replay book: median duration 35.4 s vs 35.1 s, same
size profile, same date spread, no size cherry-picking. They net **+$53.47 a trade
— but the 95% CI is −$8 to +$115**, so the edge does not separate from zero. On the
plan's own `t50/t120`, p = 0.65 buys about +$15..$39 a trade, which puts +$53 near
**p ≈ 0.68–0.70**. Hence a ladder that brackets it rather than a single target.

### The winner flips, monotonically

Best bracket at H = 2 min, −$500 stop, no goal:

| p | best · LucidPro | `t75/3r/none` (the §9 winner) | `ruler/1r/none` (preset C) |
|---|---|---:|---:|
| 0.50 | `t75/none/be1r` 22.8 | 17.8 | **4.8** |
| 0.60 | `ruler/1r/be1r` 39.2 | 29.8 | 38.0 |
| 0.65 | `ruler/1r/be1r` 65.5 | 39.0 | 65.0 |
| **0.70** | **`ruler/1r/none` 87.2** | 44.2 | **87.2** |

Mean LucidPro pass by target width — the ordering **reverses end to end**:

| target | p=0.50 | p=0.60 | p=0.65 | p=0.70 |
|---|---:|---:|---:|---:|
| 1R | **6.0** | 34.2 | 57.7 | **79.6** |
| 1.5R | 8.3 | 35.0 | 54.6 | 70.8 |
| t120 | 11.4 | 34.9 | 51.5 | 64.1 |
| 3R | 12.4 | 29.8 | 46.6 | 56.6 |
| none (trail only) | **16.0** | 29.4 | 39.2 | **48.0** |

> **At zero edge every trade is pure cost, so the best bracket is the one that
> trades least — wide stop, far or no target. With edge every trade is +EV, so the
> best bracket is the one that harvests most often — a 1R target.** The crossover
> sits near p ≈ 0.55–0.58. The §9 winner is the *worst* guide to what to run if the
> edge is real, and vice versa.

### The intraday floor gets more expensive the better you trade

| p | mean LucidPro pass | floor cost (pts) | as % of base |
|---|---:|---:|---:|
| 0.50 | 10.3% | +3.55 | 34.5% |
| 0.60 | 33.0% | +8.96 | 27.1% |
| 0.65 | 51.0% | +13.16 | 25.8% |
| 0.70 | 65.5% | +13.42 | 20.5% |

In **absolute** pass points LucidDaily costs nearly four times more at p = 0.70 than
at p = 0.50 — better trading accumulates more unrealised profit, which is exactly
what the intraday floor bills for. As a *fraction* of the base it shrinks. Both are
true; the absolute column is the one that decides anything.

### The day goal survives the whole range, as a LucidDaily instrument

Best pass at H = 2 min, by goal:

| goal | p=0.50 Pro/Daily | p=0.65 | p=0.70 |
|---|---|---|---|
| none | 22.8 / 16.0 | 65.5 / 53.0 | 87.2 / 77.5 |
| **+$500** | 24.5 / **22.2** | 61.5 / **56.0** | 87.5 / **84.8** |
| +$1,000 | 30.2 / 25.0 | 64.5 / 58.0 | 84.8 / 77.0 |

At p = 0.70 the +$500 goal is worth **+7.3 points to LucidDaily and +0.3 to
LucidPro** — it buys floor protection for almost no upside. At p = 0.65 it costs
LucidPro 4 points while still gaining LucidDaily 3. The §4 conclusion holds across
the edge range: **the goal is a LucidDaily instrument, not a general one.**

### Sizing under edge — it earns more and passes less

The main edge run fixes size at 1 NQ. That was justified by §7, whose reasoning does
*not* carry: at zero edge extra size is pure commission, but with an edge it also
harvests more edge, so the null had to be re-tested rather than assumed. Re-run with
`--keep-sizings` at H = 2 min (`bracket_survival_edgesize.json`, 6,480 rows):

| sizing | p=0.50 Pro/Daily · $/day | p=0.65 | p=0.70 |
|---|---|---|---|
| 1 NQ | 22.8 / 16.0 · −$147 | **65.5 / 53.0** · +$248 | **87.2 / 77.5** · +$414 |
| risk $150 | 22.8 / 16.0 · −$147 | 65.0 / 52.5 · +$247 | 87.0 / 76.8 · +$414 |
| risk $250 | 22.8 / 16.0 · −$153 | 64.0 / 50.0 · +$247 | 86.5 / 74.0 · +$416 |
| risk $400 | 22.8 / 16.0 · −$167 | 59.5 / 50.0 · **+$261** | 83.5 / 73.8 · **+$442** |

> **Sizing up makes more money and passes less often.** At p = 0.70 `risk400` earns
> +$442 a day against 1 NQ's +$414, while pass falls 87.2 → 83.5 (Pro) and
> 77.5 → 73.8 (Daily); across the arms where the sizer actually bites, mean pass
> drops **−7.4 points**.

The floor does not care about the mean, it cares about the path. Extra size scales
the *variance* of the equity path as well as its drift, and a drawdown floor is a
variance constraint — you reach the target faster when you survive and die before
getting there more often. So fixed-dollar sizing fails at every edge level tested,
for two different reasons: at p = 0.5 size is pure commission, at p = 0.70 it is
variance against a fixed floor. **`$/day` rising while pass falls is the signature
that variance, not expectancy, binds these accounts.**

### Is the vol ruler worth it? A fixed 40t says no

The ruler beats `t50` and `t75` once there is edge, but its median reading **is**
40 ticks — so the win could just be the width. `t40` was added as the control
(`bracket_survival_t40.json`, 2,124 rows). Mean over 8 shapes, H = 2 min:

| p | ruler − t40 |
|---|---:|
| 0.50 | +2.79 pts |
| 0.65 | +2.15 pts |
| 0.70 | **−1.00 pts** |

Small, and it **flips sign** at the edge level the reviewed book implies. At 400
bootstraps the SE on a pass rate near 85% is ~1.8 points and the per-shape spread at
p = 0.70 runs −7.0 to +3.0, so **the ruler and a fixed 40 ticks are
indistinguishable**. The width effect is an order of magnitude larger — at p = 0.70
on `1r/none`: t40 85.8, ruler 87.2, t50 83.5, **t75 73.0**.

The one real point for the ruler: it **earns materially more per day** for the same
pass rate — +$500 against t40's +$362 on `1r/none` at p = 0.70, ~38% more. The
variance signature again.

> **Superseded by §14.1.** "Indistinguishable" was an artifact of comparing group
> means. Pairing each ruler arm against the *same shape* at a fixed width removes
> the arm-to-arm variance, drops the SE from ~1.8 points to ~0.2–0.5, and the
> difference turns out to be real, larger, and sign-flipping. Read §14.1.

## 13. Addendum — time of day, and what restricting the session actually buys

Run: `--windows '*' --edge-p 0.5,0.65,0.7 --edge-h 2 --seeds 8 --max-days 200`
→ `bracket_survival_window.json`, 12,744 rows over the same 599 sessions.

**Two design choices decide what these numbers mean.**

*No entry before 09:35, in every window including the full-day control.* The ruler
is readable at 09:31:30 — three closed 30s bars — but it is still warming up, and
its developing median reads ~71 ticks there against a ~42 across-day median. So the
control here is **not** the one in §1–§12, which drew from the bell; the window
comparison is internal to this run and its control was re-run alongside it.

*Entries are pace-matched, not count-matched.* A window gets its share of the
16-a-day rate, so `h1` draws ~2 entries and `pm` ~10. That is the honest version of
"only trade this window" — trading one hour a day really is fewer trades — but it
means `all` vs `h1` mixes *when* with *how often*, and §2 already showed how hard
trade count alone moves the pass column. **So the experiment is the three one-hour
windows**: 09:35–10:35, 12:00–13:00, 15:00–16:00, same pace, same count, same
warmup. Any gap between those three is the hour and nothing else.

Differences below are **paired per arm** — same bracket, same edge level, same
account — over 118 pairs a cell, which is far tighter than comparing group means.

### The hour is real, but it is not "the first hour"

| paired difference | pass points | sign consistency |
|---|---|---|
| `h1` − `lastH` | **+5.74 ± 0.40** | 81% |
| `mid` − `lastH` | **+4.98 ± 0.26** | 85% |
| `h1` − `mid` | +0.76 ± 0.41 | 64% |

**The last hour is the bad one; the first hour and the lunch hour are
indistinguishable from each other.** `h1` − `mid` flips sign across edge levels
(+2.88 at p = 0.5, −0.54 at p = 0.65, −0.05 at p = 0.70) at 53–60% consistency,
which is noise. `mid` − `lastH` reaches 98% consistency at p = 0.70.

This is *not* what [`drift-fade-poc-price-action.md`](drift-fade-poc-price-action.md)
found. That study said the first hour carries the net on a real setup; this one says
that on **random entries**, 15:00–16:00 is the hour to avoid and 12:00–13:00 is as
good as the open. The two are compatible — a setup can concentrate its edge in the
first hour while the underlying geometry is merely no-worse there — but nothing here
supports "trade the open because the open is special."

### Stop width by hour is a confound, and it is a quarter of the effect

The ruler's median reading is **57t in the first hour, 38t at midday, 31t in the
last** — the day shape from §5, now priced. So a per-trade dollar comparison across
hours is partly a comparison of bet sizes. Holding it fixed at `t50` shrinks the
first hour's apparent advantage:

| gross $/trade, p = 0.70 | `all` | `h1` | `mid` | `lastH` |
|---|---|---|---|---|
| ruler stop (width floats) | 21.61 | **60.48** | 42.71 | 31.05 |
| fixed `t50` (width held) | 22.94 | **45.76** | 43.16 | 34.03 |

About a quarter of "the first hour is better per trade" was simply betting more.

### Every single hour beats the full day, per trade

At a fixed `t50` and p = 0.70, `all` harvests **$22.94 gross a trade** against
$45.76 / $43.16 / $34.03 for the three hours. A window cannot beat the average of a
superset it belongs to unless the *unsampled* stretches are worse — so the drag is
**10:35–12:00 and 13:00–15:00**, which no one-hour window covers. `am` ($33.66) and
`pm` ($25.65) sit exactly where that implies.

### But restricting the session is a variance trade, not an edge trade

This is the finding that matters, and it is the same shape as everything else here.
`h1` against `all`, paired:

| | p = 0.5 | p = 0.65 | p = 0.70 |
|---|---|---|---|
| pass points | **+3.47** (90%) | +1.79 (64%) | −0.93 (41%) |
| $ / day | **+$89.67** (100%) | **−$65.99** (0%) | **−$111.77** (0%) |
| `day_p10` (bad-day tail) | **+$641.96** (100%) | — | **+$456.25** (100%) |

Read the sign consistency: at p ≥ 0.65 the full day earns more **unanimously — 0 of
118 pairs disagree** — and the first hour's bad day is $456–642 less bad, also
unanimously. The pass column is where they meet, and it decays with edge exactly as
§12 predicts: worth +3.5 points at a coin flip, worth nothing at p = 0.70.

**So one-hour-only buys a much smaller bad day for about a third of the daily
earnings, and the accounts price that as a wash.** It is a real option — the same
pass rate at a third of the exposure — but it is not an edge, and at the top of the
book's CI it is a small loss.

`am` − `pm` behaves the same way: +1.14 ± 0.32 overall, sign-flipping, and only
separating at p = 0.70 (+3.78 ± 0.66, 78%). Morning beats afternoon **only if the
edge is real**.

### The day goal still works, and works hardest where you trade most

The §4 result survives every window. LucidDaily's penalty at p = 0.70, no goal →
+$500 goal: `all` 13.04 → 5.10, `pm` 10.97 → 5.83, `mid` 6.71 → 4.46. The windows
that trade least carry the least unrealised profit and so had the least to give
back; the antidote is largest exactly where the disease is.

### What this run got wrong before it finished

A 40-session smoke showed the first hour at **+$14 a day at zero edge** against
−$94 at midday, on identical trade counts — which would have been a drift result.
It did not replicate: over 599 sessions the three hours are −$25.7 / −$27.8 / −$34.7,
all negative and much closer together. Same lesson as §11's "+$305 a day" — at ~2
trades a day a 40-session smoke is a rounding error.

### Censoring

72 of 12,744 rows exceed 1% timeout at the 200-day cap (max 8.2%, mean 0.1%),
all of them the tight-trail `t35/1r/trail.5r` arm inside the one-hour windows at
p ≥ 0.65 — the known-worst bracket in the thinnest windows. Those rows read
slightly worse than they are. No headline depends on them. The 60-day cap used in
§1–§12 would **not** have been safe here: it censored 3–5% of the one-hour arms,
which is why `--max-days` exists.

## 14. Addendum — stop width and sizing, conditioned

The three questions §13's window run made answerable: whether the ruler beats a
fixed width once the comparison is paired, what a fixed width does on a day the
ruler reads wide, and whether micros are the sizing lever §7 could not find.

### 14.1 · The ruler against fixed ticks, paired — and why it loses

The window run re-asks the question with 599 sessions, three edge levels, and each
ruler arm paired against the identical shape at a fixed width (same target, trail,
account, window, goal). n = 84 pairs a cell.

| ruler − fixed, pass points | p = 0.50 | p = 0.65 | p = 0.70 |
|---|---:|---:|---:|
| vs `t40` | **+2.52** (89%) | **+3.26** (80%) | **−3.76** (8%) |
| vs `t50` | −0.36 (32%) | **+4.55** (92%) | +0.27 (50%) |
| vs `t75` | **−5.66** (2%) | **+7.19** (95%) | **+13.07** (98%) |

Every cell except two is beyond 2 SE. So it is not a wash — but the ruler still
never wins, because **it is never the best width at either end**. At a coin flip
`t75` beats it by 5.7; at p = 0.70 `t40` beats it by 3.8. It is a hedge between the
two, and the study's central result is that the right width is a *bet on your own
edge*, not something to hedge.

**Worse, its adaptivity is wrong-signed.** §13 measured the ruler's median taken
stop by hour: **57t in the first hour, 38t at midday, 31t in the last.** Under a
real edge narrow wins — so the ruler is widest exactly when it should be tightest.
Split `ruler − t40` by window at p = 0.70 and that is the entire story:

| p = 0.70 | `h1` | `am` | `all` | `mid` | `pm` | `lastH` |
|---|---:|---:|---:|---:|---:|---:|
| ruler median stop | 57t | 50t | 40t | 38t | 34t | 31t |
| ruler − `t40` | **−6.56** | −3.32 | −3.76 | +1.28 | +3.11 | **+3.93** |

The ruler beats a fixed 40 precisely where it happens to read *below* 40, and loses
where it reads above. **On the time-of-day axis there is no adaptive skill in it** —
only the width it landed on. That is *not* true on the day-level axis; see §14.2,
which is the qualification this section needs.

Dollars go the other way, as always: pooled over every window and edge level the
ruler earns **+$24.88 a day more than `t40`** (79% consistent) while passing more
only because of the low-edge cells, and its bad-day tail is **$44.88 worse**. Same
signature as sizing (§12) — more expectancy, more variance, and the floor prices
variance.

**The one thing that would rescue it.** The oracle's `p` is constant across the
session by construction, so this sim has no mechanism by which a trader's hit rate
could *rise* with volatility. If your morning edge is genuinely better and not just
bigger, a stop that widens in the morning could be right, and nothing here would
detect it. That is a question for the reviewed book, not this sweep.

### 14.2 · A fixed 40 stops working on wide days — condition on the day, not the clock

§14.1 pooled across days. Conditioning each session on the ruler's **median reading
over 09:35–11:35** and taking entries in that same two-hour window (so the condition
is live for every trade it prices) separates the two things the ruler was doing at
once. Day counts: 129 / 232 / 133 / 58 / 47 across the buckets; median session reads
52t, p90 93t, max 216t. Net $/trade, shape `1r/none`, 1 NQ, **bootstrapped over days**
(2,000 draws, day-clustered — so the 47-session tail bucket is not overstated):

| early ruler | days | `t40` | `ruler` | ruler − t40 |
|---|---:|---:|---:|---:|
| < 40t | 129 | **26.7** ± 2.7 | 20.2 ± 2.2 | **−6.5** ± 1.9 |
| 40–60t | 232 | 30.7 ± 2.0 | **46.5** ± 2.4 | **+15.8** ± 1.7 |
| 60–80t | 133 | 26.0 ± 3.0 | **74.7** ± 4.8 | **+48.6** ± 4.0 |
| 80–100t | 58 | 13.7 ± 5.3 | **98.9** ± 9.0 | **+85.4** ± 8.0 |
| **> 100t** | 47 | **0.5** ± 5.6 | **157.1** ± 14.4 | **+157.1** ± 14.2 |

Every row is beyond 2 SE. **A fixed 40-tick stop degrades monotonically as the day
widens and reaches zero — it stops harvesting entirely above ~100t.** Its win rate
falls 59% → 53% and its stop-out rate rises 41% → 47%, but that is the smaller half:
the bigger problem is that a 1R target on a 40t stop is 40 ticks, and on a day whose
noise is 100+ ticks that caps every winner inside the noise. The same collapse shows
on `none/trail1r` (+12.3 against the ruler's +169.2), so it is the stop, not the
target alone.

Width is most of the fix — a fixed `t100` goes from $0.5 to **$103.7** on those days
— and the ruler adds more on top ($157.1 at a median armed 114t). This run cannot
cleanly separate "wider still" from "adapts within the bucket," so read the ruler's
margin over `t100` as suggestive only.

**The zero-edge control is flat.** At p = 0.50 no bucket separates: every width sits
at −$12 to −$28 a trade and `ruler − t40` is inside its own SE in all five rows. So
this is a **harvesting** effect, not a survival one — the wide day does not kill a
tight stop, it just stops paying it.

> **The decomposition:** the ruler's *day-level* adaptation is real and worth a lot
> of money; its *time-of-day* adaptation (§14.1) is worth nothing. Pooling them made
> the whole thing look skill-free, which was too strong a reading.

This does **not** overturn §14.1 on pass rate. The ruler's wide-day dollars arrive
with the variance that comes with them, and an evaluation floor prices variance —
which is why the ruler still passes *less* pooled (−3.76 points against `t40` at
p = 0.70) while earning $24.88 a day more. The two sections answer different
questions, and which one binds depends on whether the objective is passing or
earning.

### The −$500 daily stop inverts into a tax

Mean $/day across the arms, H = 2 min, no goal:

| daily stop | p=0.50 | p=0.60 | p=0.65 | p=0.70 |
|---|---:|---:|---:|---:|
| account DLL | −$223 | +$134 | +$322 | **+$529** |
| −$500 self | −$147 | +$83 | +$248 | +$414 |

At zero edge the tight stop is worth **+$76 a day**; at p = 0.70 it **costs $115 a
day**, because it is cutting winning days short. It still buys a little pass rate
(87.2 vs 83.2 on the best arm) — the same variance-against-mean trade as everywhere
else — but the sign of its dollar value flips with the edge.

**Paired, the dollar cost is much smaller and the pass benefit is uniform** (window
run, n = 2,124 pairs a cell, holding arm/edge/account/window/goal/sizing/mark):

| account DLL − −$500 self | p = 0.50 | p = 0.65 | p = 0.70 |
|---|---:|---:|---:|
| pass points | −0.03 (ns) | **−0.82** (15%) | **−0.59** (17%) |
| $ / day | **−$17.27** | **+$15.89** | **+$24.31** |
| `day_p10` (bad-day tail) | **−$247** | **−$171** | **−$128** |

The $115-a-day figure above was an unpaired group mean over a wider arm set; paired,
running to the account limit is worth **$16–24 a day, not $115**. And it *loses*
pass rate everywhere — 83–85% of pairs prefer the self-stop, at both accounts, in
every window, at every day-goal setting. So the tax is real but small, and it buys
nothing: the account DLL's bad day is $128–247 worse and its pass rate is lower.
**The tighter stop wins the metric that decides an evaluation.** §16 records the
headline sign flip; this table is the one to act on.

### Two attempts is the metric that matches the budget

The operating constraint is **2 × 50K evaluations a month**, so P(at least one pass)
is what decides anything. With a +$500 goal, H = 2 min:

| | single | two attempts |
|---|---:|---:|
| p = 0.50 · LucidDaily | 22.2% | 39.5% |
| p = 0.65 · LucidDaily | 56.0% | **80.6%** |
| p = 0.70 · LucidDaily | 84.8% | **97.7%** |

A 56% single-attempt rate reads as marginal and is 81% over the budget.

### Horizon

60 minutes is uniformly worst (best-Pro 48.5 at p = 0.70 against 87.2 at 2 min) —
the edge decays. 1–5 minutes are close and best, which matches a book whose median
reviewed trade lasts **35 seconds**. A 60-minute oracle is not a model of this
trading.

### The thing this run cannot settle

The right bracket depends on which end of the reviewed book's CI is true. At the
bottom (p = 0.5) it is `t75/3r/none`; at the top (p ≈ 0.70) it is preset **C**,
`ruler/1r/none`, and the two are near-opposites. **93 reviewed trades is not enough
to tell them apart** — which makes reviewing more of the book the highest-value next
step, ahead of any further bracket work.

### 14.3 · Micros — the sizing lever §7 said did not exist, and it is the biggest one here

§7 concluded fixed-dollar sizing "is not a lever." That was true **at mini
granularity** and it was the wrong granularity. A $250 budget buys one mini at
almost every stop width, so `risk150/250/400` were near-copies of `1nq`. At micro
granularity the same rule finally has somewhere to move: $0.50 a tick, 10 to a mini,
capped at 40 (the same four minis of exposure).

**The problem it solves is real.** 1 NQ on the ruler's stop is not a constant bet —
its risk per trade swings **$166 → $613** across the volatility buckets of §14.2,
against a $2,000 max loss. That is a position that gets 3.7× larger exactly on the
days that are hardest to read. `mnq250` holds it at $218–240 flat.

And it keeps the wide-day edge. Return **per dollar risked** on > 100t days at
p = 0.70 is 0.256 for 1 NQ and 0.251 for `mnq250` — the mini is not earning a better
rate on wild days, it is only betting more.

Run: `--edge --keep-sizings` → `bracket_survival_mnq.json`, 16,992 rows. Pass rate
in the recommended config (+$500 goal, −$500 self stop, mtm), mean over 59 arms:

| p = 0.70 | exposure @40t | Pro | Daily | $/day |
|---|---:|---:|---:|---:|
| `mnq400` | 1.9 mini | 59.9 | 54.3 | $126 |
| **`1nq`** | 1.0 mini | 69.3 | 63.9 | $137 |
| `mnq250` | 1.1 mini | 69.5 | 64.3 | $142 |
| **`mnq150`** | **0.7 mini** | **84.6** | **80.9** | **$148** |
| `mnq100` | 0.4 mini | **93.5** | **91.6** | $124 |

Paired against `1nq` on the identical arm/edge/account (n = 118):

| vs `1nq` | p = 0.50 | p = 0.65 | p = 0.70 |
|---|---:|---:|---:|
| `mnq150` pass pts | **−5.85** (0%) | **+10.63** (89%) | **+16.12** (97%) |
| `mnq150` $/day | **−$25.10** | −$2.94 | **+$10.86** (69%) |
| `mnq100` pass pts | **−7.90** (0%) | **+23.69** (92%) | **+25.96** (97%) |
| `mnq100` $/day | −$11.45 | **−$18.42** | **−$13.65** |

**`mnq150` is the only configuration in this whole study that improves pass rate and
dollars at the same time** — +16 points and +$11 a day at p = 0.70, at 97% and 69%
sign consistency. Everything else here has been a variance-for-expectancy trade;
this one is not, because 1 NQ was simply oversized for the account.

Three things to keep hold of:

**The budget matters more than the idea.** `mnq400` buys 1.9 minis at a 40t stop and
is **worse than doing nothing** (−12.11 pass points pooled). `mnq250` ≈ 1.1 minis ≈
`1nq`. The gain is not "use micros," it is **size down to roughly half a mini** —
micros are just the only instrument that can express that on a $50K account.

**It inverts at zero edge.** At p = 0.50 every micro arm is *worse* (−5.9 to −7.9
points), because a micro's commission is floored at $0.50 a side rather than a tenth
of the mini's $3.50 — ten micros pay $5.00 where one mini pays $3.50, so micro
granularity costs **~1.43× the fees per unit of exposure**. You are buying risk
control with fee drag, and it only pays if the edge is real.

**Smaller resolves slower.** `mnq150` takes +3.4 days to resolve an evaluation and
`mnq100` +9.7. Neither is near the cap. The 40% consistency rule does not apply to
an evaluation at all; §15.5 prices it where it does bite, and finds the +$500 day
goal very nearly pays for it.

The per-sizing best arms (98.0% for `mnq150` on `t75/1.5r/trail.5r`, 99.5% for
`mnq100`) are the maximum over 59 arms and are **selection on noise** — the §11
lesson. Read the means above, not those.

## 15. Addendum — the funded stage, and how many payouts a month actually pays

Everything above prices the **evaluation**: reach +$3,000 before the floor reaches
you, once. That is not the thing that pays. This section runs the stage after it,
in `funded_payouts.py`, which delegates every fill, bracket, daily stop and equity
skeleton back to the gated walker and adds only the funded floor and the payout
machinery.

### 15.1 The buffer is the whole shape of the funded game

On a funded 50K both accounts share three numbers:

| | |
|---|---|
| max loss limit | $2,000 → floor opens at $48,000 |
| **buffer** = start + max loss + $100 | **$52,100** — cannot withdraw a cent below it |
| floor stops trailing at buffer − max loss | **$50,100**, and never moves again |

Those last two are the same event, which is the tidy part of Lucid's design:
**by the moment you are first allowed to take a payout, your floor has already
locked.** So a funded life is two phases, and they are not alike.

1. **Climb $50,000 → $52,100 with the floor still trailing.** This is the
   evaluation again in miniature and strictly easier than it — a $2,100 climb
   against the same $2,000 leash, versus $3,000 against it. Nearly all of the
   death risk lives here.
2. **Above the buffer the floor is nailed at $50,100 forever.** You hold a fixed
   $2,000 cushion and the game becomes: how often can I clear $500 above the
   buffer without giving that cushion back?

Then the three rulebooks diverge completely in how they let you take it:

| | LucidPro | LucidFlex | LucidDaily |
|---|---|---|---|
| **buffer** | $52,100 | **none** — but see below | $52,100 |
| gap between requests | **3 calendar days** (weekends count) | none | any day |
| gate | ≥ $500 cycle profit | **5 days of ≥ $150 each** | > $0 since last payout |
| consistency | **best day ≤ 40%** of cycle profit | none | none |
| per-payout cap | $2,000, then **$2,500 ×4** | **50% of cycle profit, ≤ $2,000, flat** | none — all above buffer |
| lifetime sim ceiling | 5 payouts / **$12,000** | 5 payouts / **$10,000** | uncapped; one $8,000 day ⇒ auto-live |
| floor | end-of-day trail | end-of-day trail | **intraday** trail |

> **CORRECTION, 2026-08-25.** The live pricing card shows Flex's **"Days to Payout:
> 5"** in the same field where Pro shows **3** — so "Pro with a five-day gate instead
> of three" is what the primary source supports, and the list below is wrong on its
> first and second points. It came from a third-party write-up since shown wrong
> about Daily's price (by 33%) and about Flex's daily loss limit (claimed "none
> anywhere"; the card shows $600–$2,700). What the card *does* confirm: Flex's eval
> carries 50% consistency where Pro's carries none, funded consistency is absent
> where Pro's is 40%, and Flex has a funded scaling plan where Pro has none. The
> buffer, the qualifying-days mechanic, the 50%-of-cycle cap and the floor snap are
> **unverified, not refuted** — no payout mechanics appear on the card for any
> product. **Every Flex row in §15.2–15.3 was simulated under the model below and
> should be read as conditional on it.** See
> [`lucid-account-rules.md`](lucid-account-rules.md).

**LucidFlex is not "Pro with a longer cycle" — it has no calendar cycle at all**,
and it differs in five ways, the last of which is a trap:

1. **No buffer**, so profit is withdrawable long before $52,100.
2. The gate is **five qualifying days** (≥ $150 each on a 50K) plus $1 of net, not
   a wait and a dollar goal.
3. **No consistency rule** — Pro's 40% is the tightest of the three.
4. The cap is **50% of cycle profit, ceiling $2,000, and it never escalates**, so
   the lifetime ceiling is $10,000 gross against Pro's $12,000 — and reaching it
   takes $20,000 of trading profit, because you only ever extract half.
5. **Requesting a payout snaps the max loss limit to $50,100 on the spot.** That is
   Flex's buffer, charged as a floor jump rather than a withdrawal block — and it
   is far more dangerous, because nothing stops you triggering it while the balance
   is low. Cash out at $51,000 and the floor leaps from $49,000 to $50,100 under a
   $50,500 balance: **$400 of room where a moment earlier there was $2,000.**

That fifth rule is unit-checked directly, and it is not a rounding effect — on a
tape of five +$200 days followed by one −$500 day, **Flex takes a $500 payout on
day 5 and is dead on day 6, while Pro survives the identical tape** because the
buffer refused the payout that killed it.

Sources: Lucid's help-centre LucidDaily payouts page for the daily side, and two
independent write-ups agreeing on the Pro ladder, the 3-day gap and the Flex rules.
Three stated rules never bind under the operating config and so are not modelled:
the funded daily loss limit (the −$500 self stop is tighter than any of them),
Pro's LucidScale DLL at 60% of the highest end-of-day profit, and **Flex's funded
contract ladder** (2 minis / 20 micros until $1,000 of sim profit) — every sizing
tested here is at or under 2 minis, so it never binds, though it would bite hard at
`mnq400` or at more than one mini.

### 15.2 A month

21 trading days over 29 calendar days, 50K, −$500 self stop, +$500 day goal,
`mnq150` sizing, averaged over the six shipped shapes, 4,000 bootstrap months.

| p | account | payouts | take-home (90%) | months paying ≥ $1 | first payout | account dead |
|---|---|---:|---:|---:|---:|---:|
| **0.70** | Pro | 1.48 | **$1,454** | 75.4% | day 11.5 | **12.8%** |
| | Flex | 1.71 | $1,351 | **90.5%** | **day 7.3** | 35.5% |
| | Daily | 3.07 | **$1,703** | 74.5% | day 11.5 | 18.2% |
| **0.65** | Pro | 0.75 | $660 | 48.5% | day 12.8 | **29.4%** |
| | Flex | 1.13 | $823 | **74.8%** | **day 8.3** | 57.4% |
| | Daily | 1.35 | $765 | 45.6% | day 12.6 | 38.1% |
| **0.50** | Pro | 0.03 | $25 | 2.9% | — | **90.4%** |
| | Flex | 0.18 | $115 | 16.2% | day 8.5 | 94.7% |
| | Daily | 0.05 | $30 | 2.9% | — | 92.9% |

**Read the p column first — it is the answer, and it is the one number this study
cannot measure.** At a real edge you clear one to three payouts a month and take
home $1,400–1,700. At a coin flip you take home nothing and are dead inside the
month nine times in ten. Nothing about the bracket, the account or the payout
policy moves that; only being right does.

Four things the mean hides:

- **The 10th-percentile month pays $0 on Pro and on Daily** — more than one month in
  ten pays nothing even at p = 0.70, because the first payout needs +$2,600 (the
  $2,100 buffer plus the $500 minimum) and that takes a **median of 11–13 trading
  days**. The 90th percentile is $3,344 (Pro) and $3,674 (Daily).
- **Flex is the exception, and it is its one real advantage.** No buffer means the
  first payout lands on **day 7.3** and **90.5% of months pay something**, with a
  10th percentile of **$256** — the only non-zero p10 in the study. If what you need
  is money arriving *soon and reliably*, Flex is the account that does that.
- **Flex buys it with roughly three times the death rate** — 35.5% against Pro's
  12.8%, and **57.4% at p = 0.65**, the worst number anywhere in this document. The
  floor snap is the mechanism: Flex lets you take a payout at a balance where Pro
  would refuse, and then charges $50,100 for it.
- **Month one understates the steady state.** Most of a first month is spent climbing
  a buffer you only climb once. Over a quarter LucidDaily averages **4.00 payouts a
  month** against the 3.07 of month one.

### 15.3 The two policy levers, and both are worth more than the bracket

Over a **quarter** (63 trading days), where Pro's five-rung ladder can actually
run out — p = 0.70, `mnq150`:

| account | policy | payouts | take-home | dead | graduated to live |
|---|---|---:|---:|---:|---:|
| Pro | take ASAP | 3.94 | $4,640 | 24.3% | 67.5% |
| Pro | **wait for the full rung** | 2.96 | **$6,274** | 20.4% | 20.1% |
| Pro | keep $1k in | 3.91 | $4,601 | 16.1% | 64.4% |
| Flex | take ASAP | 3.27 | $2,493 | 42.6% | 54.9% |
| Flex | wait for the full cap | 1.96 | $3,532 | **17.5%** | 0.2% |
| Flex | keep $1k in | 3.67 | $2,781 | 31.1% | 64.0% |
| Daily | take ASAP | 11.99 | $6,588 | 41.5% | — |
| Daily | **keep $1k in** | 11.51 | **$6,323** | **22.5%** | — |

**Pro: never take a small rung.** The ladder is five rungs and a $500 payout burns
one exactly as thoroughly as a $2,500 payout does. Waiting until the whole cap is
available earns **+$1,633 on the quarter with one fewer payout** — and the ASAP
column's 67.7% "graduated" is not a win, it is the ladder being spent at $500 a
rung. This inverts inside a single month (§15.2 shows ASAP ahead), so the month
view is actively misleading here: **the ladder is a lifetime budget, not a monthly
one.**

**Daily: leave $1,000 in the account.** The floor is locked either way, so the only
thing a withdrawal changes is how much room you have left for the next drawdown.
Keeping $1,000 above the buffer **cuts the death rate from 41.5% to 22.5% for
−$265**, about 4% of the money. That is the best-priced trade in this document.

**Flex: the quarter is where its bill arrives.** Over a month Flex looked competitive
— it pays first and most reliably. Over a quarter it earns **the least of the three
at every policy**: $2,493–3,532 against Pro's $4,640–6,274 and Daily's $6,323–6,588.
The cause is the extraction rule, not the trading. **You may only ever take half of
a cycle's profit, the $2,000 cap never escalates, and five payouts end the account**
— so the lifetime ceiling is $10,000 gross and it takes $20,000 of profit to reach.
Waiting for the full cap helps (+$1,039 and 42.6% → 17.5% dead) but at 1.96 payouts
a quarter it barely uses the ladder at all: 0.2% graduate.

The levers were not run together; Pro under *full-rung and* keep-$1k is untested and
is the obvious next probe.

### 15.4 Once risk is fixed in dollars, the ruler stop inverts and wins

§14.1 found the vol ruler losing to a fixed width, paired, at flat 1 NQ. In the
funded month it wins — and the reason is the interaction, not a contradiction.
Paired on (shape, edge, account, policy), `ruler − t40`:

| sizing | p = 0.50 | p = 0.65 | p = 0.70 |
|---|---:|---:|---:|
| `1nq` — take-home | +$9 | −$77 | +$55 |
| `1nq` — bust pts | −2.3 | +0.6 | −1.3 |
| `mnq150` — take-home | −$5 | +$34 | **+$440** |
| `mnq150` — bust pts | +1.1 | −4.8 | **−7.7** |
| `mnq250` — take-home | +$8 | +$134 | **+$489** |
| `mnq250` — bust pts | −0.9 | −6.6 | **−10.0** |

At 1 NQ the ruler is a wash at every edge level — which is §14.1's result seen
from the funded side. Under fixed-dollar micro sizing it is worth **+$440 and
−7.7 points of bust**. The mechanism is arithmetic: a $150 budget buys 7 micros at
a 40-tick stop and 5 at the ruler's 57-tick morning stop, so **the wide stop
arrives as a smaller position rather than as more risk.** The ruler's problem was
never its width, it was that at a flat 1 NQ its width *was* its risk. Fixing the
dollars decouples them and leaves only the benefit §14.2 measured — fewer
stop-outs, and a 1R target that scales with the stop instead of capping winners
inside the noise.

This is the study's clearest case of two knobs that are worth little alone and
much together, and it is why `ruler/1r/none` is the best single arm in the funded
run (1.82 payouts and 4.3% bust on Pro, against 1.42 and 9.7% for `t40/1r/none`).

### 15.5 How much to risk, and how big an account to buy

**$250 a trade on a 50K is too much, and the month view is what hides it.** `mnq250`
is ~1.1 minis at a 40-tick stop against `mnq150`'s 0.7. Paired on the identical arm,
account and policy (n = 48), over a **month** it looks nearly free — +0.08 payouts
and +$66 — but it carries **+14.1 points of bust, and 100% of the 48 pairs bust
more.** Over a **quarter** the busts compound and it loses on the money too:

| p = 0.70, quarter | payouts | take-home | dead |
|---|---:|---:|---:|
| Pro, `mnq150` (~0.7 mini) | **3.94** | **$4,640** | **24.3%** |
| Pro, `mnq250` (~1.1 mini) | 3.23 | $3,778 | 44.4% |
| Daily, `mnq150` | **11.99** | **$6,588** | **41.5%** |
| Daily, `mnq250` | 9.82 | $5,216 | 70.3% |

This is §14.3's rule restated where it costs money: **$250 buys back the position
size that made 1 NQ wrong in the first place.** Around $150 — 7.5% of the max loss —
is the budget; $250 is 12% and it is on the far side of the peak.

**The 25K is very nearly a half-scale 50K, because Lucid scales it almost
perfectly.** Max loss is 4% of the account on both, buffer is max loss + $100, the
eval target is 6%, the caps and cycle goals all halve. Run with everything scaled —
sizing, daily stop (−$250), day goal (+$250) — at p = 0.70:

| | 50K month | 25K month | 50K quarter | 25K quarter |
|---|---:|---:|---:|---:|
| Pro · payouts | 1.48 | 1.04 | 3.94 | **3.92** |
| Pro · take-home | $1,454 | $620 | $4,640 | $2,496 |
| Flex · payouts | 1.71 | 1.22 | 3.27 | **3.28** |
| Daily · payouts | 3.07 | **1.22** | 11.99 | 6.06 |
| Daily · take-home | $1,703 | $647 | $6,588 | $3,200 |

**Two things do not scale, and both show up here.**

The first is the **$500 minimum payout**, identical on every account size. On a 50K
that is 25% of the $2,000 cushion and about **3.0 days** of average profit; on a 25K
it is **50% of a $1,000 cushion and 6.2 days**. That is why the payout *counts* land
where they do: over a quarter Pro and Flex converge almost exactly (3.94 → 3.92,
3.27 → 3.28), because their gates are cycle-shaped and halve with the account — but
**LucidDaily's halve outright** (11.99 → 6.06), because Daily's whole model is
frequent small withdrawals and the ticket size stayed put while the earnings rate
halved. **If you are buying a 25K, Daily is the account the size hurts most.**

The second is **micro granularity**, which is blunter against a smaller account. A
$75 budget at a 40-tick stop buys 3 micros where 3.5 was wanted — 14% under, against
the 50K's 5% — so the scaled 25K is accidentally *under*-sized, which is most of why
its bust rates read **lower** (Pro 9.4% vs 12.8%, Flex 20.2% vs 35.5%). Read that as
a granularity artifact, not as the 25K being safer.

The sizing optimum itself survives the change of scale, which is the reassuring part:

| 25K, month, p = 0.70 | risk / max loss | payouts | take-home | dead |
|---|---:|---:|---:|---:|
| `1nq` | **20.7%** | 0.94 | $576 | **51.2%** |
| `mnq75` | 6.3% | 1.04 | $620 | **9.4%** |
| `mnq125` | 10.5% | 1.28 | $775 | 24.3% |

One NQ on a 25K is five losing trades from dead and busts half of all months — the
same mistake as 1 NQ on a 50K, twice as loud.

### 15.6 What this section does not model

Beyond the standing caveats in §11 — which all still apply and all point the same
way — the funded run adds four of its own:

- **The 40% rule is modelled but never tested against a real distribution of
  days.** With a +$500 day goal capping every winner it is close to free: the first
  payout satisfies it automatically, and later ones need three contributing days,
  which the 3-calendar-day gap already forces. **Without the day goal it would
  bind hard**, and that interaction is untested.
- **Payouts are taken the same session they qualify.** The help centre says funds
  leave "within a few minutes" of approval, so this is the honest reading and also
  the conservative one — the cushion is gone before the next day's trading.
- **I.i.d. day resampling is worse here than in the evaluation.** A funded month is
  short, and a bad week inside it is exactly what a locked $2,000 cushion cannot
  absorb. Every bust rate above is optimistic by an amount this run cannot size.
- **No account-replacement cost.** A dead funded account ends the run; it does not
  price the eval fee or the time to get back. This matters most for the 25K
  comparison in §15.5, where the eval fee is a larger share of what is at stake.
- **The 25K comparison scales our config, not just Lucid's rules.** Sizing, daily
  stop and day goal are all halved, because holding them fixed would price a sizing
  mistake rather than an account. That is the right control, but it means the table
  answers *"is a 25K half a 50K?"* and not *"what happens if I trade a 25K the way
  I trade a 50K?"* — the answer to which is the `1nq` row: 51% dead in a month.

## 16. Addendum — accounts as consumables, and how aggressive to be

Every table before this ranks configurations by **survival**, which is the correct
objective only if an account is precious. It is not: at 40% off it is a **$47–115
consumable**. `funded_campaign.py` asks the question that follows — *given a budget
for buying accounts, what maximises take-home cash?* — by walking the whole pipeline
over 12 months: buy an evaluation → pass it or bust → funded → payouts → bust or
graduate → buy another, if the budget allows. Budget is $250 a month.

Resets are not modelled on purpose: the discount code never applies to a reset, so a
fresh discounted account is cheaper at every size. Accounts run one at a time, because
N accounts traded identically multiply cash **and variance** by N with no
diversification — they bust together. That makes the per-account figure the thing to
optimise and the budget merely the thing that sets N.

### 16.1 The aggression curve, and where it turns over

Risk per trade as a fraction of the account's max loss. LucidDaily 50K, keep-$1k,
net of fees, per month:

| config | p = 0.60 | p = 0.65 | p = 0.70 |
|---|---:|---:|---:|
| **safe** — 7.5% risk, +$500 goal | $121 | $1,595 | $3,236 |
| **moderate** — 12% risk, no goal | **$234** | **$2,583** | $6,480 |
| **aggressive** — 20% risk, no goal | $234 | $2,063 | **$7,342** |

*Re-run 2026-08-25 on the twelve **verified** checkout prices in
[`lucid-account-rules.md`](lucid-account-rules.md), which replaced an inferred
LucidDaily 50K price that was 33% too high. Every cell improved and the ranking
held; the cheaper accounts help the churn-heavy configs most, which is why
aggressive gained $132 at p = 0.60 and now ties moderate there — on 29.1 accounts
a year against moderate's 18.1. **LucidDaily 50K is now the best account at every
edge level**, where previously Flex 25K won at p = 0.55 and Pro 50K at p = 0.60.*

**Moderate wins at two of the three edge levels, and beats safe at all three.** So
your instinct is right, but only halfway: **§15.5's 7.5% was optimising survival, and
for cash the budget is about 12%** — worth +61% over safe at p = 0.65 and +100% at
p = 0.70. Going on to 20% is not "more of the same": it **wins only if p is exactly
0.70**, and costs 24% of the money if p is 0.65. It is a bet on the edge, not on
aggression.

Two things flip when the objective becomes cash rather than survival:

- **Take the day goal off.** Every winning row at p ≥ 0.65 has no goal. §4's "+$500
  is the LucidDaily antidote" is a *survival* instrument — it caps exactly the good
  days that fund payouts.
- **Keep the cushion anyway.** `keep` still beats `asap` inside the aggressive
  config, by **+$503/mo at p = 0.65 and +$1,958/mo at p = 0.70**. It is the one
  cautious habit that survives the switch of objective, because it buys days alive
  rather than giving up upside.

### 16.2 Why 20% fails below p = 0.70: you spend the year re-passing evaluations

| p = 0.65 | accounts / year | never reach funded | risk of a losing year | 10th-pct year |
|---|---:|---:|---:|---:|
| safe 7.5% | 2.4 | 25% | 0.4% | $9,731 |
| moderate 12% | 8.0 | 40% | 0.5% | $13,309 |
| aggressive 20% | **19.7** | **57%** | 2.6% | **$5,782** |

At 20% you buy nineteen accounts a year and **57% of them die in the evaluation** —
before a single payout is possible. The lost resource is not the fee, it is the
**trading days spent in an evaluation earning nothing**. That is what caps the
aggressive config, and it is why the 10th-percentile year is less than half the
moderate one.

**The $250 budget never binds.** At the verified prices fees run **$15–126 a month**
across safe to aggressive, and $6–213 over every cell at p ≥ 0.60. Reinvesting
payouts into more accounts is not the lever — you already have enough money for
accounts. **The scarce resource is trading days, not dollars.** What $250 does buy
is *parallel slots*, and how many depends entirely on churn: at the moderate setting
it funds **2.2 slots at p = 0.60, 4.9 at p = 0.65 and 13.1 at p = 0.70**. Since N
identical accounts each face their own floor, slots scale cash linearly at the same
per-account bust rate — but you cannot know how many you can afford until the churn
rate reveals itself, so start with one and add on evidence.

**Buy LucidDaily's cheap evaluation, not the easy one.** Daily alone lets you pay to
choose the *evaluation's* drawdown type; it does not carry into funded, so it is a
pure one-time purchase of pass probability, and **intraday is the cheaper side**.
Holding price constant and changing only the difficulty (moderate, 50K, keep):

| p | EOD eval — net/mo, eval-fail | intraday eval — net/mo, eval-fail | EOD gain |
|---|---|---|---:|
| 0.60 | $234, 72% | $214, 78% | +$20 |
| 0.65 | $2,583, 40% | $2,593, 50% | −$10 |
| 0.70 | $6,480, 16% | $6,483, 25% | −$3 |

The EOD eval **does** pass more often — 10 points more at p = 0.65 — and it buys
**nothing measurable in net terms**. The reason is that a failed evaluation at a 12%
risk budget fails *fast and cheap*: the extra failures cost 1.7 accounts a year, or
**$130 of fees**, against a five-figure take-home. So the earlier reasoning here —
"paying not to be trailed intraday during the eval is the obvious buy" — was wrong,
and it was wrong because it priced the rule rather than the year. **Take the cheap
toggle at any premium above about $16 an account.**

At the verified prices **LucidDaily 50K is the best account at every edge level**,
including the thin ones. That is a change: on the earlier inferred prices Flex 25K
won at p = 0.55 and Pro 50K at p = 0.60, and both of those were artifacts of Daily
being priced 33% too high.

### 16.3 What `p` means, and what a day actually looks like

`p` is **not a win rate.** It is the dial on the directional oracle: for each entry,
a coin with probability `p` decides whether the side agrees with the sign of the
**2-minute** forward move. The realized win rate is lower, because the bracket
intervenes on the path, costs land on every trade, and the holding period is not the
horizon. `daily_shape.py` measures both, under the moderate config (`ruler/1r/none`,
$240 risked in micros, −$500 day stop, no goal, 599 sessions × 4 draws ≈ 35,500
trades an edge level):

| p | win rate | mean $/trade | avg win | avg loss | stop-out | target |
|---|---:|---:|---:|---:|---:|---:|
| 0.50 | 48.2% | −$23.53 | $205 | −$236 | 51.8% | 48.1% |
| 0.60 | 53.9% | **+$1.93** | $205 | −$236 | 46.0% | 53.9% |
| 0.65 | 57.2% | +$16.39 | $205 | −$236 | 42.7% | 57.2% |
| 0.70 | 60.6% | +$31.08 | $205 | −$236 | 39.4% | 60.5% |

**The single most useful number in this document is the break-even win rate: 53.5%.**
A 1R target pays $205 while a stop costs $236 — the spread, the queue and the round
turn come off the winner *and* go onto the loser, so the payoff is 0.87 : 1 and a
coin flip is not break-even. `p` converts at roughly **win% ≈ p − 7**, which puts
break-even at **p ≈ 0.60** — exactly where the table crosses zero at +$1.93.

Per funded trading day, ~14.9 trades after the daily stop truncates:

| p | green days | mean | p10 | median | p90 | worst |
|---|---:|---:|---:|---:|---:|---:|
| 0.50 | 30.3% | −$205 | −$707 | −$519 | +$734 | −$753 |
| 0.60 | 44.2% | +$34 | −$698 | **−$209** | +$1,119 | −$745 |
| 0.65 | 54.1% | +$210 | −$681 | +$134 | +$1,326 | −$790 |
| 0.70 | 63.8% | +$396 | −$664 | +$425 | +$1,540 | −$773 |

Two things worth knowing before running this live. **At p = 0.60 the median day
loses money (−$209) while the mean makes it (+$34)** — the distribution is
right-skewed, so a marginal edge feels like losing most days and being rescued by
occasional big ones. And **the −$500 daily stop actually bleeds to −$790 on the worst
days**, because it is checked *between* trades: a trade opened at −$490 realized can
still take its full stop. Against a $2,000 max loss that is **2.5 worst-days from
dead**, not four.

### 16.4 A hard equity stop is worse — and it does not even buy survival

The obvious fix for that −$790 is to make the daily stop symmetric with the day
goal: cut the open position the moment **marked** equity touches −$500, rather than
merely refusing the next entry. `hard_day_stop.py` prices it, charging the spread on
the way out (the exit lands at −$500 minus one tick of slip, because leaving at the
level exactly would be free and it is not).

**It does exactly what it promises to the tail, and it is still a losing trade.**

| p | stop | green days | mean | median | p10 | worst | days below −$500 |
|---|---|---:|---:|---:|---:|---:|---:|
| 0.65 | soft (app) | 54.1% | $210 | +$134 | −$681 | −$790 | 35.9% |
| 0.65 | **hard** | 48.6% | $179 | **−$56** | −$508 | **−$520** | **44.4%** |
| 0.70 | soft (app) | 63.8% | $396 | +$425 | −$664 | −$773 | 28.9% |
| 0.70 | **hard** | 58.6% | $360 | +$307 | −$507 | **−$520** | **36.5%** |

The worst day tightens from −$790 to −$520 and the 10th percentile from −$681 to
−$508, precisely as intended. But **the number of days that close below −$500 goes
up, not down** — 35.9% → 44.4% at p = 0.65. That is the whole mechanism: under the
soft stop a day that dips to −$600 unrealised can come back and close at −$200 or
green, and the hard stop locks it in at −$520. **You are trading a rare −$790 for a
frequent −$520**, and the median day at p = 0.65 flips from +$134 to −$56.

A funded month (LucidDaily 50K, moderate, keep-$1k, 4,000 bootstraps):

| p | stop | payouts | take-home | dead |
|---|---|---:|---:|---:|
| 0.65 | soft | **1.82** | **$1,800** | **53.3%** |
| 0.65 | hard | 1.52 | $1,515 | 54.3% |
| 0.70 | soft | **4.38** | **$4,508** | **26.4%** |
| 0.70 | hard | 3.85 | $3,982 | 26.6% |

**It costs 12–16% of the money and the bust rate does not move** — it is very
slightly worse. A thinner daily tail buys no survival because these accounts die of
accumulated drift, not of one catastrophic session; the same conclusion the loser-order-flow study reached from the tape side
(stops die of drift, not of a signal).
This is also the third time this project has found that early exits destroy P&L —
the upper-band-bounce loss study turned the panic-exit knob off for the same reason —
now at day scale rather than trade scale. **Keep the app's between-trades stop.**

### 16.5 Holding the target and tightening the stop: the win rate falls exactly as fast

Every arm so far puts stop *and* target on the same ruler reading, so `ruler/1r` is
1 : 1. `ruler_ratio.py` keeps the target where it is and moves the stop in
underneath — the same bracket restated at a higher R, and at fixed dollar risk a
tighter stop buys more contracts, so a 2R winner pays about twice what was risked
whatever the ruler reads.

| arm | med stop | micros | p | win% | mean/trade | avg win | avg loss | break-even win% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **1.00× ruler (1R)** | 40t | 11 | 0.65 | **57.2%** | **+$16.39** | $205 | −$236 | 53.5% |
| 0.75× ruler (1.33R) | 30t | 15 | 0.65 | 49.5% | +$13.10 | $273 | −$241 | 46.9% |
| 0.50× ruler (2R) | 20t | 21 | 0.65 | 38.1% | **−$0.42** | $400 | −$247 | **38.2%** |

**The 2R arm lands exactly on its own break-even win rate** — 38.1% achieved against
38.2% required, for a mean of −$0.42 a trade. Stretching R lowers the bar and lowers
the hit rate by the same amount; the price path gives back precisely what the payoff
asks for. What breaks the tie is **cost**, and it gets worse as you tighten: a
tighter stop buys more micros, so the round turn doubles from $11 to $21 while the
"fair" win of $480 arrives as $400.

The funded month is where it becomes severe:

| arm | p | green days | day median | payouts | take-home | dead |
|---|---:|---:|---:|---:|---:|---:|
| **1.00× (1R)** | 0.65 | **54.1%** | **+$134** | **1.82** | **$1,800** | **53.3%** |
| 0.75× (1.33R) | 0.65 | 48.6% | −$81 | 1.32 | $1,448 | 72.3% |
| 0.50× (2R) | 0.65 | 36.0% | −$526 | 0.56 | $683 | 90.2% |
| **1.00× (1R)** | 0.70 | **63.8%** | **+$425** | **4.38** | **$4,508** | **26.4%** |
| 0.50× (2R) | 0.70 | 41.8% | −$504 | 1.22 | $1,541 | 80.5% |

**Bust goes 53% → 72% → 90%.** A high-R bracket loses on most days and is rescued
occasionally, and an intraday-trailing floor punishes exactly that shape — the
median 2R day *is* the daily stop (−$526 at p = 0.65, −$504 even at p = 0.70).
This is §12's "with a real edge the most-harvesting bracket wins" holding below 1R
as well as above it, and it is monotone: **every step away from 1 : 1 costs money
and costs survival.**

### 16.5b The cleaner axis: pin the stop at the ruler, sweep the target

Axis A moved the stop, which confounded the payoff ratio with how wide the stop sits
relative to the day's noise. Axis B pins the stop at 1.0× ruler — the width the ruler
is calibrated to — and moves only the target, 0.50R to 1.50R. Same 599 sessions,
same $240 of micro risk, so the stop is 40t and the size 11 micros in every row and
**the average loss is −$236 throughout**, which is the internal check.

| target | p = 0.65 win% | mean/trade | avg win | break-even win% |
|---|---:|---:|---:|---:|
| 0.50R | **71.3%** | +$1.14 | $97 | 70.9% |
| 0.75R | 63.7% | +$10.31 | $151 | 61.0% |
| **1.00R** | 57.2% | +$16.39 | $205 | 53.5% |
| 1.33R | 49.9% | +$19.91 | $277 | 46.0% |
| 1.50R | 46.9% | **+$21.56** | $313 | 43.0% |

**Expectancy per trade rises monotonically with the target — it does not peak at
1 : 1.** That refutes the shape §12 suggested and the one this document expected. A
0.50R target is nearly worthless (+$1.14, sitting on its own 70.9% break-even), for
the same reason the 2R arm was: a $97 win cannot pay for a $236 loss.

The funded month tells a different story, because a floor prices variance:

| target | p = 0.65 payouts / take-home / dead | p = 0.70 payouts / take-home / dead |
|---|---|---|
| 0.50R | 0.32 / $233 / 66.0% | 1.49 / $1,139 / 32.6% |
| 0.75R | 1.30 / $1,130 / **53.1%** | 3.52 / $3,199 / **25.3%** |
| **1.00R** | **1.82** / $1,800 / 53.3% | **4.38** / $4,508 / 26.4% |
| 1.33R | 1.90 / **$2,056** / 61.5% | 4.20 / **$4,877** / 36.5% |
| 1.50R | 1.86 / $2,113 / 64.3% | 3.86 / $4,631 / 41.3% |

**Payouts and survival peak at 0.75–1.00R; dollars peak at 1.33–1.50R.** The same
variance-for-expectancy trade the whole document keeps finding, now on the target
leg. Note the month view **does not charge for the account it destroyed** — at 61.5%
bust against 53.3% you burn meaningfully more evaluations — so §16's campaign, not
this table, is the deciding test for 1.00R versus 1.33R. That run has not been done.

### 16.5c Same R, wider stop, strictly better — and the reason is commission

The two axes cross: axis A's `0.75× ruler stop / ruler target` and axis B's
`ruler stop / 1.33R target` are **the same R at different absolute widths**.

| | med stop | micros | win% | avg win | avg loss | mean/trade |
|---|---:|---:|---:|---:|---:|---:|
| axis A — 0.75× ruler, 1.33R | 30t | 15 | 49.5% | $273 | −$241 | $13.10 |
| axis B — 1.00× ruler, 1.33R | **40t** | **11** | 49.9% | $277 | −$236 | **$19.91** |

The win rate is the same to within noise (49.5 vs 49.9) and so is the payoff in R.
**The entire $6.81 difference is cost**: at fixed dollar risk a tighter stop buys
*more contracts*, so the round turn goes from $11 to $15 and the fills are worse.

That separates two things this study had been treating as one. **The R ratio sets
the win rate and the payoff; the absolute stop width sets the bill.** At a fixed
dollar risk, therefore, **prefer the widest stop that expresses the R you want** —
which is a direct restatement of §14.3's micro-fee finding (~1.43× fees per unit of
exposure) arriving from the bracket side instead of the sizing side.

### 16.6 The caveat that dominates this section

**At p = 0.70 the oracle is 16 independent 70/30 bets a day, which is a fantasy.**
It implies $396–591 of profit *per day* on a 50K — roughly $100,000 a year — and no
losing year in 1,000 runs. Read the levels in §16.1 as nonsense and the **ranking and
the turning point** as the usable output. The believable end of the table is
p ≈ 0.60, where the same moderate config nets **$172 a month** and loses money in
47% of years.

That gap is the actual finding. The reviewed book puts the edge at roughly p = 0.68
but with a confidence interval that does not separate from zero (§12), and across
that interval this table swings from *lose money* to *$6,470 a month*. **The
aggression question is second-order to the edge question by an order of magnitude**,
which is the same conclusion §12 reached from the other direction.

## 17. Addendum — what a pre-trade conviction tier is worth

The journal grades trades **A–D in review**, after the outcome is known. So the grade
can describe, but it can never set the bracket at the ticket: sizing up on the A's
would be reading the answer. This measures the thing that *could* be built instead —
a tier called **before** the fill — and prices it as a function of how well it works.

`grade_tiers.py`. Four equally-likely tiers, `p_tier = mean_p + s·(+1.5, +0.5, −0.5,
−1.5)`, which holds the **mean p fixed**. That is the whole design: at `s = 0` every
tier is the flat baseline, so any gain a policy shows is the value of *knowing which
trade is which*, never the value of a better signal. Everything else is the standing
frame — $240 of micro risk, stop at the ruler, −$500 day stop, no goal, funded month
on LucidDaily 50K, 599 sessions × 4 draws, 4,000 bootstrapped months a cell.

Tiers are drawn uniform 1/4, **not** on the journal's observed 3/21/47/24 split. That
split is hindsight: the A's are rare in review because few trades *worked*, which says
nothing about how often a conviction call would fire.

Two checks before any number is read. The engine's `day_record` now takes an optional
per-trade sizing key so per-entry sizing reuses the one equity path instead of forking
it — `--validate` still reports **27,300/27,300** arm-days reproducing `run_flat`. And
at `s = 0` the tiered walker's draw is bit-identical to `draw` and its trades reproduce
the gated `run_day` trade-for-trade, **50/50** day-cells.

### 17.1 The mechanism, and the noise floor

At `s = 0` the four tiers are the same population, and reading them apart is the
measurement error of the whole table:

| mean p | s | A | B | C | D | tier p |
|---|---|---|---|---|---|---|
| 0.65 | 0.00 | 56.8% / +$14.84 | 57.4% / +$16.84 | 56.9% / +$14.76 | 57.9% / +$19.16 | 0.65 flat |
| 0.65 | 0.02 | 58.3% / +$21.24 | 58.6% / +$22.48 | 56.5% / +$13.30 | 55.6% / +$9.16 | .68/.66/.64/.62 |
| 0.65 | 0.04 | 61.4% / +$34.78 | 57.5% / +$17.67 | 56.3% / +$12.59 | 53.5% / −$0.03 | .71/.67/.63/.59 |
| 0.65 | 0.06 | 63.4% / +$43.38 | 59.0% / +$24.54 | 55.0% / +$6.58 | 51.9% / −$6.93 | .74/.68/.62/.56 |

**The `s = 0` row spans $14.76 to $19.16 on pure noise** — a $4.40 spread between tiers
that are by construction identical. Per-tier differences under about $5 are nothing.

### 17.2 Acting on a signal you do not have is strictly expensive

The `s = 0` column of the funded month is the most useful thing here, because it is
what every policy costs when the tier is noise:

| policy at s = 0, mean p 0.65 | payouts | take-home | dead |
|---|---:|---:|---:|
| **flat** (do nothing) | **1.82** | **$1,800** | **53.3%** |
| size | 1.46 | $1,532 | 65.5% |
| target | 1.65 | $1,694 | 60.0% |
| skip_d | 1.36 | $1,208 | 53.3% |
| skip_d_size | 1.34 | $1,455 | 70.5% |

**Every policy loses to doing nothing.** Mean-preserving size variation that is
uncorrelated with edge is pure added variance, and the floor charges for it: same
mean risk per trade, death 53.3% → 65.5%. Spreading R across tiers costs 6.7 points
the same way (§16.5b: wider R, more bust). `skip_d` is the cheap one — it only gives
up the 4 trades a day it declined.

### 17.3 What it pays once the tier is real

| policy | s=0.02 | s=0.04 | s=0.06 |
|---|---|---|---|
| flat | 1.89 / $1,869 / 52.5% | 1.84 / $1,812 / 52.8% | 1.91 / $1,887 / 53.2% |
| **size** | 1.99 / $2,086 / 56.0% | **2.31 / $2,438 / 52.0%** | **2.92 / $3,112 / 45.5%** |
| target | 1.96 / $2,031 / 56.8% | 1.84 / $1,904 / 57.4% | 2.17 / $2,222 / 52.9% |
| **skip_d** | 1.79 / $1,634 / 45.6% | 2.09 / $1,899 / **39.3%** | 2.56 / $2,332 / **34.0%** |
| skip_d_size | 1.86 / $2,059 / 63.8% | 2.07 / $2,294 / 59.0% | 2.57 / $2,846 / 53.2% |

`flat` is flat across `s`, as it must be — that is the internal check that `s` does
nothing without a policy.

**The prediction stated before this run was wrong.** I expected `skip_d` to beat `size`
everywhere, on the reasoning that declining the worst tier raises the *mean* p while
re-weighting only moves dollars around a fixed one, and mean p has dominated every
table in this study. It does not: at `s = 0.06`, `size` earns **$3,112 against
skip_d's $2,332**. Re-allocating risk toward the good trades compounds with position
size, which raising the mean by 4 trades a day does not.

But `skip_d` wins the axis I was not weighing: **34.0% dead against 45.5%.** That is
the largest survival effect in the whole study bar sizing itself, and it is free —
it comes from *not trading*.

So the two objectives split again, exactly as §16 found for aggression:

- **maximising cash → `size`** (risk weighted to conviction, mean unchanged)
- **keeping the account alive → `skip_d`** (decline the worst tier, size flat)

`skip_d_size` — decline the D's *and* redeploy their risk — is the worst of both: it
gives back nearly all the survival gain (53.2%) for less money than `size`.

### 17.4 How good does the call have to be?

Reading 17.1 and 17.3 together gives the practical threshold, in realized win rate
rather than in oracle `p`:

| s | A vs D realized win% | gap | best policy vs flat |
|---|---|---:|---|
| 0.02 | 58.3% vs 55.6% | 2.7 pts | +$217, and +3.5 pts death — a wash |
| 0.04 | 61.4% vs 53.5% | 7.9 pts | +$626, death unchanged — pays |
| 0.06 | 63.4% vs 51.9% | 11.5 pts | +$1,225, death −7.7 pts — pays on both axes |

**Your A trades must actually win about 5+ points more often than your D trades before
acting on the tier beats ignoring it.** Below that, the variance the policy adds costs
more than the sorting saves. That is the number to measure a blind ticket capture
against — and it is measurable with a few hundred captured calls, long before it is
worth wiring anything into sizing.

### 17.5 Caveats

- **This prices a signal; it does not create one.** The tier is drawn independent of
  the market, so its accuracy is stipulated, not earned. Nothing here says a
  pre-trade call *can* separate realized win rate by 8 points.
- **At mean p = 0.60 the whole thing is moot.** The best cell is `size` at `s = 0.06`:
  1.11 payouts, $1,121, **71.0% dead**. The signal helps proportionally more there
  (+85% on take-home) against a base that is not fundable. Edge first, always — §12
  and §16.6 reached this from two other directions.
- **`p10` is $0 in all 40 cells.** No policy moves the bad decile off zero.
- **The month view does not charge for the account it destroyed.** `skip_d`'s 34% vs
  `size`'s 45.5% is worth real money at $76–115 an account, and only §16's campaign
  can price it. That run has not been done for these policies either.

## 18. What is still open, ranked

1. **The two dead stretches, inferred but never sampled.** Time of day was the
   biggest hole and §13 closed most of it — avoid 15:00–16:00, the first hour is not
   special on random entries, and restricting the session is a variance trade the
   accounts price as a wash. What it did *not* measure directly is where the drag
   actually lives:
   **10:35–12:00 and 13:00–15:00** are where the per-trade drag lives, and they were
   measured only by subtraction. One more probe with those as explicit windows would
   confirm or kill it.
2. **Time of day interacts with a setup, and only random entries were tested.** The
   window result is about the geometry. Whether *your* edge is time-concentrated is a
   question for the reviewed book, not for this sim — and
   [`drift-fade-poc-price-action.md`](drift-fade-poc-price-action.md) already
   disagrees with §13 on the first hour, which is the flag.
3. **Funded is modelled now (§15), but two of its levers were never run together.**
   Pro's *wait-for-the-full-rung* policy and Daily's *keep-$1k-in* cushion are each
   worth more than any bracket knob; nobody has run Pro with both. And the 40%
   consistency rule is only cheap **because** the +$500 day goal caps every winner —
   what it costs without that goal is untested.
4. **I.i.d. day resampling destroys streaks, and streaks are how accounts die.** Days
   are drawn independently, so a bad week never occurs as a bad week. A block
   bootstrap would very likely raise every bust rate; the pass rates here are
   optimistic by an amount this run cannot size.
5. **The oracle is the most flattering shape an edge can take** — perfect entry
   timing, only the direction probabilistic, and independent across entries. Real
   edge is lumpier and correlated within a day, so p = 0.70 here beats p = 0.70 in
   life.
6. **The 500-tick ruler bucket**, where the morning reading would come off a volume
   clock rather than the wall clock. §13 sharpens this: the ruler really does read
   the hours differently (57t → 31t), so a volume clock would change *which* hour
   looks wide, not merely the level. §15.4 raises the stakes — the ruler is worth
   real money once sizing is fixed in dollars, so improving the reading now has
   somewhere to land. (Micros were the other item here and are answered in §14.3.)

## 19. What to take from it

1. **Decide the objective first: surviving an account, or maximising cash.** They
   give different answers to nearly every knob below (§16). If accounts are
   consumables, risk ~12% of max loss and drop the day goal; if you want the account
   to live, risk 7.5% and keep it. **Moderate beats both safe and aggressive at every
   edge level except p = 0.70**, where 20% wins — so 20% is a bet on the edge itself.
2. If the account is **LucidDaily, run a +$500 day profit goal.** It is the single
   knob that addresses the intraday floor, and it addresses it directly — and in
   funded it very nearly pays for the 40% consistency rule as a side effect (§15.5).
3. **Size down, and use micros to do it — this is the largest single effect in the
   study.** 1 NQ is oversized for a $50K account: `mnq150` (~0.7 mini at a 40t stop)
   passes **+16 points** better *and* earns **+$11 a day** more at p = 0.70 (§14.3).
   The budget is the whole thing — `mnq400` is ~1.9 minis and is worse than doing
   nothing. At mini granularity the lever does not exist (§7); that was a statement
   about the instrument, not the idea. It **inverts at zero edge**, where micro fee
   drag (~1.43x per unit of exposure) makes every micro arm worse.
4. **Funded, expect one to three payouts a month and $1,350–1,700 take-home — if
   the edge is real, and nothing if it is not** (§15.2). On Pro and Daily more than
   one month in ten pays $0 even at p = 0.70, because the first payout needs +$2,600
   and takes a median of 11–13 trading days. At a coin flip the account is dead
   inside the month nine times in ten, on all three rulebooks.
5. **The payout policy is worth more than any bracket knob** (§15.3). On **Pro,
   never take a small rung** — the ladder is five rungs for life, and waiting for the
   full cap is +$1,634 a quarter with *fewer* payouts. On **Daily, leave $1,000 above
   the buffer** — death 41.5% → 22.5% for 4% of the money. On **Flex, the same $1,000
   cushion is not optional**: taking ASAP dies 42.6% of quarters.
6. **Pick Flex only if you need cash soon, and know what it costs** (§15.1, §15.3).
   It pays on day 7.3 with a non-zero 10th percentile — the only account that reliably
   pays a *first* month — but it earns the least over a quarter (half of Pro's best)
   because you may only extract 50% of a cycle and the $2,000 cap never escalates,
   and **asking for a payout snaps your floor to $50,100 on the spot**. It is the
   only account where the act of withdrawing can kill you.
7. **Risk ~7.5% of the max loss per trade, and check it against the max loss rather
   than the balance** (§15.5). $150 on a 50K and $75 on a 25K. **$250 on a 50K is on
   the far side of the peak** — over a month it looks free (+$66) while carrying
   +14.1 points of bust in 100% of pairs, and over a quarter it loses the money too.
8. **A 25K is close to half a 50K, except on LucidDaily** (§15.5). The $500 minimum
   payout does not scale — it is 3.0 days of profit on a 50K and 6.2 on a 25K — so
   Pro and Flex converge over a quarter while **Daily's payout count halves outright**.
   Daily is the account a smaller size hurts most.
9. **If you size in dollars, use the ruler stop; if you trade a flat 1 NQ, do not**
   (§15.4). The two knobs are near-worthless alone and worth +$440 and −7.7 points
   of bust together, because fixed dollars turn a wide stop into a smaller position
   instead of more risk.
10. **If you shorten the session, do it to cut the tail, not to raise the rate.**
   One hour a day earns about a third as much and passes about the same (§13). The
   one time-of-day rule that pays on its own is **avoiding 15:00–16:00**, worth
   ~+5 pass points against either of the other two hours.
11. **On a wide day a fixed 40 earns nothing** (§14.2) — and 1 NQ on the ruler's stop
   is $613 of risk against a $2,000 max loss. Wide days need a wide stop *and* a
   smaller position; the mini cannot do both.
12. **Do not read the pass column as a bracket ranking.** It is tracking commission
   bleed at zero edge (ρ = +0.89 with mean day).
13. **The floor cost column is the durable result**: no-target/breakeven shapes give
   up the most to the intraday floor and tight stops the least. A trail is worth
   about +1 point of that back.
14. **Do not vary size or bracket by conviction until the conviction call is
   measured** (§17). At a tier that is noise, every dynamic policy loses to doing
   nothing — mean-preserving size variation alone takes death from 53.3% to 65.5%.
   The call has to separate realized win rate by **~5+ points, best tier vs worst**,
   to break even. Past that the objectives split again: **weight size to conviction
   for cash** ($3,112 vs $1,887 a month), **decline the worst tier to survive** (34%
   dead vs 53%). And the journal's A–D grade cannot be that call — it is assigned in
   review, after the outcome.
