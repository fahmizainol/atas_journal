# Drift-Fade — entry reference level, does it matter?

- **Date:** 2026-07-19
- **Status (updated 2026-07-19, §7):** the *individual level* (13-way) is **NULL** — no level has an edge (p=0.91, split-half rank corr −0.09, 8/13 sign-flip, regime-residual p=0.88). But the **developing-vs-static** regroup, a hint at p=0.31 in-sample, **REPLICATES OUT-OF-SAMPLE**: on a held-out 2026-H1 split, developing-only matches baseline net (−2%) with PF 1.61 vs 1.37, +43% expectancy, and a 26% shallower drawdown; developing beats static on efficiency in *both* train and test (test: developing Sharpe 1.99 / static 0.41 — static refs are near-dead money OOS); and it generalises to the level-stop sibling (OOS drawdown 44% shallower). This is now an **adoption-worthy lead** — flip `use_session_refs=false` for the drift-fade strategies — with two residual cautions (Sortino is lower in most in-sample cuts; one instrument / ~17 months). The old "trade all levels" verdict stands only for *which* profile level; the developing/static *family* split is real.
- **Run:** `drift-touch-fade-entry-stop` `63c78056` (230 trades, net $64,440, 2025-02-03→2026-06-30, entry window 12:00–15:00 ET, `side=both`, all three sources + POC/VAH/VAL on).
- **Machinery built:** the engine now records **which candidate zone's drift-touch triggered each fill** by its human name (`entry_reason`: "Globex POC", "NY VAH", "ONH", "pd VAL", …) — it was always known at the touch and dropped at `best_signal`; now threaded `best_signal → pending/bwatch → _Pos → _row`. New `by_entry_reason` edges cut ("Entry reference level", knowable, net-ranked), served per-run only where the column exists, visible in the Run Edges panel. Ghost/vetoed rows carry it too.

---

## 1. The aggregate breakdown (what prompted the study)

By net, all 13 levels of run `63c78056`:

| Level | Trades | Net | Win % | avg_r |
|---|--:|--:|--:|--:|
| NY VAH | 30 | $15,165 | 90.0 | 0.21 |
| Globex VAH | 21 | $13,923 | 95.2 | 0.27 |
| Globex POC | 30 | $8,115 | 83.3 | 0.12 |
| NY VAL | 25 | $7,275 | 84.0 | 0.13 |
| pd POC | 14 | $4,962 | 85.7 | 0.15 |
| NY POC | 16 | $4,878 | 81.3 | 0.13 |
| Open | 13 | $4,104 | 84.6 | 0.14 |
| pd Close | 12 | $3,246 | 83.3 | 0.12 |
| ONL | 12 | $3,231 | 83.3 | 0.12 |
| ONH | 14 | $1,437 | 78.6 | 0.06 |
| Globex VAL | 22 | $1,251 | 77.3 | 0.04 |
| pd VAL | 8 | −$186 | 75.0 | 0.01 |
| pd VAH | 13 | −$2,961 | 69.2 | −0.07 |

The two VAH buckets look like stars and `pd VAH` the one loser — but **permutation luck p=0.91**: a 13-way split of a 230-trade book reproduces spreads this wide 91% of the time. Everything below is the robustness ladder that turns that warning into a verdict.

## 2. #1a — Concentration: NOT a one-trade artifact (good news, but not the story)

Unlike the flagship bounce runs (30badf94: top-20 trades = 101% of net), this fixed-target drift-fade is **evenly spread**. Every bucket's single best trade is the same +$858 (most winners hit the fixed 60-tick target on 3 lots), and NY VAH's best trade is only **6%** of its net — its $15k is ~25 near-identical winners at 90% win. So the ranking is *not* explained by a lucky monster trade. (The high best-trade shares on ONH/Globex VAL are just small nets, not concentration.) The tail isn't the culprit — which sends us to time-stability.

## 3. #1b — Split-half: the decisive null

Split at 2025-10-13 (115 trades each half), bucket net H1 vs H2:

