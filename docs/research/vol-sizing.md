# Vol-conditioned sizing: the stop is the tail

*2026-08-17 · `data/research/replay-trail/vol_probe.py` + `vol_size.py` · 66 sittings, 584 trades*

## The question

Read the vol ruler before placing a trade, set a max risk in dollars, and let a
recommender work out size, stop, trail and target from there. The instinct
behind it: a 50-tick stop is not the same risk on a quiet tape as on a hot one,
and a prop account with a $2,000 end-of-day trailing floor cannot afford to find
that out the expensive way.

The instinct is half right, and the half that is wrong is the half that would
have gone into the formula.

## What was run

Every stored replay sitting, re-run through the ported fill engine
(`whatif.py`), with the vol ruler's own references recomputed **causally** at
each fill — ATR(14) and the developing 10:00 median, on both 30-second clock
bars and 500-tick bars.

Two fixes to the harness were needed first, and both matter beyond this study:

- **`latencyMs` was not modelled.** The 250 ms gesture lag arrived after
  `whatif.py` was written, so every sitting recorded since failed to validate —
  trade counts matched exactly, nets were off by $5–7 a trade. Ported from
  `replaySim.ts` `stepSim` (`at = ms − lag`, gestures land at `ms + lag`).
- **Micros were priced as minis.** Orders carry `micro`, `prefs.commission`
  carries the micro rate; the port hardcoded NQ's $20 point value.

Validation went from 46/84 sittings to **66/84**. With `latencyMs: 0` and no
`micro` key both additions are inert, so the earlier trail and R-multiple
studies are unaffected.

## Finding 1 — vol says nothing about the trade

Per contract, so size cannot confound it:

| reference | ρ(vol, ticks per contract) |
|---|---|
| ATR 30s | +0.04 |
| ATR 500t | +0.05 |
| dev median 30s | +0.13 |
| dev median 500t | +0.11 |

Noise. The vol read carries no information about whether the trade works. This
is the fifth independent confirmation of the same thing — see
[atr-trail](atr-trail.md), the σ-acceptance fail, the winner-landing-depth
study, and the triple-barrier relabel.

## Finding 2 — but it does predict what a loss costs

Among losing trades, restricted to the 39 sittings that placed a **uniform 50t
stop** (ρ(placed stop, vol) = +0.06, so the user is not widening on hot days —
the confound is dead):

| | avg loss | worst | win rate |
|---|---|---|---|
| quiet | −26.5t | −65t | 30% |
| mid | −32.8t | −56t | 42% |
| hot | −40.8t | −71t | 54% |

ρ(vol, |loss|) = **+0.31**. And the mechanism, stated plainly:

| | loss ÷ placed stop | share landing *past* the stop |
|---|---|---|
| quiet | 0.46 | 17% |
| mid | 0.68 | 31% |
| hot | **1.04** | **53%** |

On a quiet tape the median loss is *half* the stop — the trade is exited before
it. On a hot tape the median loss is the *whole* stop and half of them slip past
it. The same 50-tick bracket is roughly 23 ticks of realized risk in one regime
and 52+ in the other.

## Finding 3 — which is why the formula does not want a vol term

The obvious next step is to size off expected loss rather than the placed stop.
It fails, and the reason is the useful part:

| vol bucket | n | median loss | **p90 loss** | max |
|---|---|---|---|---|
| 31–41t | 42 | 25t | **51t** | 52t |
| 41–43t | 41 | 21t | **52t** | 65t |
| 43–49t | 42 | 30t | **52t** | 52t |
| 49–64t | 41 | 52t | **53t** | 56t |
| 64–103t | 42 | 52t | **53t** | 71t |

**The median triples. The p90 does not move at all** (fitted slope 0.047 t per
tick of vol — nothing). The tail of the loss distribution *is the stop*, at
every vol level, because capping the tail is what a stop does.

So there is nothing left for a vol term to protect. Vol does not change your
worst case; it changes how *often* you pay it.

## The arms

Sizing applied to the trade list rather than re-run — this engine has no market
impact, so contract count cannot change which fills happen. All arms hold the
placed stop.

| arm | net$ | maxDD | worst trade | median loss | p95 loss | >$250 | >$500 |
|---|---|---|---|---|---|---|---|
| as-played | −29,045 | 48,426 | −15,876 | $204 | $686 | 43% | **13%** |
| flat 1 lot | −10,623 | 17,731 | −500 | $172 | $304 | 34% | 0% |
| **budget ÷ stop** | −10,912 | 17,162 | −540 | **$165** | **$298** | **32%** | **0%** |
| budget ÷ E[loss\|vol] | −19,575 | 29,787 | −750 | $254 | $534 | 51% | 11% |
| …never sizing up | −10,923 | 17,162 | −540 | $164 | $298 | 32% | 0% |

