# Interactions v9 — gap-closer, session refs, snap classes, acceptance decay: first read

- **Date:** 2026-07-19
- **Research question:** The Interactions Lab improvement backlog (2025-09-25 day review) shipped as `INTERACTIONS_VERSION 9` — five new cuts: who-closed-the-gap attribution, the VWAP midline as a touchable level, static session references (ONH/ONL, prior-day POC/VAH/VAL, prior close, open), VA-snap classes + same-minute confluence, and an acceptance-decay aggregate. Do any of them show real structure?
- **Data:** full tick cache, NQ 2025-02-03 → 2026-06-30, 363 sessions. Two snapshots: default sources `NQ_20250203-20260630_v9-7cfdd06e65e9` (ny+globex+session_refs; 19,757 touches, 2,971 snaps) and all-sources `NQ_20250203-20260630_v9-f166e8ed4082` (+vwap_bands; 28,448 touches).
- **Benchmark:** the measured null — phantom levels score **60.5% "reject" with med MFE/MAE ratio 0.99** at the 30m window. A cut must beat the rate AND show asymmetry. All rows below are medians at 30m; robustness = split-half at 2025-10-15 (H1/H2).

---

## TL;DR

- **The gap-closer cut flipped its own hypothesis.** Level-led touches (the level moving to price) were suspected fake tests. In the upper-band +1..+2 profile-pullback cut they carry the edge instead (ratio 1.6/1.85 by half) while **price-led 1st touches are dead there (H2 ratio 0.87)**. The good event is developing value rising to meet price. Engine-knob candidate for `profile-pullback-long` — but it does **not** transfer to the +1σ band (§6).
- **"Drift" touches are the strongest new row overall**: neither price nor level converged over the prior 5 bars — a slow re-test of a hugged zone. Reject ~0.70, ratio **1.84/1.71 by half**, n=1,460. Not yet artifact-proofed.
- **Same-minute multi-level snaps are a veto on the snap fade**: they revert to VWAP far less than lone snaps (rev30 0.24 vs 0.35 upside, 0.37 vs 0.46 downside). Co-snap = value migration = continuation. "Lone" is a free filter on the candidate downside-snap fade.
- **Acceptance decay confirmed**: med MFE decays monotonically 47→27 pts from 1st to 7th+ touch while the reject rate stays flat ~0.60. The reject label is free; levels become fair price; judge cuts by excursions, never by rate alone.
- **Session refs are mostly null** (ONH ratio exactly 1.00), but four fade-toward-value rows hold in both halves: ONL from above, pd POC from below, pd VAH from above, Open from above (§4).
- **Upper-band-bounce relevance:** the +1σ-from-above baseline confirms (0.672/1.38), the drop-15:xx rule survives the full window, acceptance decay backs first-touch-only — but no new gate is justified for that strategy today (§6).

---

## 1. Who closed the gap

Per-touch attribution over the last `GAP_LOOKBACK_BARS = 5`: how much of the closing distance came from price moving to the level (`price`) vs the level moving to price (`level`), `both`, or `drift` (they never converged — the touch came from tolerance/wiggle inside a hugged zone).

| cut | n | reject | med MFE | med MAE | ratio |
|---|---|---|---|---|---|
| closed by price | 15,383 | 0.609 | 36.0 | 32.8 | 1.10 |
| closed by level | 1,936 | 0.607 | 35.3 | 32.8 | 1.08 |
| closed by both | 406 | 0.581 | 37.8 | 37.3 | 1.01 |
| **closed by drift** | **1,460** | **0.701** | **46.9** | **25.8** | **1.82** |
| price-led · 1st touch | 4,248 | 0.607 | 44.5 | 41.6 | 1.07 |
| level-led · 1st touch | 1,057 | 0.624 | 42.5 | 35.5 | 1.20 |
| *null baseline* | — | *0.605* | *20.9* | *21.2* | *0.99* |

Globally, price-led vs level-led is a wash — the contamination hypothesis ("falling band chased by price dilutes every table") is **refuted** at the aggregate level. Two things stand out:

