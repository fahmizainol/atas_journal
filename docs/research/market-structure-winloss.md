# Market Structure & Price Action — what the chart shape knows that the tape doesn't

- **Date:** 2026-07-19
- **Research question:** Does price action / market structure — swing sequence (HH/HL vs LH/LL), momentum, consolidation/chop, HTF trend, structure breaks — separate winners from losers, at entry or at the underwater anchors where every order-flow feature died?
- **Data:** `vwap-upper-band-bounce` runs **cdc07ca2** (v10 adopted baseline, 222 trades / 55 stops) and **30badf94** (v8, 398 trades / 116 stops) as power check. Features from ON+RTH tick caches: 1-min/5-min bars, **causal zigzag** swings (20t / 40t reversal — a pivot exists only after price has confirmed it, no lookahead), 15s closes for underwater path anatomy. ~70 features × 4 cohorts, Mann-Whitney AUC + 1000-shuffle permutation + odd/even-session split-half, mirroring the order-flow studies.
- **Files:** `data/research/market-structure/` — `extract_structure.py`, `analyze_structure.py`, `features_{run}.parquet`, `aucs_{run}.csv`.
- **Visual companion:** `market-structure-examples.html` — eight example trades (chop stops vs clean wins; mixed-structure stops vs clear-trend wins) on 1-min candles with the overlap window and causal zigzag drawn.

---

## TL;DR — chop before entry is the first structural signal that survives everything

- **Pre-entry chop predicts stops — robustly.** `overlap_10` (mean bar-to-bar range overlap of the last 10 one-minute bars before entry) separates stops from the rest at **AUC 0.61–0.64 in every cohort of both runs** (p ≤ 0.005), with stable split halves, both calendar years, and both halves of the session. Losers enter out of overlapping, directionless tape; winners out of clean tape. Quintile stop rate runs 9%→33% (cdc07ca2) and 21%→41% (30badf94).
- **It even works at matched depth.** At the −0.40R touch — the anchor where every tape feature sat at 0.43–0.59 with unstable halves — entry-time chop still separates stop from recover at **0.639 / 0.619 (p=0.012 / 0.002)**. The trade's fate was partly visible *before it started*; the tape underwater adds nothing, exactly as the loser study found.
- **Trend clarity beats trend direction.** From the 10-pt causal zigzag: confirmed uptrends (+1) *and* confirmed downtrends (−1) both pay; **mixed structure (one higher extreme, one lower) is the toxic state** — net $3.5k on 57 trades (cdc07ca2) and **−$19.2k on 115 trades** (30badf94). The AUC scan half-missed this because it's non-monotone.
- **The two stack.** Chop (top-tercile `overlap_10`) and mixed-structure are nearly independent (r ≈ 0.09). The chop∧mixed cell is net-negative in both runs; the clean∧clear cell is ~50% of trades carrying **74% / 93% of net** at a 18–22% stop rate.
- **Structure breaks are a null.** Knifing through prior swing lows — at entry or while underwater — predicts nothing (AUC ≈ 0.5 everywhere). The classic "broke structure, get out" read has no edge here. Momentum lookbacks, range compression, and bar wicks are noise; the 5-min-slope-at-touch lead on cdc07ca2 failed to reproduce on 30badf94.
- **Underwater structure is suggestive, not stable:** stops descend more one-way (less bar overlap underwater) while recoverers chop sideways — direction agrees in both runs (the structural face of "drift, not capitulation") but ns are small and halves unstable. Not actionable.
- **Verdict: two engine-A/B leads** — a pre-entry chop veto and a mixed-structure veto, both in the entry-gate class (the class with 4 shipped gates: regime, vwap_slope, gx_poc_shape, gx_overhang — unlike in-trade knobs at 1-for-9). Static counterfactuals are NOT the A/B (weekly-VWAP lesson: vetoes interact with re-arm chains); numbers below are trade-math only.

---

## 1. The chop signal — `overlap_10`

**Definition:** for the last 10 *completed* 1-min bars before entry, take each consecutive bar pair's range intersection ÷ the pair's average range (clipped to [0,1]) and average. High = bars sitting on top of each other (rotation/chop); low = bars marching (directional tape). Entry-knowable by construction.

AUC (stop vs rest), with split halves:

