# Gate Robustness Scorecard — which confluences are real edges, which are luck

- **Date:** 2026-07-19
- **Research question:** Of the four confluences in the pinned `vwap-upper-band-bounce` baseline (`regime`, `gx_poc_shape`, `gx_overhang`, `chop`), which are genuine, stable edges and which are overfit luck riding a tail-concentrated book and ~11 gates' worth of multiple comparisons?
- **Data:** baseline `20250201-20260630-v13-a348d176` (262 trades, $150.4k net, PF 2.05, win 74.4%, maxDD −$13.7k, Sharpe 3.04, `reenter_after_stop_only=True`) plus a 9-run A/B ladder on the identical config: each gate deleted (full-stack marginal), each gate's parameter neighbors (plateau-vs-spike). All runs off cached ticks — no new data purchased.
- **Files:** `data/research/gate-robustness/` — `run_ladder.py` (ladder driver, skips existing run ids), `eval_scorecard.py` (all tests), `scorecard.json` (raw numbers), `run_ladder.log`.

---

## TL;DR — the scorecard

| gate | verdict | in-stack marginal | why |
|---|---|---|---|
| **gx_overhang** (≤50t) | **REAL — the biggest edge in the stack** | **+$38.1k net, PF +0.57, maxDD −45%, Sharpe +1.03** | Without it the *body* of the book is net-negative (off-run ex-top-20 = **−$8.8k**); benefit grows under tail-cap; both halves positive; parameter plateau. Its own ghosts net **+$22.4k** — the value is second-order (what freed arm-cycles re-fill into), the ghost ledger inverts the verdict. |
| **regime** (bbr≤0.35 @10:30) | **REAL — the cleanest bad-trade detector** | +$8.4k net, PF +0.22, maxDD −29% | The only gate whose vetoed cohort is *provably* worse: ghosts net −$27.7k, 50% stop rate vs 24% kept, AUC 0.657, **p=0.0008** — the one p-value in the study that survives a Bonferroni across the ~11 gates ever tried. Tail-robust, both halves positive, flat plateau. |
| **gx_poc_shape** (25–100t veto) | **REAL, moderate — zone edges partly fitted** | +$16.8k net, PF +0.21, maxDD −25% | Positive at *every* neighbor (both zone variants beat gate-off by ~$8k), tail-robust, both halves positive. But the exact 25–100 zone adds ~$9k over its neighbors — that increment is plausibly fit. Cohort separation marginal (AUC 0.574, p=0.06). |
| **chop** (≤0.65) | **LUCK-SUSPECT — keep only with eyes open** | +$8.2k net, PF +0.20, maxDD −28% | The only gate where both neighbors land **below gate-off** (0.60: $106.0k, 0.70: $132.4k vs off $142.3k) — a solitary spike, not a plateau. Halves flip sign (2025 −$1.1k). Vetoed cohort statistically indistinguishable from kept (AUC 0.534, p=0.39) and nets *positive* $2.4k. Widest bootstrap CI of the four ([−$34k, +$45k]). |

**The stack minus chop** is the fully-defensible core: every remaining gate passes tail-cap, halves, plateau, and selection-quality tests. Chop's +$8.2k is real money in this window but nothing in the scorecard distinguishes it from a lucky cutoff placement.

---

## Why net P&L alone was the wrong lens

1. **Tail concentration.** The baseline's top 20 trades are **77.6% of net** ($116.8k of $150.4k). Any gate that happens to spare the top-20 looks brilliant on net; any that clips two of them looks broken. Every marginal below is therefore also read ex-top-20 and winsorized at the pooled p95.
2. **Small n.** 262 trades, ~17 months. A gate that truly adds $500/month needs far more months than we have to clear noise on a monthly sign test — so *absence of temporal significance is expected even for real gates*, and presence of selection-quality significance matters more.
3. **Multiple comparisons.** ~11 gates have been A/B'd on this strategy (1 pass: `reenter_after_stop_only`). At per-test α=0.05 a scorecard this size will hand out flukes; the Bonferroni-ish bar is p≈0.005. Only regime's cohort test clears it.
4. **Re-arm interaction.** A veto doesn't just delete a trade — it changes what the engine arms next (`reenter_after_stop_only` chains). So isolated ghost P&L ≠ in-stack value, in either direction (gx_overhang is the proof). Every marginal here is a full engine re-run, never subtraction.

## The test ladder

