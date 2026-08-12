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

## 5. Weekly VWAP reclaim/breakdown as S/R after opposite-side acceptance
If price has built acceptance (spent time / formed value) on one side of the weekly VWAP, does a later return to the weekly VWAP act as S/R?

- [ ] Before starting: pull one concrete chart example of each case (a real session/week from the cache) to sanity-check the hypothesis reads as intended and the acceptance definition isn't ambiguous — one below-acceptance-then-reclaim example (resistance case), one above-acceptance-then-breakdown example (support case).
- [ ] Define "acceptance below/above" (time-in-value or POC-below/above threshold) distinct from a single dip/touch.
- [ ] Resistance leg: measure reclaim behavior at the weekly VWAP conditioned on prior below-anchor acceptance vs. no-acceptance touches — reject/hold vs. punch-through rates.
- [ ] Support leg (mirror): measure breakdown behavior at the weekly VWAP conditioned on prior above-anchor acceptance vs. no-acceptance touches — hold vs. punch-through rates.
- [ ] Compare both legs against the existing [weekly-vwap.md](weekly-vwap.md) touch/band findings (leans fade at ±1σ, lower −2σ breaks through) — is this a distinct effect or the same mean-reversion lean restated from either side?

## 6. Volume profile row binning — match TradingView's convention
App's volume profile bins the visible/session range into rows using its own scheme; TradingView's Volume Profile indicators (Visible Range, Fixed Range, Session/Periodic VP) instead offer a **Row Size** setting with two modes: "Number of Rows" (default 24, divides the range into N equal rows regardless of tick size) and "Ticks Per Row" (bins by the instrument's actual minimum tick size × a configurable tick count). Neither mode is tied to tick size by default — worth deciding which convention the app's profile should mirror, and whether to expose it as a toggle.

- [ ] Find the current row-binning logic (likely in `api/charts_data.py` / `api/sim_charts.py` volume-profile code) and document how it currently bins.
- [ ] Decide: match TV's "Number of Rows" mode (fixed row count), "Ticks Per Row" mode (tick-size-based), or expose both as a toggle.
- [ ] If adopting tick-size binning, confirm NQ's tick size (0.25) and pick a sane default ticks-per-row.
- [ ] Implement + verify profile shape (POC/VAH/VAL) is stable/sane across the switch on a known session.

## 7. YouTube video — transcribe + extract useful notes — DONE (2026-07-29)
https://www.youtube.com/watch?v=JiLLSehLntg — watch/transcribe and pull out anything relevant to the journal/Lab work.

> **Done — see `docs/research/vwap-wave-toolkit-video.md`.** It's *"VWAP Wave System Toolkit Masterclass"* (Trader Drysdale, 1h19m, 2025-08-30) — a launch walkthrough of a paid TradingView indicator, ~50 min feature demo + ~28 min Q&A, **no backtests or sample sizes anywhere** (the presenter is explicit it's discretionary, not a signal indicator). **No new mechanisms:** every feature maps onto something already built here (IB + extensions, VWAP σ-band regime coloring, weekly anchor, session VP) or already resolved null (LVN/VP geometry ×5, event-anchored VWAP, absorption/exhaustion, stacked-ref confluence). Its own headline confluence claim — "monthly VWAP + weekly lower band in the same spot = excellent long" — is the stacked-ref shape that died in the v9 monthly-robustness pass. Three notes worth keeping: (a) the **monthly VWAP anchor** is the only unbuilt construct, but three independent results argue against it as a signal (the weekly `wk_ext` gate already failed; static refs were near-dead OOS in the entry-reason study; stacked-ref died) → parity-only or skip; (b) his **"retest" definition is our touch-bar close-back artifact** verbatim — the standing anchor-bar screen applies to anyone racing it; (c) **composite multi-session LVNs** are the one untested VP cell, low prior, cheapest form is extending `lvn_causal.py` against its existing random-pullback null. Only actionable residue: draw the **IB mid** (already computed in `session_row`, not emitted by `chart_overlay`) as a default-off convention line. Full transcript at `data/research/vwap-wave-toolkit/transcript.txt`.

- [x] Transcribe the video (done via `uvx yt-dlp --write-auto-sub`; 1,730 caption lines → 92 timestamped paragraphs).
- [x] Extract any useful ideas/claims and note which (if any) map to existing backlog items or research docs — claim-by-claim table in the write-up; queue impact: **none**.