Rescaled to equal exposure so no arm can win by levering down: as-played
−$16,806 with a −$9,186 worst trade, against −$10,623 and −$500 flat.

The vol-conditioned arm is the **worst** of the disciplined arms. It sizes *up*
on a quiet tape (E[loss] < stop there), and quiet-tape trades are this book's
weakest — 30% win rate, −12t a contract. The asymmetric version that never sizes
up is indistinguishable from the plain rule, which is the honest verdict: the
vol term contributes nothing.

## What to build

**The formula needs no vol term.**

```
size = floor( maxRiskUsd / (stopTicks × tickUsd + 2 × commissionPerSide) )
```

Two things fall out of the measurement that belong in the UI:

- **$250 does not fit 1 NQ on a 50-tick stop.** 50 × $5 + $7 = $257, and
  measured, **32% of losses still exceed $250** even at one contract, because
  losses slip past the stop. Either the ceiling is ~$270, or the stop is ~45t,
  or it is 9 MNQ. `shapeRefusal` currently computes risk *gross* and passes this
  at exactly $250 — recommender and guard must agree, and net is the honest one.
- **Inconsistent size is the leak, not the bracket.** 13% of as-played losses
  exceeded twice the budget; the disciplined arms produce zero. The single worst
  trade goes from −$15,876 to −$500.

**The vol ruler's real job is a session dial, not a ticket field.** It predicts
the *rate* at which full stops get paid (17% → 53%), which is a statement about
how many trades the day can afford — not about how big any one of them is. A hot
read means *fewer trades or fewer contracts*, decided once, not a wider stop
decided per ticket.

## What got built (2026-08-17)

A different rule from the ones tested above, and worth being precise about why
that is legitimate: every arm here held the stop where it was placed and moved
only size. **"Vol sets the stop, budget sets the size" was never tested** — and
one finding argues for it. At a fixed 50t stop the loss ran 0.46x the stop when
quiet and 1.04x when hot, so a fixed distance is too far to be reached in one
regime and too close to survive in the other. A stop that tracks the ruler sits
in the same place in the noise every day. That is a claim about *placement*, not
about edge, and it is the one being tested by hand now.

Shipped as the **Σ on the floating ticket** (`frontend/src/lib/riskSizer.ts`,
panel in `TicketKnobs`, both `/charts/replay` and `/live`):

- stop = **1.0 x** the ruler's ATR reading, at the drawn timeframe;
- three presets whose budgets are the **daily loss limit divided by how many
  losers the day should absorb** — safe 6, moderate 4, aggressive 3;
- both routes priced side by side, minis and micros, each carrying its own
  losers-to-the-floor count;
- risk **net** of both sides' commission.

Two behaviours fall out of the arithmetic rather than being written as rules.
The budgets ride on the account's *remaining* day loss, so they shrink as the
day goes badly. And as the ruler widens the minis simply stop fitting and drop
out of the safe and moderate rows — the size-down signal, with no threshold
anywhere. On a live check at a 51t reading with $636 of day-loss room left, the
panel offered 4/6/8 MNQ and no mini at any appetite.

Nothing recommends a target or a trail: `min_target_ticks` (100) and the
measured-best 1R exit contradict each other, and a recommender that guesses on
an open question is worse than one that stays quiet.

Pinned by `tests/test_risk_sizer.py` against a fixture generated from the real
lib (`tools/ticket-sizer/run.sh`), and by `tools/browser/sizercheck.mjs`.

## Caveats

- The book is net negative across all 584 trades at any size (−$18/contract
  flat). These are blind drill and replay sittings including a lot of learning.
  The study speaks to the *shape* of risk, not to profitability, and every arm's
  account is caught by the floor at the same 8th sitting.
- ρ = +0.31 means vol explains ~9% of loss-size variance. Real, weak.
- The developing median is undefined before 10:00 ET, and **318 of 421 entries
  land in the 9 o'clock hour**. For opening-drive trading the pane's middle line
  is not available; ATR and yesterday's settled median are what exist.
- 18 sittings still do not validate, including the two best ever recorded —
  frozen by a pre-refactor engine build (see [replay-exit-whatif](replay-exit-whatif.md)).
