# EMA 20/50/200 — touches, stacking, spacing: descriptor or signal?

- **Date:** 2026-07-28
- **Status:** **RESOLVED NULL** as a signal source; useful only as a chart-level regime descriptor. Stack *formation* is a null trigger, EMA *spacing* does not predict forward chop (it predicts label churn and restates ATR), the textbook *with-stack pullback touch* adds nothing over a random with-stack entry, and *freshness* (time since last contact) has no gradient. One modest survivor: a bull stack already formed at 09:35 (overnight-warmed) leans the rest of day up (62% pos, median +56 pts vs 57%/+17 baseline) — asymmetric (bear-open predicts nothing) and likely collinear with overnight drift. Follow-through (§4b/§4c): the lean split ema-pullback-long hard, an `open_stack_veto` knob was built (v2) and its A/B **passed as a filter but the host strategy failed OOS** — knob validated, nothing adopted.
- **Research question:** Does the classic 20/50/200 EMA read on 1-minute NQ — touches/rejections, the full stack ordering, compression/expansion of the spacing — carry forward-looking information, or is it another rear-view descriptor?
- **Visuals:** `ema-open-stack-examples.html` — four example days for the §4 opening-stack lean (sweet cell, its worst loser, the no-stack control, the dead bear side). `ema-reclaim-examples.html` — five reclaim events for TL;DR-8 (the p100 tail, the go-nowhere median, the whipsaw mode, the short-mirror bounce, a 22-reclaim churn day).
- **Data:** cached tick sessions (367 days, 2025-02 → 2025-12 sample frame), 1-min bars over the ON+RTH stream, `ewm(adjust=False)` warmed over the overnight — the chart's own lines. Deep dives on 4 seeded-random days (2025-02-19, 2025-05-02, 2025-09-23, 2025-11-21); aggregates on a seeded 80-day sample (~30.8k RTH minutes, ~5.7k forward windows); day-level cut on all 363 usable days. Scripts: `ema_obs2.py`, `ema_stack.py`, `ema_spread.py`, `ema_final.py` (session scratchpad, seeds recorded inline).

---

## TL;DR

**The stack is a good rear-view mirror and a bad windshield** — same epitaph as the EMA-vs-band, RSI, and structure-events studies.

1. **Touch/rejection direction follows the day, not the line.** On the up day rejections were from above (support), on the down day from below (resistance); on the balance day the 20 was hit every ~14 min and "held" crosses led nowhere. The EMA sits where a with-drift pullback ends; it doesn't cause the bounce.
2. **Full stacks are the resting state** — 73% of RTH time (42% bull / 31% bear), median run ~31 min, p90 ~2h; median 11 ordering flips/day. The two "200-in-the-middle" orderings are rare (~5%, 7–8 min runs) and mechanically mark a rotation *in progress* — a narrator, not a signal.
3. **Stack formation is a null trigger.** ~2.6 bull + 2.1 bear formations/day, ~80% persist ≥15 min, but next-30m drift after formation (bull mean +0.3 pts, 52% pos; bear median +1.5, 53% pos *against* the stack) is at or below the any-window baseline (+5.5, 52%). By the time the third crossing confirms, the move is spent.
4. **Spacing does not identify future chop.** Span/ATR vs next-30m efficiency (|net|/range): **ρ −0.04**. What tight spacing predicts is *ordering flips* (ρ −0.42) — near-tautological label churn. Raw span vs forward range (ρ +0.40) is volatility clustering; ATR-normalizing kills it (−0.07). The "squeeze → breakout" read is the same confound, and span/ATR has a strong time-of-day shape (2.2 open → 3.9 midday → 2.7 close), so any threshold is partly a clock.
5. **The textbook trade fails its null.** With-stack pullback touches of the 20/50 (episode starts, touch bar excluded): next-30m with-stack drift +3.6/+3.4 pts, 54–55% pos — vs **+5.4, 54%** for random minutes in the same stack state on the same days. The touch adds nothing over just being in the stack; joins aVWAP-reclaim and LVN-retrace in the "trigger ≈ random with-drift entry" ledger.
6. **Freshness has no gradient.** 3–15 min since last contact +6.3 (55% pos), 15–45 +4.7 (53%), 45–120 +11.1 (n=102, 53% — tail-driven). A long-untouched EMA does not hold better.
7. **The one survivor: opening stack, bull side only — and it survives the ON-drift control.** Days already bull-stacked at 09:35: rest-of-day net 62% positive, median +56.5 pts (n=127) vs mixed-open 58%/+17 (n=102). Bear-open days are a coin flip *even with-stack* (49%, median −0.5, n=134) — the familiar day-with/long-side asymmetry. The obvious confound fails to explain it: ON net itself barely predicts the day (ρ +0.04 with rest-of-day net), yet bull-open beats not-bull *within every ON tercile* (med +22.5 vs +4.6 ON-down / +93.8 vs +13.2 ON-flat / +13.5 vs +5.5 ON-up), and within bull days ON magnitude adds nothing (ρ −0.04). The stack encodes the overnight's *shape* (its recent trend), not its net. Split-half stable: bull vs not-bull holds in both halves (H1 +34.8 vs +13.0; H2 +59.4 vs −1.5), and the ON-flat+bull sweet cell repeats (med +88.0 n=17 / +59.0 n=23, 65% pos both). Still post-hoc and day-level — a context lean to re-cut on current strategy baselines, not a knob.

