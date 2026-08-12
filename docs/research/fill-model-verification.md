# The fill model vs the account that actually paid

**Date:** 2026-08-08
**Data:** 479 live prop-firm executions across 6 accounts, May 26 – Jul 2 2026,
from the July-11 DB backup (`journal.db.pre-mode-folders.bak` — the live
accounts were never re-imported into the current `journal.db`), raced against
the cached tick tape.
**Model under test:** `frontend/src/lib/fillModel.ts` — `commission: 7`/side,
`slipTicks: 1`, `queueTicks: 1`.
**Script:** `data/research/fill-model-verify/verify.py` (per-fill results in
`fills.parquet`, headline numbers in `summary.json`).
**Verdict:** the tape is the tape you traded (fills match it to ~8 ms), the two
tick knobs are confirmed — 1 tick of spread is the modal cost of crossing and
the 1-tick queue rule is right for ~86% of real passive fills — but the
**commission default was 2× reality**: these firms charged **$3.50/side**
($2.55 on one), not $7. **Adopted same day**: `DEFAULT_FILL_MODEL.commission =
3.5`, with a one-time `loadFillModel` migration mapping a stored 7 (the
two-day-old shipped default, never a user's choice) down to 3.5.

---

## 1. Why this dataset can judge the model

Every fill in the executions table is a real CME trade with a millisecond
timestamp, a price, a size, and (on five of six accounts) the commission the
firm actually charged — struck on the same feed the tick cache holds. That
makes it the one ground truth the Replay/Live fill engine can be checked
against: not "does the sim feel right", but "does the account the sim charges
match the account that charged me".

One cohort had to be excluded up front: the 71 fills of 2026-06-16 were struck
on NQU6 (ATAS says so, and their price levels sit hundreds of points off the
NQM6 tape) but the volume-roll map hands that session to NQM6 and no NQU6 tape
for the day was ever bought. Untestable, not evidence of anything. Denominator
below: 408 fills.

## 2. Tape fidelity — the license for everything else

**362 of 408 fills (88.7%) match a same-price print within ±2 s** of their
journal stamp. Median clock skew **+8 ms** (p10 −172 ms, p90 +787 ms):
Rithmic's stamps and Databento's tape are the same clock for practical
purposes, and the tape the replay fills from is demonstrably the tape the
account traded.

The 46 misses are the skew tail, not missing data: every one of their prices
printed on the tape somewhere (median 12.8 s away, 21 within 10 s), clustered
on fast tape where the same price doesn't linger. No fill's price failed to
exist.

## 3. `slipTicks: 1` — confirmed as the mode, with an honest tail

The matched print's aggressor flag classifies each fill: my buy matching a
buy-aggressor print means I crossed the book; matching a sell-aggressor print
means my resting bid was hit. **264 aggressive / 98 passive** (73% aggressive —
a scalper's book).

For aggressive fills, the distance from the print immediately before the fill
is what crossing actually cost:

| adverse ticks | ≤0 | +1 | +2 | +3 | ≥4 |
|---|---|---|---|---|---|
| share | 12% | **52%** | 14% | 12% | 11% |

Mode **+1 tick** — the model's constant is the single most common outcome, and
the "book is one tick wide almost all the time" premise holds. Mean is 1.69
because of the tail: a third of aggressive fills paid 2+ ticks, and that
measurement is *charitable* (it references the print microseconds before the
fill; referencing the price at click time, which is what the sim does, would
read wider). The tail is latency-plus-momentum — the cost the model's header
explicitly declines to model — so the right reading is: `slipTicks: 1` is
correct for the calm-tape majority, and fast-tape entries cost ~1 extra tick on
average that the sim does not charge. A user who wants the pessimistic book can
set 2; the default stays honest.

## 4. `queueTicks: 1` — right rule, slightly strict

The engine refuses a passive fill until the tape trades a full tick through the
level. Of the 98 real passive fills, **86% had that trade-through within the
60 s before the fill** (68% within 10 s). The 14 fills without one are fills
the replay would have delayed or denied — i.e. the rule denies ~1 in 7 fills
that reality granted (favorable queue position), and grants nothing reality
refused. That is the correct side to err on for a practice account: it can make
the sim slightly *harder* than live, never easier. Keep 1.

## 5. `commission: 7` — wrong by 2×

What the firms actually charged, per contract per side, straight off the
executions table:

| account | contracts | charged | per side |
|---|---|---|---|
| 2165011 | 67 | $234.50 | **$3.50** |
| LTE100-9GY28W6R | 74 | $259.00 | **$3.50** |
| LTE100-BXH602Q9 | 50 | $175.00 | **$3.50** |
| LTE050-NH5L2R46 | 22 | $77.00 | **$3.50** |
| LFE050-CUS67R16 | 140 | $357.00 | **$2.55** |
| 2105480 | 172 | not recorded | — |

$3.50/side — $7 the **round turn** — at four of five accounts that recorded it.
The model's default charged $7/side ($14 the round turn): the practice account
was paying double the real toll, which biased every replay-vs-live comparison
and punished scalps twice as hard as the funded account would. The likely
origin of the error: "$7" was remembered from the statement, but it was the
round turn.

**Fixed same day**: `DEFAULT_FILL_MODEL.commission = 3.5` and the tooltip copy
with it, plus a one-time migration in `loadFillModel` — a stored commission of
exactly 7 is read back as 3.5, because 7 was the shipped default for the
model's first two days and never a number anyone chose; without the migration
the saved model would keep overcharging every browser that had already opened
the Simulator.

## 6. The journal's P&L columns, decoded

For every single-lot round trip (288 of 296): `pnl = price_pnl × $20 =
profit_ticks × $5`, and `price_pnl` is the signed open-to-close price
difference in points on all 296. So **`pnl` is gross** — commission is *not*
inside it (total commissions ≈ $1,100 vs a pnl-minus-gross residual of just
+$325, all from the 8 multi-lot rows, where ATAS's lot handling is
inconsistent). Any engine-vs-journal P&L comparison must therefore compare
gross points and add commission separately — which is exactly how the fill
engine books (`pnl = pts × pointValue × size − fees`), so the arithmetic is
like-for-like once the commission constant is fixed.

## 7. What this does not verify

No order type, resting level, or stop trigger survives in the export — fills
only. So the queue and slip tests are inferential (aggressor-flag
classification), the stop's gap-through booking is untested here (it is,
however, data-honest by construction: it books a print that existed), and
latency stays unmodelled and conflated with skew. The 22 July fills had tape;
the 71 Jun-16 fills did not (wrong contract cached).
