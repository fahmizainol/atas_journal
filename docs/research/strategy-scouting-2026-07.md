# Strategy scouting pass — July 2026

- **Date:** 2026-07-19
- **Research question:** With the flagship stable (v13-a348d176: $150.4k net, 262 trades, PF 2.05, Sharpe 3.04 over 2025-02→2026-06), where should the next strategy come from? Full sweep of every data source in the repo: the v9 interactions snapshots (monthly-robustness pass — lab-backlog queue item 1), the IB/ORB cache with stops enforced, the weekly-VWAP study as a standalone, the v7 regime cache, and every sim strategy's run history + ghost cohorts.
- **Method:** three parallel analysis passes; every headline number below was reconciled against its source doc before the new cuts were trusted. Scripts were scratch-only; the numbers that matter are recorded here.

---

## TL;DR — ranked outcomes

1. **Drift touches SURVIVE the monthly pass — the strongest Lab lead to date.** 17/17 months above null on both reject rate and MFE/MAE ratio, unconcentrated, passes the circularity control. Spec to trade it: `drift-touch-fade-spec.md`.
2. **5m ORB with the stop enforced is a real edge (+0.28R/trade, 9/12 months, both halves positive)** — and a correction: the remembered +0.49R no-stop figure was the Aug-2025→Jan-2026 window only; full-year no-stop is exactly **0.00R**. The stop *creates* the expectancy by truncating a −38R left tail. Tail-concentrated; the unlock is an entry-time trend proxy nobody has built.
3. **The lower-band short mirror has never faced a real window** — 10 runs, all 1–2 weeks of Oct 2025 (filtered PF 1.24–1.30). Its habitat exists and is untouched: 76 sessions (23%) with bbr@10:30 ≥ 0.65. One full-window run with the mirrored regime gate is the cheapest new information in the repo.
4. **profile-pullback-long deserves promotion to the 17-month window** — 8-month PF 1.54, maxDD −$1.5k, different location than the flagship.
5. **The old candidate edge is dead:** lone-down VA-snap fade fails monthly (9/17 months, gap manufactured by 4 outlier months). Also dead: pd VAH-from-above, band+session-ref stacking (top-5 sessions = 112% of the edge), weekly >+2σ standalone short (sign flipped OOS), IB-breakout variant, Globex-band bounce family.
6. **Flagship leak found:** 32 pre-10:30 entries slip in before the regime checkpoint on bad-bbr days, netting **−$15.7k**. An earlier regime read (09:45 checkpoint or a Globex pre-open proxy) is a live A/B candidate worth more than most new strategies.

---

## 1. Interactions v9 monthly-robustness pass (lab-backlog item 1 — DONE)

17 monthly cuts, Feb 2025 → Jun 2026, on the v9 snapshots (`7cfdd06e65e9` defaults / `f166e8ed4082` +vwap_bands). All §-references are to `interactions-v9-findings.md`. Every headline number reconciled exactly before cutting.

### 1.1 Drift touches (§1) — SURVIVES

| month | n | reject | ratio | | month | n | reject | ratio |
|---|---|---|---|---|---|---|---|---|
| 2025-02 | 80 | .738 | 1.84 | | 2025-11 | 90 | .611 | 1.03 |
| 2025-03 | 88 | .682 | 1.96 | | 2025-12 | 88 | .682 | 2.22 |
| 2025-04 | 111 | .694 | 1.51 | | 2026-01 | 85 | .612 | 1.04 |
| 2025-05 | 97 | .763 | 2.43 | | 2026-02 | 99 | .657 | 1.69 |
| 2025-06 | 76 | .645 | 1.99 | | 2026-03 | 82 | .744 | 2.36 |
| 2025-07 | 79 | .810 | 2.02 | | 2026-04 | 80 | .750 | 1.77 |
| 2025-08 | 67 | .657 | 1.66 | | 2026-05 | 97 | .711 | 2.20 |
| 2025-09 | 74 | .689 | 1.49 | | 2026-06 | 72 | .764 | 1.82 |
| 2025-10 | 95 | .716 | 1.73 | | *null* | | *.605* | *0.99* |

Ratio > null in **17/17** months (min 1.03, ≥1.2 in 15/17); reject > null in 17/17; no month n<30; beats the same-month non-drift benchmark in 15/17. Concentration: 354 sessions contribute, top-10 = 24.1% and top-20 = 39.5% of sum(MFE−MAE) — broad.

**Circularity control (the §7 to-do):** for each drift touch, count same-day prior rejects of the same zone. The clean cell — zones that had **zero** prior rejects that day — keeps reject 0.684 / ratio **1.65** vs non-drift 1.11; prior rejects add a little (→1.91) but are not the cause, and non-drift touches get *no* lift from prior rejects (1.11 vs 1.12). Drift ∩ 1st-touch (maximally anti-circular): 0.698 / 1.73. **Not circular — promotable to an engine A/B.** Spec: `drift-touch-fade-spec.md`.

