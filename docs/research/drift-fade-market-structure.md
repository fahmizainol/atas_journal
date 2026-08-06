# Drift-Fade Market Structure — a lookahead post-mortem and an honest null

- **Date:** 2026-07-19, corrected 2026-07-20 after the engine A/B exposed the first pass as a lookahead artifact. This doc records both: the artifact (and how it slipped past every robustness check), and what the corrected analysis actually says.
- **Research question:** Does price action / market structure at entry separate the drift-touch-fade's winners from its stops — and does the flagship's structure finding (chop, mixed swings) transfer?
- **Data:** run **03f4c56c** (the adopted developing-refs-only config, 2025-02 → 2026-06) on both siblings: `drift-touch-fade` (156 trades / 17 stops / +$47.6k) and `drift-touch-fade-entry-stop` (155 / 21 stops / +$53.4k). Same entries, two stop rules — one entry sample, not independent replications.
- **Method:** side-aware port of the flagship structure extractor (`data/research/market-structure/extract_structure_drift.py`) — price axis mirrored for shorts so every feature reads trade-relative; same causal zigzag, ~70 features, AUC + permutation + split-half. Corrected features/AUCs are in `features_dtf*_03f4c56c.parquet` / `aucs_*.csv` (the committed versions are the corrected ones).

---

## TL;DR

1. **The first-pass finding — "approach momentum into entry predicts stops at AUC 0.83" — was outcome leakage, not a signal.** The drift engines run on ON+RTH ticks (`session="globex"`), so `trades.parquet.entry_idx` indexes the combined array; the extractor read it with the flagship's RTH-only convention, anchoring every "entry-time" feature **~2 hours into the future** (by the day's overnight tick count). A feature like `mom_30m_r` measured there is just "is price later in the session below the entry print" — i.e., the trade's outcome. Stops → fake positive with-trade momentum; wins → fake counter-move. AUC 0.83 of pure future.
2. **Every robustness check passed anyway** — permutation p, odd/even session splits, both calendar halves, monotone dose-response, a plausible mechanism ("the developing level chases price"), agreement across ~10 correlated features. None of that defends against leakage, because a leak replicates everywhere the outcome does. **The one check that caught it was the real engine A/B**, where the veto has to act on live data.
3. **The honest A/B of the approach-momentum veto FAILED, decisively.** At the true entry time, most drift touches arrive *with*-move (median `mom_30m_r` ≈ +0.3R — unsurprising: these are drift days). The veto killed ~⅔ of entries, halved net on both siblings, and the vetoed ghost cohort finished **positive**:

   | | trades | net | PF | maxDD | Sharpe | stops | vetoed ghosts |
   |---|---|---|---|---|---|---|---|
   | level-stop, off | 156 | **$47,568** | 1.69 | −$10,884 | 2.16 | 17 | — |
   | level-stop, veto 30m | 52 | $25,461 | 2.62 | −$6,723 | 2.28 | 4 | 113, **+$29,829** |
   | entry-stop, off | 155 | **$53,400** | 1.90 | −$6,378 | 2.96 | 21 | — |
   | entry-stop, veto 30m | 52 | $26,991 | 3.02 | −$4,569 | 2.78 | 5 | 112, **+$30,606** |

   PF/DD/expectancy "improve" only because the book shrinks to a third; the discarded trades made money. Fixed contracts means the smaller book cannot be sized back up (the size-up lesson). The knob (`approach_mom_veto_min`) ships **default-off; leave it off**. Runs: v2 `523f4000` (off — reproduces v1 03f4c56c tick-for-tick) and `dfd0089d` (veto 30m).
4. **The corrected structure scan is a null at entry.** With true anchors, no entry-knowable feature separates stops robustly on either run: best are `zz20_uppurity` (0.62, p=.10, halves 0.78/0.46 — flips) and `bar_closeloc` (0.63, p=.05, entry-stop only). Momentum drops to 0.57 (p≈0.35). The corrected mom≤0 cohort: 50 trades +$23.7k vs mom>0: 106 trades +$23.8k — no separation, exactly matching the A/B. The flagship's chop signal (`overlap_10`) does not transfer either (0.47–0.53). With only 17–21 stops the power is limited, but there is no lead here.
5. **Prior studies are unaffected.** The flagship engine's `entry_idx` is RTH-based (verified 12/12 sampled trades align to the tick) — the market-structure, big-trade and loser order-flow studies keep their conventions and conclusions. The bug was porting that convention onto the globex-session engine.

## What actually failed, mechanically

- `extract_structure_drift.py` did `rth = cached_rth(...); et = rth.ts[trade.entry_idx]` — but for these engines `entry_idx` counts overnight ticks first. The recovered "entry timestamp" landed mid-to-late session, after the trade had resolved. All swing/momentum/position features anchored there inherited the outcome. The underwater anchors (`t25`/`t40`) were shifted garbage too.
- The fix anchors by **timestamp** (`entry_ts_utc`/`exit_ts_utc` searchsorted into the tick array), which is immune to any index convention — with `.to_datetime64()` for the conversion, because `np.datetime64(Timestamp)` truncates to µs and misplaces anchors inside same-nanosecond sweep bursts. A guard now warns when the tick at the recovered anchor is >50t from the recorded fill.
- The tell in hindsight: stop medians like "entry sits 164t beyond the last favorable pivot with zero adverse pivots knifed" describe *where price went during the trade*, not an entry state; and the first-pass features put median winner `mom_30m_r` at −0.30R on a strategy that by construction trades drift days. The corrected medians put *all* trades at ≈+0.2 to +0.45R with-move.

## What survives (from trade rows, engine-computed — never touched by the bug)

- Per-level texture: Globex VAL remains the weakest surface (76% win, net −$5.9k level-stop / +$0.7k entry-stop); NY VAH the strongest (94%/90% win, ~+$17.7k/+$16.0k). Thin per-level ns; the entry-reason study's individual-level null stands.
- Stops split 11 short / 6 long (level-stop); short-side losses remain the concentration, consistent with earlier drift-fade reads.
- The v2 re-baselines reproduce v1 03f4c56c exactly with the knob off — the engine change is a clean no-op.

## Lessons (the durable part)

1. **When porting an extraction across engines, verify the anchor invariant first**: assert the tick at the recovered entry index/timestamp matches the recorded fill price. One line; it would have caught this instantly.
2. **Split-half, permutation, temporal replication and dose-response do not detect lookahead.** They test sampling noise, not causality. A leaked feature replicates out-of-sample forever. The engine A/B — or any test that must act on live data — is the causality check.
3. **Too-good-to-be-true has a number**: AUC 0.83 with 2/89 stops in the kept cohort on a strategy whose 10 prior gate ideas mostly failed should have raised the leakage prior before the A/B, not after.

**Verdict:** no structural or price-action gate lead on the drift-fade family at entry. The approach-momentum veto knob stays shipped, default-off, with a failed A/B on record (gate-class tally now ~1 pass in a dozen). The 17–21 stops remain unexplained by everything tried so far — tape (prior studies), and now structure.
