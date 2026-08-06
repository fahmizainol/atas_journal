# Globex Bounce (invert-on long) — where the P&L actually lives, and the vwap_slope@09:45 gate

- **Date:** 2026-07-29
- **Research question:** Every full-range config of `vwap-globex-bounce` (all invert-on: buy the pullback into the Globex *lower* dev1, revert toward the mid) is net-negative. Is there structure in *when* it loses — regime, hour, band width — and is any of it knowable early enough to act on?
- **Data:** the three full-range v14 runs (2024-03 → 2026-06, 607 sessions), chiefly the flattest chassis `20240303-20260630-v14-1f435cba` (variant A, stop 150, RR 4, trail 75 step 75 BE 4, min band width 50, daily loss stop 1000: 1,762 trades, net −$5,862, PF 0.986, maxDD −$34.1k), its regime artifact and edges cuts, plus two new engine A/B arms.
- **Files:** `data/research/globex-bounce-regime/` — `run_ab.py` (gate ladder), `run_extra.py` (invert-off full-range arms), `eval_ab.py` (A/B + ghost-cohort eval).

---

## TL;DR

1. **The whole loss is one day-class.** On the baseline, trend-down days net **−$95.5k over 116 days**; the other four classes net **+$89.6k combined** (trend-up +$38.5k, mixed +$35.0k, balance +$19.9k, parked −$3.7k). Split-half stable (trend-down −$49.8k / −$45.7k) and stable per-class economics across years — the 2024-positive / 2025-negative year swing is mostly regime *mix* (22% down-days in 2024 vs 28% in 2025–26), not behavior change.
2. **The bleed is knowable at 09:45 but not at 10:30.** In the regime boards only five KPIs hold at 09:45 (`ny_vwap_slope_deg/ppm`, `abr`, `longest_hold_min`, `net_conviction`). Post-hoc, a post-checkpoint veto off `ny_vwap_slope@09:45` recovers ~$25k; **every 10:30 read fails post-hoc** (dropped trades net ≈ 0 or positive) because the morning damage is already booked by 10:30. `bbr` — the existing `regime` gate's KPI — also fails at both checkpoints on this strategy.
3. **Engine A/B (new runs, gate added to the strategy):** `vwap_slope` 09:45, slope_min −1.0 → **net −$5.9k → +$14.8k, PF 1.054, maxDD −$34.1k → −$20.0k, Sharpe −0.13 → +0.42, both halves positive (+$9.2k/+$5.6k), vetoed ghost cohort −$51.5k** (genuinely bad trades, not the gx_overhang mirage signature). Engine results match the post-hoc re-cut *exactly* (pure stand-down, no re-arm interaction), so the whole post-hoc threshold ladder is engine-accurate. The untuned default (slope_min 0.0) is too strict: +$2.7k and H1 flips negative.
4. **Honesty clause — why this is not an adoption.** slope_min −1.0 sits mid-plateau (−2 … −0.25 all positive) but was picked on the full range; the A/B is confirmatory of mechanics, not out-of-sample. The monthly sign test is 15 better / 13 worse (p≈0.85) — the gate's value is a few big saved months (2025-01 +$6.5k, 2025-09 +$6.5k, 2026-06 +$6.0k), i.e. tail-bleed insurance, not a steady edge. And the ceiling is low: even gated, ~$6.4k/yr at PF 1.05 on one contract. The gate turns a money-loser into roughly break-even-plus; it does not make this a strategy worth funding.

## The cuts that mattered (baseline 1f435cba)

| cut | result |
|---|---|
| by class | trend_down **−$95.5k** (116d, −$823/day); all other classes positive except parked (−$3.7k) |
| by year | 2024 +$10.9k / 2025 −$13.8k / 2026 −$3.0k — tracks down-day share (22% → 28%) |
| by hour | 15:00–16:00 +$15.6k (positive all 3 years, and the only hour positive *on* down days); 12:00–14:00 −$20.6k. Mostly regime in disguise: on non-down days the morning is strongly positive too. Doesn't clear the edges luck bar on its own. |
| by hold time | <5 m −$132.7k (827 trades, the stop-outs); every bucket ≥5 m positive. Path-dependent — not actionable at entry. |
| exit reason | stops −$429.9k / trail +$377.0k — the familiar wide-trail shape: the trail is load-bearing. |
| behavioral | 97.6% of losers were green at some point but 81.7% died from <0.5R peaks; trades underwater >1 m win ~42–48% vs 82% for <1 m — drift deaths, not capitulation (same profile as the upper-band loser study). |