| test | what it asks | immune to |
|---|---|---|
| T1 marginal | full-stack A/B: baseline vs gate deleted (real engine, re-arms intact) | subtraction bias |
| T2 months | per-month sign test on baseline-vs-off monthly nets (ties dropped, exact binomial) | tail months |
| T3 bootstrap | block bootstrap (month & day blocks, 10k) of the daily delta → CI of the gate's total contribution | serial correlation (partly) |
| T4 tail | Δnet ex-top-20 and winsorized at pooled p95 | tail concentration |
| T5 selection | kept book vs 10k random same-size subsets of kept ∪ unique-ghosts: win-rate / mean-R percentile | "any filter helps" illusion |
| T6 cohort | kept vs unique-ghost R distributions: Mann-Whitney, AUC, stop rates | net-dollar distortion |
| T7 neighborhood | parameter neighbors: plateau (robust) vs spike (fit) | cutoff placement luck |
| T8 halves | Δnet sign 2025 vs 2026 | single-regime windows |

T5/T6 run in the **ghost frame** (baseline's `vetoed.parquet`, unique vetoes only — rows where *only* that gate fired), so they measure selection quality without re-runs; caveat: ghost outcomes are simulated without capacity effects and their *dollar* totals are directional-only (see gx_overhang).

## Full results

Baseline: 262 tr, $150,439, PF 2.05, maxDD −$13,731, Sharpe 3.04, ex-top-20 $33,627.

### T1 — full-stack marginals (gate deleted)

| gate off | run | trades | net | PF | maxDD | Sharpe | Δnet | ΔPF | ΔmaxDD |
|---|---|---|---|---|---|---|---|---|---|
| regime | 9b22b36d | 286 | $142,043 | 1.83 | −$19,318 | 2.74 | **+$8,396** | +0.22 | +$5,588 |
| gx_poc_shape | 8a55b2dd | 281 | $133,685 | 1.84 | −$18,268 | 2.68 | **+$16,755** | +0.21 | +$4,538 |
| gx_overhang | 1b55f645 | 341 | $112,350 | 1.48 | −$25,144 | 2.01 | **+$38,090** | +0.57 | +$11,413 |
| chop | d2f44d4e | 289 | $142,269 | 1.86 | −$19,057 | 2.78 | **+$8,170** | +0.20 | +$5,327 |

Every gate is net-positive in-stack *in this window* — that is exactly why the rest of the ladder exists.

### T2–T4 — time stability and tail robustness

| gate | months better (p) | month-block CI | Δnet ex-top-20 | Δnet winsor p95 | Δ2025 / Δ2026 |
|---|---|---|---|---|---|
| regime | 7/13 (1.0) | [−$14.1k, +$29.9k] | **+$12,745** | +$12,718 | +$6.0k / +$2.4k |
| gx_poc_shape | 9/14 (0.42) | [−$4.5k, +$39.1k] | **+$14,576** | +$14,589 | +$12.1k / +$4.6k |
| gx_overhang | 8/15 (1.0) | [−$4.0k, +$83.3k] (p=0.077) | **+$42,446** | +$42,419 | +$27.7k / +$10.4k |
| chop | 8/14 (0.79) | [−$34.0k, +$45.3k] | +$8,906 | +$8,892 | **−$1.1k** / +$9.2k |

- No gate clears the monthly sign test — expected at this n (see above). What separates them: the first three gates' deltas **grow** when the tail is removed (they defend the body of the book), and their halves agree. Chop is the only sign-flip.
- gx_overhang's 8/15 months next to a +$38k total is the signature of a *concentration* benefit: it doesn't shave a little every month, it prevents the blow-up compositions. The off-run's ex-top-20 net is **−$8,819** — without this gate, everything outside the top 20 trades loses money.

### T5–T6 — selection quality (ghost frame, unique vetoes)

| gate | unique ghosts | ghost net | ghost win / stop | AUC kept>ghost (p) | kept-vs-random pctile (win / meanR) |
|---|---|---|---|---|---|
| regime | 44 | **−$27,669** | 47.7% / **50.0%** | **0.657 (0.0008)** | 99.96 / 99.96 |
| gx_poc_shape | 65 | −$6,415 | 67.7% / 32.3% | 0.574 (0.063) | 89.8 / 98.8 |
| gx_overhang | 277 | **+$22,411** | 66.8% / 32.9% | 0.565 (0.009) | 99.0 / 99.3 |
| chop | 67 | +$2,358 | 67.2% / 32.8% | 0.534 (0.39) | 91.3 / 96.2 |

- **regime is the only true bad-trade detector**: its vetoes lose money outright, stop at 2× the kept rate, and the separation is the study's only Bonferroni-surviving p-value.
- **gx_overhang's ghosts make money** — cutting them still adds $38k in-stack. This is the confluence-breakdown lesson quantified again: for risk/composition gates the ghost ledger's dollars invert the verdict; only the full A/B tells the truth.
- **chop's cohort is statistically dead** — its vetoes are indistinguishable from its keeps.

### T7 — parameter neighborhood

| gate | tighter | pinned | looser | off | shape |
|---|---|---|---|---|---|
| regime bbr | 0.30: $150,458 / PF 2.05 / DD −$13.7k | **0.35: $150,439 / 2.05 / −$13.7k** | 0.40: $145,885 / 1.99 / −$16.0k | $142,043 | **plateau** (0.30 ≈ pinned to the dollar; graceful decay) |
| gx_poc_shape zone | 25–75: $141,256 / 1.93 / −$16.0k | **25–100: $150,439** | 25–125: $141,604 / 2.02 / −$18.2k | $133,685 | **positive everywhere, peaked** — neighbors beat off by ~$8k; the extra ~$9k at 25–100 is plausibly fit |
| gx_overhang | 40: $150,452 / **PF 2.07** / −$13.7k | **50: $150,439 / 2.05** | 60: $145,840 / 1.99 / −$18.3k | $112,350 | **plateau** (40 ≈ pinned; every neighbor ≫ off) |
| chop | 0.60: $105,997 / 1.81 / ex20 **−$1,990** | **0.65: $150,439** | 0.70: $132,416 / 1.82 | $142,269 | **spike** — both neighbors fall *below off*; only cutoff placement wins |

The neighborhood test is what separates chop from the rest: for the three real gates, *every* neighbor beats gate-off (the effect exists across the parameter range); for chop, the effect exists only at 0.65.

## Verdict rules used

- **REAL:** T1 positive on net *and* risk, T4 same-sign (tail-robust), T8 halves agree, T7 plateau (all neighbors ≥ off), and T5/T6 selection quality above chance.
- **LUCK-SUSPECT:** T1 positive but T7 spike, or T8 flip, or T5/T6 at chance. (T2/T3 significance is a bonus, not a requirement, at n=262.)
- **FAIL:** T1 wash or negative — none of the four; the historical fails (vwap_slope variants, clarity, wk_ext, …) never made it into this stack.

## Actionable residue

1. **Chop is the only decision point.** The pinned baseline keeps it (user's call, 2026-07-19, pending this scorecard). The scorecard's read: its +$8.2k is not distinguishable from cutoff luck — if it is kept, treat its contribution as $0 in expectancy planning and re-score after ~3 more OOS months. If the July–Sep 2026 window erodes the 0.65 spike, drop it without ceremony.
2. **Do not re-tune gx_poc_shape's zone.** 25–100 may be ~$9k of fit; the honest expectation is the neighbor-level ~$8k marginal, which is still clearly worth having.
3. **gx_overhang at 40 is marginally better than 50** (PF 2.07 vs 2.05, same DD, same net) — inside noise; not worth a re-pin, but if the gate is ever re-cut, sweep 35–55 first.
4. **Never read ghost dollars for composition gates** (gx_overhang here, gx_overhang in the confluence-breakdown study, twice now): unique-ghost net inverted the in-stack verdict by $60k.
5. **Future work:** walk-forward with expanding windows + CSCV/PBO (López de Prado) once ~24 months accumulate; Deflated Sharpe accounting for the full ~11-gate search history; re-score all four gates on each quarterly re-pin.

## Run inventory (this study)

| label | run id |
|---|---|
| baseline (pinned) | `20250201-20260630-v13-a348d176` |
| off:regime | `…-9b22b36d` |
| off:gx_poc_shape | `…-8a55b2dd` |
| off:gx_overhang | `…-1b55f645` |
| off:chop | `…-d2f44d4e` (pre-existing) |
| regime bbr 0.30 / 0.40 | `…-da5c091a` / `…-8c45d29a` |
| pocshape 25–75 / 25–125 | `…-4074af2b` / `…-6f034831` |
| overhang 40 / 60 | `…-61f149c8` / `…-b690b287` |
| chop 0.60 / 0.70 | `…-4a1f81e9` / `…-9951f7bf` (pre-existing) |