## 8. Simulator: on-chart session-P&L summary — TODO (filed 2026-07-31)
A small always-on readout drawn **on the replay chart** summarising how the current
simulated session is going, so the number is in your eye-line while you trade
instead of in a panel you have to look away to read.

Filed while making the Simulator mobile-friendly: in the new **fullscreen** mode
the chart is the whole viewport and the ticket/blotter are hidden behind a toggle,
so there is currently *no* persistent view of session P&L there — which is exactly
where this indicator earns its place.

- [ ] Decide the content. Minimum: realised P&L + trade count + W/L. Candidates
      beyond that: open-position P&L, win rate, largest win/loss, current R
      multiple, peak-to-trough drawdown for the session.
- [ ] Decide the form. Cheapest is an HTML overlay in the corner of the chart
      container (like `.chart-legend` / `.chart-tools` already are) — no chart
      plugin needed, styles for free, and it can't cost frame time. A
      lightweight-charts custom primitive is only warranted if it has to be
      anchored to price/time, which a summary box does not.
- [ ] Pick the corner + collapse behaviour so it never covers the tape it is
      about (the legend owns top-left, the tools top-right; bottom-left is free,
      and in fullscreen the transport already floats over the bottom edge).
- [ ] Feed it from the existing derived state — `trades` / `realized` / `hud.openPnl`
      in `Simulator.tsx` are already computed and already re-render on change; do
      not add a second source of truth or a per-frame recompute.
- [ ] Respect the frame loop: the HUD is throttled to ~80ms on purpose
      (`pushHud`), so the indicator must not force a render per tick.

> Not research — a Lab-facing feature, no edge claim attached. Same
> "quality-of-life, no research risk" class as the sim-runtime item in the queue
> below.

## 9. YouTube video — "VWAP Is Outdated" 2026 overhaul — TODO (filed 2026-08-07)
https://www.youtube.com/watch?v=bPVOB96JWuw — *"VWAP Is Outdated — Here's The
2026 Overhaul You Asked For"*, The Good, The Bad And The Bitcoin, 11:54,
published 2026-08-04. Short lesson video (not a livestream), so the payoff — if
any — is a **mechanism**, not a trade log.

- [ ] Transcribe (`uvx yt-dlp --write-auto-sub`, same as item 7) and write the
      claim-by-claim table into `docs/research/`.
- [ ] For each claim, name which of the two buckets it lands in **before**
      costing any work: *already built here* (session/weekly/monthly VWAP
      anchors, σ-band regime coloring, IB + extensions, session VP) or *already
      resolved null* (event-anchored VWAP reclaim, stacked-ref confluence,
      LVN/VP geometry ×5, EMA-vs-band collinearity, oscillator-at-fill bleed).
      The two prior VWAP-toolkit teardowns both landed 100% inside those two
      buckets — assume this one does too until a claim escapes them.
- [ ] Apply the standing artifact screens: any "retest"/"reclaim" definition
      that scores the touch bar's own close is the touch-bar artifact by
      construction; any indicator whose value at fill correlates >0.7 with
      stretch-from-anchor is that anchor renamed.
- [ ] Only if a genuinely unbuilt construct survives both screens: cost it as a
      Lab study, not a build.

> The `/trading-video-teardown` skill covers the transcription + timestamp
> mechanics; it is aimed at trade extraction, so for a lesson video use only its
> transcript half. Prior art: item 7 (`vwap-wave-toolkit-video.md`) and the
> livestream teardown (`vwap-wave-livestream-teardown.md`).

---

## Simulator: vol-regime chip + ATR clock (queued 2026-08-03)

Surface the [vol-clock](vol-clock.md) regime label in the Replay Simulator. Two pieces, both read-only over existing artifacts:

- [ ] **Regime chip (session-constant):** fold `{regime, pctl, atr_pts}` from
      `data/research/atr-band/daily_atr.parquet` into the `/simulator/session`
      payload meta (`api/routers/simulator.py`, one lru-cached parquet read;
      honest-absence `null` for the first ~20 sessions under `min_periods`).
      Chip near the day picker: `QUIET · pctl 0.21 · exp ATR 361pt`. Causal by
      construction — the label is yesterday's ATR, knowable pre-open, so no leak.
      Show the *percentile*, not just the word (label is wrong on ~⅓ of tercile
      boundaries; flips cluster on NFP/earnings days — vol-clock §8).
