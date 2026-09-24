# The candles own a quarter of the pane

**Date:** 2026-08-12
**Question that started it:** would ticks look smoother rendered in WebGL (or in
a Rust/wgpu terminal) than they do in lightweight-charts?
**Surface under test:** `/charts/replay` — `frontend/src/components/charts/ReplayChart.tsx`,
lightweight-charts 5.2.0.
**Method:** real Chrome via the browser harness (`tools/browser/lib.mjs`),
1600×900 @ DPR 2 — main pane 563 css px = **1126 device px**. Sessions pinned by
writing the `sim.resume` bookmark before load, since the picker otherwise draws a
random day. Chart and series exposed on `window` through temporary hooks, removed
afterwards.
**Verdict:** the renderer is not the problem. The price scale is held open to
**3–10× the traded range** by context layers anchored far from price, so the
candles are compressed into a ribbon and one NQ tick collapses below one device
pixel. Median pane occupancy across 8 random sittings was **24%** — the chart
sits at or under its own `RIBBON = 0.25` "unreadable" threshold *by default*.
Recovering that space is what makes ticks look smooth.

**Adopted 2026-08-14** — items 1, 2 and 4 of §7 shipped; §8 has the re-measurement.

**Companion page:** [chart-price-scale-occupancy-visual](chart-price-scale-occupancy-visual.html)
— the same four sessions in three framings, toggleable (keys `1`/`2`/`3`), with
the occupancy gauge beside each chart. Open it from Lab → Research.

---

## 1. What lightweight-charts actually does with a price coordinate

The horizontal-line renderer — the live price line, every VWAP mid, every VA
edge — rounds to **bitmap** pixels:

```js
// lightweight-charts@5.2.0 dist/lightweight-charts.development.mjs:1878
const y = Math.round(this._private__data._internal_y * verticalPixelRatio);
```

So the snap quantum is one *device* pixel, not one css pixel: half a css pixel at
DPR 2. That is finer than the "it snaps to whole pixels" intuition, and it means
the interesting quantity is not *whether* it rounds but whether the rounding is
coarser than a tick:

```
device_px_per_tick = pane_height_css × DPR × 0.25 / visible_range_points
```

Below 1.0 the rounding is collapsing distinct prices onto the same row and the
tape visibly steps. At or above 1.0 it is sub-tick and invisible. On this pane
(1126 device px) the boundary sits at a visible range of **281 points**.

## 2. The measurement — 8 random sittings

| | median | min | max |
|---|---|---|---|
| pane occupancy (bars ÷ scale) | **24%** | 9% | 71% |
| device px per tick | **0.89** | 0.26 | 5.05 |

**Steppy (< 1 device px/tick) on 5 of 8 loads.** The bars themselves were normal
throughout — 40 to 135 points — while the scale ran 56 to 1025 points.

Re-measured against what the scale *would* be if it fitted the bars (+12% pad,
the framing `jumpToPrice` already applies by hand): **1.68–5.71 device px/tick,
steppy on 0 of 8.**

This is the whole finding. The vertical resolution is being spent on empty air
before the rasteriser is ever reached, and no renderer swap recovers it.

## 3. Who actually holds the scale open

`ReplayChart` already excludes two groups from the fit — Modern VWAP's non-mid
lines (`:1884`) and the IB extension guides (`:1927`) — with comments giving
exactly the right reason. The groups that were never given the same treatment
are what this section measures, by demoting one at a time, cumulatively, on
four pinned sessions.

Scale in points, with pane occupancy in brackets:

| session | bars | base | − σ bands | − weekly mid | − anchored mid | − dev VA |
|---|---|---|---|---|---|---|
| 2026-08-05 | 179 | 2031 (9%) | 1078 (17%) | **260 (69%)** | 260 (69%) | 194 (92%) |
| 2026-08-07 | 286 | 1988 (14%) | 595 (48%) | 397 (72%) | 397 (72%) | 286 (100%) |
| 2026-07-29 | 457 | 1048 (44%) | 517 (88%) | 485 (94%) | 485 (94%) | 457 (100%) |
| 2026-08-11 | 220 | 299 (73%) | 233 (94%) | 233 (94%) | 233 (94%) | 220 (100%) |