| cohort | cdc07ca2 | 30badf94 |
|---|---|---|
| entry, all trades | **0.624** (p=.004, halves .627/.631) | **0.608** (p=.001, halves .649/.578) |
| −0.25R touchers, stop vs recover | 0.611 (p=.039) | 0.604 (p=.005) |
| −0.40R touchers, stop vs recover | **0.639** (p=.012, halves .684/.588) | **0.619** (p=.002, halves .640/.602) |
| 2025 only / 2026 only | .597 / .664 | .632 / .581 |
| early-half / late-half of session | .588 / .619 | .603 / .618 |

Not a proxy for anything already gated: spearman vs band width .06/.11, 5-min slope −.09/.03, 30-min momentum −.17/−.05, entry hour .25/.16 (and hour itself is AUC .51–.56, dead — chop holds within both halves of the day). Largest correlate is range compression at −.31/−.33, i.e. mildly related to "coiling" but far from identical, and `rng_compress` itself is noise-band.

Quintile ladder (stop rate / avg R):

| quintile | cdc07ca2 | 30badf94 |
|---|---|---|
| 1 (cleanest) | 8.9% / +0.27 | 21.2% / +0.10 |
| 2 | 22.7% / +0.35 | 21.5% / +0.37 |
| 3 | 29.5% / +0.32 | 27.5% / +0.25 |
| 4 | 29.5% / +0.26 | 34.2% / +0.05 |
| 5 (choppiest) | 33.3% / +0.14 | 41.2% / +0.01 |

Static veto counterfactual at the top-tercile cut (~0.60–0.61): 30badf94 would drop 133 trades netting **$6.0k of the run's $122.5k** (keep 95% of net on 67% of trades); cdc07ca2 drops 74 netting $26.0k of $124.5k (keep 79%) — less dramatic on the current baseline, partly because the re-entry knob already prunes the book. Trade-math, not a conclusion.

**Reading:** the strategy is a with-trend pullback buy; when the last 10 minutes are rotation, the "pullback" is more often just chop with no impulse behind it. This is the entry-time cousin of every regime finding to date (losses are regime, not geometry — and now: not tape, but *texture*).

## 2. Trend clarity — the mixed-structure tax

`zz40_trend` from the causal 10-pt zigzag at entry: +1 if last swing high > prior high AND last swing low > prior low, −1 if both lower, 0 if mixed.

| state | cdc07ca2 n / stop% / net | 30badf94 n / stop% / net |
|---|---|---|
| −1 confirmed down | 38 / 21% / $37.2k | 76 / 30% / $43.6k |
| **0 mixed** | **57 / 33% / $3.5k** | **115 / 37% / −$19.2k** |
| +1 confirmed up | 127 / 22% / $83.8k | 207 / 24% / $98.1k |

Direction doesn't matter (the confirmed-*down* cell is fine — these are band bounces, often V-day reversals); *ambiguity* does. Note the linear AUC (~0.54–0.56, p≈.04–.07) understated this because −1 and +1 flank the bad middle.

Stack with chop (top-tercile `overlap_10`), corr ≈ 0.09 — near-independent:

| cell | cdc07ca2 n / avgR / net | 30badf94 n / avgR / net |
|---|---|---|
| clean ∧ clear | 114 / +0.38 / $91.6k | 197 / +0.28 / $113.9k |
| clean ∧ mixed | 34 / +0.11 / $6.9k | 68 / +0.04 / $2.7k |
| chop ∧ clear | 51 / +0.28 / $29.4k | 86 / +0.16 / $27.8k |
| **chop ∧ mixed** | **23 / −0.05 / −$3.4k** | **47 / −0.19 / −$21.9k** |

## 3. Underwater structure — the drift question (suggestive only)

The loser study's "stops die of drift" invited a structural test: at matched depth, is the *shape* of the decline different? Direction says yes: stops show **less** bar overlap underwater (0.65 vs 0.78 at −0.25R on cdc07ca2, 0.62 vs 0.68 on 30badf94; AUC 0.25/0.29, p=.067/.016) — terminal drift is more one-way; recoverable heat chops sideways on the way down. But the finite-n is small (touches inside the first minute produce no bars), split halves are unstable, and the 15s-close efficiency version of the same idea points the other way weakly. Fails the stability bar the entry signal clears. Park it; a re-cut with more trades (or 15s bars for overlap) could revive it.

Everything else underwater — momentum into the touch, 5-min slope at the touch, max retrace, new-low cadence, push count — noise band or unreproduced, joining the order-flow graveyard at the same anchors.

## 4. Nulls worth recording