- [ ] **ATR clock gauge (live):** client-side `(session hi−lo) / expected ATR`
      developing with the replay clock — the §8 `tr_vs_atr` ratio, live. Shows
      mid-replay when the day is violating its label (NFP days blow through
      1.0× by late morning). No new endpoint once expected ATR ships in meta.
      Post-IB rule of thumb for the gauge (vol-clock §10c): once the IB
      completes, the rest of the day adds ≈0.4×ADR14 of *new* range regardless
      of how wide the morning was — so expected day range ≈ current range +
      0.4×ADR, updateable at 10:30.
- [ ] Docstring note: the parquet is a frozen research artifact (thru
      2026-06-30, matches the replayable cache while the data budget is empty);
      re-run `atr-band/build_features.py` if new sessions are ever fetched.

> Not research — a Lab-facing display aid, no edge claim attached; practice
> context for the vol-clock habitat findings (UB=quiet, DTF=mid/hot).

Context lines worth showing on the chip's tooltip/expanded view (descriptive
leans from vol-clock §10 — informational only, each verified NOT tradeable):

- **quiet:** first IB break skews **up** ~63% (vs 50% on hot; stable in both
  halves) — but the break carries no expectancy to the close (+3 pts mean), so
  it's orientation, not a signal. An early *down* break on a quiet day is the
  statistically unusual open (37%).
- structure/IB shape is otherwise regime-invariant — a hot day is the same day
  at 2–4× speed; expect identical chop fraction and break timing, just bigger
  bars and shorter holds.

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
- External-material check (`vwap-wave-toolkit-video.md`, item 7): the mainstream practitioner VWAP/volume-profile toolkit contains nothing we haven't built or already falsified — useful as a coverage check on the null shelf, and a reminder that "retest = closed back through" signals are the touch-bar artifact by construction.
## Interactions Lab: IB width column in the sessions table — DONE (2026-08-03)

Surface per-session IB range in the Lab's sessions listing (the
coverage-driven table on the Interactions page).

> **Done.** New `GET /interactions/ib/sessions` (`ib.session_widths()`) next to
> the coverage endpoint, and an **IB width** column in the Sessions table
> between *Vol regime* and *Touches*: a narrow/mid/wide chip in the IB overlay's
> own lime ramp, the IB range in points beside it, and a tooltip carrying
> `ib_vs_adr`, the `adr14` denominator and the pinned edges. Sorts on the ADR
> ratio (not points — a points sort is meaningless across a window whose
> baseline range doubles), same reasoning as the vol column's percentile sort.
> The panel caption names the source window so a dash reads as "outside the
> snapshot", not "no data".

