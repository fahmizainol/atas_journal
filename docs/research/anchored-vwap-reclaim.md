# Candidate #4 deep-dive — Anchored-VWAP reclaim

- **Date:** 2026-07-20
- **What this is:** a deep-dive on candidate **#4** from `playbook-scouting-tradezella.md` — the 🟡 needs-engine "anchored-VWAP reclaim" idea (prior-day-low / first-swing anchor). Source distillation, the two mechanics that are genuinely new to our stack, worked NQ examples, and the causal null-controlled test that resolved it.
- **> VERDICT (2026-07-20): NULL. Do not build.** Across 239–360 NQ days and ~2,900 reclaims, the anchored-VWAP reclaim has **no forward edge that survives the right control**. It never beats "buy a random long on the same day" on a risk-adjusted basis (dR −0.09 pdl / −0.10 swing), and its edge over a *fake-reclaim* null **flips sign with the anchor choice** (−13t pdl vs +14t swing) — the signature of noise, not signal. What little positivity exists is day-drift (up days +0.13–0.22R, down days −0.17–0.22R, ≈ symmetric), which the random-long null already harvests better. See [§ Outcome](#outcome--resolved-null-2026-07-20).
- **⚠ Data caveat (2026-07-22):** the `pdl` arm was computed on a tick cache that omits the live **16:00–17:00 ET hour every weekday** (cache = `rth` 09:30–16:00 + `on` 18:00–09:30, nothing between), which skews any anchor carried across a session boundary. Reproduced against ATAS on 2025-03-31's anchor: at Sun 19:42 / 19:59 price reads +16–21 pts *inside* our lower-dev2, where ATAS (full data) shows a dev2 tag-and-reject. **Verdict unchanged** — the `swing` arm is intraday-anchored (gap-free) and equally null, and the decisive rand-null control is VWAP-independent. **Fix applied 2026-07-22:** the hour is now a cached `post` segment (16:00→18:00 ET) spliced into the pdl frame, and the corrected re-run (237 days, 945 reclaims) gives dR vs the rand-null **−0.099** (was −0.090) — still negative, still split-half consistent, day-drift still the only structure. The §Outcome table below is the pre-fix run; the corrected deltas are within noise.
- **Sources:** TrendSpider, TradingSim (anchored-VWAP guides), OrderFlowLabs (VWAP reclaim), plus futures-specific AVWAP writeups. Instrument-agnostic; the one worked reclaim example in the primary sources is crypto — **no futures validation upstream.** Links at the bottom.

---

## Core idea in one sentence

Anchor a VWAP at a *chosen structural point* (the prior-day low, the session's first swing low) instead of the bell; when price **loses** that line and then **reclaims it from below**, go with the reclaim as a day-with continuation trigger, stop under the swing, target ~1.5R then trail.

## The two mechanics that are new to our stack

Everything else here we already own; these two we don't:

1. **Event-anchored VWAP.** Our three anchors — NY bell, Globex 18:00, weekly — are all *time*-anchored (`src/journal/sim/vwap.py`, anchored by slicing the tick frame). None is anchored at a *price event*. The prior-day-low anchor is "the average buyer's price since the low"; holding above it = strength, losing it = fading.
2. **The lose-then-reclaim trigger.** Every entry we model is an acceptance-pullback (ride a band) or a breakout. None is *lose it → hold below → reclaim from below*. That sequence is the whole novelty.

## What each source contributes

| Source | Its job in the setup | Key rules |
|---|---|---|
| **TradingSim / TrendSpider — anchored VWAP** | Anchor selection | Anchor to prior/all-time high or low, a **significant-volume day**, a news catalyst, or a high-volume consolidation; AVWAP flips support↔resistance on the cross ("what was resistance becomes support") |
| **trader-dale — top-3 AVWAP setups** | Structural anchors | Anchor to a **prominent swing point** "all traders recognise", to the **first candle of the week**, or to a **heavy-volume accumulation zone**; *"works best in trending markets… doesn't work in ranges"* |
| **OrderFlowLabs — VWAP reclaim** | The trigger definition | *"price loses VWAP and then reclaims it from below **with volume**… a shift in session bias, especially if it coincides with a value-area reclaim"*; **not a standalone signal** — needs order-flow confirmation |
| **snappchart — momentum reclaim** | The mechanical entry | Strong open → fade below → consolidate 15–30 min → **reclaim on a volume spike**; enter on the reclaim-bar close; stop = reclaim-bar low or a few ticks under VWAP, whichever wider; target HOD / measured move; *"a reclaim without a volume spike is a drift, not a reclaim"* |

**No-trade filters** (converging): VWAP slope flat/falling; major resistance directly overhead (no runway); **no volume spike**; and *"if the session opens already beyond the band, no trade."*

## The setup, step by step

1. **Anchor** a VWAP at the chosen structural point (here: prior RTH low, or the session's first confirmed swing low).
2. **Lose it** — price trades and *holds* below the anchored line.
3. **Reclaim it** — a bar closes back above the line from below (the sources add: on a volume spike).
4. **Enter** long on the reclaim-bar close.
5. **Stop** under the reclaim-bar low / a few ticks under the line.
6. **Target** ~1.5R, then trail under the +1σ band / next shelf.

## Worked NQ examples

Two real sessions, same rule, opposite outcomes — the reason a worked example proves nothing and the null-controlled test is mandatory. Rendered: `data/research/avwap-reclaim/avwap_reclaim_examples.html` (script `avwap_example.py`).

- **2025-03-31 (rule → continuation).** Trend day. Three reclaims of the prior-day-low aVWAP in the 11:00–12:15 window, each → clean continuation (+150–230t MFE, MAE ≤65t) into a +1,127t close. The shape the playbook sells.
- **2025-03-11 (chop → whipsaw).** Range day. The *same* trigger fires **ten** times, every one closes red, net −132t. The line is crossed back and forth all session; the reclaim carries no information. The failure the anecdote forgets.

The MVP's own scan foreshadowed the result: reclaims cluster on trend *and* chop days alike (e.g. 03-11 and 03-27 each threw ten reclaims for a net loss).

---

## The causal test

Two anchors, tested identically. Script: `data/research/avwap-reclaim/avwap_outcomes.py`.

**Anchors (both causal — no lookahead):**
- **`pdl`** — anchored at the **prior RTH session's low**, accumulated across the overnight into today's RTH via the sim's own tick-by-tick `vwap_bands`. The prior-day low is fully known at today's open. Same-contract only (roll-boundary days skip, so n_days = 239 < 360).
- **`swing`** — anchored at the session's **first confirmed swing low** (zigzag, 22-pt confirmation); reclaims counted only *after* the pivot is confirmed.

**Reclaim (real signal):** a 500-tick bar closing above the line by >2 ticks, after holding ≥3 bars below it (dead-band de-noise). Enter long at the reclaim close; stop = reclaim-bar low − 2t (floored at 10 pts); 2R:1R bracket, stop-first-within-bar (conservative), applied identically to real and null. MFE/MAE and bounded forward net (15 bars) reported bracket-free.

**Two nulls — because a single-window winner proves nothing (the LVN #3 lesson):**
- **CROSS null** — raw below→above crosses of the line that are *not* committed reclaims (brief pokes / "drifts"). Isolates whether the *loss-and-hold* commitment matters vs. any cross.
- **RAND null** — random same-session longs, matched count (3 per reclaim), same stop/target. Isolates session drift ("buy any long today"). The real signal must beat **both**, and be split-half stable, to be worth an engine A/B.

## Outcome — RESOLVED NULL (2026-07-20)

Full sample 2025-02-03 → 2026-06-30. `fwd15` = net ticks 15 bars forward; `dR` = real R_mean − null R_mean.

**Prior-day-low anchor (`pdl`), 239 days, 971 reclaims**

| group | n | fwd15_med | %fwd>0 | MFE_med | MAE_med | R_mean | hit2R |
|---|---|---|---|---|---|---|---|
| REAL reclaim | 971 | −1t | 0.49 | 351t | 346t | −0.021 | 0.32 |
| NULL raw cross | 837 | +4t | 0.51 | 352t | 264t | −0.021 | 0.32 |
| NULL random long | 2913 | +1t | 0.50 | 322t | 315t | **+0.069** | 0.35 |

- edge vs cross-null: **d_fwd15 −12.7t**, dR −0.000
- edge vs rand-null: **d_fwd15 −9.4t**, **dR −0.090**
- split-half: negative in **both** halves vs both nulls (H1 d_cross −15.1 / d_rand −12.7; H2 −10.4 / −5.9)
- day direction: up days R +0.128, down days −0.173 (pure drift)

**First-swing anchor (`swing`), 360 days, 1,951 reclaims**

| group | n | fwd15_med | %fwd>0 | MFE_med | MAE_med | R_mean | hit2R |
|---|---|---|---|---|---|---|---|
| REAL reclaim | 1951 | +6t | 0.52 | 361t | 357t | −0.002 | 0.33 |
| NULL raw cross | 1737 | −9t | 0.48 | 328t | 397t | −0.050 | 0.31 |
| NULL random long | 5853 | +1t | 0.50 | 292t | 300t | **+0.098** | 0.36 |

- edge vs cross-null: d_fwd15 +14.0t, dR +0.048
- edge vs rand-null: d_fwd15 +5.6t, **dR −0.100**
- split-half: positive both halves vs both nulls on fwd15 (H1 +4.4 / +1.1; H2 +23.5 / +9.9)
- day direction: up days R +0.223, down days −0.219 (pure drift)

### Why it's null

1. **It never beats the random-long null on risk (R).** Both anchors: dR −0.090 (pdl), −0.100 (swing). A random long on the *same selected days* does **better** risk-adjusted than a reclaim-timed long — because it isn't pinned to the line and catches more of the trend. The reclaim's job was to time the entry; it times it *worse* than a coin flip on those days.
2. **The cross-null edge flips sign with the anchor.** pdl −12.7t vs swing +14.0t. Whether "the hold/commitment matters" depends entirely on which line you anchor to — a real mechanism wouldn't reverse. The swing-positive cell is small and R-marginal (+0.048) and doesn't carry to the metric that decides trades.
3. **The only robust structure is day-drift.** Up days positive, down days negative, near-symmetric → net ≈ 0. The reclaim is a **long that fires more often when the day happens to be up** — no more, no less. Both nulls already price that in; the rand-null prices it in *better*.
4. **Confirms the standing priors by direct test.** VWAP-geometry-as-location joins VP-geometry (POC/VAH/VAL) with no edge. And the confirmations we deliberately dropped — the *volume-spike* reclaim and the *value-area* coincidence — lean on order-flow / VP signals already dead on our tape, so they can't rescue a price-only signal that's already ≤ null. Same arc as candidate #3.

### What would have changed the verdict (and didn't)

- A reclaim edge over the **rand-null on R** in *either* anchor → none.
- A **consistent-sign** cross-null edge across both anchors → the sign flipped.
- Split-half instability that hid a real edge in one window → no; both anchors are split-half *consistent* (pdl consistently negative, swing consistently ~flat-positive-but-drift).

The reclaim is a real, common, eye-catching *pattern* with no forward *information* beyond the day's own drift. Do not build the anchor or the reclaim trigger.

---

## Assets

- `data/research/avwap-reclaim/avwap_example.py` — MVP: prior-day-low anchored VWAP + reclaim/loss markers, single-day / `combined` / `scan` modes; writes self-contained SVG HTML.
- `data/research/avwap-reclaim/avwap_outcomes.py` — the outcomes study: two anchors (`pdl`/`swing`), committed-reclaim detector, two matched nulls, split-half; writes `avwap_outcomes_{anchor}.parquet`.
- `data/research/avwap-reclaim/avwap_reclaim_examples.html` — the two worked examples (rule + failure).

## Sources

- Anchored VWAP strategies (TrendSpider) — https://trendspider.com/learning-center/anchored-vwap-trading-strategies/
- Anchored VWAP for day trading (TradingSim) — https://www.tradingsim.com/blog/anchored-vwap-strategies
- What is VWAP / reclaim (OrderFlowLabs) — https://orderflowlabs.com/blogs/theblog/what-is-vwap
- Top 3 Anchored VWAP setups (Trader-Dale) — https://www.trader-dale.com/top-3-anchored-vwap-trading-setups-9th-oct-25/
- VWAP momentum reclaim setups (snappchart) — https://www.snappchart.app/blog/strategy-playbooks/vwap-momentum-trading-strategy
- Anchored VWAP for futures (JustinTrading) — https://justintrading.com/anchored-vwap-futures/
