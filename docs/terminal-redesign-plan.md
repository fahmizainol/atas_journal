# Terminal redesign — build plan

*Written 2026-08-12. Phases 1–3 are built on `feat/terminal-redesign` and verified;
phases 4–7 are not started. [Parity with the prototype](#parity-with-the-prototype--the-checklist)
is the element-by-element checklist — every control the prototype draws, where it is in
the app today, and which phase closes the gap. Work that list, not a memory of the
screenshots.*

*The design this implements is the clickable prototype at
`docs/research/terminal-redesign.html` (built by `demo/terminal_redesign_demo.py`) —
read that first if the "why" of any phase below is unclear, because the arguments live
there and are not repeated here.*

The goal is a chart workspace that holds **up to four charts on one tape**, with the
chrome that makes four charts readable rather than four charts crowded: tools out of
every canvas, one control that names which chart it acts on, and order state drawn on
all of them.

---

## Status

| Phase | What | State |
|---|---|---|
| 1 | The layout model (`lib/paneLayout.ts`) | **Built** — `1b32786` |
| 2 | N panes in the Simulator + the layout picker | **Built** — `1b32786` |
| 3 | Order pills everywhere; orders from any pane | **Built** — `da176c8` |
| 4 | Pane linking — crosshair and right-edge sync | Not started |
| 5 | The focus model | Not started |
| 6 | The left tool rail | Not started |
| 7 | Live gets the same layouts | Not started |

Branch: `feat/terminal-redesign`, three commits, **not pushed**. Master is at `ea97845`.

### What phases 1–3 actually did

- `frontend/src/lib/paneLayout.ts` — six layouts (`one`, `col2`, `row2`, `left3`,
  `top3`, `quad`) placed on one 3×3 line grid: two content tracks per axis with a
  divider track between them. A pane is four grid line numbers. The one-pane case
  falls out for free because a pane spanning lines 1..4 swallows the divider track.
- `frontend/src/components/charts/LayoutPicker.tsx` — icons **derived** from the
  placement table, so the picture and the grid cannot disagree.
- `Simulator.tsx` — `chart2Ref`/`engine2Ref` became arrays indexed by pane; the six
  drive sites (`setTape`, `setSnapshot`, `applyStep`, `setPosition`, `setOrders`,
  `setTrades`, `setContextRanges`) became loops. Per-pane bucketing, indicator
  visibility (`prefsPane={`p${i}`}`) and legend state, all kept across layout changes.
- Every pane draws the position, the working orders and the fills, and every pane
  takes the same order gestures. The ⚓ anchor is the one per-pane gesture.
- `tools/layoutcheck.mjs` — proves every layout tiles the grid, no browser needed.
- `tools/browser/panecheck.mjs` — 20/20 against the dev server.

### Already in the app before this build started

Do not re-build these; they were mistaken for missing because the *prototype* lacked
them, not the app:

- **The indicator legend already collapses**, per pane, with persisted state
  (`IndicatorLegend`, `loadLegendOpen(prefsPane)`).
- **The order pills already carry money** — `OrdersPrimitive` draws `SL −$200` and
  `TP +$400 · 2.0R` on a working order's legs, `PositionPrimitive` draws
  `LONG ×1 +$120` with running P&L. Phase 3 only made them appear on *every* pane.
- **NQ→MNQ cross-routing already exists** on Live (`InstrumentSwitch` in
  `RoutingPanel`, `RoutingStatus.symbol` vs `feed_symbol`, `pointValue` on the order
  and position views).

---

## Phase 4 — pane linking

**Why.** There is no crosshair or range sync anywhere in `ReplayChart` today. With two
panes that was survivable. With four charts over one tape it is not: the whole point of
the grid is reading one moment at four bucketings, and without a shared crosshair you
are eyeballing which bar on the 1h pane corresponds to the one you are hovering on the
5m.

**The design decision that matters.** Link means **the same right edge, not the same
visible window**. Pushing one pane's range onto the others is what TradingView does and
it is wrong for mixed bucketings — the prototype proved it: an hourly pane forced into a
5-minute pane's window shows *four candles*. Pin the right edge and let each pane keep
its own span, so scrolling back an hour anywhere moves every pane back an hour and each
still shows the amount of history its bucketing is for.

**Shape.** A module like `lib/chartFocus.ts` — module state, not React state, because
these are read inside chart event handlers that were installed once and must not be
re-bound per render. Something like `lib/paneLink.ts`:

- panes register/unregister (mount order), as `chartFocus` already does;
- a pane publishes its crosshair time + price and its visible right edge;
- subscribers apply it, guarded by a re-entrancy flag (a `setVisibleRange` inside a
  range-change handler re-enters immediately otherwise).

`ReplayChart` already has both hooks: `subscribeCrosshairMove` (line ~2099, currently
only feeds its own legend/OHLC readout) and `subscribeVisibleLogicalRangeChange` (~2076).

**Watch for.** `setCrosshairPosition` needs a series and a time that exists on *that*
pane's bucketing — a 1-minute timestamp is not a bar on the 1h pane. Use the crosshair's
time and let the receiving chart snap, and swallow the throw when it can't.

**Verify.** Extend `panecheck.mjs`: hover a price on pane 0, assert the other panes'
legends print an OHLC readout for the corresponding bar; scroll pane 0 back and assert
every pane's right edge moved while their spans stayed different.

**Also needs.** A link toggle. The prototype put it on each pane as a `⇄` badge and in
the top bar. Two views of the same tape at different bucketings should scroll together;
four charts used as four different questions should not — so it is a toggle, not a
default.

---

## Phase 5 — the focus model

**Why.** Phase 6 cannot exist without it: a single tool rail has to act on *something*.
It also removes a duplication that phase 2 left in place — every extra pane carries its
own `TimeframeControl` at bottom-left because the top bar's could not reach it.

**Shape.**

- Focus is page state (`focus: number`), claimed on pointer-enter or press. **Reuse
  `lib/chartFocus.ts`** — it already elects a keyboard owner on exactly those events for
  exactly this reason. Do not build a second election.
- Draw it loudly: a 1px accent ring on the pane (`.sim-pane.focused`), plus the pane
  number spelled out on the top bar beside the control that acts on it. A quiet
  treatment fails the obvious test — with two 15m panes side by side you cannot tell
  which one the timeframe button is about to change.
- The top bar's `TimeframeControl` acts on the focused pane. Pane 0's bucketing is the
  page's own `timeframe`; panes 1..n read `paneTfs[i]`. Once the bar can reach every
  pane, delete the per-pane `.sim-pane-tf` pickers.
- `1`–`4` focus a pane.

**Rules carried over from the prototype, both load-bearing:**

1. **The order hotkeys stay page-level.** `q`/`w`/`s` mean the same thing wherever the
   pointer is — a market order does not belong to a chart. Only the *pointing* gestures
   (space+click, ＋Order, long-press) are per pane, because those name a price.
2. **The order dock must never follow focus.** In the prototype it did, and clicking low
   on an unfocused pane re-parked the ticket at that pane's bottom-centre *between
   mousedown and mouseup* — the ticket swallowed the click, and the next one would have
   landed on BUY. A surface carrying market-order buttons must never arrive under the
   pointer as a side effect of a gesture aimed at something else. Today `QuickDock` sits
   on pane 0 and stays there; if it is ever made movable, move it only on a gesture
   aimed at the dock itself.

---

## Phase 6 — the left tool rail

**Why.** There are now **four** in-canvas tool rails (`.chart-tools`, top-right of every
`ReplayChart`), each eating chart pixels, each acting only on its own canvas. A tool is a
mode of the terminal, not a property of one chart.

**Shape.** A ~38px rail outside every canvas, left of the grid, holding the app's own
vocabulary — ＋Order, fixed-range VP, measure, anchored VWAP, price line, then the
"take things away" group below a hairline. Arming a tool arms it **for the focused
pane**; the rail shows what is armed and the focused pane shows the hint.

**The hard part** is that arming currently lives *inside* `ReplayChart` (`armRuler`,
`armAvwap`, `armHline`, `armOrder`, and the `arm()` that makes them mutually exclusive).
Two options, decide before starting:

- **A.** Lift arming to the page and pass `armed: ToolId | null` down as a prop, with the
  chart reporting completion. Cleaner end state, touches a lot of `ReplayChart`.
- **B.** Keep arming in the chart and have the rail call the focused pane's handle
  through the existing imperative `ReplayChartHandle`. Much smaller diff; the rail
  becomes a remote control rather than the owner.

B is the cheaper first move and is reversible into A. Whichever, the in-canvas rail
should stay behind a flag until the new one is proven, because it is the only way to
reach those tools today.

**Cost note.** The rail plus a dock is ~358px of 1920 on a 1080p window. It pays for
itself the moment there are two panes; on a single pane it is a straight loss against
today's in-canvas rail. If a one-pane layout should keep the old rail, that is a
deliberate choice to make, not an accident to discover.

---

## Phase 7 — Live gets the same layouts

**Why.** Everything above is Replay-only. `LiveChart.tsx` has no layout state, no panes,
no picker — it is still a single chart, and it is the page that actually trades.

**Shape.** Mostly mechanical reuse: layout state + `LayoutPicker` in its `ChartTopBar`
slot, `chartRef` → array, the same drive-site loops, and the broker's orders/position
pushed to every pane (`brokerViews` already produces the same `WorkingOrderView` the
paper blotter uses, and it carries no x-coordinate, so this is the same one-line-per-site
change phase 3 was).

**What is genuinely different from Replay:**

- Live has **no transport** and no second engine per pane in the replay sense — the panes
  are bucketings of a live tape. Check how `LiveChart` builds its bars before assuming
  `ReplayEngine` per pane is the right shape.
- Live has its own prefs (`live.chartKnobs`), not `sim.prefs`. The layout needs a home
  there, with the same migration care phase 2 took.
- Routing can point at MNQ while the tape is NQ. Panes should wear the
  `→ MNQU6` badge the prototype drew, so "where would a click on *this* chart send" is
  never a guess.

**Verification is manual.** `/charts/live` is manual-test-only by standing rule — gestures
there reach Rithmic. Do not script that page. Build it, then hand it over to be tested by
hand, and say plainly in the handover that it is unverified.

---

## Parity with the prototype — the checklist

The target is **1:1 on chrome anatomy and affordances**: every control the prototype
draws, in the same place, meaning the same thing. It is deliberately *not* 1:1 on the
internals, because the prototype is a sketch in three places and copying it literally
would delete working features:

1. **The legend.** The prototype shows 7 indicator rows and a fold. The app's
   `IndicatorLegend` has ~14 rows, a per-row `…` settings panel, dim states for layers
   switched off by their own setting, and an appearance panel on the header. Keep the
   app's; take only the fold's *summary* (`ƒ 2/7`), which the app's header lacks.
2. **The dock.** The prototype's is a mock with three meters and a pad. `RoutingPanel`
   is 1,409 lines of account tagging, single-use tokens, broker reconciliation,
   one-click and the untagged state. Keep the app's; take the *layout* — persistent
   right column instead of a summoned rail.
3. **The `read-only` / `TRADE` pane badge is obsolete, not missing.** Every pane places
   orders now (phase 3), so there is nothing for it to say. Do not build it.

Legend: **✓** in the app · **~** partly there · **✗** missing · **n/a** deliberately not.

### Top bar (36px)

| Prototype | App | Phase |
|---|---|---|
| ▤ nav menu | ✓ `NavMenu` | — |
| Title + symbol + caret → setup | ✓ `ChartTopBar` title slot | — |
| Timeframe segmented + `⋯` | ✓ `TimeframeControl` | — |
| Layout picker (icon = current layout) | ✓ `LayoutPicker` | 2 |
| "acting on **pane N**" focus note | ✗ | **5** |
| `⇄ link` toggle | ✗ | **4** |
| Replay \| Live tabs | ✓ | — |
| 🔊 sound cue cycle | ✓ | — |
| ⛶ fullscreen | ✓ | — |
| Dock toggle (`▤▎`) | ~ lives on `.sim-rail` as `sim-rail-btn`, not the top bar | **6** |

### Left tool rail (38px, outside every canvas)

Every one of these exists today **inside** each canvas as `.chart-tools`, so phase 6 is a
move, not a build — except where noted.

| Prototype | App | Phase |
|---|---|---|
| ⌖ cursor (disarm) | ~ Esc disarms; no cursor button | **6** |
| 🧾 ＋Order | ~ in-canvas, **touch only** (`COARSE_POINTER`) | **6** |
| 📊 fixed-range VP | ~ in-canvas | **6** |
| 📏 measure | ~ in-canvas | **6** |
| ⚓ anchored VWAP | ~ in-canvas | **6** |
| ━ price line | ~ in-canvas | **6** |
| 🔔 alert | ~ price lines already chime on cross; no separate tool | **6** |
| ✎ drawings flyout | ✗ (prototype marks it unbuilt too) | later |
| ƒ indicators picker | ~ the legend is the picker; no rail entry | **6** |
| 🧹 clear / 🗑 delete | ~ in-canvas, appears with what it removes | **6** |
| ⚙ appearance | ~ on the legend header | **6** |

### Per pane

| Prototype | App | Phase |
|---|---|---|
| Legend: symbol + bucketing | ✓ | — |
| Legend: OHLC readout | ✓ `.chart-ohlc` | — |
| Legend: fold with `ƒ on/total` count | ~ folds, but shows no count | **5** |
| Legend: rows with swatch, name, value | ✓ (richer than the prototype) | — |
| `→ MNQU6` routed-contract badge | ✗ | **7** |
| `⇄` link badge | ✗ | **4** |
| Focus ring | ✗ | **5** |
| ◎ back-to-price | ✓ `.chart-jump` | — |
| Armed-tool hint | ~ in-canvas banners | **6** |
| Order pills + lines | ✓ every pane | 3 |
| Fixed-range VP / measure overlays | ✓ | — |

### Right dock

| Prototype | App | Phase |
|---|---|---|
| Persistent right column | ~ summoned `.sim-panel`, pinnable via `railPinned` | **6** |
| Account dot + select + kind badge | ✓ `RoutingPanel` (Live) | — |
| Routed instrument + `$/tick` + `⚠ chart is NQ` | ✓ `InstrumentSwitch` | — |
| Guard meters (day loss, trailing DD, contracts) | ~ `Discipline` shows the rules, not three meters | **6** |
| Order pad MKT/LIM/STP + size + stop + target | ✓ | — |
| Risk/reward hint line (`−$200 · +$400 · R:R`) | ~ shown, verify shape | **6** |
| Review sentence + Send + countdown | ✓ | — |
| One-click switch | ✓ | — |
| Position row + FLATTEN | ✓ | — |
| Working list + cancel | ✓ | — |
| Footer: reconciled / last price | ✓ | — |
| Untagged-account warning | ✓ | — |

### Floating ticket (over one pane)

| Prototype | App | Phase |
|---|---|---|
| Drag grip, draggable, remembers position | ✓ `QuickDock` | — |
| BUY / SELL with prices | ✓ | — |
| Position chip + flatten | ✓ | — |
| Size stepper on the ticket | ✗ (size lives in the rail) | **6** |
| `⊥ 40t −$200` / `⊤ 80t +$400` knobs | ✗ (bracket lives in the rail) | **6** |
| Routed symbol + account kind | ✗ | **7** |
| Never moves pane on its own | ✓ by construction (pane 0 only) | — |

### Overlays

| Prototype | App | Phase |
|---|---|---|
| Layout modal | ✓ popover instead of modal — better, keep | — |
| Indicator picker modal | ~ the legend does it inline | **6** |
| Session setup behind the title | ✓ `.sim-setup` | — |

### Responsive

| Prototype | App | Phase |
|---|---|---|
| < 900px → focused pane only, layout kept not cleared | ✓ CSS media query | 2 |
| < 1100px → dock folds, ticket carries order entry | ✗ | **6** |

---

## Open items, not phases

- **The guardrails currently refuse every entry on NQ.** This instance's configured
  levels are `stop_ticks_min = 100` against `max_risk_usd = 250` at $5/tick — 100 ticks
  × $5 = $500, so no size satisfies both, and the refusal message points at the stop
  floor rather than at the contradiction. The code *defaults*
  (`lib/guardRules.ts`: 40-tick floor, $250 ceiling) are consistent; the configured ones
  are not. They are consistent for MNQ. **Nothing has been changed** — decide whether the
  ceiling should scale with the routed contract, or whether the floor is wrong.
- **Saved layouts.** Layout + per-pane bucketing + indicators is a workspace, and
  workspaces want names. The shape would be the existing `chartPrefs` blob with a list
  around it.
- **A structural guarantee was traded away.** The context pane used to be un-tradable
  *by construction*. Now the discipline layer is the only thing between a gesture on
  pane 3 and a fill. That was a deliberate call; it is worth re-confirming after real use.
- **Per-pane instruments** (NQ/ES/CL in different panes) need a symbol-link group model
  and a second tape subscription. Bigger than chrome; decide before the layout model
  grows, not after.

---

## Harness notes — read before writing another browser check

These cost real time to rediscover and are now written into `tools/browser/panecheck.mjs`
as well:

- The dev server is on **:5173**, the route is **`/charts/replay`** (not `/charts`).
- **Space is only the placing modifier while the pointer is over the chart.** Move the
  pointer in first, *then* press Space.
- `page.mouse.move` to the coordinates the pointer is already at generates **no event**.
  Two moves, or the pane never sees the `pointerenter` that makes Space its modifier.
- The click must land in the **price area**. A `y` in the volume or CVD sub-pane has no
  price behind it and the handler correctly does nothing.
- The **indicator legend is a DOM overlay at the top-left** and starts open on pane 0, so
  on a narrow pane it covers the middle and eats the click. Aim right of centre
  (`fx ≈ 0.78`).
- The chart **ignores Space while a form control has focus** — that is the control's own
  key. `selectOption` leaves the `<select>` focused; blur before the gesture.
- Counting "non-transparent pixels" on a chart canvas is **useless** — every pixel is
  opaque, so the count is just the area. Use a checksum. And note a checksum is dominated
  by **crosshair paint**, so it cannot tell you an order was drawn; read the page's own
  state (`.sim-rail-badge` carries the working count and is drawn whether the rail is
  open or shut).
- To place an order at all on this instance, stub the guard levels **in the browser**
  (`page.route("**/live/routing", ...)`), never by writing settings.
