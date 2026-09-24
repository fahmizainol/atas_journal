# Participation floor — resolved null (volatility renamed)

**Question.** Creamer's one genuinely new knob ([teardown](creamer-orderflow-interview-2026-08.md)):
*below 20,000 contracts per 5-minute MNQ candle, don't trade — participation is
dying and moves won't carry.* Nothing in the study log gates on volume regime,
so this was the only untested cell in the whole interview.

**Answer: it does not survive as an independent knob.** Volume-for-time-of-day
predicts **how big** the next move is (2.04×) and says **nothing** about how well
it carries (1.02×). It is realised volatility wearing different clothes, and
volatility is already spoken for by [vol-clock](vol-clock.md). Do not build it.

His *rule* is probably still right in practice. His stated *reason* is not — and
the difference matters, because the reason is what would have been coded.

---

## Method

Script: `data/research/participation-floor/screen.py`. 599 sessions,
2024-03-04 → 2026-06-30, 42,354 RTH 5-minute bar-observations (10,782 in the
first-90-minute variant).

**Two porting problems, both handled.**

*Wrong instrument.* The tick cache is NQ; his threshold is MNQ — 1/10 the
notional and a retail-heavy participant mix. There is no fixed divisor between
them, because the ratio drifts with time of day and regime. So the constant was
discarded and the concept ported as a percentile.

*The clock confound.* Volume declines into lunch by construction, so a raw
volume gate is partly a clock in disguise — and the clock is already adopted.
Measured: **ρ(log volume, minutes-from-open) = −0.529**. High collinearity does
not kill the idea; it means the raw gate is untestable and the residual is what
must be tested:

```
resid = log( bar volume / median volume for that time-of-day slot )
```

"Unusually quiet **for this time of day**", not "quiet".

**Outcome is his own claim, not a P&L.** Does the move carry?

```
eff = |net move over next 30 min| / (sum of |5-min moves| over that span)
```

Directional efficiency — 1.0 is a straight run, ~0.2 is chop ending where it
started. Model-free, so it screens the premise before any strategy work is spent
on it. The anchor bar is excluded from the outcome, per the touch-bar discipline
from [weekly-vwap-context](weekly-vwap-context.md) and
[structure-orderflow](structure-orderflow.md).

---

## Results

**The residual predicts size, not quality.**

| | ρ (Spearman) |
|---|---|
| residual → forward path (realised vol) | **+0.376** |
| residual → forward net move | +0.230 |
| residual → **efficiency** | **+0.012** |

**Quintiles of the residual** (1 = unusually quiet for the time of day):

| q | n | eff | net pts | path pts | volume |
|---|---|---|---|---|---|
| 1 | 8,471 | 0.401 | 25.87 | 65.03 | 2,317 |
| 2 | 8,471 | 0.410 | 31.82 | 77.40 | 3,970 |
| 3 | 8,470 | 0.413 | 37.70 | 91.19 | 5,111 |
| 4 | 8,471 | 0.414 | 43.98 | 106.88 | 6,461 |
| 5 | 8,471 | 0.409 | 52.68 | 130.30 | 8,620 |

Net move and path both roughly **double** from Q1 to Q5. Efficiency is flat to
three decimal places across the entire volume spectrum:

```
Q5/Q1  net move  2.04x
Q5/Q1  efficiency 1.02x
```

**Holding volatility roughly fixed, the residual adds nothing.** Efficiency for
resid-Q1 vs resid-Q5 *within* bands of forward path:

| path band | resid Q1 eff | resid Q5 eff | diff |
|---|---|---|---|
| 1 | 0.420 | 0.409 | +0.011 |
| 2 | 0.389 | 0.412 | −0.023 |
| 3 | 0.379 | 0.411 | −0.031 |
| 4 | 0.403 | 0.422 | −0.019 |
| 5 | 0.416 | 0.396 | +0.020 |

Sign flips three times, no consistent direction. Once realised volatility is
held roughly constant the residual carries no information about move quality.

**Same answer in his own window.** Restricted to the first 90 minutes:
ρ(residual, efficiency) = **+0.003**, quintile efficiency 0.420 / 0.415 / 0.422 /
0.422 / 0.418. Flat.

**One result that does not survive conditioning.** Q1-vs-Q5 clustered by session
(n = 428) gives efficiency +0.0280 in favour of the *quiet* bars, paired
t = +2.90 — the opposite of his claim, and nominally significant. It should not
be believed: it is ~7% relative on a flat pooled table, it does not survive
conditioning on forward path above, and the residual is computed globally so
session membership in Q1/Q5 partly compares *sessions* rather than bars within
them. That is the Simpson shape from [vah-snap-resistance](vah-snap-resistance.md).
Recorded, not adopted.

---

## Why the rule probably works anyway

Moves in quiet periods **carry just as well proportionally** — efficiency sits at
0.40–0.41 whether the bar is in the quietest or busiest quintile. They do not
fail to follow through. What actually happens is that they are **half the size**
(25.9 pts vs 52.7 pts).

Against fixed costs — $3.50/side commission plus a 1-tick spread and queue, from
[fill-model-verification](fill-model-verification.md) — and a fixed stop
geometry, a 26-point move is a materially worse proposition than a 53-point one
at identical quality. The edge in his rule is not *"moves don't carry"*, it is
*"there is half as much to win against the same cost"*.

Which is a statement about volatility, not participation. A gate correlating
+0.376 with realised volatility and +0.012 with move quality is measuring
volatility with extra steps — the same result as
[atr-vwap-band](atr-vwap-band.md), where the σ-band turned out to be intraday ATR
renamed at ρ .96.

## Verdict

**No knob.** The participation floor collapses into the ATR/vol-clock finding
already adopted. Anything it would have gated is better gated on ATR, which is
measured directly rather than inferred from contract counts, and which does not
carry the MNQ→NQ porting problem.

Residual value: the screen itself. `screen.py` computes time-of-day-residualised
bar features against forward follow-through and is reusable for the next "new
regime gate" proposal — of which there will be more.

**Standing conclusion:** volume regime, as an independent input, is dead here.
Do not re-test it without a mechanism that is not volatility.