## Gate mechanics note

`vwap_slope` was added to the strategy's allowed confluences (registry-only change — gates don't ride the base rule path, so no version bump and existing runs stay trusted). The gate reads `ny_vwap_slope_ppm` at the 09:45 checkpoint and stands the session down below `slope_min`; entries before 09:45 pass untouched (09:31–09:45 entries are net **+$19.9k** on the baseline — the pre-checkpoint book is fine).

Post-hoc = engine exactly (n and net identical on both arms): this gate is a pure entry veto from a fixed wall-clock minute, vetoed days are stood down entirely, so no daily-loss/re-arm path divergence exists. Future threshold variants of *this* gate on *this* strategy can be trusted from a re-cut without new runs.

## Invert-off (the registered default, never run full-range before)

Two new full-range arms on the same chassis (`156d02a2` plain, `44dacfbf` gated):

- **Plain: net +$179 on 1,657 trades** — dead flat, but not featureless: it is a **pure trend-up harvester**. By day class (regime cache, full coverage): trend_up **+$111.6k** on 793 trades; balance −$43.9k, mixed −$26.4k, trend_down −$31.7k, parked −$9.5k. The two flavors are complementary regime bets — invert-on profits everywhere *except* trend-down, invert-off profits *only* on trend-up.
- **Gated (vwap_slope 09:45 ≥ 0): FAILS.** Net +$2.0k, halves flip (+$14.7k / −$12.7k), months 13/28, and the vetoed ghost cohort nets **+$20.7k** — the gx_overhang mirage signature. The gate also discards $28k of the trend_up habitat while keeping −$75k of other-class bleed.

The asymmetry is the lesson: a weak 09:45 morning predicts a bad day for the dip-buy (veto works), but a strong 09:45 morning does **not** predict a full trend-up day (selection fails). Early weakness is informative; early strength isn't. Don't try to rescue invert-off with earlier/looser slope reads — its habitat isn't knowable at 09:45.

## Verdict

- The invert-on long is a **"not-a-down-day" bet** with stable class-conditional expectancy; its raw edge (~+$5/trade gross) is smaller than round-trip costs ($14).
- `vwap_slope` 09:45 (−1.0) is real as a *bleed limiter* — ghost cohort −$51.5k, DD nearly halved, both halves positive — and ships as a knob worth leaving **on for any future run of this family**, but the family itself remains experimental: PF 1.05 is not fundable, and the gate's monthly consistency is luck-level.
- The residual −$56.6k of trend-down bleed in the gated arm belongs to down days that develop *after* 09:45; 10:30 reads can't catch them (damage front-loaded). If this family is ever revisited, the open question is an intraday stand-down (e.g. session-loss-count halt) rather than a later checkpoint.
- Invert-off is a **regime-complementary null**: flat overall, all P&L on trend-up days, habitat not selectable at 09:45 (gate A/B failed with net-positive ghosts). Don't build on it.

## Addendum (2026-07-30): Globex-VWAP state at entry + MAE/MFE — gated arm 74e6af45

Follow-up question on the adopted-knob arm (`20240303-20260630-v14-74e6af45`, 1,158 trades, net +$14.8k): does the *Globex-anchored* VWAP's own state at the moment of fill (slope, occupancy, stretch, channel width) sort winners from losers? Features were reconstructed causally from `ticks[:entry_idx+1]` with the engine's own `vwap_bands` (entry_idx verified = the lower-1 fill tick; occupancy is time-weighted). Script: `data/research/globex-bounce-regime/extract_gxvwap.py`.

**Answer: the local VWAP state is NULL; the one robust structure is trade *sequence* — the day's first fill carries all the P&L.**

### VWAP-state features: flat

Every feature vs R / win / heat / MFE has |Spearman ρ| ≤ 0.06 (n=1,158): slope over 15/30/60 m and since the open (raw and σ-normalized), time below the mid over 15/60 m and since open, time stretched below lower-1, σ width, ON-range position, mid-vs-RTH-open. Stops and non-stops have near-identical medians on all of them — losers are not "steeper-slope" or "deeper-occupancy" entries. Quartile humps exist (mild-negative σ-slope Q3 +$21.5k, σ 52–79 pt Q3 +$22.4k, 17/26 months) but they're interior-bin solitary peaks with flat neighbors — the gate-robustness scorecard's luck signature — and their trade overlap is low (28 trades share all three cells), so they aren't one coherent regime. The stretch-freshness U (never-below-lower-1-in-60 m = +$19.2k) decomposes entirely into the sequence effect below (fresh-but-not-first = −$2.2k) and goes 14/28 monthly, permutation p=0.06. Nothing here to build on — consistent with the family verdict that this trade's fate is the day class, not the entry-moment geometry.