Reading it:

1. **The ±1σ/±2σ VWAP lines are the largest single offender on every day.**
   `mkBand` (`:1832`) is called four times — globex, NY, weekly, anchored — and
   its `line()` helper sets no `autoscaleInfoProvider`, so **all 20 lines vote**.
   Demoting the 16 non-mid ones is the same call `:1884` already makes for
   Modern VWAP, and it roughly halves the scale.
2. **The weekly VWAP mid is the decisive second.** On 08-05 it alone takes the
   scale 1078 → 260 points, occupancy 17% → 69%. Its mid sat near 29,050 with
   price at 29,915 — 865 points below, on an anchor the code at `:1858`
   describes as *context, nothing the replay measures against*.
3. **The anchored mid costs nothing** in these sittings — the ⚓ band is built
   empty and stays empty until someone anchors, so it has no points to fit.
4. **Developing VA lines are the tail**, worth 10–30% of what remains.
5. **A day that was already fine stays fine.** 08-11 begins at 73% and is not
   harmed by any of it.

### What does *not* vote, contrary to first reading

The composite VP levels are drawn *inside* `CompositeProfilePrimitive`
(`:197–199` — the `C-VAH` / `C-POC` / `C-VAL` labels), and neither that
primitive nor `VwapBandPrimitive` implements `autoscaleInfo`. **Primitives do
not contribute to the fit.** The composite is drawn where it belongs and is
simply clipped when it is off-screen — which is exactly the behaviour this study
is proposing for the series layers. The `C-VAL 29032.50` label visible on 08-05
is a coincidence of it sitting near the weekly mid, not evidence that it was
pulling the scale. (This is a correction: the composite was the first suspect,
from reading a screenshot, and demoting layers one at a time refuted it.)

## 4. The chart already knew

`RIBBON = 0.25` (`:425`) is the threshold below which the ◎ button lights up,
and its comment names the cause outright:

> A quarter of the pane: below that the candles are a ribbon and the day cannot
> be read off them, which on a chart that carries a weekly VWAP and a
> multi-session composite is a thing autoscale does on its own, without anyone
> touching the scale.

The measured median occupancy is 24%. **The chart is in its own
declared-unreadable state more often than not**, and `jumpToPrice` is a manual
rescue from a default that should not need rescuing. Half the comment's
attribution is right (weekly VWAP) and half is not (the composite is a
primitive, §3).

## 5. What this does and does not buy

**Does:** on days where autoscale had blown the scale out, framing the traded
range is transformative — 08-05 goes from an unreadable ribbon to a chart you
can work off, and 0.14 → 1.27 device px per tick, i.e. from ~7 ticks sharing a
pixel row to sub-tick.

**Does not:** make every session smooth. 07-29 spans 457 points of bars across
globex and RTH, so even perfectly fitted it is 0.50 device px/tick. On
wide-range sessions the remaining lever is zoom (fewer bars in view), not the
scale — and no renderer choice changes that either.

**Not measured:** `/charts/live`, which is manual-test-only. It carries the same
weekly anchor and band construction, so the same ratio is expected but is
unverified.

## 6. The trade-off any fix has to answer

A demoted series still draws at its true price — it is clipped, not deleted. So
a weekly band 800 points away leaves the screen until price approaches it.
**Off-screen and non-existent look identical**, which is the one genuine cost
here. The honest version ships with an edge marker: a chevron at the pane
boundary naming the nearest off-screen level and its distance.

Second knock-on: with occupancy normally high, the ◎ off-tape button and the
`RIBBON` check fire far less often. That is the fix working, not a regression,
but it makes the button close to vestigial.

## 7. Proposed change, in priority order

The shortlist the measurement supports. 1, 2 and 4 shipped; 3 did not.

