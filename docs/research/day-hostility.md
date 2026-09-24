# Can a day be labelled unfair? — 2026-03-25, and the number that was actually wrong

**Date:** 2026-08-30
**Data:** 384 cached NQ RTH sessions, 2025-01-02 → 2026-06-30 (370 of them for the
level pass, which needs a prior session and the overnight on disk). Entries are
placed **every minute through the window, on both sides**, so no entry signal is
involved anywhere — this is a base rate of geometry, not of a setup.
**Scripts:** `data/research/day-hostility/` — `base_rate.py` (the stop-sizing
sweep), `level_depth.py` (how far price pokes through a level), `predictors.py`
(does any causal KPI separate a bad morning), `the_day.py` (2026-03-25's own two
trades), `report.py` (ranks any day against the corpus). Fills delegate to
`src/journal/replay_whatif.py` via `bracket_survival.py`, so the arithmetic is the
one the replay UI runs.
**Question:** 2026-03-25 felt like a day where every trade got stopped and no
watched level was respected. Is that a property of the day, and can it be labelled
early enough to stand down?
**Verdict:** **no, on both counts, and the premise is wrong.** Measured at the
day's own volatility the morning was the **93rd percentile** of 384 sessions for
random entries and the afternoon the **97th** — it was one of the *best* mornings
in eighteen months, not one of the worst. The same tape ranks in the **4th
percentile** when the stop is 30 ticks. **The variable was never the day; it was
the ratio of the stop to the day's bar width.** The level complaint is the same
number wearing a different hat: at a fixed 10-tick tolerance the day is
bottom-quintile for level respect, at a tolerance that scales with the session it
is dead average (53rd percentile) and its penetration-to-volatility ratio is
slightly *better* than median. And **no causally-knowable KPI earns a stand-down
rule** — the strongest one at 10:30 separates its best and worst deciles by 1.3
points on a base of 33.7, which is noise. This is the fourth independent time this
repo has looked for a day-quality gate and not found one.

---

## 1. What the day actually was

RTH opened at 24430.75 and covered 239.5 points (24286.50 – 24526.00). The median
**one-minute bar between 09:30 and 12:00 was 85 ticks — 21.25 points**, against a
whole-day median of 65.5. The vol ruler (`PresetRuler`'s `s30`, the median 30-second
bar range since the bell — the number every bracket preset sizes from) read **68t
median across the morning against a corpus median of 57t**, and **84–88t at the
two moments that matter below**.

The day was also **already labelled before the bell**. `sim/vol_regime.py` carries
a shifted daily-ATR percentile — it only ever sees the *prior* session — and
2026-03-25 walked in at **percentile 0.966, "hot"**. So did every other session
that fortnight; late March 2026 was a hot stretch end to end.

So a 30-tick stop that morning was **roughly a third of a single one-minute bar**.

## 2. The same tape, priced at four stop sizes

Identical entries, identical fills. The reading is the **2R** target share — how
often a random entry reaches twice its stop before losing it. 1R is ~50% by
construction on a two-sided sample (long's target is short's stop) and measures
almost nothing; 2R requires the day to actually go somewhere.

| stop | 2R target share | percentile | net/trade | percentile |
|---|---|---|---|---|
| **ruler (68t)** | **0.376** | **92.7** | **+$32.16** | **96.4** |
| 56t | 0.345 | 58.3 | −$1.36 | 59.6 |
| 40t | 0.338 | 45.3 | −$8.41 | 47.7 |
| **30t** | **0.300** | **3.6** | **−$26.33** | **4.4** |

*(AM window, 09:35–12:00. 09:35 rather than 09:30 because the ruler refuses to read
under three closed 30-second bars and has no fallback leg — the ticket would not
arm either.)*

**Bottom 4% and top 7% are the same session.** The only thing that moved is the
stop. A day cannot be hostile and generous at once, so "hostile" was never a
property of the day.

The afternoon, which the question left open: **top 3% at the ruler stop and
top 15% at every fixed stop tested.** 2026-03-25 after 12:00 was an excellent
tape by any sizing.

## 3. The levels were respected exactly as usual

Same logic applied to the second complaint. For each Tier-A level (overnight
high/low, prior high/low/close) that was in play, every excursion past it is
measured: how far beyond does price travel before returning. A level is only
"respected" relative to how far past it you were willing to let price go, so the
tolerance is the variable, not the level.

| metric | 2026-03-25 | corpus median | percentile |
|---|---|---|---|
| median penetration | 6.0t | 5.0t | 53.5 |
| 75th-pct penetration | 15.0t | 14.0t | 58.6 |
| **held within a fixed 10t** | **0.639** | **0.688** | **21.1** |
| **held within ¼ of the day's ruler** | **0.768** | **0.761** | **52.7** |
| penetration ÷ ruler | 0.086 | 0.091 | 43.2 |

