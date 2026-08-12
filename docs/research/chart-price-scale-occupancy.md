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
Recovering that space is what makes ticks look smooth. **Nothing adopted yet** —
this is the measurement, not the fix.

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

Not adopted — this is the shortlist the measurement supports.

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

## 8. Reproducing

The probes were one-off scripts under `tools/browser/`, deleted after use, and
the `window` hooks in `ReplayChart.tsx` were reverted — the file is clean. To
redo it: expose `chart`, the candle series and the band refs on `window`; pin a
session by writing `sim.resume` (`frontend/src/lib/replayResume.ts`) in an init
script; then compare `priceScale("right").getVisibleRange()` against the high/low
of the bars inside `timeScale().getVisibleLogicalRange()`. Screenshots from the
run are in `tools/browser/shots/autoscale/`.