- [x] Columns: `ib_range` (pts), `ib_vs_adr`, and the narrow/mid/wide tercile
      chip — edges **pinned** at 0.46/0.67×ADR14 (`ib.WIDTH_TERCILE_EDGES`,
      measured 0.458/0.668 over the snapshot's 349 ADR-covered sessions), so
      "narrow" means the same thing in a one-month view and a full-window one.
      The study's own `_ib_width_terciles` still cuts its window's quantiles —
      there the point is an even split, here it is comparability.
- [x] Source: reads the widest default-window (`ib_minutes=60`) snapshot on
      disk rather than recomputing — `adr14` chains through prior sessions, so
      a day computed inside a one-month window would get a different
      denominator (or none) than the same day in the full window. Today that
      resolves to `NQ_20250201-20260630_v1-aada9fa2b02c`; it self-updates if a
      wider snapshot is ever built. Honest absence twice over: days outside the
      snapshot are absent from the payload, days inside its ADR warm-up come
      back with a null `width` rather than a made-up bucket.
- [x] Why (vol-clock §10c): width is a causal-at-10:30 day-character read —
      wide leans trend day (60% vs 48%), narrow leans balance/churn (29% vs
      12% balance); orthogonal to the vol regime; UB's sole bad cell is
      hot+narrow. Display aid only — no edge claim, `ib_width` gate stays off.
      The cell's own comment says so, so the next reader can't mistake the chip
      for a signal.

---

## Simulator: indicator suite (queued 2026-08-03)

Calibration instruments for the Replay Simulator, all grounded in findings
that survived their controls — the design rule is **context, not signals**
(every signal-shaped indicator idea in this repo has failed its A/B). Extends
the vol-regime chip + ATR clock section above; roughly ordered by
value-per-effort within each tier.

**Tier 1 — nearly free once the regime chip ships (same parquet/meta) — DONE
(2026-08-03):**

> **Done — both shipped, and the stated dependency turned out not to be one.**
> Neither indicator needs `daily_atr.parquet`: both are cut against **ADR14**,
> which lives in the IB study, so the payload got its own `context` block
> instead of waiting on the regime chip. That chip is still open above and drops
> into the same block when it lands (`{regime, pctl, atr_pts}` beside `adr14`).
>
> **Server.** `ib.day_context(root, day)` returns `{adr14, source}` out of the
> *widest* snapshot on disk — same rule as `session_widths` and for the same
> reason: the ADR chain makes a day's denominator a property of the window it
> was computed in, so every caller must get the full run's number. It is cached
> on a (dir, name, mtime, size) signature because a per-session endpoint would
> otherwise re-parse a megabyte of JSON to read one float. `/simulator/session`
> ships `context = {adr14, adr_source, ib_minutes, ib_width_edges,
> post_ib_add_x}`; the two research constants come down with it rather than
> being re-typed in TypeScript, so there is one place they are pinned.
> `POST_IB_RANGE_ADD_X = 0.4` is new in `ib.py`, cited to §10c.
>
> **Client.** The engine now publishes a developing **RTH** high/low
> (`Snapshot.range` / `StepResult.range`) beside the IB box it already tracked —
> RTH-only because `adr14` is a mean of RTH day ranges, and a range that counted
> the overnight would be measured with the wrong ruler. Verified: the tick-derived
> RTH range equals the study's minute-bar `day_range` to the cent on sampled
> sessions, so the numerator and denominator agree. Both indicators render in
> `components/charts/SimIndicators.tsx` as a strip over the chart's bottom-left —
> on the chart because fullscreen *is* the chart, and an instrument that vanishes
> when you concentrate is one you never calibrate against. Collapsible to a pill
> and remembered (`SimPrefs.indicators`), like the other reading choices that
> cannot touch a fill. Fed off the existing throttled HUD push, so no extra
> render per frame.
>
> **Causality.** Everything shown is knowable when it is shown: `adr14` is the
> fourteen sessions *before* this one, the IB range and day range come off tape
> already played. The width bucket does not appear until the window actually
> closes, so a blind replay cannot read the answer early.

- [x] **Range-budget fuel gauge:** built as a *budget*, which is the honest shape
      of the §10c finding — post-IB expansion is width-flat, so at IB close the
      day gets `0.4×ADR14` of new range and then spends it. The bar fills with
      what has been spent (`day range − IB range`) and the label reads
      "*n* pts left"; past the budget it flips orange and reads "over by *n*",
      which is the state the gauge exists for — a day that has already run
      further than a typical one is the worst possible time to project another
      target. Dormant before the IB closes ("budget at IB close") rather than
      guessing at a budget it has no basis for.
- [x] **IB-width chip at 10:30:** narrow/mid/wide in the IB overlay's own lime
      ramp, reusing `ibPalette.width` so a day reads identically here and in the
      Interactions sessions table; edges arrive from `WIDTH_TERCILE_EDGES`, and
      the bucket is withheld if the study's IB window ever stops matching the
      engine's (the edges were measured on a 60-minute IB). Shows
      `IB forming · n pts` until the window closes. Tooltip carries the §10c
      priors *and* the caveat that they are recognition, not prediction
      (post-IB expansion is width-flat; narrow days still close outside their IB
      62% of the time; the `ib_width` gate stays off). No ADR14 — a day outside
      the study or inside its ADR warm-up — degrades to the bare point range
      with the reason in the tooltip, never a made-up bucket.
- [ ] **Deferred to the regime chip: the hot+narrow cell.** The one prior from
      the §10c addenda that is *not* on the tooltip, because it is a two-way cut
      (width × vol regime) and the replay has no regime label yet. It is the
      only genuine dependency Tier 1 had on the chip above — add
      "hot + narrow = the one negative UB cell (−0.20 avgR, n=28)" to the width
      tooltip once `regime` is in the same `context` block. Descriptive either
      way; the cells are n=18–34.

**Tier 2 — ports of layers already built elsewhere in the app:**

- [ ] **NY VWAP ±σ bands + IB overlay** on the replay chart — the geometry the
      live strategies trade; `ib.chart_overlay` + band layers exist on
      strategy charts, this is plumbing. Practice on the engine's own lens.
- [ ] **Developing POC/VAH/VAL** — post-lookahead-fix only (developing values,
      never the EOD shape; the replay context makes causality non-negotiable).
- [ ] **Big-lot participation bubbles** (≥10-lot prints): the one entry-time
      tape signal that survived robustness (AUC 0.66; sizing on it still
      failed — display only). `demo/big_trades_demo.py` is the renderer to
      port; teaches that size arrives in bursts. *Superseded by the event-burst
      item in "composite profile + event overlay" below — same renderer, one
      step further on. Build that instead of this.*

**Tier 3 — live meters, highest training value:**

- [ ] **Chop meter:** live `overlap_10` bar-overlap fraction — the first
      robust structural stop predictor (AUC .61–.64 both runs; gates failed,
      awareness dial is the right form). Red when the last 10 bars stack.
- [ ] **Pace timer on open positions:** elapsed hold vs the regime's typical
      winner-resolution window (median ~4 min hot / ~17 min quiet, vol-clock).
      Answers "stale or still normal?" with data.
- [ ] **10:30 regime-checkpoint readout:** the bbr@10:30 number the flagship's
      cleanest gate uses (ghost AUC .657) — show the detector the engine
      trusts at the moment it trusts it.

**Deliberately absent (spec guardrail):** no IB extension targets
(placebo-equal, §10b), no EMA 9/20 (band-collinear ρ≈.96), no RSI (stretch
renamed ρ .81), no FVG/pattern overlays (survey: null-shaped). Leaving lore
off the chart is the feature.

---

## Simulator: composite profile + event overlay (queued 2026-08-04, BUILT 2026-08-04)

Port the two layers built in `demo/composite_profile_demo.py`
(`docs/research/composite-profile.html`) onto the replay chart: a **multi-session
composite volume profile** frozen at the prior close, and the **event overlay**
that stands in for Pulcini's MBO step (see
`docs/research/pulcini-scalper-podcast-2026-08.md`). Reading aids under the
suite's standing rule — **context, not signals** — and the demo's own null says
so explicitly: both event proxies land *further* from the frozen levels than the
session's own volume does (+20.5 / +22.9 pt paired on 40 sessions, +6.8 / +7.4
on 120, sign never flips). Nothing here is a signal, and the composite level
family is the one VP cell that has never been null-checked.