At a **fixed** tolerance the day is bottom-quintile and the complaint is correct
as felt. At a tolerance that **scales with the session** it is the 53rd percentile
— the middle of the corpus — and the penetration-to-volatility ratio is *better*
than median. 233 excursions across 5 levels is a large enough sample that this is
not a thin-day artifact.

The levels held as well as they ever do. The 10-tick ruler held against them did not.

## 4. There is no day-label worth standing down on

The obvious follow-up: fine, the day was fine *for a scaled stop* — but is there
some other day that genuinely isn't, and can it be spotted by 10:30? Every KPI in
the regime artifact (`sim/regime.py` v8, read at its causal 09:45 and 10:30
checkpoints) was scored against the vol-scaled outcome, deliberately the
normalised one so the exercise cannot simply rediscover volatility.

| best predictors | ρ | worst decile | best decile |
|---|---|---|---|
| 10:30 `chop_occ_rth` | −0.173 | 0.344 | 0.331 |
| 10:30 `chop_occ_30m` | −0.151 | 0.338 | 0.334 |
| 09:45 `ny_touch_hold_ratio` | +0.140 | 0.334 | 0.343 |
| 10:30 `st_choch_rate` | +0.137 | 0.332 | 0.348 |

Corpus mean 0.337. **The strongest signal available moves the base rate by 1.3
points between its extreme deciles**, and the sign on chop is the *opposite* of
the intuition — choppier mornings scored marginally *better* for a 2R random entry,
because chop at this scale means wide two-sided swings, not stillness.

This agrees with everything this repo has already found: the market-structure
study's gate A/Bs failed, the regime v8 artifact shipped deliberately with no gate,
and the gate-robustness scorecard flagged chop as luck-suspect. A stand-down rule
on day type is not supported by four independent passes at it.

## 5. The day's actual record: two trades

The corpus work above is about what *could* have happened. The journal says what
did — and it is two trades on a `replay` account, both **short**, both inside the
first nine minutes, held **28 seconds** and **12 seconds**.

| | entry | exit | held | against | result |
|---|---|---|---|---|---|
| **A** | 09:35:31 short 24466.00 | 24479.00 (`stop`) | 28s | 52t | −$267 |
| **B** | 09:38:30 short 24506.00 | 24518.50 (`manual`) | 12s | 50t | −$257 |

**Trade A was stopped correctly and the stop saved money.** Price ran 240 ticks —
60 points — against it, to the session high, before it turned. No survivable stop
existed; the ruler's 88t would have lost too, and larger. −$267 was the cheap
outcome.

**Trade B is the whole study in one trade.** Its worst adverse excursion over the
next 30 minutes was **63 ticks**. The ruler at that instant read **84t**. He closed
it manually at **50t**. Price then fell **714 ticks — 178 points — within thirty
minutes**, in his direction.

> The read was right. Both trades were shorts near the session high on a day that
> closed 178 points lower. **A stop sized by his own tool survives B and the trade
> pays for the month.** A 50-tick stop does not, and neither does closing it by
> hand twelve seconds in.

This is the sub-30-second exit the manual-trade behaviour audit already named as
the one real leak, and it cost the day's best move.

## 6. What to actually do

**Not** a day filter. The number to watch is a ratio, and it is knowable from
09:31:30:

    stop ÷ vol ruler (s30)

At 1.0 the morning ranked 93rd percentile. At **0.44** (30t against a 68t reading)
it ranked 4th. That single ratio reorders the day from the top decile to the bottom
decile of eighteen months, and it is the one thing on the ticket that was wrong.
The bracket presets already size from exactly this reading — the failure on
2026-03-25 was overriding them, not lacking them.

Two caveats worth stating plainly. Sizing the *stop* to volatility without sizing
the *position* to the stop just converts a tick loss into a dollar loss — the Σ
sizer's job, and the reason those two panels sit together. And a hot day at ruler
width is only cheap in ticks: 84t on 1 NQ is $420 of risk, which is most of a
LucidPro daily budget on one trade. **The honest conclusion is not "trade it with a
wider stop" but "trade it smaller with a wider stop, or not at all — and if not at
all, say so because the risk is too large, not because the day is unfair."**

## 7. Reproducing

```
.venv/bin/python data/research/day-hostility/base_rate.py     # ~5 min, 384 sessions
.venv/bin/python data/research/day-hostility/level_depth.py   # ~4 min, 370 sessions
.venv/bin/python data/research/day-hostility/predictors.py
.venv/bin/python data/research/day-hostility/the_day.py
.venv/bin/python data/research/day-hostility/report.py 2026-03-25
```

`report.py` takes any date, so the next day that feels unfair can be put through
the same three questions rather than argued about.