### 1.2 Lone-down VA-snap filter (§3) — DIES

Lone > multi at rev30 in only **9/17** months (8/17 at rev60) — a coin flip. The aggregate ~10pp gap was manufactured by four months where multi-down reversion collapsed to 0.00–0.19 (2025-02/-08/-11, 2026-02); elsewhere multi frequently reverts *more*. Lone-down's own rev30 swings 0.25–0.71 by month. The standing "downside VA-snap fade to VWAP" candidate edge has no stable monthly footing — **deprioritized**.

### 1.3 Session refs (§4) — one survivor

Monthly n is thin (n<30 in 10–14 of 17 months per row); two-month buckets are the honest read:

| row | buckets >0.99 | bucket ratios (Feb25→Jun26) | verdict |
|---|---|---|---|
| **pd POC from below** | **9/9** | 1.92 1.11 1.41 1.94 1.58 1.27 1.68 2.21 1.47 | **SURVIVES** |
| ONL from above | 5/9 | 0.88 0.94 0.69 1.33 0.86 1.47 1.71 2.45 1.25 | MIXED — Feb–Jul 2025 dead |
| Open from above | 6/9 | 2.63 0.95 1.54 0.75 1.34 1.85 0.98 1.46 1.26 | MIXED — alternates |
| pd VAH from above | 5/9 | 1.72 1.41 0.85 2.04 0.90 0.83 2.04 2.56 0.95 | DIES |

pd POC-from-below (overhead rejection; n=381, ratio 1.56) is the only ref row with an unbroken direction — modest, likely a confluence/gate ingredient rather than a standalone. ONL's split-half H1 1.23 was masking a dead six-month run — another argument for monthly cuts over halves.

### 1.4 Band +1σ stacked-with-session-ref (§6 lead) — DIES

Top-5 sessions carry **111.9%** of the total edge (the rest net negative); the largest quarter by n (2025Q4, n=17) is inverted at ratio 0.23. A few good sessions plus a hole where the most data is. Do not carry forward.

Still monthly-untested: the level-led profile-pullback arm condition (§5) — it was not in this pass.

## 2. IB/ORB with the stop enforced

Cache `data/cache/ib/NQ_20250201-20260131_v1-312c8eb56ec2.json`, 257 sessions (2025-02-03→2026-01-30); stops recomputed on minute bars from the tick cache, verified against the cache to rounding.

**Provenance correction:** the "+0.49R no-stop" figure was the Aug-2025→Jan-2026 window (n=129). Full-year no-stop = **0.00R exactly** (April 2025 alone −59R; unstopped losses reach −38R in stop units).

**The surviving shape:** 5m ORB — enter the 9:35 close in the first-candle direction, stop at the candle's opposite extreme, EoD exit:

| n | avg R | win | total R | months + | halves | p90 |
|---|---|---|---|---|---|---|
| 257 | **+0.283** | 26.8% | +72.7 | 9/12 | +37.1 / +35.6 | +3.5R |

The stop *creates* the edge by truncating the left tail — the classic low-accuracy/right-tail ORB profile. ATR-fraction stops score higher on paper (+0.39R at 0.05×ADR14) but are ~14-pt-stop slippage lotteries. Shorts fine stopped (+0.39R vs longs +0.17R) — ORB is a day-with trade both ways.

**Filters:** every honest entry-time filter washes out or is a luck-suspect spike — VWAP-side dead (−0.02/+0.05), candle-strength *reverses* on the stopped variant, **narrow-IB is the worst width tercile locally (−0.62R — the external claim reversed)**, OR-width's +0.57R middle bucket is non-monotonic, weekday is noise. The two big splits are look-ahead only: end-of-day regime class (trend +0.47R vs non-trend −0.47R no-stop) and break-alignment (+0.31/−1.03). **Fragility: top-5 days >100% of total R.** The strategy is cheap tickets on trend days; its live viability hinges on an entry-time trend proxy from the 9:30/9:45 regime-checkpoint raw features — unbuilt, untested, and the single unlock worth pursuing before promoting `orb-breakout` beyond its smoke test.

**Dead:** the IB-breakout variant (first 60m-IB break ≥10:30): −0.03R all-in, and the narrow-IB filter makes it *worse* (−0.23R).

**Local day-type base rates** (n=257, replacing the folklore): normal 16.0% · normal-variation 53.7% · trend 9.7% · neutral-center 8.9% · neutral-extreme 11.7%. Either-side break 96.1%; median extension 0.60×IB; ≥1× on 24.5% of days, ≥2× on 4.3% — the platform 1×/2× extension "targets" print far less often than the lore implies.

## 3. Weekly VWAP as a standalone — dead

