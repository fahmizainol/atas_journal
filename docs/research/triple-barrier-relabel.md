# Triple-barrier relabel — Stage 1 (drift-fade + upper-band)

**Date:** 2026-07-26 · **Status:** Stage 1 complete on two strategies (offline relabel, no engine run) · **Basis:** [price-action-to-data survey](price-action-to-data-survey.md) open question #1 — would a volatility-scaled first-touch relabel move any borderline verdict?

Runs under test:

| strategy | run | trades | net | PF | Sharpe | maxDD |
|---|---|---|---|---|---|---|
| `drift-touch-fade-entry-stop` | `20250203-20260630-v2-b0c570aa` | 154 | $72,402 | 1.96 | 2.98 | −$6,375 |
| `vwap-upper-band-bounce` | `20250201-20260630-v13-a348d176` | 262 | $150,439 | 2.05 | 3.04 | −$13,731 |

(Upper-band's `a348d176` is the audited canonical v13 stack from the [gate-robustness scorecard](gate-robustness.md); a couple of sweep siblings edge it on raw net by <0.4%.)

Scripts: `data/research/triple-barrier-driftfade/relabel.py` → `relabeled__<slug>.parquet` → `analyze.py <slug>`.

## What Stage 1 does

The journal scores a trade by MFE/MAE over the engine's **actual** hold (`entry_idx → exit_idx`) — a label blind to a counterfactual stop. Triple-barrier (López de Prado) relabels each trade by whichever barrier price touches **first**, walking forward from entry: a profit barrier, a stop barrier, a vertical (time) barrier. Barriers here are symmetric multiples *k* of a per-trade realized-vol unit σ (stdev of 1-min log returns over the trailing 30 min, in points), capped at the RTH close. An `eng_match` config uses the trade's own engine stop/target distances, capped at the engine's actual exit, as a sanity anchor.

**Correctness anchors (this is what makes the numbers trustworthy):**

- The relabel rebuilds the **exact** tick array the engine traded, read-only (`cached_*` readers — never buys). The splice layout differs by strategy and is **auto-detected** per run by whichever array minimises `|price[entry_idx] − avg_entry|`: drift-fade (`session="globex"`) splices the overnight in front of RTH (`median 0.000`); upper-band is RTH-only (`median 0.133`, the residual because it fills a *limit* at dev1, so `avg_entry` is the fill, not the entry tick). Choosing the wrong array would misalign every index by ~60–90 pt — the detector prevents that.
- The trailing-vol window reads from the overnight++RTH array by timestamp even when the walk uses RTH-only, so morning entries (upper-band's 09:45 checkpoint) still get a vol estimate instead of dropping out.
- **Walk validated exact on both:** recomputing MFE/MAE over the engine's own `[entry_idx, exit_idx]` window matches the stored excursions to `0.000` pt on all 154 + 262 trades; `eng_match` agrees with the engine's realized win/loss 100%.

## Result — the relabel is NOT inert, and the disagreement is one-directional on BOTH strategies

`engW/barL` ("path tax") = the engine banked the trade but a symmetric k·σ stop touches **first**. `engL/barW` = the engine's exit cut a trade a first-touch would have won. At **2σ**:

| strategy | agree | flip | engW/barL (path tax) | $ (share of net) | engL/barW | $ |
|---|---|---|---|---|---|---|
| drift-fade | 73.4% | 26.6% | **39** | 44,937 (**62%**) | 2 | −4,929 |
| upper-band | 77.9% | 22.1% | **53** | 51,478 (**34%**) | 5 | −9,207 |

- The **path-tax cell is fat** on both: a quarter of trades are winners whose price pokes a full 2σ against them before reverting. On drift-fade that is **62% of the entire net**; on upper-band **34%**.
- The **reverse cell is ~empty** on both (2 and 5 trades). The engines almost never give up early on a would-be winner.
- **Split-half stable** on both (drift-fade 27.7/25.4%; upper-band 24.3/20.4%) — not a one-half artifact.
- The **time barrier never binds** at 2σ (30/60/120-min columns identical) — every 2σ trade resolves to a directional touch inside 30 min.
- On upper-band the path tax **concentrates at the open**: 9–11 am ET carries essentially all of it, noon ~$0. Consistent with the regime gate's morning stand-down logic.

σ medians: drift-fade 8.1 pt, upper-band 11.1 pt. Both engines' stops are far wider (drift-fade ~43.8 pt ≈ 5.4σ), which is exactly the point below.

## Interpretation — the wide, asymmetric stop is load-bearing, not incidental

This is the clean answer to survey question #1, and it **replicates across two independent strategies**: path-blind labeling *is* materially distorting the outcome accounting — but in the direction that **vindicates the engines' current wide, asymmetric, trailing stops.**

Both strategies are mean-reversion-flavoured entries (fade a drifted-into level; buy a pullback to a band). By construction they catch a level price pokes *through* before it reverts. A tight symmetric vol-stop (2σ) would convert **$45k / $51k of realized winners into losers** — it stops out the very noise the edge is built on. Fresh, independent confirmation of three standing priors:

- losers "die of drift not capitulation" (loser order-flow study);
- "early exits destroy PnL" (upper-band-bounce loss study — the panic-exit knob that A/B-failed);
- wider-stop / re-entry is a live engine lead.

## What this means for Stage 2 (meta-label)

The tight-barrier relabel is a **diagnostic**, not the meta-label target. Because it disagrees one-directionally (it only ever calls winners losers, never the reverse), a meta-label trained on tight-barrier first-touch labels would mostly learn "this entry is volatile" and discard good trades. **A tight vol-scaled stop is contraindicated for both strategies — do not build it, and do not build the meta-label on tight-barrier labels.**

The legitimate Stage-2 target is the engine's **own** realized outcome (which `eng_match` reproduces exactly): *can leakage-safe entry-time features skip the real losers without cutting the survivors?* — a precision filter à la `reenter_after_stop_only`. But the near-empty `engL/barW` cell says there is almost no room to *tighten* exits; the decision-relevant lever this whole exercise surfaces is stop **width**, which is an engine A/B (already a standing wider-stop lead), not an offline relabel.

**Recommendation:** Stage 1 resolved on both strategies with the same verdict. Do **not** proceed to a tight-barrier meta-label or a vol-scaled stop knob. If Stage 2 happens, target the engine's realized outcome with entry-time features, and treat "wider stop" as a separate, higher-prior engine-A/B track.

---

# Stage 2 — engine-outcome meta-label (RESOLVED NULL on BOTH strategies)

**Date:** 2026-07-26/27 · **Status:** complete, NULL on both — offline bar failed, no engine A/B run.

Built the corrected Stage-2 meta-label: predict each strategy's **own** realized losers (target `net_pnl ≤ 0`) from leakage-safe entry-time features, to skip them. Scripts: `meta_features.py` (26 features: config/structure, position-in-day-range, trailing momentum, and 60s/300s tape with the canonical A=buy CVD) → `meta_label.py` (L2 logistic, pure numpy; purged K-fold — contiguous time folds + 1-session embargo, scaler/impute fit on train only). Both take `SLUG RUN` args.

**On both strategies the model fits in-sample and generalises to nothing:**

| metric | upper-band (a348d176) | drift-fade (b0c570aa) |
|---|---|---|
| trades / losers | 262 / 67 (25.6%) | 154 / 29 (18.8%) |
| in-sample AUC | 0.696 | 0.761 |
| **OOS AUC (purged K-fold)** | **0.527** | **0.431** |
| shuffle-null (mean, p95) | 0.479, 0.574 | 0.455, 0.604 |
| empirical p (null ≥ real) | **0.167** | **0.600** |
| split-half OOS AUC | 0.548 / 0.503 | 0.413 / 0.456 |
| naive skip-sweep net delta | negative at every thr (−$5.8k…−$51k) | negative at every thr (−$5.8k…−$23k) |

The large in-sample → OOS collapse on both is the textbook overfit signature — and drift-fade is *worse than a coin flip* out-of-sample (0.431 < 0.5, 60% of random shuffles beat it, both halves below 0.5, and the trades it flags as losers are 0% actual losers at most thresholds). The skip-threshold sweep confirms it from the P&L side on both: at **every** threshold the naive-subtraction net delta is **negative**, so the flagged "likely losers" are net-positive on average and skipping them costs money. And that is the *upper bound* — a faithful engine A/B could only match or worsen it (freeing a slot re-materialises a missed trade), so no engine run was warranted on either.

**Verdict:** the meta-label has **no out-of-sample edge** on either strategy. Expected: the trades already survived their full confluence stacks (upper-band 17 gates — [gate-robustness scorecard](gate-robustness.md); drift-fade its own regime/vwap_slope/wk_ext/chop stack), leaving no residual entry-time separability between their winners and losers. This joins the long line of filter A/Bs that die (chop, clarity, size-up, panic-exit, …); the one that ever passed (`reenter_after_stop_only`) worked off live ghost-re-arm state, not an entry-time classifier.

**Net of the whole study:** triple-barrier's real payoff here was the **Stage-1 diagnostic** — it proved the wide trailing stop is load-bearing on both strategies. Stage 2 (both the tight-barrier form Stage 1 ruled out, and the engine-outcome form tested here) does not add edge. The remaining live lever is stop **width**, on its own engine-A/B track.