- **Structure breaks: dead.** Depth below the last pre-entry swing low at the touch (AUC .50–.54), count of swing lows broken while underwater (.50–.53), swing lows already knifed through at entry (`lows_above`, ~.50). "Break of structure" carries no stop-vs-recover information for this strategy.
- **Momentum lookbacks (1/5/15/30-min into entry): noise** (.46–.53). The 30-min flavor leans winner-ward (median +0.95R vs +0.69R run-up on cdc07ca2) but p=.47.
- **HTF 5-min slope:** promising at the −0.40R touch on cdc07ca2 (recoverers 2× slope, AUC .36, p=.017) — **failed to reproduce** on 30badf94. Classic.
- **Consolidation/range compression, minutes-since-high, session-open/Globex position, bar wick/close-location:** all noise band. Close-location's weak lean (losers enter after a strong up-bar, .57/.54) echoes the loser study's `pre60_runup` chase tell — real-ish, tiny, untradeable.
- **Swing counts, up-purity, approach-leg depth/duration/efficiency:** nothing stable.

## 5. The engine A/B — both gates FAILED; ship `enabled: false`

Both gates were built (`chop` / `structure_clarity`, engine v13) and run against the adopted
config re-hashed on v13 (`7adf2e4d`, which reproduces cdc07ca2's book to the cent). The ladder:

| run | trades | net | PF | maxDD | Sharpe | 2025 net | 2026 net | ghost (n / would-be net / stop%) |
|---|---|---|---|---|---|---|---|---|
| baseline v13 `7adf2e4d` | 222 | **$124,508** | 1.98 | −12,350 | 2.68 | 94,413 | 30,096 | — |
| chop 0.55 `d372ff99` | 140 | $53,546 | 1.75 | −10,362 | 1.81 | 33,826 | 19,721 | 285 / $81.2k / 31% |
| chop 0.60 `a64c2fd9` | 176 | $87,287 | 1.88 | −16,066 | 2.37 | 62,766 | 24,521 | 116 / $23.1k / 30% |
| chop 0.65 `12ebc2b7` | 199 | $124,774 | **2.13** | **−10,367** | **2.77** | 87,733 | **37,041** | 47 / $6.7k / 32% |
| chop 0.70 `957fd77a` | 214 | $118,124 | 1.99 | −12,524 | 2.66 | 96,045 | 22,078 | 15 / $1.8k / 47% |
| clarity 40t `b44846da` | 202 | $103,292 | 1.93 | −13,617 | 2.40 | 78,836 | 24,456 | 69 / $2.4k / 33% |

- **The study's recommended cut (0.60 tercile) fails hard: −30% net.** The AUC was real; the
  veto still loses, because the vetoed cohort's ghost book is *positive* ($23k) — a 30% stop
  rate still cashes 70% winners — and vetoes break profitable `reenter_after_stop_only`
  re-arm chains on top.
- **The 0.65 rung is a mirage-shaped positive**: net flat (+$266), every risk metric better
  (PF 2.13, maxDD −16%, Sharpe 2.77), 2026 OOS +23%. But it is a **solitary peak** — 0.60 is
  −30% and 0.70 is −5%, *below baseline* despite vetoing 15 trades that were 47% stops. When
  one threshold in a four-rung sweep wins and both neighbors lose, the win is the sweep, not
  the signal. Not adopted; revisit only if the flat-net/better-risk shape survives on future
  OOS months without re-tuning the threshold.
- **structure_clarity fails at −17% net** while its ghost ledger shows it filtered genuinely
  bad trades ($2.4k across 69, 33% stop rate). The chain cost exceeded the veto benefit — the
  purest demonstration yet that this book's vetoes are judged by their interactions, not
  their targets.
- **Scorecard update: entry gates are no longer 4-for-4.** These are the 9th and 10th A/B
  failures against one pass (reenter). The engine's patience — taking every armed entry and
  re-arming after stops — keeps beating every filter pointed at it, even filters built on the
  most reproducible signal the Lab has produced.

Both knobs ship in the engine defaulting `enabled: false` (available for future A/Bs; present
in the run form). Deferred leads unchanged: underwater one-way-drift detector, time-underwater
× chop interaction.

---

*Method footnote: causal zigzag — running extreme flips to a confirmed pivot only when price retraces ≥ threshold from it; features at time T use only pivots confirmed ≤ T. Multiple-testing context: ~70 features per cohort; the chop signal's defense is not its p-value but reproduction across two runs, four cohorts, two years, and two day-halves. All stats on Databento prints; bars built from ON+RTH caches; NQ tick = 0.25.*