From the weekly cache (283 seasoned sessions) + tick-cache excursions, sessions opening > weekly +2σ (n=24): median drift −56.5 pts but only 62% close down, weekly-mid hit rate **21%** (median target distance 280 pts), median adverse for a short at the open **+87 pts** (p75 185). And the sign flipped out of sample: median drift −100.8 pre-Nov-2025 (n=14) → **+80.9 after** (n=10; 2026Q2 has six +145..+224-pt up-drifts from >+2σ opens). The reversion was a 2025-summer phenomenon; at most a "skip longs" read, already harvested by the reenter knob. Weekly −1σ touch longs: +17 pts of median edge against ~60-pt excursions — too thin. −2σ still breaks through (never fade it).

## 4. Regime coverage and the flagship

v7 cache, 363 deduped sessions. EOD classes: trend_up 110 · mixed 90 · trend_down 66 · balance 41 · parked 29 · unknown 27. Good-regime days (bbr@10:30 < 0.35): 190 of 336 measurable.

- **The flagship already harvests its habitat:** 157/190 good days traded (83%). Of the 33 untraded, 21 were fully vetoed with ghost sums of −$13.2k (the gates were right) and only **12 had no band-touch setup at all — just one of them a trend_up day**. There is no untapped pile of good-regime long days.
- **The leak runs the other way:** 32 flagship trades entered *before* the 10:30 regime checkpoint on days the checkpoint would have failed = **−$15.7k** (vs +$155.8k on good days). An earlier regime read — the 09:45 checkpoint, or a Globex-based pre-open proxy — is a concrete A/B candidate on the current baseline.
- **The untapped cohorts are mirror-side:** 66 trend_down / 76 high-bbr (≥0.65) sessions that no live strategy touches; median eod net_travel −0.46 on the high-bbr set. This is the lower-band mirror's habitat (§5).

**Ghost capacity check (v13-a348d176, sole-gate attribution):** gx_overhang +$22.4k (the audited path-dependence mirage — do not chase), chop +$2.4k, reentry_halt −$4.3k, gx_poc_shape −$6.4k, regime **−$27.7k** (cleanest gate, confirmed again). in_trade capacity ≈ $0 realizable. **No free ghost PnL remains in the flagship.**

## 5. Sim inventory — verdicts

| strategy | evidence | verdict |
|---|---|---|
| vwap-upper-band-bounce | v13-a348d176: $150.4k / 262 / PF 2.05 / Sharpe 3.04 | flagship |
| profile-pullback-long | v4-5092c2f1 (8mo): +$9.4k / 101 / PF 1.54 / DD −$1.5k; consistent across first-touch configs | **promote to 17-month window** (+ regime/vwap_slope stack; keep first-touch, never dwell) |
| vwap-lower-band-bounce | 10 runs, all 1–2wk Oct-2025; filtered PF 1.24–1.30, 57–59% win | **never seriously run — full-window run with mirrored regime gate (bbr ≥ ~0.65)** |
| orb-breakout | 1-week smoke, −$6.8k | parked pending the entry-time trend proxy (§2) |
| vwap-dev1-fade-long | registered, **zero runs ever** | zero-cost gap fill, low prior (short twin dead) |
| value-rotation | best variant +$5.1k/PF 1.11; base short −$9.7k | dead (balance-day study confirmed) |
| vwap-dev1-fade-short | ~50 runs, best cherry PF 1.12 among ~40 losers | dead |
| vwap-globex-bounce (+retired lower slug) | full window: long −$15.8k, short −$31.3k | dead both ways |

## 6. Recommended queue

1. **Build `drift-touch-fade`** per the spec; sides split, no gates, full 17-month window; robustness ladder from `data/research/gate-robustness/`.
2. **Full-window lower-band-mirror run** with the mirrored regime gate — cheapest new information, everything already built.
3. **Earlier-regime-read A/B on the flagship** (09:45 checkpoint / pre-open Globex proxy) targeting the −$15.7k pre-10:30 leak.
4. **Promote profile-pullback-long** to the 17-month window.
5. **ORB entry-time trend proxy** from the 9:30/9:45 regime-checkpoint features; only then revisit `orb-breakout`.

## 7. Provenance

Sources reconciled against: `interactions-v9-findings.md` + v9 snapshots (`7cfdd06e65e9`, `f166e8ed4082`), `data/cache/ib/NQ_20250201-20260131_v1-312c8eb56ec2.json` + tick-cache recompute, `data/cache/weekly_vwap/NQ_20250203-20260630_v1-15c6404e2574.json`, `data/cache/regime/*_v7.json`, run artifacts under `data/sims/*/` (metrics.json / trades.parquet / vetoed.parquet / missed.parquet), gate audit in `data/research/gate-robustness/`. Related docs: `initial-balance-orb.md`, `weekly-vwap.md`, `gate-robustness.md`, `drift-touch-fade-spec.md`.
