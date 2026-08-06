# Weekly VWAP band touches — does approach context matter?

**Verdict: mostly null.** The hand observations from Feb 2025 are real episodes and
the extractor finds them, but scored across every cached session the approach
context (failed-out retest, acceptance pullback, origin depth) does **not**
meaningfully change what a weekly ±1σ touch does next. The one thing that
survives is the thing the pooled study already knew: touches from the mid's side
lean slightly toward rejection (~54%), worth ~0.05–0.2σ at 60–120m — small, and
context does not amplify it. This joins VAH-snap, stable-level S/R, aVWAP-reclaim
and LVN-retrace in the "geometry context has no edge" column.

- Data: `data/research/weekly-vwap-context/` — `extract_touches.py` →
  `touches.parquet` (5,786 events) + `sessions.parquet`, `analyze_touches.py`.
- **Worked examples on charts**: [weekly-vwap-context-examples.html](weekly-vwap-context-examples.html)
  — the six hand-cited Feb-2025 episodes plus a deterministic both-outcomes
  sample of four events per cohort (`render_touch_examples.py`).
- Scope: every cached NQ Globex session (ON+RTH minute bars) 2025-02 → 2026-06,
  363 sessions, 254 seasoned (week's first session excluded), developing weekly
  bands sampled causally at each bar's last tick.
- Events: every touch of weekly ±1σ (and mid), episode-ized with a 0.25σ re-arm
  so a choppy hour hugging the band is one episode. 1,555 seasoned band touches.

## Motivating observations (hand-scored on 02–21 Feb 2025)

1. Retest of +1σ from below *after price failed out of the upper band* → fails
   down (Feb 7 10:06, Feb 10 11:27→14:21, Feb 17, Feb 19 15:33…).
2. Pullback onto +1σ from above *after acceptance in the upper band* (time
   spent there / +2σ touched with strength) → bounces (Feb 3, 4, 6, 9, 12, 13).
3. Touch of +1σ after a full traverse from the mid / −1σ → rejections, but
   counters "just rippin' thru" (Feb 11, 13 vs Feb 17, 19).
4. Vague: many mid-crossings ⇒ rotational day; mid becomes support after long
   upper-band residence (and resistance after lower-band residence).

## Cohorts and outcome

Upper1 shown, lower1 mirrored and pooled; outcome axis is **toward the mid** vs
**away from the mid**, decided by a 60-min first-crossing race at ±0.30σ from
the touched level.

| cohort | definition | hypothesis |
|---|---|---|
| retest_after_fail | from mid's side, ≥5m prior residence beyond the band | rejects (obs 1) |
| fresh_deep | from mid's side, no residence, 120-min origin crossed the mid | mixed (obs 3) |
| fresh_shallow | from mid's side, no residence, shallow origin | — |
| pullback_accepted | from outside, ≥15m residence or ±2σ touched | holds/bounces (obs 2) |
| pullback_brief | from outside, no acceptance | breaks |

## Results

Naive race (touch bar included) vs conservative race (from the next bar — the
touch bar's approach-side extreme can predate the touch inside the bar):

| cohort | n (decisive) | toward-mid, naive | toward-mid, next-bar | med edge 60m (σ) | 120m (σ) |
|---|---|---|---|---|---|
| retest_after_fail | 543 (468) | **0.615** | **0.536** | +0.046 | +0.070 |
| fresh_deep | 176 (166) | 0.663 | 0.543 | +0.122 | +0.201 |
| fresh_shallow | 147 (110) | 0.545 | 0.536 | +0.055 | +0.093 |
| pullback_accepted | 641 (531) | 0.444 | 0.494 | −0.010 | +0.013 |
| pullback_brief | 48 (36) | 0.444 | 0.486 | +0.037 | +0.015 |

- **The touch-bar artifact was most of the signal.** Naively, obs 1 and obs 2
  both look confirmed (61.5% reject; 55.6% bounce). Scored from the next bar,
  every from-inside cohort collapses to ~53–54% and pullbacks to a coin flip.
  The naive numbers partially re-count the approach itself as the outcome.
- **Obs 1 (failed-out retest): no incremental edge.** 53.6% toward vs 53.6–54.3%
  for fresh touches — the context tag adds nothing (perm p = 0.30/0.19 even on
  the naive scoring). The cited Feb episodes are correctly tagged and did
  reject; they're just not representative.
- **Obs 2 (acceptance pullback): null.** Accepted vs brief p = 1.0, and the
  residence-time sweep has no dose-response (0–4m residence bounces 67%, 60m+
  only 53–54%; the 30–59m+2σ cell at 83% is n=18). "Acceptance" as measured by
  time-beyond-band or a ±2σ touch does not predict the band holding.
- **Obs 3 (origin depth): artifact + one thin oddity.** The naive gradient
  (deep origin 70.5% reject → shallow 42.1%) flattens to ~49–61% next-bar.
  Shallow-origin touches breaking more (42% toward, n=38) is the only residual
  and is too thin to trust. Full-envelope traverses do *not* rip through —
  if anything they mildly revert, same as everything else.
- **Obs 4a (rotation): weakly right.** corr(mid crossings, |drift|/range) =
  −0.12; 7+ crossings ⇒ median directionality 0.41 vs 0.52 at zero crossings.
  Real but descriptive — crossings are only knowable in hindsight.
- **Obs 4b/c (mid as S/R after band residence): null.** Mid-hold rate is
  50–60% across every residence bucket, no dose-response.
- Stability: the from-inside toward-lean itself is stable (14/17 months,
  halves 0.60/0.64 naive) — it's the *context differentiation* that isn't there.

## Scorecard by original observation

Every seasoned ±1σ touch belongs to exactly one cohort → one observation
(1,555/1,555 tagged). "Pred hit" = the outcome each observation *predicted*
(obs 1/3 predict rejection toward the mid; obs 2a predicts a bounce away; obs
2b a break); edges are signed in the predicted direction, in weekly σ.

| observation | n | pred hit, naive | pred hit, next-bar | months >50% | med edge 60m | 120m |
|---|---|---|---|---|---|---|
| **1** retest after failing out → reject | 543 | 0.615 | 0.536 | 11/17 | +0.046 | +0.070 |
| **3a** fresh deep traverse | 176 | 0.663 | 0.543 | 9/16 | +0.122 | +0.201 |
| **3b** fresh shallow approach | 147 | 0.545 | 0.536 | 8/13 | +0.055 | +0.093 |
| **2a** pullback with acceptance → bounce | 641 | 0.556 | 0.506 | 8/17 | +0.010 | −0.013 |
| **2b** pullback w/o acceptance → break | 48 | 0.444 | 0.486 | 1/2 | +0.037 | +0.015 |

Obs 4 (session-level): rotation corr(mid-crossings, |drift|/range) = −0.121
(right-signed, weak, n=283); mid-holds-after-≥30m-band-residence actually
*reverses* on the next-bar race (46.6% vs 49.6% without residence) — null.

Every observation's predicted direction was right-signed on naive scoring and
collapses to ≈52–54% (obs 1/3) or a coin flip (obs 2) once the touch-bar
artifact is removed — the observations correctly described *shapes that exist*
but not shapes that predict.

### Split by band side (next-bar hit rate / med edge 60m·120m in σ, predicted direction)

| observation | upper1 | lower1 |
|---|---|---|
| **1** retest → reject | n=276 · 0.558 · +0.07/+0.07 | n=267 · 0.514 · +0.03/+0.07 |
| **3a** deep traverse | n=60 · **0.475** · −0.03/+0.10 | n=116 · **0.579** · **+0.21/+0.29** |
| **3b** shallow fresh | n=87 · 0.574 · +0.06/+0.09 | n=60 · 0.490 · +0.04/+0.11 |
| **2a** accepted → bounce | n=312 · 0.482 · +0.01/+0.03 | n=329 · 0.527 · −0.00/−0.05 |
| **2b** brief → break | n=22 · 0.647 · +0.29/+0.44 | n=26 · 0.350 · −0.21/−0.24 |

Expanded obs 2a upper1 chart examples:
[weekly-vwap-obs2a-upper-examples.html](weekly-vwap-obs2a-upper-examples.html)
— three holds / three breaks (≈ the real 48/52 ratio), date-spread
deterministic picks.

Expanded obs 3a chart examples:
[weekly-vwap-obs3a-examples.html](weekly-vwap-obs3a-examples.html) — five
lower1 (three bounces / two punch-throughs, ≈ the real ratio) and four upper1
(two of each), date-spread deterministic picks.

Bounce confirmation test by side: upper1 bounce-long loses at every rule
(win 40–47%, mean −0.09…−0.13σ); lower1 bounce-short is mildly positive
(win 50–55%, mean +0.07…+0.14σ).

### NY session only (840 of 1,555 touches, 09:30–16:00 ET)

Same ordering, slightly friendlier levels: obs 1 = 54.2% next-bar, obs 3a =
55.4%, obs 2a = coin flip (50.2%; upper1 2a is the worst repeated cell at
43.9% — RTH pullback-buys onto +1σ lose). **lower1 deep traverse survives the
filter as the best cell (n=71, 57.7%, +0.29σ both horizons) but is
second-half-concentrated (halves 51.4% / 63.9%, months 6/11)** — possible
recent-regime artifact, not a stable property. NY-only confirmation test:
immediate fade mean +0.04σ (the afternoon lean), rules add nothing; bounce
negative at every rule.

The coherent asymmetry: the **−1σ band repels price from both sides** (deep
traverses down into it bounce 57.9%, +0.21σ; pullbacks up into it from below
resume lower), while the **+1σ band is porous** (deep traverses up punch
through 52.5%, pullback-longs onto it from above lose). Cell caveats: the
strongest small cells (obs 2b n=22/26, upper 3a n=60) are exactly the sizes
that produced dead leads in prior studies, and none of these splits was
pre-registered — treat as descriptive, not tradable.

## Follow-up: wait-for-confirmation vs entering at the touch

`confirm_test.py` — on all 866 from-mid-side ±1σ touches (the fade setup),
compare entering at the touch bar's close against waiting up to 30 min for a
confirmation, aborting if price first trades 0.30σ through the level:

| rule | entered | aborted | med give-up (σ) | win rate | mean edge (σ) | 95% CI |
|---|---|---|---|---|---|---|
| immediate | 866 | 0 | 0.00 | 0.532 | −0.005 | [−0.07, +0.06] |
| close 0.05σ back | 579 | 287 | 0.10 | 0.547 | −0.002 | [−0.07, +0.07] |
| close beyond touch-bar extreme | 481 | 385 | 0.17 | 0.555 | +0.022 | [−0.05, +0.09] |
| two closes back | 597 | 269 | 0.09 | 0.547 | +0.019 | [−0.05, +0.09] |

- **No variant's mean edge is distinguishable from zero** (per-trade noise is
  0.9σ; the means are ~0.02σ).
- **On the setups that do confirm, waiting is strictly worse**: paired
  diff (confirmed entry − immediate entry, same events) is −0.17 to −0.28σ
  with CIs entirely below zero. The give-up is real and large.
- The *entire* apparent benefit of confirmation comes from the aborts — and
  the abort trigger (0.30σ break-away before confirming) is mechanically the
  loss condition itself. Confirmation here is just a stop-before-entry: an
  immediate entry with a 0.30σ stop buys the same protection without paying
  the give-up.
- Afternoon RTH (12:00–16:00 ET, n=187) is the only cut with a positive
  immediate mean (+0.12σ, echoing the drift-fade afternoon finding), and
  confirmation adds nothing there either (all rules ≈ equal win rate).

**The mirror trade (obs 2) is included**: joining the bounce at a
pullback-from-outside, immediately or after the mirrored confirmation (closes
back away from the mid; abort on a 0.30σ break toward it). It is a coin flip
everywhere — 48.4–48.8% win rate at every rule on the 689 bounce setups (641
accepted / 48 brief), mean edges −0.01…+0.01σ, split-halves straddling zero.
Confirmation neither helps nor hurts a trade that has no edge to protect.

**Verdict: confirmation does not rescue the weekly-band fade — or the bounce.**
It converts give-up into abort-protection roughly one-for-one and the net stays
at zero.

Worked examples on charts:
[weekly-vwap-confirm-examples.html](weekly-vwap-confirm-examples.html) —
confirmed-won / confirmed-lost / aborted / cost-of-waiting panels
(`render_confirm_examples.py`).

## Notes

- The RTH/overnight split changes levels, not conclusions (retest cohort is
  64% RTH; overnight touches are uniformly weaker).
- All of the user's cited episodes were located and classified as intended —
  this is the VAH-snap lesson again: eyeballed exemplars select on outcome
  (Simpson's trap), and the counter-examples were already in the hand notes.
- Anything actionable here is already captured by the pooled band-fade lean and
  by the (failed) `wk_ext` gate history — no new knob or gate is justified.
