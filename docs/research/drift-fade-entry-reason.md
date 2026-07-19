# Drift-Fade — entry reference level, does it matter?

- **Date:** 2026-07-19
- **Status:** **RESOLVED NULL** — which reference level a drift-touch fade enters on has **no demonstrated edge**: not in aggregate (permutation p=0.91), not across the window's two halves (bucket rank corr **−0.09**, 8/13 buckets sign-flip avg_r), not before or after conditioning on the entry-knowable regime (raw p=0.91 → regime-residual p=0.88). The one non-significant *direction* worth a re-test on more data: **developing profile levels** (Globex/NY POC/VAH/VAL) run cleaner and steadier than **static/overnight refs** (Open/ONH/ONL/pd\*) — avg_r 0.150 vs 0.077, and stable H1↔H2 vs a coin-flip — but at p=0.31 it is a hint, not a finding. **No knob built.** Trade all levels; the edge is the drift-touch-fade mechanic itself, uniform across references.
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
- **Carry forward (low prior):** developing-vs-static (§5). Re-cut on `drift-touch-fade` (level-stop) and on the afternoon-only cohort as it grows; build the `use_session_refs` A/B only if it clears its own permutation bar there first.