8. **Reclaim events (follow-up, all 363 days): NULL, individually and jointly.** Genuine reclaims (≥45 of prior 60 min below the line, then a close above): the 20 and 50 are churn (77–85% whip back below within 15 min, forward drift ≈ 0 or negative). The 200-reclaim and the anecdote's joint 50+200 single-push reclaim (n=586) look alive against a naive null (+60m med +6.0, 55.5% pos) but sit exactly at the any-minute baseline (med +6.5, 55.2%), and the distance-matched cut kills the recency story: near-above-200 minutes drift the same whether the cross was ≤10 min ago (+0.9, 51.8%) or >30 min ago (−0.2, 53.5%). Hold-confirmation (wait 10 bars) only pays away the move (200-held +30m −3.7). The 2025-11-21 +286 was tail, not type. Short mirror (losing both EMAs) is the familiar anti-signal — price bounces (43–44% follow-through) — the "don't short breakdowns" asymmetry, not an edge. **Methodological catch:** a per-day equal-weight sample of *conditional* state minutes (4 draws/day from e.g. "below the 200") manufactured ±10–18 pt phantom state drifts by oversampling rare-state days; the full 128k-minute population shows the true distance-to-200 gradient is ~±2 pts. Pool the population (or match on distance) before believing any state-conditioned drift.

**Verdict:** as a *signal source*, no gate, no A/B — it would walk into the 1-pass/12-fail scoreboard with a visibly drift-collinear feature the regime artifact already covers (ghost AUC .657). Legitimate uses: eyeball shorthand on the chart, the 200-sandwiched rotation tell, and (optionally) stack-state occupancy/flip-count as descriptive regime KPI columns à la BOS/CHoCH. The one exception ran its course in §4b/§4c: the opening-stack lean became the `open_stack_veto` knob on ema-pullback-long, whose A/B validated the filter but exposed the strategy as OOS-dead — knob kept (default off), nothing adopted.

---

## 1. Four days, episode-level

Contact episodes (within 2 pts, gaps <3 bars merged; reject = ≥10 pts away within 20 min):

| day | character | what the EMAs did |
|---|---|---|
| 2025-05-02 | +95 trend up | 50 rejected from above 15×, +35…+77 follow-through; 200 untouched until 14:49 (5.5h above). Textbook dynamic support — because the day was up. |
| 2025-09-23 | −170 trend down | Mirror: 200 = 5/5 rejections from below (−24…−55 next); 50 capped every rally incl. a 66-min hold above the 20 that died anyway. The 50 held the regime, the 20 was noise. |
| 2025-02-19 | +40 balance | 28 interactions with the 20 (one per ~14 min), 11 "held" crosses that led nowhere; mid-range 200 a coin flip (4 failed / 2 held crosses). |
| 2025-11-21 | +110 net, 669-pt range | Morning ran over all three EMAs both ways (±100-pt excursions). One clean event: 13:20–13:30 reclaim of 50+200 together, both held, +216/+286. |