- **Drift** is the one closer class with real asymmetry, and it survives the split: H1 0.708/1.84, H2 0.693/1.71. A zone price has been rotating against, re-tested slowly, rejects hard relative to what it gives back. Caveat: "drift" partly *selects for* zones price already respects intraday — needs artifact-proofing before promotion (e.g. control on same-day prior-reject count).
- Where the closer matters is **inside the named pullback cut** (§5).

## 2. Acceptance decay

The same touches bucketed by nth test of the zone that day:

| nth | n | reject | med MFE | med MAE | ratio |
|---|---|---|---|---|---|
| 1st | 6,429 | 0.619 | **47.3** | 39.8 | 1.19 |
| 2nd | 3,887 | 0.599 | 39.3 | 36.0 | 1.09 |
| 3rd | 2,595 | 0.632 | 37.0 | 31.8 | 1.17 |
| 4th–6th | 4,136 | 0.627 | 32.5 | 27.5 | 1.18 |
| 7th+ | 2,578 | 0.604 | **27.0** | 24.2 | 1.12 |

Exactly the predicted pinning pattern: **median MFE decays monotonically ~47→27 pts while the reject rate never moves off ~0.60**. Both legs shrink together (ratio flat), so this is not a directional edge — it is proof that (a) the 3-pt reject label is nearly free, and (b) repeated tests turn a level into fair price. Methodological consequence: every cut in this lab must be judged on median excursions, not reject rate.

## 3. VA-snap classes and confluence

`snap_class`: jump ≥ 20 pts = `node_flip` (the value area re-seating on another volume node), else `creep`. `co_snaps`: other levels snapping in the same minute.

| cut | n (triv) | rev30 | rev60 | avg move | avg adverse |
|---|---|---|---|---|---|
| node_flip up | 873 (125) | 0.312 | 0.430 | 104 | 67 |
| node_flip down | 674 (170) | 0.427 | 0.536 | 126 | 83 |
| creep up | 566 (96) | 0.297 | 0.371 | 78 | 50 |
| creep down | 311 (156) | 0.421 | 0.540 | 92 | 59 |
| **lone up** | 857 (167) | **0.349** | 0.452 | 104 | 64 |
| **lone down** | 613 (212) | **0.462** | 0.573 | 120 | 71 |
| **multi-level up** | 582 (54) | **0.242** | 0.340 | 79 | 55 |
| **multi-level down** | 372 (114) | **0.366** | 0.478 | 107 | 82 |

- **Class changes magnitude, not rates**: node-flips move further in reversion *and* further against, at essentially the same revert rates as creeps. Not a filter by itself.
- **Confluence is the finding**: same-minute multi-level snaps revert ~10pp less than lone snaps on both sides. Two boundaries re-seating together is one value-migration event — continuation, not the fade. Since the standing candidate edge here is *downside* snaps reverting to VWAP, **"lone" is a free tightening of that cut** (lone down: rev30 0.462, rev60 0.573).

## 4. Static session references

Per label, approach-side rows ≥30 touches, med30. Split-half shown for the four that beat null:

| cut | n | reject | ratio | H1 ratio | H2 ratio |
|---|---|---|---|---|---|
| ONH (all) | 983 | 0.610 | **1.00** | — | — |
| **ONL from above** | 475 | 0.644 | 1.42 | 1.23 | 1.63 |
| **pd POC from below** | 381 | 0.640 | 1.56 | 1.28 | 1.80 |
| **pd VAH from above** | 392 | 0.658 | 1.34 | 1.21 | 1.38 |
| **Open from above** | 453 | 0.642 | 1.31 | 1.25 | 1.45 |
| pd VAL from below | 322 | 0.584 | 0.99 | — | — |
| pd Close (all) | 816 | 0.615 | 1.10 | — | — |

ONH is the poster child for the null (ratio 1.00 on n=983 — the most-watched line on every chart does nothing). The four survivors share a shape: **price above, falling onto a reference, bouncing** — fade-toward-value longs at ONL / pd POC-overhead rejections. Directionally consistent with "NQ edges are day-with only" (long-side bounces in an up-drifting cache). All四 hold direction in both halves with H2 stronger, but ratios are modest and a monthly-robustness pass has not run.

## 5. The upper-band profile-pullback cut, re-read through the gap-closer

The named cut (pullback-from-above onto POC/VAH inside NY +1..+2σ), 1st-touch rows:

