# Lab Backlog

- **Owner:** afahmi
- **Created:** 2026-07-18
- **Purpose:** Running todo list for Lab exploration + Lab-facing features. Lab-first: prototype and measure in the Interactions Lab before promoting anything to a sim gate/strategy.

---

## 1. Fundamentals / event-day overlay — RESOLVED (null result, 2026-07-19)
Explore whether scheduled macro & earnings events carry an edge (or a "stay-flat" veto) for the intraday strategies.

> **Done — see `docs/research/event-day-overlay.md`.** Macro day-tags (FOMC/CPI/NFP/PCE/GDP), pre/post-event days, earnings (all AMC → next-session tag), and FOMC statement-time alignment, run on the v10 upper-band baseline (398 trades through 2026-06). **No significant link anywhere** (event-day p=0.58); the first-pass in-sample leads (NFP/GDP softness, event+post p=0.088) reversed when the Mar–Jun 2026 sample arrived. Post-earnings sessions are, if anything, slightly *better*. Decision: no gate, no sizing knob, no Lab tag UI. The verified 2025-02→2026-06 calendar (shutdown delays/cancellations included) lives in that doc's §5 for any future revisit.

- [x] Assemble event calendar (macro + NVDA/AAPL/MSFT/AMZN/META/GOOGL earnings, primary-source-verified).
- [x] Measure baseline edge conditioned on event/pre/post/earnings tags + FOMC time-of-day.
- [x] Decide filter vs signal: **neither** — proven null, do not build.
- ~~Expose session tags in the Lab~~ — dropped; no signal to surface.

## 2. Weekly anchored VWAP — RESOLVED (anchor shipped; gate A/B failed, 2026-07-19)
Add a weekly-anchored VWAP (anchor = Sunday Globex open / Monday session start) alongside the existing daily/overnight anchors.

