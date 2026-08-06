# Does a stable ("flat") VP level hold as support/resistance?

**Question** (follow-up to the VAH-snap study): the snapped/violently-relocated
VAH didn't act as resistance. What about the opposite — a POC/VAH that has been
*sitting still*? Does a stable, non-moving VP level hold as S/R when price
arrives?

**Verdict: NO — stability makes no difference, and developing VP levels are not
reliable S/R in the first place.** Across 13,284 touches (5,759 in RTH), price
breaks *through* a developing POC/VAH/VAL slightly more often than it rejects off
it (54.5% break / 45.5% hold), and how long the level had been stable does not
move that number at all: fresh levels hold 45.6%, entrenched (>60 min) levels
hold 45.7%, permutation p = 1.00. The hold rate is a coin-flip-below-even at
every age and every flatness, and it flips sign across the two halves of the
sample — the signature of no signal.

## Design

`stable_level_study.py` + `analyze_stable_level.py`. All causal
(`levels_in_force`, the engine's own reading; Globex-anchored bands). 360
sessions, 2025-02-03 → 2026-06-30, six developing levels: {Globex, NY} ×
{POC, VAH, VAL}, 1-minute grid.

- **Touch event**: price arrives within 6t of the level (prior minute was
  farther). Approach direction sets the test — from below = resistance test,
  from above = support test. 30-min dedup per level.
- **Stability** (the independent variable): `age_min` = minutes the level had
  sat within ±2t of its touch value (backward scan, capped 180 min);
  `drift_30m_t` = max level move over the prior 30 min (flatness).
- **Outcome**, first-to-hit on ticks within 60 min: **hold** = price fell 15t
  back on the approach side first (level rejected it); **break** = price traded
  12t through the level first. (Decisive on essentially every touch.)
- **Confound guard**: recorded approach distance and velocity, since a stable
  level might simply sit farther from price.

## Results

**Hold rate by level age** — dead flat around 45%, never reaching the 50%
coin flip:

| age at touch | n (decisive) | HOLD | break |
|---|---|---|---|
| fresh <5m | 3,265 | 45.1% | 54.9% |
| 5–15m | 914 | 47.4% | 52.6% |
| 15–30m | 481 | 45.5% | 54.5% |
| 30–60m | 499 | 45.1% | 54.9% |
| 60–120m | 322 | 44.1% | 55.9% |
| entrenched >120m | 278 | 47.5% | 52.5% |

- **No stability effect**: fresh (<15m) 45.6% vs stable (≥60m) 45.7%,
  permutation p = 1.00; Spearman(age, held) ρ = +0.015. Flatness by
  `drift_30m` is equally flat (45.5–46.2% across all bins).
- **No confound rescue**: slicing age × approach-distance, no cell shows a
  stable-level edge — the highest hold is *fresh + near* (52%), the exact
  opposite of the hypothesis.
- **POC is marginally the best of a bad lot**: stable Globex POC holds 47.9%
  (n=280), stable NY POC 43.5% — both still below even, edges (VAH/VAL) too
  thin to read.
- **Not robust in time**: the stable-cohort hold rate is 40.2% in the first
  half and 52.4% in the second — it straddles the coin flip and flips, which
  is what a non-signal looks like.

## Where the touches do least badly (the fresh+near cell)

Slicing hold rate on age × approach-distance, one cell stands out — and it is
the *opposite* of the stability hypothesis:

| | near ≤12t | mid 12–25t | far >25t |
|---|---|---|---|
| **fresh** <15m | **52.0%** (446) | 45.2% (838) | 45.9% (1382) |
| **mid** 15–60m | 46.8% (171) | 41.6% (361) | 46.7% (418) |
| **stable** ≥60m | 41.6% (125) | 44.6% (177) | 48.4% (289) |

Fresh+near: 52.0% hold, 95% CI [47.4%, 56.6%]. It beats the 45.5% base rate
(z=+2.74, p=0.006) and is robust — split-half 53.0% / 50.6%, permutation vs all
other cells p=0.006, clean monotonic distance dose-response within fresh
(6–12t 49.8% → >35t 44.4%), POCs consistently >50%. **But** it is
indistinguishable from a 50% coin flip (z=+0.85, p=0.39).

The driver is **proximity, not age**: down the `near` column the rate *falls*
with age (fresh 52% → stable 41.6%), so an old nearby level holds worse, not
better. A just-formed level right where price is trading is a genuine balance
point — a real coin flip. A level touched from far away (>35t) or a stale one is
an arrival *with momentum*, and momentum carries through, which is why those
cells sag to ~44%. So the one non-losing S/R context is freshly-formed + nearby +
POC + tested as support (56%) — and even that only reaches a coin flip.

## Why this fits everything else

This is the same result the repo keeps finding: **VP geometry has no durable
predictive edge here** (drift-fade POC price-action, market-structure, and
gate-robustness all landed null on shape/confluence/geometry). A developing
level is not a wall that price respects — it is a magnet that gets consumed.
A stable level just means volume kept trading *there*; when price finally
leaves, it is as likely to be pushing through as bouncing. The one real VP
signal in the codebase, `gx_poc_shape`, is about the node's *position relative
to VWAP* (thin-rally vs accepted-value), not whether a level acts as S/R.

**Caveat on the absolute number**: break (12t) triggers on a slightly shorter
move than hold (15t), which biases the 45% a touch low — but the *comparison
across age buckets* is threshold-independent, and that is what answers the
question. Even generously, there is no stability edge.

## Robustness re-run at symmetric 30/30t (2026-07-27)

Full 360-session re-score with hold and break both at **30 ticks** (7.5pt —
swing scale, and symmetric, which removes the caveat above):

- Overall hold snaps from 45.5% to **49.3%** — the honest number is a clean
  coin flip. The sub-50% at 12/15t was the threshold asymmetry, as suspected.
- The stability null is unchanged: fresh 48.6% vs stable ≥60m 50.0%
  (perm p=0.54), Spearman(age, held) ρ=+0.016, stable split-half still flips
  (45.9% → 55.1%). Age buckets wander 43–58% with no monotone trend.
- Same confound picture: fresh+near is still the least-bad cell (52.1%).

Data: `stable_level_events_30t.parquet` (extractor takes `SL_BREAK_B`,
`SL_REJECT_R`, `SL_OUT_SUFFIX` env vars; analysis honors `SL_OUT_SUFFIX`).

## Excursion follow-up: how far & which way after a touch (2026-07-27)

Companion question, same touches: instead of a hold/break race, measure the
raw 60-min path — max excursion above/below the level and net position at
+60m. Full sweep in `stable_level_excursion.parquet`; per-level chart in
**[stable-level-excursion.html](stable-level-excursion.html)**; one-day visual
`stable_level_excursion_demo.py`.

- **Direction: nothing, at every level.** 54% of touches finish the hour
  above the level (the sample's bull drift); no level's net mean clears
  |t|=1.4; the continue-through vs bounce-back median ratio is 0.90–1.03
  everywhere — price travels equally far both ways off every level.
- **The typical touch pokes ~150t up AND ~150t down** inside the hour before
  settling ~20t net — a touch is an exploration, not a bounce. This is the
  excursion-shaped restatement of the S/R null.
- **Level identity predicts swing SIZE, not direction**: VAL touches carry
  the widest envelopes (~176–190t vs ~130–150t at POC/VAH) — value-area lows
  get touched in faster tape. Stop-sizing context, not a directional signal.
- **Regime-control kills the two seductive sub-cells.** ny_VAH-as-support
  (+34t, t=+2.1) and gx_VAL-as-resistance (−30t) look like continuation
  edges, but on those same sessions the *other* touches moved further in the
  same direction (+43t / −57t): both cells **lag** their day's drift
  (leave-one-out residuals −34t and +58t, sign-flipped). Day regime, not
  level behavior — the same verdict as everywhere else in this repo.

## Files

- `data/research/market-structure/stable_level_study.py` — extractor
  (env-tunable thresholds)
- `data/research/market-structure/analyze_stable_level.py` — analysis
- `stable_level_events.parquet` — 13,284 touches (canonical 12/15t)
- `stable_level_events_30t.parquet` — same touches re-scored at 30/30t
- `stable_level_excursion_sweep.py` / `stable_level_excursion.parquet` —
  60-min up/down/net excursion per touch
- `analyze_excursion.py` — by-level excursion breakdown
- `excursion_bylevel_chart.py` → `docs/research/stable-level-excursion.html`
- `stable_level_chart.py` — per-session chart of the study (level tracks +
  touch outcomes); `stable_level_excursion_demo.py` — per-session excursion
  stems/whiskers