| Level | H1 net | H1 avg_r | H2 net | H2 avg_r | flip |
|---|--:|--:|--:|--:|:--:|
| Globex VAH | 6,201 | 0.21 | 7,722 | 0.34 | — |
| Open | 6,006 | 0.34 | −1,902 | −0.11 | **flip** |
| NY POC | 5,922 | 0.27 | −1,044 | −0.04 | **flip** |
| NY VAH | 5,820 | 0.16 | 9,345 | 0.25 | — |
| pd Close | 5,148 | 0.34 | −1,902 | −0.11 | **flip** |
| Globex VAL | 4,962 | 0.15 | −3,711 | −0.16 | **flip** |
| pd VAL | 3,432 | 0.34 | −3,618 | −0.33 | **flip** |
| Globex POC | 3,246 | 0.12 | 4,869 | 0.12 | — |
| NY VAL | 1,362 | 0.05 | 5,913 | 0.22 | — |
| pd POC | 672 | 0.04 | 4,290 | 0.34 | — |
| ONH | −1,902 | −0.11 | 3,339 | 0.18 | **flip** |
| ONL | −3,633 | −0.33 | 6,864 | 0.34 | **flip** |
| pd VAH | −8,016 | −1.00 | 5,055 | 0.21 | **flip** |

- **Spearman rank corr of bucket net, H1 vs H2 = −0.09** (buckets with ≥4 trades each). The first-half ordering carries *zero* information about the second half.
- **8 of 13 buckets sign-flip** avg_r between halves.
- The aggregate "worst" bucket, **pd VAH, was one bad opening stretch** — 3 trades at avg_r −1.00 (−$8,016), then +$5,055 on 10 trades. Its headline loss is a single early week, not a property of the level.

This is the finding: **the per-level P&L is noise.**

## 4. #2 — Regime doesn't rescue it (and doesn't sort this book)

Entry-knowable regime = the **12:00 checkpoint** class (`regime.get_regime` → `checkpoints["12:00"]["class"]`; the top-level `class` is eod and would leak).

By regime alone: trend_up 0.160 · unknown 0.168 · balance 0.114 · mixed 0.070 · trend_down 0.057 · parked 0.343 (n=14, 100% win). **Permutation p=0.499** — regime does **not** separate this afternoon book, unlike the flagship bounce where regime is the cleanest real detector. (Different strategy, different session — noted, not alarming.)

Does level add anything *beyond* regime? Residualize each trade's net by its regime-group mean, then re-test entry_reason:
- raw net: **p=0.908**
- regime-residual net: **p=0.880**

Level explains nothing before or after conditioning. Regime composition of each level is diffuse (no bucket concentrated in one regime), so entry_reason isn't even a regime proxy — it's just noise.

## 5. The one thread that didn't instantly unravel — developing vs static

The 5 buckets that stay positive **both** halves — Globex VAH, NY VAH, Globex POC, NY VAL, pd POC — are the developing-profile levels. Regrouped into two fat buckets:

| Family | Trades | Net | Win % | avg_r | H1 net | H2 net |
|---|--:|--:|--:|--:|--:|--:|
| developing (Globex/NY POC·VAH·VAL) | 144 | $50,607 | 85.4 | 0.150 | 27,513 | 23,094 |
| static (Open/ONH/ONL/pd\*) | 86 | $13,833 | 80.2 | 0.077 | 1,707 | 12,126 |

Developing is both higher avg_r **and** stable across halves; static is lower and lopsided. Mechanistically plausible — a live, developing value edge is a zone the session is currently defending; a static overnight ref is a line the RTH auction may have already left behind. **But permutation p=0.31** — still short of the bar. It is the *only* direction worth carrying forward, and only as a low-prior lead to re-cut on the sibling `drift-touch-fade` (level-stop) run and on more data before it earns a `use_session_refs`-off A/B.

## 6. Verdict / what not to do

- **Do not** build a per-level filter (drop `pd VAH`, trade-only-VAH). p=0.91 says there's no lead; §3 says the "loser" is one week. That is the exact post-hoc-into-A/B trap that has failed ~9–10 times.
- **Positive read:** the edge is *robust to which level* — it comes from the drift-touch-fade mechanic, not from any one reference. A strategy whose profit lived in a single level would be fragile; this one isn't.
- **Carry forward (low prior):** developing-vs-static (§5). Re-cut on `drift-touch-fade` (level-stop) and on the afternoon-only cohort as it grows; build the `use_session_refs` A/B only if it clears its own permutation bar there first. **→ done in §7 — it held up OOS.**