1. Give `mkBand`'s `line()` the same `autoscaleInfoProvider: () => null` that
   `:1884` gives Modern VWAP, for every non-mid key. Largest win, and it makes
   two sibling code paths agree.
2. Demote the **weekly** anchor's mid as well. This is the real judgment call —
   it is the difference between 17% and 69% on the worst day, against an anchor
   the code already calls context-only.
3. Optionally demote the developing VA lines. Smallest effect; they are also the
   levels the rules genuinely test against, so the case for keeping them in the
   fit is the strongest of the three.
4. Ship the off-screen edge marker with 2, not after it.

## 8. What shipped, and the same four sessions again

**2026-08-14, `ReplayChart.tsx`** — and so also `/charts/live`, which mounts the
same component.

1. `mkBand`'s `line()` sets `autoscaleInfoProvider: () => null` on every non-mid
   key, matching what the Modern VWAP rings already did. Sixteen of the twenty σ
   lines stop voting.
2. The weekly anchor is built `mkBand(hues.vwap.weekly, { context: true })`,
   which extends that to its mid. It is the only anchor demoted mid and all.
3. **Developing VA lines were left in the fit**, as §7 argued they should be —
   they are levels the rules test against, and §3 measures them as the tail.
4. Edge markers (`.chart-edge`): the nearest demoted level off the top and off
   the bottom, named with its distance from the last trade, hung on the price
   axis at the two edges of the price pane. Hoverable for the level's actual
   price, and carrying a `+n` when more are out there the same way.

Two judgment calls the measurement did not make:

- **A half-pane deadband.** A σ line a few points over the edge is off screen in
  the literal sense and nobody has lost it. Only levels further than half the
  visible range past the edge get a marker, so the envelope stays quiet and the
  case the markers exist for still speaks.
- **Hidden layers are skipped.** A level you toggled off is not a level that went
  missing, and saying so at the edge would be noise about your own decision.

Re-measured the same way, same four pinned sessions, 1600×900 @ DPR 2, clock at
11:00. "Before" is the same reading with the bars' span unioned against the
levels this change demoted — the scale those layers were holding open:

| session | bars | scale before → after | occupancy | device px/tick |
|---|---|---|---|---|
| 2026-08-05 | 286 | 2071 → **286** | 14% → **100%** | 0.14 → **0.98** |
| 2026-08-07 | 256 | 1957 → **345** | 13% → **74%** | 0.14 → **0.82** |
| 2026-07-29 | 486 | 1090 → **514** | 45% → **95%** | 0.26 → **0.55** |
| 2026-08-11 | 212 | 281 → **233** | 75% → **91%** | 1.00 → **1.21** |

Against §3's prediction (the `− anchored mid` column: 260 / 397 / 485 / 233) the
measured scales are 286 / 345 / 514 / 233 — the differences are the clock, not
the change. **No session was made worse**, including the one that was already
fine. §5 holds on both counts: 08-05 goes from 7 ticks sharing a pixel row to
sub-tick, and 07-29 is transformed as a chart (45% → 95%) while staying steppy at
0.55 device px/tick, because 486 points of bars is a zoom question and no scale
fix reaches it.

Edge markers on the same four: 08-05 one (`▼ WK VWAP +1σ 239 +3` — the whole
weekly band under the day, so even its +1σ is below price), 08-07 two, 07-29 one,
08-11 **none**, which is the deadband working — that session had nothing far
enough out to be worth a word.

## 9. Reproducing

The probes were one-off scripts under `tools/browser/`, deleted after use, and
the `window` hooks in `ReplayChart.tsx` were reverted — the file is clean. To
redo it: expose `chart`, the candle series and the band refs on `window`; pin a
session by writing `sim.resume` (`frontend/src/lib/replayResume.ts`) in an init
script; then compare `priceScale("right").getVisibleRange()` against the high/low
of the bars inside `timeScale().getVisibleLogicalRange()`. Screenshots from the
run are in `tools/browser/shots/autoscale/`.
