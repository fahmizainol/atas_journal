# Playbook scouting — TradeZella & external sources (order flow · volume profile · VWAP)

- **Date:** 2026-07-20
- **Research question:** Pull external, documented playbooks (TradeZella's featured strategies + reputable order-flow / volume-profile / VWAP guides) and pitch the ones that (a) fit our NQ intraday, **day-with-only** style and (b) could be auto-backtested — mapping each to an existing sim family or flagging the engine work needed. Capture with sources; decide what to build later.
- **Method:** web pull of TradeZella's `/strategies` featured playbooks + `/blog` scalping guide, cross-referenced against our own study graveyard (see memory index). No code run; this is a candidate list, not a result.

## How to read this — verdict legend

- 🟢 **BUILD-NOW** — expressible as a JSON `config.json` against an existing `config_cls`; auto-backtestable with **no engine change**.
- 🟡 **NEEDS-ENGINE** — fits the style but needs a new detector / anchor / entry trigger the engine doesn't have yet.
- 🔴 **GRAVEYARD** — overlaps a setup our own research already tested to ~null/dead. Reported so we don't re-pitch it as fresh.

The honest headline: **TradeZella's catalog confirms our instincts more than it expands them.** The fade / mean-reversion / VP-as-S/R / absorption half of every playbook is stuff we've already killed. Two ideas map onto existing families as pure configs, and three genuinely-new-to-us ideas (LVN continuation, anchored-VWAP reclaim, trap-reclaim of a session ref) are day-with and worth engine work.

**Market provenance (added 2026-07-20).** Every candidate is now tagged with the market its source was written for — legend: 📈 stocks · ⚙️ futures · 🪙 crypto · 🌐 multi. The split is decisive. Both 🟢 "build-now" ideas (#1, #2) come from TradeZella's **stock** scalping blog (5%+ gaps, relative volume, $0.10 stops) — different microstructure, and the likeliest reason candidate #1's momentum thesis *inverted* on NQ. The best-fit sources are **explicitly NQ/ES** — Fabio's Auction/LVN, Yush's order flow, Crudele — and those are exactly the 🟡 ideas (#3, #5, #7) already flagged as most novel. **Caveat the other way:** several graveyard items (G2 order flow, G3 VP-geometry, G4 LVN-fade, G6 Crudele) are *also* NQ/ES-native, so market is **not** their excuse — they failed on our own NQ data and the verdict stands. Net: prioritize the NQ/ES-native playbooks; treat the stock-scalping configs as translations, not templates.

---

## TL;DR — ranked candidates

| # | Candidate | Market (source) | Maps to | Day-with? | Verdict |
|---|---|---|---|---|---|
| 1 | **Momentum-gated EMA micro-pullback** | 📈 Stocks | `ema-pullback-long` + `regime`/`vwap_slope` | ✅ | 🟢 build-now |
| 2 | **Trend/volume-confirmed ORB + measured-move target** | 📈 Stocks | `orb-breakout` + `vwap_slope`/`regime` | ✅ | 🟢 build-now |
| 3 | **LVN-retrace continuation + big-lot aggression** ([deep-dive](lvn-retrace-continuation.md)) | ⚙️ Futures — **NQ/ES** | new location (LVN) on `profile`-style base | ✅ | ~~🟡 needs-engine~~ → 🔴 **TESTED NULL** (2026-07-20) |
| 4 | **Anchored-VWAP reclaim** (prior-day-low / first-swing anchor) ([deep-dive](anchored-vwap-reclaim.md)) | 🌐 Multi (example crypto) | new VWAP anchor + reclaim entry | ✅ | ~~🟡 needs-engine~~ → 🔴 **TESTED NULL** (2026-07-20) |
| 5 | **Trap-reclaim of a session ref** (sweep ONH/ONL → reclaim → go with) | ⚙️ Futures NQ/ES + 🌐 multi | partly `value-rotation`; session-ref version new | ✅ | 🟡 needs-engine |
| 6 | **Prior-balance-POC magnet target** (target refinement) | ⚙️ Futures | `target_mode` add (prior-day POC) | n/a | ~~🟡 needs-engine~~ → 🔴 **NO MAGNET** (2026-07-20, geometry pre-check) |
| 7 | Big-lot threshold calibration (NQ 75 / ES 200 lots) | ⚙️ Futures — **NQ/ES** | our big-lot participation signal | n/a | 🟢 note — cross-check only |
| — | VWAP deviation reversion scalp | 📈 Stocks | our `vwap-dev1-fade` family | ❌ | 🔴 graveyard |
| — | CVD/delta divergence · absorption · exhaustion (as entry) | ⚙️ Futures / general | order-flow signals | — | 🔴 graveyard |
| — | VP-geometry S/R (VAH/VAL/POC bounce, volume-shelf, D-fade, naked POC) | ⚙️ Futures (NQ/ES ex.) | profile levels | ❌ | 🔴 graveyard |
| — | LVN-rejection **fade** (mean-reversion model) | ⚙️ Futures NQ/ES | counter-trend + absorption | ❌ | 🔴 graveyard |
| — | Liquidity sweep of **static** session refs | 🌐 Multi (fut/fx/crypto) | `drift-touch-fade` + `use_session_refs` | ❌ | 🔴 graveyard |
| — | Crudele BB environment-first (swing 1–5d) | ⚙️ Futures (index) | validates `regime` gate | — | 🔴 doesn't fit session model |

---

## 🟢 Build-now candidates (config against an existing family)

### 1. Momentum-gated EMA micro-pullback

- **Market (source):** 📈 **Stocks** — TZ scalping blog, framed as up 5%+ with 3× relative volume and $0.10–0.20 stops. Stock microstructure; the most likely reason the momentum thesis *inverted* when applied to NQ (see outcome below).
- **Source:** TradeZella scalping guide, "Micro Pullback on Strong Momentum" — after a strong initiative move (their stock version: up 5%+, 3× rel-vol, ≥$1 from open), buy the pullback onto the 5-min VWAP / 20-MA (1-min), enter on the reversal-candle-high break, stop below the pullback low, **exit if it gives back >50% of the move**, target prior high of the run. Session 09:30–11:30.
- **Maps to:** `ema-pullback-long` (pullback onto 1-min 9/20 EMA in the upper channel) — we just built this and memory flags it **untuned**. The only missing piece is TZ's *precondition*: a strong initiative move **before** the pullback. Encode that context with gates we already own — `regime` + `vwap_slope` ON — plus the morning window (`entry_open 09:30`, `entry_close 11:30`) and the give-back exit via `invalidate_*`/`exit_return_to_source_ticks`.
- **Prior:** pure day-with; plugs the "untuned" gap on a strategy we own.
- **Correction (build note):** *not* config-only as first pitched — `run_session_ema_pullback` never calls `build_gates`, and `ema-pullback-long` has an empty gate whitelist, so `regime`/`vwap_slope` would need engine wiring. Per the weekly-VWAP lesson (re-cut a lead on the baseline via config before building a knob), the momentum thesis was tested with existing knobs (band region + time window) first.

- **OUTCOME — 2026-07-20 · thesis REFUTED, inverse lead surfaced.** Baseline (73dbb43a, full-day `above_dev1`) is a net **loser**: −$6,353, PF 0.96, exp −$10.6/trade, Sharpe −0.34, 602 trades. Config-only A/B on the current baseline, cached range 2025-02→2025-12:

  | Window | n | net | exp/trade | PF | Sharpe |
  |---|---|---|---|---|---|
  | Morning 09:45–11:30 (`d25c38a0`) | 286 | **−$16,889** | −$59.1 | 0.78 | −1.70 |
  | Afternoon 11:30–16:00 (`bc875e6a`) | 378 | **+$11,698** | +$30.9 | 1.14 | 0.71 |
  | Full-day baseline (`73dbb43a`) | 602 | −$6,353 | −$10.6 | 0.96 | −0.34 |
  | Morning + `above_dev2` (`f7d494df`) | 1 | +$756 | — | — | n=1, phantom setup |

  TradeZella's **morning momentum window is exactly where the EMA pullback bleeds worst** (−$59/trade vs −$10.6 full-day) — the thesis is not just unsupported, it's inverted. The `above_dev2` "ride the far band" momentum-context is a phantom (1 touch all year). **But the inverse cut is a real lead:** afternoon-only turns the full-day loser into +$11.7k / PF 1.14 / Sharpe 0.71 — the **same afternoon-only signature as `drift-touch-fade`** (memory: "afternoon-only is the edge, mornings lose in every config"). Two independent strategies pointing at the same NQ time-of-day structure.
- **Next:** the afternoon-only EMA pullback is a config-only lead worth a **split-half + OOS** check before anything is adopted (post-hoc time-of-day re-cut — the class of lead that has reversed OOS before). Do **not** wire the `regime`/`vwap_slope` gate: the config cut already shows the losses live in the morning, so a momentum/regime veto can at best trim toward the still-negative full-day line, not manufacture the afternoon edge. maxDD −$10.8k on +$11.7k net (recovery ~1.1) is thin — treat as a lead, not an edge.

### 2. Trend/volume-confirmed ORB + measured-move target

- **Market (source):** 📈 **Stocks** — TZ scalping blog (running-stock framing, $-based stops). The ORB *mechanic* is instrument-agnostic, but the ≥1.5× volume + broader-market-alignment thresholds are stock-calibrated and would need re-tuning for NQ.
- **Source:** TradeZella scalping guide, ORB scalp — first-15-min high/low; enter on a **candle close** beyond the range with **breakout volume ≥ 1.5× the range's avg** and **broader-market alignment**; stop below the mid of the range; **target = 1× the opening range projected** (measured move); half off at 1:1, trail the rest. Session 09:45–10:30.
- **Maps to:** `orb-breakout` (`entry_mode`, `stop_mode:range`). Memory's IB/ORB study: stop-enforced 5m ORB **survives (+0.28R/trade, 9/12 months)** but *"needs an entry-time trend proxy nobody has built."* TZ hands us exactly that — the ≥1.5× volume + market-alignment filter. Use `vwap_slope`/`regime` as the trend proxy; set target to the measured move (`target_mode:ticks` sized to range, or `target_mode:rr`).
- **Prior:** directly answers a known open question in our own ORB study, rather than a fresh guess.
- **Next:** write config on the 5m ORB with `stop_mode:range` + trend gate + measured target; A/B the target style (measured vs `rr`) and the volume/trend gate.

---

## 🟡 Needs-engine candidates (fit the style, new machinery required)

### 3. LVN-retrace continuation + big-lot aggression  — ~~*most novel to our stack*~~ → **TESTED NULL**

- **🔴 RESOLVED NULL (2026-07-20):** built a causal, leg-anchored LVN-retrace detector and tested it over 281 days (832 retraces) vs a matched random-pullback null. No forward edge (real −0.09R vs null, split-half-stable, not significant); the big-lot trigger makes it *worse*. VP-geometry + order-flow priors confirmed by direct test. **Do not build.** Full method + numbers in the deep-dive.
- **📄 Deep-dive:** [`lvn-retrace-continuation.md`](lvn-retrace-continuation.md) — full auction logic, worked NQ examples, *which profile the LVN is measured on* (answer: the impulse-leg sub-window, **not** our session/globex profile), the lookahead trap, and the [§ Outcome](lvn-retrace-continuation.md#outcome--resolved-null-2026-07-20).
- **Market (source):** ⚙️ **Futures — explicitly NQ/ES.** Fabio Auction is a "futures scalping strategy… on NASDAQ and ES"; Carmine is "futures & index instruments with deep liquidity"; Yush is "**NQ and ES futures only**". The strongest native-market fit in the whole list.
- **Sources:** Auction Market Theory + LVN (Fabio) — *Trend Model*: apply volume profile to the **impulse leg that broke structure**, mark the **Low-Volume Nodes**, set alerts; on the retrace into the LVN, enter **with the trend only when order flow shows aggression** (big buy/sell prints, footprint imbalance) — *"no aggression = no trade"*; target the **prior-balance POC** (full exit; ~70% reverse from balance). Carmine LVN — LVN as a *zone*, first 2–3 hrs, few high-quality setups. Yush order flow — valid AOI needs **≥2 of 4 confirmations**; **big-trade threshold NQ 75+ lots / ES 200+**.
- **Why it's interesting:** it's the rare TZ idea that's (a) **day-with** (trend-continuation retrace), (b) uses **our one live entry-time signal** — big-lot participation (memory: AUC 0.66, split-half + within-session robust) — as the trigger, and (c) trades a **new location** we don't have: the LVN (low-volume gap in the developing profile), as opposed to our POC/VAH/VAL. Our "VP-geometry-has-no-edge" verdict was measured on POC/VAH/VAL as S/R — **LVN-as-continuation-launchpad has never been tested here.**
- **Engine work:** LVN detection on the developing/impulse-leg profile (new reference type), plus wiring the existing big-lot signal as an entry trigger rather than a research-only feature. Base could clone `ProfilePullbackConfig`.
- **Caution:** size-up on the big-lot signal already **failed** its A/B (memory: bigtrade study, 7th confirmation reversed OOS). This pitch uses big-lot as an *entry filter at a new location*, not as a size knob — a different question, but the low prior on order-flow-as-alpha stands.

### 4. Anchored-VWAP reclaim (prior-day-low / first-swing anchor) — ~~*highest infra reuse*~~ → **TESTED NULL**

- **🔴 RESOLVED NULL (2026-07-20):** built both anchors (prior-day-low + first-swing) causally and tested ~2,900 reclaims over 239–360 days vs two matched nulls (a fake-reclaim cross-null and a random-long drift-null). No forward edge that survives: never beats the random-long null on R (dR −0.09 pdl / −0.10 swing), and the cross-null edge **flips sign with the anchor** (−13t pdl / +14t swing). All that's left is day-drift (up days win, down days lose, ≈ symmetric). VWAP-geometry joins VP-geometry with no edge. **Do not build.** Full method + numbers in the [deep-dive](anchored-vwap-reclaim.md#outcome--resolved-null-2026-07-20).
- **Market (source):** 🌐 **Multi (stocks/futures/crypto).** AVWAP guides are instrument-agnostic; the one worked reclaim example is **crypto (BTC)**. No futures-specific validation in the source — the technique transfers, the parameters don't.
- **Sources:** TrendSpider / TradingSim AWAP guides + OrderFlowLabs — anchor VWAP to a **chosen point** (prior-day low/high, session's first swing, breakout bar) rather than the bell; **reclaim setup**: price loses the anchored VWAP then **reclaims it from below on volume**, enter on the reclaim-bar close, stop under the swing low, target ~1.5R then trail under a +1σ band / next shelf. "If the session opens already beyond the band, no trade."
- **Maps to:** we have three VWAP anchors (NY bell, Globex 18:00, weekly) but **no event/swing-anchored VWAP** and **no reclaim entry** (our entries are acceptance-pullback or breakout, not lose-then-reclaim). This is a genuinely new dynamic-support line + a new day-with trigger.
- **Engine work:** arbitrary-anchor VWAP (reuse the VWAP machinery, new anchor selection) + a reclaim-from-below entry variant. Highest reuse of existing infra of the three 🟡s.

### 5. Trap-reclaim of a session ref (sweep → reclaim → go with the reversal)

- **Market (source):** ⚙️/🌐 **Futures NQ/ES (Yush) + multi (Liquidity Playbook: futures/forex/crypto).** The trap-reclaim confirmation half is NQ/ES-native; the liquidity-sweep half is cross-market and price-action-only.
- **Sources:** Yush *confirmation entry* — "price breaks the range, buyers chase, price returns inside (trapping breakout traders)" → trade the reversal; Liquidity Playbook — sweep a respected high/low, wait for rejection/reclaim, enter after liquidity is taken, target the opposite pool.
- **Maps to:** the **VA-edge version already exists** as `value-rotation` (`arm_beyond_ticks` + `accept_inside_bars` + variant-B stop-into-rotation) — no need to rebuild that. The **new** part is trapping a *session reference* (ONH/ONL/PDH/PDL) rather than a VA edge, entered as a **day-with reclaim** (go with the reversal that traps the breakout crowd), distinct from a static fade.
- **Prior / caution:** memory — `value-rotation` on flat/balance days **loses** (balance-day study: NQ edges are day-with only), and **static** session refs are **near-dead OOS** (drift-fade entry-reason). So the *sweep-of-a-static-ref* flavor has a low prior; the interesting cut is the trap-reclaim as a **continuation** entry (with-move) rather than a rotation-back-to-POC fade.
- **Engine work:** a sweep-then-reclaim trigger on session refs; could extend `DriftFadeConfig` variant-B (`confirm_ticks` beyond the touch extreme) toward a with-move reclaim.

### 6. Prior-balance-POC magnet target — ~~*minor target refinement*~~ → **NO MAGNET (pre-check)**

- **🔴 NO MAGNET — geometry pre-check (2026-07-20).** Before adding a `prior_poc` target_mode, measured where the two candidate strategies' *actual* favorable moves top out relative to the prior-day POC (`data/research/prior-poc-magnet/poc_magnet.py`, on real drift-fade + profile-pullback fills). It fails on three counts, on both strategies: (1) **wrong side** — the prior POC is on the profit side of entry only **45%** (drift-fade) / **30%** (profile-pullback) of the time, so it can't even be a target for most trades; (2) **too far** — when it *is* ahead, moves reach only **10%** (fade) / **29%** (pullback) of the distance to it (median ~110–200 pts overhead vs. 20–40 pt moves); (3) **no clustering** — stall prices land within ±10 pts of the POC just 3–6% of the time, no better than prior VAH/VAL/mid or a random in-range level. **Root cause:** a prior-*day* POC is a day-range-scale level, but our setups are intraday scalps/pullbacks; and a day-with long is usually *already above* yesterday's value, so the POC sits behind it, not overhead. Fabio's "enter below old value, target up into it" geometry is backwards for our entries. **Do not build** for these strategies — it would only matter for a larger-move day-with strategy we don't have. (The *nearer* prior balance — Globex/developing POC — is already a target_mode / already an entry ref, not novel.)
- **Market (source):** ⚙️ **Futures.** Fabio Auction (NQ/ES) exits at the prior-balance POC; the futures VP guides (FuturesHive ES/NQ) target edge-to-edge. Native to futures profile trading.
- **Source:** Auction Market model exits **the full position at the prior-balance POC** (~70% reverse from balance). Volume-profile guide: **edge-to-edge / next-shelf** targeting.
- **Maps to:** we have `target_mode: poc` but it tracks the **developing** POC, not the **prior-day/prior-balance** POC. Adding a `prior_poc` target (session-ref POC) is a small schema/engine add usable across day-with strategies as an alternative to `rr`/`dev2`.
- **Prior:** cheap, orthogonal to entry; worth A/B-ing on the drift-fade and profile-pullback once available.

### 7. Big-lot threshold calibration — *note, not a strategy*

- **Market (source):** ⚙️ **Futures — explicitly NQ/ES** (Yush: NQ 75-lot / ES 200-lot). The exact instruments we trade, so the threshold transfers directly.
- **Source:** Yush order flow — **NQ big trade = 75+ lots, ES = 200+**. Our big-lot participation signal already exists; this is an external second opinion on the threshold. Worth a one-line sensitivity check against whatever cutoff we use, no build required.

---

## 🔴 Graveyard cross-check (already tested ~dead here)

Kept as short cards — the external rules are worth having on file so we don't re-scout them cold, each paired with the specific study that killed it and any residual untested angle. **Dead here ≠ dead everywhere:** most of these are counter-trend or VP-geometry setups that fail *on NQ intraday in our engine*; they may live on other instruments/timeframes we don't trade.

### G1 · VWAP deviation reversion scalp

- **Market (source):** 📈 **Stocks** — TZ scalping blog ($0.20–0.30 targets, $-based stops). Same stock provenance as candidate #1, so the market confound applies here too — but it's *also* our dead `vwap-dev1-fade` family on NQ, so the verdict is double-confirmed.
- **Playbook (TZ scalping guide):** price extends far from session VWAP → snaps back; fade the extension with a tight target ($0.20–0.30 / next swing), 60–70% claimed win rate, windows 09:45–11:30 & 14:30–16:00.
- **Why dead here:** this *is* our `vwap-dev1-fade-long/short` family. Repeatedly: **NQ edges are day-with only** — fades lose even for the "clean" rotation trade (balance-day study; scouting-2026-07 §5). The high win rate is real but the left tail eats it.
- **Residual:** none — the mirror (day-with band *bounce*) is the live version and we already trade it.

### G2 · CVD / delta divergence · absorption · exhaustion (as an entry signal)

- **Market (source):** ⚙️ **Futures / general** — footprint/CVD tooling is futures-centric (GoCharting, Bookmap), and TZ Auction/Yush apply it to NQ/ES. **Market is not the excuse:** these signals died on our own NQ tape (loser + big-trade studies), so the null is native, not a translation artifact.
- **Playbook (GoCharting, Bookmap; TZ Auction & Yush aggression triggers):** enter when CVD confirms the break (new-high price + new-high delta), or fade when price makes a new high but delta diverges (lower high over 3–5+ bars); "absorption" = heavy delta, no price move → level defended; "exhaustion" = price to new highs, delta doesn't follow.
- **Why dead here:** dead at **every live anchor** — loser study: absorption/exhaustion AUC 0.43–0.59 at entry, underwater, and stop; CVD carries **zero entry signal** for losers *and* for big wins (big-trade study). Winners fill *into* selling, so absorption points the wrong way.
- **Residual:** **big-lot participation** is the lone survivor (AUC 0.66, robust) — but size-up on it already failed its A/B. Only used as an entry *filter* in candidate #3, not as standalone alpha.

### G3 · Volume-profile geometry as S/R

- **Market (source):** ⚙️ **Futures — NQ/ES examples** (Forrest Knight uses NQ; FuturesHive is ES/NQ). **Market is not the excuse:** VP-geometry still nulls on our own NQ data (stable-level, vah-snap, pd-VAH studies) — the setup, not the instrument, is what's dead.
- **Playbook (Forrest Knight VP; TradingSim; FuturesHive):** trade reactions at profile edges — VAH/VAL bounce, "volume-shelf" (sharp HVA→LVA drop-off), D-shaped-day VA fade, naked/prior POC as an unvisited magnet; confirm with a signal-candle close, target edge-to-edge (next HVN).
- **Why dead here:** **VP-geometry-has-no-edge**, null on repeat — stable/flat developing levels don't hold better than fresh ones (stable-level study, perm p=1.00); VP levels break through ~45/55 at every age; VAH snapping above price is *acceptance*, not resistance (vah-snap study); pd-VAH dies monthly (scouting §1.3).
- **Residual:** the **LVN as a continuation launchpad** (candidate #3) — our nulls were all measured on POC/VAH/VAL *as S/R*, never on the low-volume gap as a with-trend entry.

### G4 · LVN-rejection fade (Auction mean-reversion model)

- **Market (source):** ⚙️ **Futures — NQ/ES** (Fabio/Carmine). Native market; it's the *reversion direction* (counter-trend + absorption), not the instrument, that's dead here.
- **Playbook (Fabio Auction *Mean-Reversion Model*; Carmine LVN bounce):** after a failed breakout, wait for a reclaim inside balance, pull back into an LVN in the reclaim leg, enter *against* the failed break on absorption, target the balance POC.
- **Why dead here:** both legs sit in our graveyard — it's counter-trend (G1) gated by absorption (G2). Two dead signals stacked don't make a live one.
- **Residual:** only the **continuation** LVN model (#3) survives the day-with filter; this reversion twin does not.

### G5 · Liquidity sweep of static session refs

- **Market (source):** 🌐 **Multi (futures/forex/crypto)** — the Liquidity Playbook is explicitly cross-market and price-action-only. So the market fit is fine; the miss is that static session refs are near-dead OOS on our NQ data.
- **Playbook (Liquidity Playbook):** mark a respected high/low (PDH/PDL/ONH/ONL), wait for price to sweep it (run the stops), enter the reversal after rejection/reclaim, stop past the swept extreme, target the opposite pool; only in a set session window.
- **Why dead here:** expressible as `drift-touch-fade` + `use_session_refs`, but **static session refs are near-dead OOS** — the drift-fade entry-reason study found developing-vs-static replicates OOS with static refs contributing almost nothing (drop Open/ONH/ONL/pd* → PF/DD/expectancy *improve*).
- **Residual:** the **trap-reclaim as a *with-move* continuation** (candidate #5) — distinct from the static fade, and the one cut worth a look.

### G6 · Crudele "environment-first" futures trend (swing)

- **Market (source):** ⚙️ **Futures — index (S&P / Nasdaq / Russell), often via options.** Native instrument (ES/NQ/RTY) — the misfit is the *daily-swing timeframe* (1–5 day hold), not the market.
- **Playbook (Anthony Crudele):** Bollinger Bands 20/3 on the *daily* classify consolidation (bands contract) vs expansion (bands point out) vs mean-reversion (bands re-contract after a peak); trade *with* expansion, fade only to a 50% Fib of the band swing; 1–5 day hold, index futures, often via 3–5 DTE options.
- **Why it doesn't fit:** it's a **daily-chart swing** strategy — our engine is single-session intraday, so the environment classification and 1–5 day hold don't map. Not "dead," just off-model.
- **Residual:** its spine — *classify the regime first, only trade with expansion* — is exactly our `regime` gate, memory's **cleanest real detector**. A BB-expansion *regime variant* is a validation experiment, not a standalone strategy.

---

## Sources

TradeZella featured strategies:
- Futures Trend Trading (Anthony Crudele) — https://www.tradezella.com/strategies/futures-trading-strategy
- Liquidity Trading Playbook — https://www.tradezella.com/strategies/liquidity-strategy
- Auction Market Theory + LVN (Fabio) — https://www.tradezella.com/strategies/auction-market-strategy
- Carmine LVN Strategy — https://www.tradezella.com/strategies/low-volume-node
- Yush Order Flow Strategy — https://www.tradezella.com/strategies/order-flow-strategy
- Volume Profile Strategy (Forrest Knight) — https://www.tradezella.com/strategies/volume-profile-strategy
- Strategies index — https://www.tradezella.com/strategies

TradeZella blog / learning:
- 4 Scalping Strategies (VWAP reversion, ORB, micro-pullback, L2 momentum) — https://www.tradezella.com/blog/scalping-strategies
- Order Flow Trading: Explosive Moves — https://www.tradezella.com/blog/order-flow-secrets-how-to-catch-explosive-moves-using-order-flow
- Order Flow Terms & Concepts — https://www.tradezella.com/learning-items/order-flow-terms-and-concepts

External order-flow / volume-profile / VWAP guides:
- Delta & Cumulative Delta divergence (GoCharting) — https://gocharting.com/docs/orderflow/delta-and-cumulative-delta-bars
- Cumulative Volume Delta strategy (Bookmap) — https://bookmap.com/blog/how-cumulative-volume-delta-transform-your-trading-strategy
- Volume Profile day-trading guide (TradingSim) — https://www.tradingsim.com/blog/advanced-day-trading-strategies-using-volume-profile
- Volume Profile for ES/NQ (FuturesHive) — https://www.futureshive.com/blog/volume-profile-trading-strategy-2025
- Anchored VWAP strategies (TrendSpider) — https://trendspider.com/learning-center/anchored-vwap-trading-strategies/
- Anchored VWAP for day trading (TradingSim) — https://www.tradingsim.com/blog/anchored-vwap-strategies
- What is VWAP / reclaim (OrderFlowLabs) — https://orderflowlabs.com/blogs/theblog/what-is-vwap
