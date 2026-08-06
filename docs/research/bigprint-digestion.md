# Big-print digestion — null-check of the "9 years of MBO" video claims

**Date:** 2026-08-02 · **Data:** 599 cached NQ sessions (2024-03 week + 2024-12-19 → 2026-06-30), full-day tick tape · **Scripts:** `data/research/bigprint-digestion/{extract,analyze}.py` · **Grid:** `grid.csv`

## Question

MatFinOg's 2026-08-02 video ("I distilled liquid gold from 9 years of MBO data")
claims that following the minute's biggest large print on NQ has an edge that
(a) builds to a 15–20 min "digestion" plateau, (b) grows with size from 70–190
lots then **inverts** above ~200 (exhaustion), and (c) lives in prints landing in
the candle's **wick**, not the body. All his numbers are in-sample optimizer
sweeps, gross of costs, no OOS. Our 18 months are an out-of-sample check of the
three named claims.

## Method

His measurement, replicated exactly: signal on minute *i* → enter `open[i+1]`,
exit `open[i+1+N]`, direction = side of the minute's biggest print (B long,
A short), gross, $20/pt, RTH primary. Two event units: raw **prints** and
**sweeps** (same-side fills glued within 250 ms / 1.00 pt — his MBO data sees
order-level size, our tape sees fills, the sweep is the order-shaped unit).
Guardrails he skipped: day-clustered t (overlapping holds), session split-half,
long/short split, candle-sign momentum control, price-only wick control.

## Results

**Baseline** (candle color, all 225k RTH minutes): ≈ $0 at every horizon. ✓ matches.

**A. Digestion curve — SHAPE REPLICATES, significance doesn't.**
Sweeps ≥100 lots: $8 (h1) → $16 → $19 → $18 → $30 (h10) → **$37 (h15)** → $29 →
$23 → $20 (h30). Prints same hump, peak $33 @ h15. That is his build-plateau-die
arc with the peak at 15 vs his 19. But: naive t=2.2 collapses to **day-cluster
t=0.97** (563 days); win rate 50.9%. Split-half: +$48 / +$29 (sign-stable, both
halves positive, neither significant). Long $33 / short $42 — **not** drift
harvest. Candle-sign on the same minutes: $13 (cluster t=0.15) — the whale's
side adds real information over minute momentum; whale-against-candle still
makes $34. Gross $37 ≈ 1.9 pt; a realistic round trip (1-tick spread crossed
twice + commission) is ~$10–15, so ~$20–25 net *if the mean is real* — but at
cluster t≈1 the CI comfortably includes zero, so it isn't evidence you can fund.

**B. Size ceiling — DOES NOT REPLICATE.** Bands at h15 are non-monotonic noise
(prints: [70,100) +$33, [100,150) +$65, [150,200) **−$22**, [200,300) +$16;
sweeps [200,300) is the *best* band at both horizons, +$63 @ h15). No clean
70–190 ramp, no inversion at 200. Only [300,∞) leans negative (−$27/−$36,
n≈230–320, n.s.) — at most a whisper of exhaustion at the very extreme.

**C. Wick vs body — REVERSED.** His $34-wick/$8-body becomes wick $28 / body
**$48** here. Worse for the story: the "absorption" cohort (wick print whose
side agrees with the rejection, his flagship narrative) is **dead — $1.68,
t=0.04**; the whole wick contribution comes from prints *fighting* the
rejection ($39). Price-only control (dominant-wick bar, no big print, rejection
direction) = $2, so no wick artifact either — the wick just doesn't matter.

**Overnight** ≥100 @ h15: print +$7 (t=0.24), sweep −$17 (t=−0.91). ✓ his ON
null replicates.

## Verdict

- The one honest signal — big-print side carries directional information that
  takes ~15 min to digest — is **consistent with our big-trade order flow study**
  (big-lot participation AUC 0.66, the one live entry-time signal) and extends
  it: the horizon hump is real in shape. But at day-cluster t≈1.0 and ~1.9 pt
  gross, it is **not fundable** and does not move the size-up verdict (7 failed
  A/Bs; knob stays off).
- His two novel claims (size ceiling with >200 inversion, wick location) look
  like **optimizer-grid artifacts of his in-sample sweep** — neither survives
  contact with out-of-sample data, and wick actually reverses with his
  absorption cohort at exactly zero.
- His final $53/trade stacked config depended on the wick filter (reversed
  here) and a balanced-book filter (untestable without L2). Prior for it
  surviving OOS: low.
- Nothing to build. If big-print side is ever revisited, the 15-min horizon —
  not 1 min — is the right race window, and sweeps ≥100 are the right unit.

## Follow-up: pairing with vwap-upper-band-bounce (2026-08-03)

Post-hoc split on the current baseline (v13 a348d176, 262 trades): bucket each
entry by the freshest ≥100-lot sweep in the prior 5/10/15/30 min (no lookahead —
actual sweep timestamps vs `entry_ts_utc`). Script `pair_upperband.py`.

**Direction consistent, gate impossible.** A fresh *opposing* (sell) sweep
dampens the long's expectancy at every window — avgR +0.11 vs +0.31 for
no-sweep at 15 min — and the dampening is sign-stable in both halves
(0.19 vs 0.39, 0.07 vs 0.23). So "entering against an undigested sweep costs
you" is real-shaped. But the fresh-sell cohort is **still net-positive**
(+$6.0k on 29 trades, 79% win — more small wins, fewer runners; the seller has
to finish before the runner can leave). A veto would delete profitable trades —
the same ghost-cohort-net-positive wall every upper-band gate has hit. A fresh
*supporting* (buy) sweep adds nothing (+0.25 vs +0.31 none). n=29 in the key
cell; the avgR gap is within noise anyway.

**Fill-sweep contamination check.** Drawing the examples exposed that some
"fresh sells" land milliseconds before the entry timestamp — they're the sweep
that *fills* the resting long, not prior context. Excluding them (window
`[entry−15m, entry−30s)`) makes the dampening **stronger**: truly-prior
fresh-sell avgR **+0.06** vs +0.30 none, while the 3 fill-adjacent trades all
won (+0.53R) — "winners fill into selling," drawn on the tape. Cohort stays
net-positive at every buffer, so the verdict is unchanged.

**Verdict: no knob, no gate.** The digestion signal is visible inside the
strategy exactly as it was standalone — real direction, too weak to fund an
action. Texture note for the loss studies: opposing-sweep entries win *more
often* but smaller (79% win, avgR 0.11) — consistent with "winners fill into
selling," runners just need the seller done first.

**Visual examples:** `docs/research/bigprint-sweep-examples.html`
(`visual_examples.py`) — six real trades on 1-min candles with sweep bubbles,
the shaded 15-min window, and entry/stop/exit: the modal small win, the
runner-anyway, the loser, a clean no-sweep runner, the dead fresh-buy tailwind,
and the whale-is-the-fill case.