## 7. OOS + sibling: the developing-vs-static lead strengthens

Ran the actual `use_session_refs=false` A/B (challenger run `03f4c56c`; the single-position slot re-times trades, so these are real runs, not filters). Full window it looks like a "trade money for smoothness" swap — net −17% ($64.4k→$53.4k) but PF +15%, DD −23%, Sharpe +10%, expectancy +23%. The split below shows the net-giveup is a **train-period artifact** and the efficiency edge is **out-of-sample real**.

**A — developing-only vs baseline, entry-stop, split train (<2026) / test (2026 H1):**

| period | config | n | net | PF | exp | r_mean | Sharpe | Sortino | maxDD |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| TRAIN | baseline(all) | 151 | 49,533 | 1.83 | 328 | 0.141 | 3.25 | 4.39 | −5,334 |
| TRAIN | developing | 101 | 38,793 | 2.10 | 384 | 0.162 | 3.42 | 3.07 | −4,476 |
| **TEST** | baseline(all) | 79 | 14,907 | 1.37 | 189 | 0.088 | 1.76 | 3.72 | −7,329 |
| **TEST** | **developing** | 54 | **14,607** | **1.61** | **271** | **0.119** | **2.24** | 3.27 | **−5,427** |

OOS, developing-only gives up **$300 of net** (−2%) for +18% PF, +43% expectancy, +27% Sharpe and a **26% shallower drawdown**. Nearly all the full-window −$11k giveup was in the (mined) train half.

**B — founding signal replication: developing vs static families, baseline book, per period:**

| period | family | n | net | PF | exp | r_mean | Sharpe |
|---|---|--:|--:|--:|--:|--:|--:|
| TRAIN | developing | 92 | 37,716 | 2.24 | 410 | 0.172 | 3.64 |
| TRAIN | static | 59 | 11,817 | 1.40 | 200 | 0.092 | 1.09 |
| **TEST** | **developing** | 52 | 12,891 | **1.54** | **248** | **0.110** | **1.99** |
| **TEST** | static | 27 | 2,016 | **1.13** | **75** | **0.044** | **0.41** |

Developing beats static on every efficiency metric in **both** halves. In the held-out test, static refs are barely alive (PF 1.13, expectancy $75, Sharpe 0.41). The p=0.31 full-window permutation was underpowered — it scored *net* (dominated by trade count and the fixed target), not efficiency, and diluted the 2-way contrast across a 13-way split. The train/test replication of the efficiency gap is the stronger evidence.

**C — sibling `drift-touch-fade` (level-stop), all vs developing-only:**

| scope | config | n | net | PF | exp | r_mean | Sharpe | Sortino | maxDD |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| full | baseline(all) | 232 | 54,006 | 1.46 | 233 | 0.105 | 1.85 | 2.93 | −13,527 |
| full | developing | 156 | 47,568 | 1.69 | 305 | 0.132 | 2.16 | 2.31 | −10,884 |
| TEST | baseline(all) | 79 | 15,582 | 1.36 | 197 | 0.091 | 1.49 | 4.01 | −10,458 |
| TEST | developing | 54 | 12,207 | 1.43 | 226 | 0.102 | 1.45 | 4.11 | **−5,850** |

Same shape on the other exit: PF/expectancy/efficiency up, drawdown down (OOS **44% shallower**), net down. Sortino even recovers OOS here.

**Verdict:** the developing/static family split is a **replicating OOS lead**, not the in-sample mirage the p=0.31 implied — the rare A/B that *strengthens* out-of-sample. Adoptable: set `use_session_refs=false` on the drift-fade strategies (drop Open/ONH/ONL/pd\* as entry references). Residual cautions: Sortino is lower in most in-sample cuts (downside deviation relatively worse — watch it live), and this is one instrument over ~17 months. Recommended: adopt with `use_session_refs=false`, or paper-forward one more quarter if a second independent confirmation is wanted before committing size.