**Most of this is already in the sim.** Scoped 2026-08-04 against the code, not
from memory:

- `SimPrefs.historyDays` already loads 0/1/3/5/10 prior sessions as **real tick
  tape**, glued in front by `concatTapes` — a multi-session composite needs no
  new endpoint and no new data. This is normally the expensive part.
- `volumeProfile.ts:83` `computeTickProfile` already gives exact
  volume-at-price → POC/VAH/VAL.
- `RangeProfilePrimitive` already draws a profile pinned to a bar range, in the
  two z-layers this needs. A composite is that, with the range set to the
  history stretch.
- Sweeps already exist in the engine at the demo's exact constants
  (`SWEEP_GAP_MS = 250`, `SWEEP_SPAN_TICKS = 4` = 1.00 pt,
  `DEFAULT_BIG_LOTS = 50`), rendered by `BigTradePrimitive` with a user setting.
- **Freezing is free.** The engine already treats the history stretch as "bars
  and nothing else — no VWAP, no value area, no IB, no big trades", so a
  composite over history-only is frozen at the prior close *by construction*.
  The circularity guard is structural rather than a rule someone has to
  remember.

- [x] **Composite profile over the history days** — one profile call plus a
      primitive instance. Decide whether the grouping rule is the
      balance rule (accumulate while each prior session's VA still touches the
      composite-so-far's, restart on a clean break, cap 5) or just
      `historyDays`. The demo measured the fixed window as the worse rule on NQ
      — balance runs are median 2 days (p90 4), and VA width goes
      226 → 466 → 1,154 → 1,753 pt across balance/3/10/20-day — but
      `historyDays` is already a user setting, so the fixed rule is the
      zero-work option. Balance costs one `computeTickProfile` per prior day
      plus a ~30-line accumulate/break loop.
- [x] **HVN/LVN by prominence** — `volumeProfile.ts` has POC/VA only. Port
      `nodes()` from the demo (~40 lines) plus a binning step: it bins at tick
      level and the prominence detector wants a smoothed histogram. LVNs only
      *between* accepted humps (n_LVN = n_HVN − 1). Two knobs
      (prominence 15–60%, smoothing 2–8%) — the right setting is not knowable in
      advance, which is the thing to feel out.
- [x] **Event bursts** — sweeps already exist, so this is ~30 lines of
      clustering lifted from the demo (big sweeps within 60 s / 5 pt, ≥150 lots).
      Draw as a band spanning the prices the event printed across: band
      *height* is the read (tall = size that walked = stop-run shape;
      flat = size that went nowhere = absorption's signature). Supersedes the
      Tier 2 "big-lot participation bubbles" item above — same renderer,
      one step further on.
- [x] **Absorption** — ~40 lines, and the one item with a genuine design
      change: the demo scores concentration (lots per point traversed) against
      the **whole session's** median, which is lookahead. In a replay it must be
      a running median or a warm-up window. Do not substitute an absolute price
      band — measured, that is unusable on NQ (median 15 s RTH range
      4.75–6.00 pt, median 60 s range 11.75–26.75 pt; 60 s / ≤4 pt / ≥900 lots
      fired **0 times in 3 sessions**).
- [ ] **Events on the context days** — the engine deliberately computes no big
      trades for the history stretch, so this is an engine change. Not needed
      for the reading that matters (today's events against levels frozen before
      they printed); only do it if the prior days look bare.

**Deliberately not ported: the null scorecard.** It is a 40/120-session
aggregate; one replayed session is ~19 events, so a per-day version would swing
wildly and — against this suite's own rule — a noisy per-day statistic reads
exactly like a signal. Pin the fixed whole-run number as a caption instead, and
leave the scorecard in the demo page.

Plumbing tax, as usual, is the bulk of the diff rather than the bulk of the
thinking: `SimPrefs` fields + `loadSimPrefs` validation, an `IndicatorLegend`
entry, settings UI in `Simulator.tsx`, primitive lifecycle in `ReplayChart.tsx`.

### What shipped (2026-08-04)

Four of the five boxes. `lib/compositeProfile.ts` (both rules),
`profileNodes()` in `lib/volumeProfile.ts` (+ `computeTapeProfileRanges`, since
a composite is several non-adjacent spans of one glued tape), `TapeEvent` in
`replayEngine.ts` (bursts + absorption), and two primitives
(`CompositeProfilePrimitive`, `EventBandPrimitive`). Setup bar: **Composite**
(off / balance run / all prior days, shown only with prior days loaded),
**Nodes** (off / 15-60% prominence), **Events** (off / ≥1× / ≥2× / ≥3×). Four
legend keys. Defaults: composite `balance`, nodes 35%, events **off** — the
demo's own default, and the honest one for a proxy that measured negative.

Ported behaviour was checked against the demo's own functions rather than
eyeballed (esbuild bundle + Node harness, driven from Python):

- `profileNodes` ≡ `nodes()` — same HVN/LVN prices, heights and prominences on a
  three-hump synthetic profile.
- bursts ≡ `burst_events()` — 8/8 identical on a synthetic session (lots, band,
  member count, side). This one needed a fix: clustering the *running* sweep put
  two sweeps in the wrong burst, because the span test then runs on a partial
  high. Sweeps are now clustered when the run **ends**, which costs a few
  hundred ms of latency and buys numbers that match the write-up exactly.
- the streaming path (40 `advance()` steps) produces byte-identical events to
  one `snapshotTo()` over the whole session.
- balance rule: takes 2 of 3 days when the oldest breaks away, caps at 5 of 7
  overlapping days, and on five real cached NQ sessions (2026-06-23→29) reports
  per-day VAs of 206-417pt and a 5-day composite of 406pt — the study's scale.

Three deliberate departures from the demo, all forced by the replay being
causal:

1. **Absorption scores against a running median**, not the whole session's
   (that is lookahead here), with the demo's 20-window warm-up before anything
   is scored and RTH only — the overnight trades a fraction of the volume
   through a fraction of the range, and one median across both would fire on
   every window at the open. On the synthetic session this finds 11 of the
   demo's 12 windows, all clearing their own floor.
2. **The balance rule walks back from yesterday** rather than partitioning the
   corpus forward. The chart's question is "which run is today part of", which
   needs no days beyond the ones already loaded.
3. **RTH-only composite**, so the histogram under the context bars is not the
   volume of the bars drawn above it. A profile of a day means its RTH profile,
   which is what the run lengths and VA widths were measured on.

Second pass, same day, after looking at it: the bands were drawn wholly under
the candles at 10% alpha and were near-invisible, so the outline moved *over*
them (opaque, 1.5px, + a solid side flag and a 6px floor) while the wash stayed
underneath. And the demo's **`drawMarginal`** is now ported too
(`lib/eventMarginal.ts`) — event lots binned onto the price axis and stroked as
a staircase over a profile's own histogram, so a burst can be lined up with the
hump it landed in. It is drawn over both the viewport profile (right edge,
growing left) and the composite (pinned left, growing right), measured off each
one's own baseline and width so the outline and the bars cannot drift apart;
checked bin-for-bin against the template's `marginal()`. Filtering moved into
one place in `ReplayChart` — the bands, both marginals and the legend counts all
read the same filtered list, so they can't disagree about what is on screen.

Third pass: a **developing NY profile** as its own layer
(`DevelopingProfilePrimitive`) — the demo live view's other half. Today's
volume-at-price from the bell to the clock, drawn as a histogram in its own
gutter beside the viewport profile's, with the same event marginal over it, and
recomputed off the tape on each bar close (the span is the first NY bar to the
live edge, i.e. exactly what the NY value area accumulates over). All three
profile widths came down at the same time — the viewport one to 11% of the pane,
the fixed-range tool and the composite to 42% of their own span, where the POC
row used to reach clean across the box it was measuring.

One thing the check caught: shading the histogram by its *own* value area
disagrees with the developing VAH/POC/VAL lines beside it. Binning to 0.5pt rows
changes the value-area walk (it annexes a *pair* of rows at a time, so a 0.5pt
row steps a point where the engine steps half of one) — measured on NQU6
2026-06-25 that moves VAH 11.75pt while POC and VAL land identically. The
histogram now takes its shape from the tape and its levels from the engine, so
the two are one distribution again.

Not done: **events on the context days**, exactly as scoped — it is an engine
change, and the reading that matters is today's events against levels frozen
before they printed. Revisit only if the prior days look bare.

Fourth pass (user-asked, same day), two changes:

**Departure #3 above is now a setting, defaulting the other way.** A **Span**
select (globex + RTH / RTH only) decides how much of each context day the
composite is built from; `globex` is the default, so a level is built from
everything the day traded before the bell rather than from the day session
alone. The span ends at the 16:00 close under both settings — the post-close
hour belongs to the *next* day's overnight, and counting it here would put the
same ticks in two days — and a day whose overnight was never cached simply
starts at its bell. `buildComposite` was unchanged: the page cuts the spans,
the library sums what it is given.

The cost is that the balance rule discriminates less. Measured on NQU6
2026-06-23→29: per-session VAs go 205→300, 243→439, 411→362, 240→296pt, and the
balance walk over those five days goes from **1 day to the cap of 5** — wider
value areas touch more often, so the break test stops firing and the cap does
the work. The study's run-length and VA-width numbers are RTH numbers; switch
the span back to read against them, or when the night is genuinely a separate
auction.

**The developing NY profile names its own HVN/LVNs.** Same `profileNodes` at the
same setup-bar prominence — one knob for both readings, since "how big does a
hump have to be" is one question asked of two distributions, so the **Nodes**
select is no longer hidden when the composite is off. Drawn as prices over the
candles like the composite's, but only from the bell rightward: a developing
node has no meaning over bars that printed before the distribution existed, and
starting where it was measured is also what tells the two families apart at a
glance (violet/indigo from the bell, rose across the whole pane). Re-read on
each bar close rather than cached — this is the one distribution on the chart
that changes every bar, so there is nothing to cache against. Own legend key
(`developingVpNyNodes`), on by default, since the prominence knob is the first
switch.

---