| cut | n | reject | ratio | H1 | H2 |
|---|---|---|---|---|---|
| 1st touch (as shipped) | 437 | 0.654 | 1.39 | — | — |
| **1st touch, level-led** | 149 | 0.664 | **1.59** | 0.71 rej / 1.60 | 0.63 rej / 1.85 |
| **1st touch, price-led** | 199 | 0.618 | **0.93** | 1.14 | **0.87** |

34.5% of the cut's 1st touches are level-led, and they are where the edge lives; price-led touches inside the same cut are at-or-below null and *decay* in H2. Interpretation: the tradeable event is **developing value migrating up underneath a consolidating price** — value chasing price is trend confirmation; price falling onto a static shelf is not. This is live-computable at touch time (only past bars), unlike the hindsight day-class splits that failed before — which makes it a legitimate **A/B candidate for `profile-pullback-long`** (arm only when the level closed ≥60% of the gap). Prior for the A/B stays low: the v4 dwell-knob lesson (any added requirement inverted the edge) cuts both ways.

## 6. Relevance to vwap-upper-band-bounce

The strategy's entry proxy — **NY +1σ touched from above** (bands snapshot, n=1,157):

| cut | n | reject | ratio | H1 | H2 |
|---|---|---|---|---|---|
| all | 1,157 | 0.672 | 1.38 | — | — |
| closed by price | 908 | — | — | 1.57 | 1.30 |
| closed by level | 104 | — | — | 2.74 | **1.20** |
| 1st touch · level-led | 42 | — | — | 2.52 | **0.79** |
| closed by drift | 90 | — | — | 2.02 | 1.68 |
| before 15:00 | 1,024 | 0.68 | — | 1.70 | 1.34 |
| 15:00+ | 133 | 0.624 | **1.12** | — | — |
| stacked with a session ref | 66 | 0.697 | 1.82 | — | — |

- **Confirms:** the baseline event beats null on the full window; the **drop-15:xx rule survives** (15:00+ ≈ null with the smallest excursions); acceptance decay backs first-touch-only.
- **Does not transfer:** the level-led finding collapses on the band (H2 1.20; 1st-touch level-led H2 **0.79** on n=28). `closed_by` is a profile-pullback knob, **not** a band-bounce knob — do not build it into this strategy.
- **Leads only (too small to act):** band drift touches hold 2.02/1.68 (n=90, and they are re-tests, not the first touch the engine trades — a faint echo of the reenter-after-stop result); band touches stacked with a session ref show 1.82 on n=66.

## 7. Caveats & next steps

- Whole-cache mining with the same detector that generated the hypotheses; split-half is weak protection and **the monthly-robustness pass has not run** — prior Lab leads (stacked+open_z, prior=balance) died exactly there.
- `closed_by = level`/`drift` may partly proxy trend context — the known circularity ("trend day" is partly defined by these touches holding). Needs a control before any engine A/B.
- **30m touch scores ≠ engine PnL.** Globex levels scored best in the v3 study and lost money in the sim. Every lead here goes Lab → engine A/B before touching a strategy.
- Next, in order: (1) monthly-robustness pass on drift, the four session-ref rows, and lone-down snaps; (2) artifact-proof drift vs same-day prior-reject count; (3) `profile-pullback-long` A/B with a level-led arm condition; (4) tighten the downside-snap fade study to lone snaps.

## 8. Provenance

- Code: `src/journal/sim/interactions.py` v9 (`closed_by`/`price_closed_pts`/`level_closed_pts`, `snap_class`, `co_snaps`, `session_refs` source, NY/Globex VWAP midline under `vwap_bands`, aggregates `who_closed_gap` / `acceptance_decay` / `vasnap_by_class` / `vasnap_confluence`). Tests: `tests/test_interactions.py`.
- Snapshots (reopenable in the Lab): `data/cache/interactions/NQ_20250203-20260630_v9-7cfdd06e65e9.json` (defaults), `NQ_20250203-20260630_v9-f166e8ed4082.json` (+vwap_bands).
- v8-and-earlier snapshots are invalidated by the version bump; the null baseline (60.5%/0.99) predates v9 and was not re-measured — the outcome thresholds it depends on did not change.
