# Manual-trade behaviour audit — paper, prop, replay

- **Date:** 2026-08-09
- **What this is:** a behavioural audit of every *discretionary* round trip in the journals — the paper session (`data/journal.db`, `account='paper'`), the six prop-firm accounts (`data/journal.db.pre-mode-folders.bak`), the in-app replay sim (`account='replay'`), and the archived ATAS replay book. No strategy logic here; the question is only **which habits cost money.**
- **> VERDICT: one habit dominates everything else — the trade that resolves inside 30 seconds.** It is ~half of all entries, it loses in every cohort, every account, every hour of the day, and both split halves. Payoff ratio 0.84 against a 54% breakeven. **But it is an entry problem, not an exit problem** — 85% of its damage is stops firing, so no minimum-hold knob can enforce it (see [§ guardrails](#what-this-implies-for-charts-guardrails)). Four folk-wisdom leaks — *revenge re-entry*, *averaging into a loser*, *consecutive-loss tilt* and *entry-rate throttling* — **do not survive a clean definition** and should not be built as guardrails. Three of them looked large on the first pass.
- **Costs:** all `net` figures are ATAS gross minus **$7.00/contract round turn** ($3.50/side, per `fill-model-verification.md`). ATAS `pnl` is gross.

---

## The book

| cohort | n | days | span | gross | **net** | win% | PF |
|---|---:|---:|---|---:|---:|---:|---:|
| paper (live chart) | 13 | 1 | 2026-08-07 | +710 | **+612** | 53.8 | 1.44 |
| prop (6 accounts) | 296 | 17 | 2026-05-26 → 07-02 | +10,430 | **+8,302** | 48.3 | 1.33 |
| replay — in-app sim | 162 | 7 | played Aug 2026 | −15,267 | **−17,969** | 40.7 | 0.63 |
| replay — ATAS archive | 2,067 | 57 | 2026-01-08 → 06-26 | −135 | **−15,801** | 42.8 | 1.00 |

Per prop account:

| account | n | win% | net | note |
|---|---:|---:|---:|---|
| 2105480 | 129 | 58.1 | +7,038 | the good one |
| 2165011 | 34 | 58.8 | +6,052 | best expectancy (+178/trade) |
| LTE050-NH5L2R46-TEST001 | 11 | 54.5 | +2,228 | |
| LFE050-CUS67R16-TEST001 | 68 | 44.1 | −818 | |
| LTE100-9GY28W6R-TEST002 | 30 | 20.0 | −3,053 | |
| LTE100-BXH602Q9-TEST001 | 24 | 25.0 | −3,145 | |

Note the shape: the ATAS replay book is **gross-flat and net −$15.8k** — the entire replay era was paid to the broker.

---

## Finding 1 — the sub-30-second trade (the whole story)

Split every cohort on `close − open < 30s`:

| cohort | fast n | fast net | fast win% | slow n | slow net | slow win% |
|---|---:|---:|---:|---:|---:|---:|
| prop | 138 (47%) | **−15,616** | 26.1 | 158 | **+23,918** | 67.7 |
| replay ATAS | 1,039 (50%) | **−130,051** | 25.3 | 1,028 | **+114,250** | 60.5 |
| replay in-app | 52 (32%) | −10,712 | 28.8 | 110 | −7,257 | 46.4 |
| paper | 5 (38%) | −890 | 20.0 | 8 | +1,502 | 75.0 |

**Every dollar the prop accounts made, and more, was made in the ≥30s bucket.**

### It is not the "losers resolve faster" tautology

The obvious objection is that a stopped-out trade is short by construction. It doesn't hold:

- **Losses are the same size in both buckets.** prop: −39.9 ticks mean fast vs −44.3 slow. replay: −38.7 vs −42.5. Fast losses are full stop-outs, not small scratches.
- **Wins are not.** Fast winners take a **median 11 ticks** (prop) / 18 (replay). Slow winners take 46 → 56 → 76 ticks as hold time rises.
- So the payoff ratio inside the fast bucket is **0.84** (prop) / 0.73 (replay) — needing a **54–58% win rate to break even, against an actual 25–26%**. The slow bucket runs 1.51 payoff at a 60–68% win rate.

The asymmetry is the finding: **when these trades are wrong they pay the full stop, and when they are right they get closed for a tenth of it.**

### It is not scale-outs

Zero of the 36 fast prop winners share an open timestamp with another leg — they are standalone trades, not partial profit-takes. Restricting to lone-leg trades only leaves the effect intact: **−88/trade fast vs +166/trade slow.**

### It replicates everywhere

- **Per day:** the fast bucket was the worse of the two on **14 of 14** prop days that had both, and **54 of 56** replay days. It lost money on 14/16 prop days and 53/57 replay days it appeared on.
- **Per account:** the slow bucket is positive in **all six** prop accounts (+49 to +650/trade). The fast bucket is negative in four and ~flat in two.
- **Per hour:** negative in 09:30–10, 10–11, 11–16 and post-16 in both prop and replay. Not an opening-bell artifact.
- **Split-half by date:** prop delta −233 (H1) / −311 (H2) per trade; replay −164 / −355. Same sign, same order of magnitude.

---

## Finding 2 — tilt is real, but the trigger is the hole, not the streak

Three formulations of tilt were tested. **Two are dead, one is strongly alive**, and the
live one is the reason the daily loss limit works.

### 2a. The trigger that works: cumulative day drawdown

Expectancy of a trade as a function of **how far down the day already was when it was
opened**:

| already down more than | prop n | prop net | prop per trade | prop win% | *everything else* |
|---|---:|---:|---:|---:|---:|
| $0 | 135 | +2,541 | +18.8 | 45.9 | +35.8 |
| $250 | 99 | +2,218 | +22.4 | 45.5 | +30.9 |
| **$500** | 58 | **−2,313** | **−39.9** | **37.9** | **+44.6** |
| $750 | 35 | −2,896 | −82.7 | 31.4 | +42.9 |
| $1,000 | 20 | −1,714 | −85.7 | 35.0 | +36.3 |
| $1,500 | 7 | −886 | −126.6 | 14.3 | +31.8 |

There is a clean inflection at **−$500 on the day**: above it the prop book earns
**+$44.6/trade at a 51% win rate**; below it, **−$39.9/trade at 38%**, deteriorating
monotonically the deeper the hole. The replay book is negative at every depth
(−$31 to −$49/trade across 890 trades) and the in-app sim likewise.

Measured from the day's **peak** instead of from zero, replay is even sharper — trades
opened while at the day's highs earn **+92.4/trade** against −18 to −33 in every
drawdown bucket — though that version sign-flips across the prop split halves, so the
from-zero cut is the one to trust.

**Split-half, "in a hole (day P&L < −$500)":** prop delta −40.9 (H1) / −107.3 (H2);
replay −19.1 / −73.4. Same sign in all four — this replicates.

### 2b. The behavioural fingerprint: you speed up after losses

Median seconds to the next entry:

| cohort | after a win | after a loss | after a big loss |
|---|---:|---:|---:|
| prop | 156s | **84s** | 84s |
| replay ATAS | 65s | **40s** | 39s |

The clip roughly doubles after a loss in both books. Combined with the earlier cascade
observation (after a sub-30s loss, 59.6% of prop's next trades are themselves sub-30s
against a 41% base), the *behaviour* change is unambiguous. What the first pass got
wrong was assuming it had to show up as a per-trade expectancy penalty indexed on
consecutive losses.

### 2c. RETRACTED — the consecutive-loss formulation

The first cut said trades taken after **2+ consecutive losses on the same day** cost
**−$42,487** in the replay book (738 trades, 32.5% win) against −$548 on prop, and that
the damage sat almost entirely in legs opened *while another leg was still live*
(replay −$37,951 over 258 such legs at a 17% win rate).

**That streak counter had lookahead.** It walked rows in *open* order and asked whether
each preceding row was a loss — but for overlapping legs the preceding row had not
closed yet, so a leg was being labelled "2 losses deep" using outcomes unknown at the
time it was opened. Recomputed using only trades that had **actually closed** before
this one opened:

| cohort | prior closed losses | not an add | an add (opened while in position) |
|---|---:|---:|---:|
| prop | 0 | −1.7/trade | −40.9 |
| prop | 1 | +108.2 | +201.6 |
| prop | 2 | −14.7 | **+95.3** |
| prop | 3 | +116.8 | −13.8 |
| replay ATAS | 0 | −15.8 | −3.1 |
| replay ATAS | 1 | −0.7 | −18.8 |
| replay ATAS | 2 | −22.1 | **+21.1** |
| replay ATAS | 3 | +3.5 | −11.2 |

No monotone pattern in either book, and the "adding while tilted" cell flips to
*positive* in both. **Counting consecutive losses does not find tilt** — count dollars
lost on the day instead (§2a). Prop earns **+$39.6/trade** after a sub-30s loss and
**+$32.2/trade** for the whole remainder of a day that contained a worst-quartile loss.
A single bad trade, or a run of them, is not what breaks the session.

### 2d. Why 2a works and 2c doesn't

A losing streak is a *count*; a hole is an *amount*. Three small losses leave the book
roughly where it started and the next trade is fine. One −$700 trade puts you in the
zone where the win rate drops 13 points. The streak counter treats those identically,
which is why it found nothing — and it is also why the **daily loss limit** was the only
knob to survive the first pass: it is the one rule that happens to be indexed on the
right variable.

---

## Finding 2e — winning vs losing days: it's the rate, not the count

| cohort | | days | mean trades | median trades | median gap between entries | % resolving <30s | win% | avg qty |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| prop | green | 12 | 15.8 | 14 | **136s** | **48.2** | 57.9 | 1.0 |
| prop | red | 7 | **15.3** | **15** | **76s** | **70.7** | 18.3 | 1.1 |
| replay | green | 28 | 28.0 | 20 | 63s | 47.0 | 53.8 | 1.1 |
| replay | red | 29 | 44.2 | 32 | 46s | 54.8 | 35.4 | 1.1 |

**On the funded accounts the trade count is identical on winning and losing days —
15.8 vs 15.3.** What separates them is that on a red day the gap between entries is cut
in half and the share of trades that resolve inside 30 seconds goes from 48% to **71%**.
Size doesn't move at all (1.0 vs 1.1) — there is no martingale here.

The replay book *does* trade ~1.6× more on red days, but that excess is hole-digging:
**64% of all trades on a red replay day were opened when the day was already −$500 or
worse** (green days: 8.5%). On prop the same split is 36% vs 10%. The extra trades are a
*symptom* of the drawdown, not the cause of it — which is why a flat daily trade cap was
refuted while the drawdown-indexed rule survives.

### The non-circular version

Day P&L is the sum of the trades, so "more trades on losing days" is partly mechanical.
The clean test measures only the **first five trades** and scores everything *after*
them:

| cohort | first 5 | pace of first 5 | days | rest-of-day per day |
|---|---|---|---:|---:|
| replay | green | fast | 8 | +201 |
| replay | green | slow | 15 | +252 |
| replay | **red** | **fast** | 19 | **−803** |
| replay | **red** | **slow** | 12 | **−147** |
| prop | green | fast | 3 | +75 |
| prop | green | slow | 3 | +638 |
| prop | **red** | **fast** | 3 | **−1,295** |
| prop | **red** | **slow** | 4 | **+825** |

Pace on its own predicts nothing — after a green start, fast and slow are the same
(+201 vs +252). **The signal is entirely in the interaction: a red start you slow down
on is recoverable; a red start you speed up on is not.** Prop's cells are 3–4 days each,
so read prop as corroboration of replay's 54-day version, not as evidence on its own.

Related descriptive stat, with the caveat that it is partly definitional (a red day's
cumulative peak is early by construction): on green days the median day peaks at trade
#8 (prop) / #16.5 (replay) and **0.5 / 1.5 trades follow the high-water mark**. On red
days the peak is at trade #1 / #3 and **8 / 28 trades follow it**.

## Finding 3 — giveback

| cohort | days green at some point | of those, closed red | median peak | median final | median giveback |
|---|---:|---:|---:|---:|---:|
| prop | 15 / 17 | **3** | +1,024 | +678 | 162 |
| replay ATAS | 50 / 57 | **22** | +899 | −122 | 1,043 |
| replay in-app | 5 / 7 | **4** | +996 | −3,480 | 3,370 |

What a hard daily loss limit would have produced (in-sample, don't fit the level):

| limit | prop | replay ATAS | replay in-app |
|---|---:|---:|---:|
| none (actual) | 8,302 | −15,801 | −17,969 |
| −$500 | 8,482 | +1,098 | −3,553 |
| −$1,000 | 8,330 | **+10,930** | −5,317 |
| −$1,500 | 6,690 | +11,020 | −8,845 |
| −$3,000 | 8,302 | −5,891 | −15,892 |

The point isn't the number — it's that a daily stop is **free on the disciplined account and worth ~$27k of swing on the undisciplined one**. That is the profile of cheap insurance.

---

## Finding 4 — the prop short book doesn't work

| month | dir | n | net | per trade | win% |
|---|---|---:|---:|---:|---:|
| 2026-05 | long | 48 | +7,759 | +161.6 | 62.5 |
| 2026-05 | short | 54 | −1,217 | −22.5 | 64.8 |
| 2026-06 | long | 112 | +1,197 | +10.7 | 42.0 |
| 2026-06 | short | 71 | −1,665 | −23.5 | 35.2 |

Shorts lose at essentially the same rate in both months, so this isn't one trend month. Note the May shorts won **64.8%** of the time and still lost money — small wins, full-size losses, the same asymmetry as Finding 1. Consistent with `balance-day-fade-study.md` ("NQ edges are day-with only").

---

## Finding 5 — the exit mix (in-app sim, where exit reason is recorded)

| exit | n | net | per trade | mean ticks | median hold |
|---|---:|---:|---:|---:|---:|
| stop | 72 | **−28,334** | −393.5 | −31.1 | 40s |
| target | 11 | +8,947 | +813.4 | +82.5 | 76s |
| trail | 18 | +1,542 | +85.7 | +12.3 | 35s |
| manual | 56 | +1,157 | +20.7 | **+5.9** | 37s |
| reduce | 5 | −1,281 | −256.2 | −15.0 | 27s |

Targets get hit 11 times against 72 stops. The 56 manual exits average **+5.9 ticks** — that is the 11-tick habit from Finding 1, visible directly in the exit reason. Paper is the same shape at n=13 (8 stops −558, 2 targets +1,096, 3 manual +74).

---

## What did NOT survive — do not build these

Both of these looked like large, obvious leaks on the first cut and are **artifacts of how the round trips were grouped.** Worth recording so they don't get re-found.

### "Revenge re-entry" — dead

First cut said: re-entering the *same direction* within 60s of a loss cost **−$5,022** (prop) and **−$62,316** (replay). But that flag was catching **scale-in legs** — rows whose open preceded the previous leg's close, i.e. adds to a live position, not re-entries at all.

Restricted to genuinely sequential trades (the prior trade had actually closed):

| cohort | same dir, <60s after a loss | net | per trade |
|---|---:|---:|---:|
| prop | 21 | +1,288 | **+61.3** |
| replay ATAS | 252 | +2,540 | **+10.1** |

Positive in both, and split-half inconsistent (prop +184 / −14). **Re-entering quickly after a loss is not a leak.** A cooldown timer would be enforcing folklore.

### "Averaging into a loser" — dead

First cut said adverse-add *episodes* returned −$176/episode (prop) and −$32,243 total (replay). That test attributed the **whole episode's** P&L to the add — but adds happen *because* price moved against you, so the flag is selecting on the outcome it claims to explain.

Measured per leg — does the added lot itself make money?

| cohort | add at worse price | net | per leg | add at better price | per leg |
|---|---:|---:|---:|---:|---:|
| prop | 25 | +620 | **+24.8** | 17 | +116.8 |
| replay ATAS | 232 | +1,350 | **+5.8** | 249 | −22.1 |

Mildly positive in both, and the split halves disagree on sign. Note this contradicts the *mechanical* result in `winner-landing-depth-study.md` — but that study tested systematic averaging-down inside a strategy, which is a different question from a discretionary trader's occasional add. **No evidence here for a `no_add_to_loser` block.**

### "Too many trades per day" — replay only

replay ATAS: capping the day at the first 15 trades turns −$15,801 into **+$4,994**, and #16+ is negative in both split halves (−14.7 / −26.4 delta). But on the prop accounts the split halves **flip sign** (H1 +55/trade, H2 −147/trade) and no cap beats the actual book by much. Overtrading is a sim habit, not a funded-account habit.

---

## What this implies for `/charts` guardrails

### The uncomfortable summary

Finding 1 is large, robust and repeatedly replicated — and **no exit-side knob can
enforce it.** In the only cohort where the exit reason is recorded (in-app replay +
paper), the sub-30s bucket decomposes as:

| exit | n | net | mean ticks |
|---|---:|---:|---:|
| stop | 20 | **−10,685** | −49.9 |
| manual | 26 | −1,896 | −4.5 (median −3) |
| reduce | 3 | −267 | −4.3 |
| target | 3 | +1,238 | +52.3 |
| trail | 5 | +8 | +7.2 |

**85% of the fast-bucket damage is stops firing** — the market resolving the trade, not
a hand on the button. The 26 manual fast exits are small *scratches* (median −3 ticks),
not the 11-tick profit-grabs the prop tick-distribution implies. A minimum-hold rule
would have touched −$1,896 of a −$10.7k problem.

So the leak lives in the **entry**, and the journal alone cannot say what made those
entries bad — that needs level context at send time, which isn't recorded yet.

### Also refuted today

- **Entry-rate throttle — dead.** Expectancy by time since the previous *entry* shows no
  usable pattern (prop: <30s +8.8, 30–60s +141.6, 1–2m −27.1, 2–5m +53.1, 5–10m −25.6).
  And the share of trades that then resolve in <30s is ~45–54% *regardless of spacing* —
  how long you wait does not predict whether the next trade is a fast one.
- **Consecutive-loss lockout — dead**, see the Finding 2 retraction above.

### What is left, ranked by evidence

1. **Daily loss limit — now the headline knob, and tiered.** It is the direct counter to
   the one tilt formulation that survives (§2a), it has a clean path-dependent
   counterfactual, and no labelling trap. Free on prop (8,302 → 8,330 at −$1,000), worth
   **+$26.7k of swing** on the replay book. Suggested tiering, off the §2a inflection:
   **warn at −$500** (the point where prop expectancy crosses from +45 to −40/trade),
   **size-cap to 1 contract at −$750**, **hard disarm at the firm's own limit × ~0.65**.
2. **`max_qty = 2`.** Prop's 99th percentile *and* maximum are both 2 contracts. Purely a
   fat-finger guard; costs nothing because it never binds.
3. **`require_bracket` + a stop clamp.** 12 prop losses ran past −80 ticks and cost
   −$6,334 against a +$8,302 book; the replay tail is 79 losses costing −$47k. Note the
   saving is *not* linear — capping the stop would have converted some of those into
   earlier, different outcomes, which this data cannot re-simulate.
4. **A sub-30s counter in the HUD** — not a rule, an instrument. Show the live trade's age
   and the trader's own running sub-30s stats (26% win / 0.84 payoff). Zero risk, and it
   targets the one finding that is actually real.
5. **A long/short flag on prop** — shorts lost in both months at a 65% win rate. Surface,
   don't block.

### Do not ship

Post-loss cooldown timers, `no_add_to_loser` blocks, daily trade caps on live,
**consecutive-loss** lockouts, entry-rate throttles. **Every one of these was refuted on
this trader's own data** — three of them after they had already looked like large,
obvious leaks on a first pass.

Note the distinction that matters: a lockout keyed on *losses in a row* is refuted; the
same lockout keyed on *dollars down on the day* is the best-supported rule in this
document. Same intervention, different index.

### What to instrument so Finding 1 becomes actionable

- **Exit reason on live fills.** The routing path already distinguishes stop / target /
  manual; the ATAS import does not. Without it the prop book's 138 fast trades can't be
  decomposed the way the sim's 57 can.
- **Entry context at send time.** Distance from the nearest reference level, and whether
  price had just moved impulsively. That is the untested hypothesis behind the fast
  bucket, and `preview()` is the natural place to capture it.

---

## Reproducing

Scripts (scratchpad, not committed): `load.py` normalises all four cohorts into one frame with direction, net-of-cost P&L, hold time, intraday sequence, prior-trade context, and position episodes; `report.py` is the 14-section behavioural sweep; `robust.py` the split-half and confound checks; `final.py` the clean per-leg re-tests; `fast.py` the Finding-1 stress test.

Two traps worth remembering for any re-run:

- `gap_s` between round trips goes **negative** for scale-in legs. Binning it without handling that silently drops them from one test and mislabels them as re-entries in another. Both errors happened on the first pass here.
- Anything measured at *episode* level and flagged by "did price move against the first entry" is selecting on the outcome. Test the added leg's own P&L instead.