> **Done — see `docs/research/weekly-vwap.md`.** Anchor built (`weekly.py`, seeded accumulation off per-session sums), drawn on every sim/Lab chart (orange, own toggle), Lab study at `/interactions/weekly-vwap`. Findings: weekly ±1σ leans fade, lower −2σ breaks through; sessions opening >+2σ revert hard. The >+2σ loser pocket on the v10 baseline (−$14.5k/48) had already been harvested by the reenter knob on v12, and the `wk_ext` gate **failed its engine A/B** (net −3.7%, Sharpe worse; PF/DD better isn't enough — the bar is all-four). Knob ships, stays off. The anchor, charts, study, and regime findings stand.

- [x] Add the weekly anchor to the VWAP computation + chart (should slot into the uniform Strategies chart — overnight candles + both VWAP anchors + profile).
- [x] Explore price interaction with weekly VWAP + bands: touches, dev-band fades/bounces, distance-to-weekly as a regime feature.
- [x] Compare weekly-VWAP context against daily-VWAP signals — does it filter the upper-band-bounce losers (regime, not geometry)?
- [x] Engine A/B: `wk_ext` entry gate (veto entries above weekly mid +2σ; inert on the week's first session) — **FAILED** (run `v12-07f1531f` vs baseline `v12-a0512f69`): net −3.7%, Sharpe worse, halves disagree; PF/DD better but the bar is all-four. The reenter knob had already harvested the >+2σ pocket, and vetoing what remained broke profitable re-arm chains. Knob ships, leave off. Item closed.

## 3. Kelly criterion what-ifs — VWAP upper-band-bounce
Position-sizing exploration for the upper-band-bounce strategy using its measured win-rate / payoff.

- [ ] Compute full/half/quarter-Kelly from the strategy's realized win rate & avg win/loss R.
- [ ] Simulate equity curves under each sizing scheme (fixed-R baseline vs Kelly fractions), report drawdown + geometric growth.
- [ ] Sensitivity: how fragile is Kelly to win-rate drift given the regime-dependence already documented?
- [ ] Surface as a Lab what-if panel (sliders for Kelly fraction).

## 4. IB range on the chart — DONE (2026-07-19)
Draw the Initial Balance range (9:30–10:30 ET high/low) on the Lab/Strategies chart.

> **Done.** `ib.chart_overlay` (in `src/journal/sim/ib.py`) computes the IB off the RTH ticks in the *same window as the study* and every sim/Lab chart payload now carries an `ib` slot (`api/sim_charts.py` trade + day charts, `interactions.day_chart` both bar paths). Drawn as lime flat segments from the bell to the close — line series, not full-pane price lines, so the IB doesn't leak over the overnight candles — with faint dashed 1×/1.5×/2× extension guides (the study's ext_x units: edge + m×IB-range) starting where the IB completes, excluded from autoscale. Two legend toggles: `Initial Balance` (default on), `IB extensions` (default off — platform convention, no efficacy claim). Honest-absence rule: a session whose data ends inside the window draws nothing. Tests pin the window/snap (`tests/test_ib.py`) and chart-vs-study agreement was verified on real cached sessions.

- [x] Add an IB high/low band overlay (first 60 min RTH).
- [x] Optional: IB-extension multiples (1×/1.5×/2×) as faint guide lines — platform convention, no efficacy claim (default-off toggle).
- [x] Wire to the IB/ORB study so break/extension stats line up with what's drawn — same window/definition via `ib.chart_overlay`; asserted equal to `session_row`'s `ib_high`/`ib_low` on real data. (The chart draws the study's 60-min default; the panel's `ib_minutes` knob stays a study-only what-if.)

---

## What's next (queue, 2026-07-19, revised after the scouting pass)

The v9 monthly-robustness pass is **DONE** — full sweep write-up in `docs/research/strategy-scouting-2026-07.md`. Verdicts: **drift touches SURVIVE** (17/17 months + circularity control — spec in `docs/research/drift-touch-fade-spec.md`), pd POC-from-below survives (9/9 buckets), the **lone-snap veto DIES** (9/17 months), pd VAH and the band+ref stack die. The same pass corrected the ORB record (full-year no-stop = 0.00R; the stop-enforced 5m ORB is +0.28R/trade) and found the flagship's pre-10:30 regime leak (−$15.7k on 32 trades). Revised queue:

1. **Build `drift-touch-fade`** per the spec — new registry entry, sides split, no gates, full 17-month window, then the gate-robustness ladder.
2. **Full-window `vwap-lower-band-bounce` run with the mirrored regime gate** (bbr@10:30 ≥ ~0.65) — 76 untouched habitat sessions; everything already built; cheapest new information in the repo.
3. **Earlier-regime-read A/B on the flagship** (09:45 checkpoint or a Globex pre-open proxy) targeting the −$15.7k pre-10:30 leak.
4. **Promote `profile-pullback-long` to the 17-month window** (+ regime/vwap_slope stack; keep first-touch, never dwell).
5. **Kelly what-ifs** (former item 3) — pure analysis + a Lab what-if panel, no engine risk. Use the current baseline's realized win/payoff and the regime-dependence + tail-concentration docs for the sensitivity read.
6. **ORB entry-time trend proxy** from the 9:30/9:45 regime-checkpoint raw features — the single unlock for `orb-breakout` (the stopped 5m ORB is tail-concentrated "trend-day tickets"); only revisit the strategy after this exists.
7. **Re-run the baseline to populate `recovery_s`/`giveback_s`** — the edges excursion-timing panel is shipped but those fields only fill on post-change runs; one re-run of the current baseline populates it.
8. **Sim runtime backlog** — regime_pnl serial tail + incremental window caching. Quality-of-life, no research risk.
9. *(Optional, cost decision)* **Buy the 16:00–17:00 ET segment** for the tick cache if the weekly VWAP should match ATAS exactly — the seeds currently omit that hour by construction.

### Notes
- See `docs/research/initial-balance-orb.md` for the IB/ORB primary-source study; local stop-enforced numbers now in `strategy-scouting-2026-07.md` §2.
- Upper-band-bounce loss study (sizing, regime filter): losses are regime not geometry; leave the panic-exit knob off.
- The Interactions-Lab improvement backlog shipped as v9 on 2026-07-19 — first read in `docs/research/interactions-v9-findings.md`; monthly-robustness verdicts in `strategy-scouting-2026-07.md` §1. Still monthly-untested: the level-led profile-pullback arm condition (v9 §5).
- Confirmed dead this pass (don't revisit without new data): weekly >+2σ standalone short (sign flipped OOS), weekly −1σ longs (too thin), IB-breakout variant (narrow-IB filter reversed locally), Globex-band bounce family (both sides, full window), all flagship gate relaxations (ghost pockets negative or audited mirage).
- Shipped-but-off knob drawer: panic_exit, size_up_*, pyramid_direction (non-default), wk_ext — all A/B-failed; only reenter_after_stop_only passed and is in the baseline.