First ~30 min lawless on every day; 20–66-min "hugs" during consolidation make touch-counting meaningless there.

## 2. Ordering states (80 days)

| state | occupancy | med run | reading |
|---|---|---|---|
| 20>50>200 | 42.0% | 31m (p90 126m) | regime |
| 200>50>20 | 31.0% | 32m (p90 112m) | regime |
| 50>20>200 / 200>20>50 | ~11% ea | 10m | pullback (20 dips through 50) |
| 20>200>50 / 50>200>20 | ~2.7% ea | 7–8m | rotation corridor (something crossing the 200) |

Regime flips must walk through the corridor states (adjacent swaps only); 2025-11-21 did the full bear→bull walk in 20 minutes, then rode +56/+94/+126-pt legs.

## 3. Formation, spacing, touch-vs-null, freshness — the numbers

- **Formation:** bull forms next-30m mean **+0.3** / 52% pos; bear forms mean −6.3 but median +1.5 / **53% pos against the stack**; baseline +5.5 / 52%. Whipsaw (<15 min unwind) ~20–21%.
- **Spacing (5,688 windows):** span/ATR → efficiency ρ −0.04 (buckets 0.45→0.42 flat); → flips ρ −0.42 (1.78→0.38); raw-pts span → range ρ +0.40 (vol clustering, dies at −0.07 ATR-normalized). P(top-quintile forward range) by span/ATR bucket: 24/20/23/18/14% — the "squeeze" is high current ATR wearing a costume. With-stack drift is 51.5–53% pos in *every* spacing quintile — spacing doesn't grade trend quality either.
- **Touch vs null (n=2,679 touches / 480 null draws):** EMA20 +3.57 (55%), EMA50 +3.40 (54%), null **+5.40 (54%)**. AM touches +1.4 (57%) vs PM +5.2 (53%) — inside noise.
- **Freshness:** no monotone story (see TL;DR #6).

## 4. Opening-stack cut (363 days)

| 09:35 state | n | net mean | net med | % pos | eff |
|---|---|---|---|---|---|
| bull | 127 | +36.8 | +56.5 | **62.2%** | 0.46 |
| mixed | 102 | −11.8 | +17.0 | 57.8% | 0.41 |
| bear | 134 | −1.5 | +0.5 | 50.7% | 0.43 |

Bear-open with-stack drift: mean +1.5 (i.e. *up*), 49% with-stack — worse than nothing at predicting down days.

**ON-drift control (`ema_oncontrol.py`):** the stack is collinear with ON net as expected — P(bull | ON-up tercile) = 0.59 vs 0.14 | ON-down — but ON net itself is a dead predictor (ρ +0.04 vs rest-of-day), so the lean is not ON drift restated:

| ON tercile | bull med (n) | not-bull med (n) |
|---|---|---|
| ON down | +22.5 (17) | +4.6 (104) |
| ON flat | **+93.8** (39) | +13.2 (82) |
| ON up | +13.5 (71) | +5.5 (50) |

Within bull-open days, ON magnitude adds nothing (ρ −0.04). Split-half: separation holds in both halves (H1 bull +34.8 vs +13.0; H2 +59.4 vs −1.5) and the ON-flat+bull cell repeats (+88.0 / +59.0 med, 65% pos both halves, n=17/23). Reading: the stack summarizes whether the *late overnight* trended — shape, not net — and a flat-net overnight that nonetheless built a bull stack (quiet grind up into the bell) is the strongest cell. Next step if pursued: re-cut per-day PnL of the current adopted runs by opening stack state (the weekly-VWAP lesson — current baseline first, knob later, if ever).

## 4b. Baseline re-cut: existing runs by opening stack state (`ema_baseline_cut.py`)

Per-day net split by the 09:35 state (127 bull / 102 mixed / 134 bear days):

| run | bull $/day (PF) | mixed $/day (PF) | bear $/day (PF) |
|---|---|---|---|
| upper-band v13 a348d176 | 578 (1.86) | 496 (2.83) | 198 (1.88) |
| upper-band v10 cdc07ca2 (adopted) | 467 (1.78) | 549 (3.43) | 69 (1.33) |
| profile-pullback v5 ecf94b1c | 43 (1.32) | 19 (1.19) | 23 (1.21) |
| drift-touch-fade v2 dfd0089d | 108 (inf, n=16) | 84 (inf) | 24 (1.20) |
| drift-fade entry-stop v2 952b84c0 | 124 (1.24) | 270 (1.55) | 143 (1.38) |
| **ema-pullback v1 bc875e6a** | **182 (1.91)** | 40 (1.36) | **−115 (0.68)** |

Reading: the lean does **not** justify gating the adopted strategies — every state cell is
net-positive for the upper-band family (bear days just earn less; a size tilt at most, and the
size-up A/B history says leave it). The exception is **ema-pullback-long**: bull-open +$23.1k
PF 1.91 vs bear-open **−$15.4k PF 0.68** — buying EMA pullbacks against a bear-stacked open is
the loss engine of the whole run. Mechanistically coherent (counter-regime longs) and consistent
with the day-level lean. → The one build this study recommends: a session-level open-stack veto
knob on ema-pullback-long (whole-day on/off, so no intraday re-arm chain perturbation), ship off,
engine A/B. Caveats: single v1 untuned run, window ends 2025-12, 3×6 cells of multiple
comparisons — the A/B is the arbiter.

## 4c. Engine A/B: `open_stack_veto` on ema-pullback-long (2026-07-28)

Knob built (v2): `open_stack_veto` = `off | bear | not_bull` on `EmaPullbackConfig` — the
1-minute 20/50/200 ordering read once at the close of the 09:35 bar; a vetoed day takes no
trades at all (whole-day stand-down, re-arm chains untouched, charts still render), with a
causality floor so nothing fills before the read bar closes. `off` takes the identical code
path to v1. Three arms on the bc875e6a config, full cached window 2025-02-03 → 2026-06-30 —
the 2026-01→06 segment is true OOS (v1 and this study only ever saw through 2025-12, and an
IS-only A/B would pass by construction since the veto came from that run's own split):

| arm | full net (PF, maxDD) | IS 2025 net (PF) | OOS 2026 net (PF) |
|---|---|---|---|
| off `451b729e` | −2,253 (0.98, −26.3k) | +11,698 (1.14) | −13,951 (0.78) |
| bear `663eabca` | +20,367 (1.25, −10.3k) | +27,137 (1.74) | −6,770 (0.85) |
| not_bull `c3685eb4` | **+23,004 (1.44, −6.4k)** | +23,097 (1.91) | **−93 (1.00)** |

(IS of the off arm = bc875e6a exactly, n=378.) Per-state $/day on the off arm: IS bull
+292 (PF 1.91) / mixed +58 (1.36) / bear −177 (0.68); OOS bull **−2 (1.00)** / mixed −209
(0.61) / bear −153 (0.66).

Two findings, pulling opposite directions:

1. **The lean replicates OOS.** Non-bull opens stay toxic out of sample (mixed −209/day,
   bear −153/day), and each veto tier removes OOS losses monotonically (−13.9k → −6.8k →
   −0.1k). `not_bull` beats `off` on every metric in both segments — net, PF, maxDD,
   expectancy. As a *filter*, this is the rare post-hoc lead that held up.
2. **The host strategy fails OOS.** The 2026 bull-open cohort decayed from +292/day to
   −2/day — the best gated arm is exactly break-even out of sample. A veto can only delete
   losses; it cannot make the surviving cohort pay, and in 2026 nothing in this strategy
   pays. (Strategy-specific decay, not a regime excuse: the upper-band family stayed
   net-positive through Jun 2026 on the same tape.)

**Verdict:** knob validated (`not_bull` is the configuration if this strategy ever runs),
strategy **not adoption-worthy** — ema-pullback-long stays experimental, knob ships default
`off`. First A/B where the gated arm beats baseline on everything yet nothing gets adopted:
the gate passed, the strategy underneath didn't.

## 5. Method notes

- Touch bar excluded from all outcome measurement (weekly-VWAP-context lesson); entries scored from the close of the bar after contact.
- Episode merging (gap <3 bars) matters: raw per-bar events triple-count every hug.
- All thresholds arbitrary (2-pt contact, 10-pt reject, 8-bar hold, 30-min horizon); nothing here was tuned, nothing survived to deserve tuning.