### The one live cut: first trade of the day

| rank in day | n | net | avg R | stop % |
|---|---|---|---|---|
| 1st | 379 | **+$27.6k** | +0.12 | 29% |
| 2nd | 257 | −$9.6k | −0.03 | 30% |
| 3rd | 179 | +$0.9k | +0.03 | 28% |
| 4th+ | 343 | −$4.1k | 0.00 | 33% |

Split-half stable (+0.087 / +0.146 avg R; later-trades negative in both halves), permutation p=0.017, and it survives the time-of-day control (within the 09h entry hour alone: first +0.132 vs later −0.046; same sign at 10h/11h — so it's not "early is good", it's *first* is good). Monthly sign test is only 16/28 (median diff +0.177) — tail-flavored, in line with this family's insurance-not-edge character. Reading: the first pullback-to-lower-1 of the session is the informative one; once the day has already bounced (or stopped) there, re-fills at the same band are dead money. Rhymes with the upper-band family's `reenter_after_stop_only` finding, but here the after-STOP cohort isn't separable at this n without a dedicated cut. If this family is ever revisited, a `first_entry_only`-style A/B (or the existing daily re-arm knobs re-cut post-hoc first) is the queue item — expected effect: drop ~779 trades netting −$12.8k and ~$11k of commission.

### MAE/MFE shape (stop 37.5 pt, trail 75 t / BE 4 t)

- **Binary outcomes, no middle ground.** Loser median heat = 37.6 pt (the full stop); winner median heat = 8.9 pt, p90 = 18.9. Heat quartiles: worst-heat quartile is 0% win / −1.01R (all stops), while the two lowest-heat quartiles run 93–95% win. Trades either work almost immediately or ride to the full stop.
- **Stops die without ever threatening the trail.** Stopped trades' median MFE = 7.5 pt; only 2% ever reached the 18.75-pt trail-arm distance. No "almost worked" cohort — a tighter trail can't rescue them, and (with winners' p90 heat at 19 pt vs the 37.5-pt stop) a meaningfully tighter *stop* would start clipping winners well before it saved much: heat-vs-R ρ is fully explained by the win/stop split.
- **The trail gives back most of the excursion.** Trail exits (765 = 66% of trades): median MFE 36.7 pt but median captured only ~1 pt — median giveback 25.2 pt, median capture ratio 0.05. The BE-4 ratchet converts early MFE into scratches; combined with $14 round-trip, this is why 64% win rate nets PF 1.05. Same wide-trail-is-load-bearing shape as the triple-barrier study — the giveback is the price of the occasional +150-pt runner, not a fixable leak.

**Verdict:** no VWAP-state gate is warranted (family stays experimental per the main verdict). The only structure worth a future engine A/B is first-fill-only. MAE/MFE confirms the exit stack is already shaped correctly for the payoff profile; don't tighten either leg.

### Behavioral edges panel (same run)

Same behavioral fingerprint as the baseline and the upper-band family, sharper: **everything is decided in seconds at the band.** 98% of losers were green at some point but 83% peaked below 0.5R and died (−$262.9k of the gross loss); 100% of winners took heat but median recovery from max heat is **5.8 s** (75% < 30 s), and losers collapse through their peak just as fast (median 5.3 s, 82% < 30 s). Entry discriminators are dead (band width AUC 0.47, time-at-band 0.53, neither clears luck). Daily concentration is extreme: top 3 days = **93% of net**.

The underwater-survival panel looks like a lead (< 1 m dwell = 84% win / +$142.7k; every ≥ 1 m bucket net-negative, −$128.0k combined, split-half stable) but it is a **stop tautology**: a stopped trade's underwater dwell ≈ its duration. Excluding stops, trades that dwelt ≥ 1 m underwater still win **91% / +$101.4k** (avg R +0.486 vs +0.498 for fast resolvers). An underwater-timeout knob would forfeit that $101k to pre-empt stops that mostly ride to the full −1R anyway — same verdict as the upper-band panic-exit/underwater A/B failures; don't build it. The first-fill edge is *not* "first fills resolve faster": first beats later within both dwell buckets (+0.372 vs +0.342 fast; −0.196 vs −0.303 slow) — a uniform shift, consistent with a context effect rather than a mechanics effect.
