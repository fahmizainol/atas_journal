# Terminal redesign — build plan

*Written 2026-08-12; phases 4–7 built the same day, phases 10–11 on 2026-08-14. Phases
1–6 and 10–11 are on `feat/terminal-redesign` and verified by
`tools/browser/panecheck.mjs` (35/35), `tools/browser/accountcheck.mjs` (21/21) and
`tools/browser/smoke.mjs` (63/63), plus `tests/test_replay_account.py` for the account
itself. **Phase 7 (Live) is built and UNVERIFIED** — `/charts/live`
is manual-test-only by standing rule, so it has been typechecked and built and
nothing more. [Parity with the prototype](#parity-with-the-prototype--the-checklist)
is the element-by-element checklist — every control the prototype draws, where it is in
the app today, and which phase closed the gap.*

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
| 4 | Pane linking — crosshair and right-edge sync | **Built** — `3c6786b` |
| 5 | The focus model | **Built** — `3c6786b` |
| 6 | The left tool rail | **Built** — `1799b81` |
| 6b | Ticket knobs, the dock's opener, the <1100px fold | **Built** — `60e837d` |
| 7 | Live gets the same layouts | **Built, unverified** |
| 8 | The design pass — identity block, bar controls, focus, dock | **Built** |
| 9 | Follow-ups — legend bucketing picker, readout flicker, order-pad minimise, rail pin | **Built** |
| 10 | The replay account, the sitting lifecycle, the blown-account protocol | **Built** — `04051dc`…`02769ab` |
| 11 | Design-language parity — one guard readout, one refusal, one prefs shape, one keymap | **Built** — `716f3c7`…`86ce1ce` |

Branch: `feat/terminal-redesign`, **not pushed**. Master is at `ea97845`.

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

### What phases 4–7 actually did

- `frontend/src/lib/paneLink.ts` — one crosshair and one right edge across the grid.
  Module state, like `chartFocus`, and it knows nothing about lightweight-charts.
- `frontend/src/lib/chartTools.ts` + `components/charts/ChartToolRail.tsx` — the tool
  vocabulary named once, and one rail outside every canvas driving the focused pane
  through `ReplayChartHandle.armTool`.
- `components/charts/TicketKnobs.tsx` — size and both bracket legs on the floating
  ticket, each quoting money as well as ticks.
- `ReplayChart` gained: `linked` / `onLinkedChange`, `routedTo`, `onFocus`,
  `onToolsChange`, and four handle methods (`armTool`, `clearAvwap`, `deleteSelected`,
  `clearDrawings`). A chart handed `onToolsChange` renders no in-canvas rail.
- `Simulator.tsx` and `LiveChart.tsx` both draw the same grid, the same rail, the same
  badges and the same top-bar controls. Live keeps its own copy of the six new prefs.
- `tools/browser/panecheck.mjs` — 30/30, now covering focus, the link both ways round,
  and the rail following focus.

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

**THE ONE THAT COST TIME — write this down.** `setCrosshairPosition` deliberately does
**not** fire `subscribeCrosshairMove`. It passes `skipEvent` all the way down to
`setAndSaveCurrentPosition` (lightweight-charts 5.2, `lightweight-charts.development.mjs`
~7062 / ~13226). So a pane told to follow moves a crosshair with **no numbers beside
it** — which is exactly the readout you turned the link on for. The OHLC readout is now
a function (`paintOhlc`) that both the subscription and the link call. Anything else
built on the assumption that a programmatic crosshair behaves like a real one will hit
this too.

**Verify — done.** `panecheck.mjs` hovers pane 0 and asserts panes 1 and 3 print an
OHLC readout; drags pane 0 and asserts pane 1 repainted; then turns the link off and
asserts it did not. All three run with the tape **stopped**, which is the only way any
of them mean anything — a running replay repaints every pane on its own.

*Harness trap found here:* the aiming point matters. The indicator legend is a DOM
overlay at the top-left and starts open on pane 0, so a pointer at 0.5 of a narrow pane
lands on the legend. Focus still works from there (the legend is inside the pane) but
the crosshair never moves — which reads as a broken link when it is a wrong aim. Aim
`fx ≈ 0.78`.

**Also needs — done.** A link toggle, `⇄`, in the top bar (global) and on each pane
(that pane's membership). Two views of the same tape at different bucketings should
scroll together; four charts used as four different questions should not. Default **on**
and persisted (`sim.prefs.linkOn`, `paneLinked`): reading one moment at four bucketings
is what the grid is *for*, and a link nobody switched on is a feature nobody finds. With
one pane it does nothing at all, so the single-chart page is unmoved.

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
  pane, delete the per-pane `.sim-pane-tf` pickers. **Done** — the pickers are gone.
- ~~`1`–`4` focus a pane.~~ **The plan was wrong here**: the bare digits `1`–`8` have
  picked the *bar size* since before there were panes. Taking a binding away from a page
  you drive by keyboard is worse than spending a modifier, so focus is **Shift+1…4**,
  read off `e.code` (shifted digits are punctuation, and which punctuation depends on the
  keyboard layout). The digits still pick the bar size — of the focused pane now, so the
  key and the bar cannot disagree.

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

B is the cheaper first move and is reversible into A. **B is what was built.** The rail
calls `armTool` on the focused pane's `ReplayChartHandle`, and the chart reports back
through `onToolsChange` so the rail cannot claim something is armed that isn't.

That reporting is an **effect over rendered state**, not a call inside each `arm*`
function. That is the load-bearing choice: every path that changes a tool — a rail click,
a key, Escape, a drawing being deleted, a session landing — reports the same way, which
is what makes "Esc disarms the rail" true without anyone wiring it up.

**Two things are new rather than moved.** `⌖` cursor (Esc has always disarmed, but Esc
is not discoverable and a rail with no way back to "just pointing" reads as one you can
get stuck in — it is lit when nothing else is, so the rail always has exactly one lit
button); and `＋Order` on a desktop, which in the canvas was touch-only on the grounds
that Space+click is strictly the better mouse gesture. That is true, and is also why
nobody with a mouse ever found the tool.

Every tooltip names the pane it would act on. A rail with no addressee is worse than no
rail.

**The flag turned out to be unnecessary and was not built.** A chart handed
`onToolsChange` does not render its in-canvas rail at all; a page that draws a single
chart passes nothing and is untouched. Not rendering rather than hiding matters: a
`display:none` copy is still a second `[data-tip^="…"]`, which is what broke a strict
locator in `smoke.mjs`.

**Cost note.** The rail plus a dock is ~358px of 1920 on a 1080p window. It pays for
itself the moment there are two panes; on a single pane it is a straight loss against
today's in-canvas rail. **Decided: the page rail is always on**, because uniform chrome
beats a layout-dependent rail — and the in-canvas one floated *over* candles, so the
trade is 38px of width against covered price action.

---

## Phase 7 — Live gets the same layouts

**Why.** Everything above is Replay-only. `LiveChart.tsx` has no layout state, no panes,
no picker — it is still a single chart, and it is the page that actually trades.

**Shape.** Mostly mechanical reuse: layout state + `LayoutPicker` in its `ChartTopBar`
slot, `chartRef` → array, the same drive-site loops, and the broker's orders/position
pushed to every pane (`brokerViews` already produces the same `WorkingOrderView` the
paper blotter uses, and it carries no x-coordinate, so this is the same one-line-per-site
change phase 3 was).

**What is genuinely different from Replay — and how it went:**

- Live has **no transport**, but it *does* use `ReplayEngine` per pane after all: the
  page already builds one over the growing tape and re-derives it whenever the header is
  repaired. An extra pane is the same engine at another bucketing over the same array.
  Priming mid-session is not a special case — the engine folds from row zero to the
  clock, and the frame loop carries it on.
- **The ordering trap.** A pane's chart can mount *before* the first live print. It then
  reports itself ready to a page with no tape, and nothing would ever come back to it. So
  `onAppend` primes the panes too, through a ref (it is defined before `primePane` is).
  The same call repairs them when a corrected header rebuilds pane 0's engine.
- Live has its own prefs (`live.chartKnobs`), not `sim.prefs` — the grid you watch a
  session on is not automatically the grid you study a replay on. Six new fields, same
  validators as the replay's.
- Routing can point at MNQ while the tape is NQ. Every pane wears a `→ MNQU6` badge —
  but **only while orders really route**, since paper fills off the tape in front of you
  whatever routing says, and a badge that is always there stops being read.
- The floating ticket's knobs price in the **routed** contract on a real account. An MNQ
  stop read at NQ's $20 a point is ten times the risk that is actually on.

**Verification is manual, and has NOT happened.** `/charts/live` is manual-test-only by
standing rule — gestures there reach Rithmic. It has been typechecked and production-built
and nothing else. **Test by hand before trusting it**, and in particular: that the panes
come up drawn on a session that is already running (the ordering trap above), that the
broker's position and stops appear on *every* pane, and that the `→` badge says the right
contract when routing is pointed at the micro.

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
| Legend: fold with a count | ✓ — **the checklist was wrong**: `.chart-legend-count` has printed `shown/total` all along | — |
| Legend: rows with swatch, name, value | ✓ (richer than the prototype) | — |
| `→ MNQU6` routed-contract badge | ✓ Live, while routing really differs | 7 |
| `⇄` link badge | ✓ per pane, toggles that pane's membership | 4 |
| Focus ring | ✓ `.sim-pane.focused` | 5 |
| ◎ back-to-price | ✓ `.chart-jump` | — |
| Armed-tool hint | ✓ in-canvas banners, plus the rail's own lit button | 6 |
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

## Phase 8 — the design pass

*After looking at the built thing beside the prototype, four corrections. All four
are visual/behavioural; none change what an order carries.*

**The pane's identity block.** What the chart is, which bar it draws, where its
orders route and the bar under the pointer were **three overlays in three
corners** — the legend top-left, the OHLC readout centred at the top, the routed
contract nowhere at all. Centred was defensible with one chart; on a four-pane
grid it put four readouts down the middle of the screen, none of them beside the
chart it described. They are one block now, top-left, in the order you read it:
`NQU6 5m → MNQU6` · `O H L C` · `ƒ 9/13` · rows. Drawn as text over the tape with
a shadow rather than a stack of chips — a chip per row is a second rectangle
competing with the candles, and with a dozen layers on it is a wall of them.

**The timeframe control.** The Lab's `.radio-group` is a bordered strip with a
filled accent selection, which is right for a *form* — it reads as one field with
one answer. On a 36px chart bar it reads as a **button**: a solid violet block
next to the layout picker and the link toggle, all of which are chrome, and the
eye goes to it before the tape. On the bar it now loses its box and its fill and
states the selection the way every other bar control does. Same component, scoped
with `.chart-topbar`.

**No autofocus.** Focus is claimed by a **press**. Chrome that re-aims itself at
whatever the pointer brushed past on its way somewhere else is chrome you stop
trusting — and with a tool rail acting on the focused pane, a hover-claim means
arming whichever chart your cursor last crossed.

> The seam this opened, and the fix. Focus is really *two* elections: which pane
> the chrome acts on, and which pane answers keys (`lib/chartFocus`). Making only
> the first press-only left Escape following the pointer while the rail followed
> the press — so the rail could arm pane 0 and no Escape would ever reach it.
> Both are press-only now, and the key handler additionally answers **while the
> pointer is over the pane**. That is what keeps Space+click working on a pane you
> have only pointed at, without letting a pass-over move anything.

**The dock's grammar.** The panel had the right controls in the right order; what
it lacked was a grammar. Flat rows of 12px muted labels on one flat card read as a
settings form — fine when it was summoned, wrong now it is a column you sit beside
all session. So the prototype's two levels: the dock is `--card`, and every group
inside it is a **bordered section on `--bg` with a 10px uppercase title**. The
depth runs the other way from a normal card — container lighter than contents —
which is what makes a section read as a well you look into rather than a tile
stacked on the page.

**Deliberately restyle-only.** Every control and behaviour is untouched. Two class
names were added (`.sim-sec-t`, `.sim-kinds`) and one inline-styled row became a
segmented strip; `RoutingPanel`'s 1,409 lines of account tagging, single-use tokens
and broker reconciliation were not opened. *Not* done, and deliberately: the
sections were not re-ordered to the prototype's, because the order you reach for
things by muscle memory is worth more than matching a sketch.

---

## Phase 9 — the follow-ups

**The bucketing label is the picker.** Each pane's legend prints which bar it
draws; that label now opens a list. This is *not* the per-pane picker phase 5
deleted — that was a second whole `TimeframeControl` parked in the pane's corner,
competing with the bar's for the same job. This is no resident chrome at all, and
it aims at its own pane by construction: re-bucketing a pane you are not working
in is one gesture instead of focus-then-pick. Every bucketing, not the bar's
short list — the `⋯` exists because a 36px row has an end, and a popup does not.

> Two things it had to get right. The outside-press guard has to match *this*
> legend's wrapper, not `[data-legend-tf]` — matching the selector meant pressing
> pane 2's picker did not close pane 1's, and two panes sat there with their lists
> open. And the popup's own listeners are capture-phase, or Escape reaches the tape
> before the popup hears about its own dismissal.

**The readout stopped flickering.** Reported: expanding the indicators and hovering
the rows made the candle info blink. It was a consequence of consolidating the
block — hovering a legend row takes the pointer *off the canvas*, the crosshair
reports nothing, and the readout blanked. Being inside the legend now, a blank
readout is a missing **row**, so everything below it jumped a line as you moved
down the list.

Fixed the way the prototype has it: the readout always draws, falling back to the
newest bar when nothing is under the pointer. That needs `paintOhlc` called from
two more places — `applyStep` (the idle readout has to keep up with the forming
bar, or it freezes the moment you stop pointing) and `setSnapshot` (or a session
opens with the whole block missing until the pointer first crosses the chart).

**The order pad minimises.** A `−` on its title bar collapses the BUY/SELL window
to an "Order Pad" badge; the badge brings it back. Deliberately *not* the same box
with its body hidden — the point is to give the tape its corner back, so a
minimised pad releases `--chart-floor` and renders nothing else. The badge is
fixed rather than draggable: what is behind it is the pair of buttons you can
least afford to go looking for.

> It went mid-right edge first, which is wrong: that band is where every
> price-anchored label lives — VAH/POC/VAL, HVN/LVN, the order and position chips
> — and they cluster around the current price, which *is* the middle of the pane.
> Bottom-right is the corner nothing claims, and it is beside the foot of the tape
> where the window itself parks.

Minimised state is its own key (`chart.quickDockMin`), not a field on the saved
position: minimising must not forget where the window was, and putting the window
back at the foot of the tape must not un-minimise it.

**The tool rail pins and unpins.** Phase 6 decided the page rail is always on and
noted the cost: 38px of 1920, a straight loss on a single pane. That is now the
choice it should have been. Unpinned, the rail goes out of flow and the grid gets
the width back — which is the assertion `panecheck` makes, since "you can see the
chart behind it" is a fact about *width*, not about opacity.

And it is barely there rather than a floating panel. A solid box over the candles
is exactly what pinning already avoids, so an unpinned rail that merely *moved*
the box would be the worse of both — column given up, tape still covered. No fill,
no border; legibility comes from a blur behind the glyphs and a per-button backing
that appears only under the pointer. The armed tool keeps its solid accent, because
"what is this click about to do" is the one thing here that must not be a guess.

> `--chart-rail` had to stop being an inline style for this. `ReplayChart` set it
> to `0px` inline when the page drew a rail; an unpinned rail floats back over that
> corner and the page has to be able to say so, which an inline value outranks. It
> is a class now (`.chart-no-rail`), and only pane 0 is indented — `place[0]` starts
> at grid line 1/1 in every layout, so it is the only pane the rail is ever over.

---

## Phase 10 — the replay account and the sitting lifecycle

**The problem this phase exists for.** A replay rep is free. No stakes, no witness,
no memory — so a sitting that ends $900 down ends by closing the tab, and the next
one starts from $0 an hour later with nothing carried across. Every previous
discipline layer in this codebase (`guardRules.ts`, `routing.py`) rules on *one
order at a time*; none of them can say "you already blew this account on Tuesday".
That is the gap: the replay enforces shape and enforces the day, and has no notion
of an account that a run of days can end.

So: a **persistent replay account** under the real LucidPro 50K rules, a **sitting
lifecycle** (arm → trade → finish → forced review → 1h cooldown → next), and a
**blown-account protocol** (auto-flatten → autopsy → scoped review → a written cause
of death → a 1-day timeout → a fresh account with that cause pinned to it).

### The rules being modelled

From [`docs/research/lucidpro-50k-survivability.md`](research/lucidpro-50k-survivability.md) §1 —
the real ones, not a house version:

Start **$50,000** · MLL **$2,000 end-of-day trailing** (`floor = min(peak_close, 52,100) − 2,000`;
the floor locks at 52,100 → **$50,100 forever**) · DLL **$1,200 soft** (locked out for the
session, account survives) · target **+$3,000** · caps **4 minis / 40 micros**.

Two policy numbers that are ours rather than Lucid's: **60 minutes between sittings**
and **24 hours after a blow-up**. Settled with the user, along with: the forced
review is **scoped** (the death sitting in full, plus flagged trades — not every
trade ever), there is **no probation or escalating penalty**, and the cause of death
is **pinned into the next epoch** where it can be read all the way through it.

### The nine decisions

- **D1 — no stored balance.** Equity is `$50,000 + Σ net_usd` over settled attempts
  since the epoch started, walked in `created_at` order. Ground truth is the attempt
  files under `data/replays/<date>/<id>/`; a balance field would be a second source
  of truth that a deleted attempt could desync. String-sorting `created_at` is
  correct — every stamp comes from `replays._iso`, one format, always UTC. The walk
  also yields "newest created_at", which is the hour gate, so the gate needs no
  stamp of its own.
- **D2 — minimal stored state.** `data/replays/account.json` holds
  `{"epochs": [{"started_at", "cause_of_death"?}]}` and nothing else. Death, the
  cooldown end (`died_at + 24h`) and the floor are all *derived*. Written through
  the `replays._write_json` temp-and-replace pattern, for the reason given there.
- **D3 — the account is always on**, independent of the `replay_guardrails` switch.
  Those are rules *under test* and the switch exists so a rule can be measured with
  it off. The account is not a rule under test; it is the stakes. Nothing turns it off.
- **D4 — flags are computed server-side** at finish-save time, by a ~40-line mirror
  that reads the stored `trades.json` and never re-derives a fill. Four kinds: **fast**
  (resolved under 30s, mirroring `guardRules.FAST_TRADE_MS`), **traded-in-the-hole**,
  **loss over `max_risk_usd`**, and **rewind-used** (straight off `attempt.rewinds`).
  Zero flags auto-passes `finished` → `reviewed`: a clean sitting is not made better
  by a ceremony, and a review you always have to click through is one you stop reading.
- **D5 — the review UI is the Simulator**, in a read-only `review` mode with the
  recorder never armed. The alternative was a review page of its own, which would
  need a second chart, a second replay engine and a second everything. The entry
  point is `ReplayHistory.tsx`, which writes a resume point plus a review marker and
  navigates to `/charts/replay`.
- **D6 — prefs unify as a shared shape and a shared loader**, with the localStorage
  keys unchanged (`sim.prefs`, `live.chartKnobs`). Zero migration risk is the point:
  the duplication being removed is in the *type and the validators*, not in the store.
- **D7 — a settled attempt is one whose status is not `active`**, plus a lazy
  stale-active sweep at derive/gate time: an `active` attempt whose `updated_at` is
  over 60 minutes old is closed. Otherwise the way to hide a bad sitting from the
  account is to close the tab, which is exactly the behaviour this phase exists to
  price.
- **D8 — the day boundary for the daily loss limit** is the America/New_York date of
  `created_at`, because that is the day the prop firm counts.
- **D9 — live equity** is server settled equity + `dayState.realized` + open P&L,
  joined **client-side**, in the one place that already joins those two
  (`guardRules.equityStop`). The floor is EOD-trailing, so it is constant for the
  whole of a sitting — which is what makes this join safe to do in the browser.

> **The timestamp rule — the one correctness trap in this phase.** There are two
> unrelated time families here. Replay `entryMs`/`exitMs` are *display-zone wall
> clocks with the zone dropped* (see the `journal.replays` docstring); `created_at`
> and `finished_at` are true UTC. Flags use only intra-tape deltas (`exitMs − entryMs`),
> and epochs, gates and day-loss grouping use only `created_at`. Nothing ever
> subtracts one family from the other.

### Architecture

**New `src/journal/replay_account.py`** — `load_state`/`save_state`,
`sweep_stale_actives`, `settled_attempts`, `derive() → AccountView`, `refusal()`
(the create gate: hour / unreviewed / blown / cooldown), `ensure_epoch`,
`write_cause`, `flags_for`. Death is the first attempt whose closing equity is at or
under its own floor; status runs `blown` (until a cause is written) → `cooldown`
(until +24h) → `can_reset`. `now` is injectable, or none of it is testable.

**`GET /replays/account`** returns the whole view: `now`, `equity`, `floor`,
`peak_close`, `status`, `day_net`, `day_loss_remaining`, `target_remaining`,
`next_sitting_at`, `cooldown_until`, `can_reset`, `review_block`, `epoch`,
`last_death`, `caps`. `now` is in there so every countdown on the client runs off
the server's clock rather than the browser's.

**API changes** in `api/routers/replays.py`, all new routes declared **above**
`/replays/{attempt_id}` — the path-swallow trap already documented on
`backfill_journal`:

- `GET /replays/account` (sweeps stale actives first)
- `POST /replays/account/cause` `{cause_of_death}` → the view; 409 if nothing died
- `POST /replays` gates through `replay_account.refusal()` → **409** `{code, message, until}`;
  after a completed cooldown it mints the next epoch
- `PUT /replays/{id}` computes and stores `flags` when the status arrives as
  `finished`, auto-patching to `reviewed` when there are none
- `PATCH /replays/{id}` accepts a `review` body and refuses `status:"reviewed"`
  until every flag has a verdict

**Frontend.** A `useReplayAccount` hook on the `["replays","account"]` key,
invalidated wherever `["replays"]` already is. `guardRules.ts` gains `accountStop`
(floor breach on live equity) and `accountRefusal` (why no *new* sitting may open —
null while an attempt is open, because resuming is free), both always-on, sitting
beside `equityStop`. The Simulator prepends `accountRefusal` to the entry-refusal
chain ahead of the `guardsOn` branch, fires the existing auto-flatten effect on
`accountStop` and force-finishes after it, and grows an account row on the recap,
a countdown chip, and a pinned cause-of-death strip that sits above the pane grid
for the whole of the next epoch.

The **autopsy** is composed client-side from `GET /replays`, which already inlines
records and summaries — the epoch equity curve is a cumulative sum in `created_at`
order and the totals come off the existing `replayStats.pool`. **Review mode** skips
`armAttempt`/`adoptAttempt` entirely (the recorder is off, nothing is written) and
refuses `placeOrder`; a new `ReviewPanel` in the rail lists the flags, seeks to
`flag.ms − 60s` at 1×, takes a leak/justified verdict and a note on each, and files
the lot.

## Phase 11 — design-language parity

Phase 7 gave Live the same layouts. This closes the rest of the gap, so the two
terminals are one design rather than two that resemble each other.

- **One guard readout.** A new `GuardMeters.tsx` takes a single feed shape (day-loss
  bar, floor-distance bar, contracts against the 4/40 cap for the contract in hand,
  the lock chip, the behaviour numbers) with an adapter on each side: the replay's
  is account + `dayState`, Live's is `GuardState`. It replaces the replay-local
  `Discipline` strip. This is also the answer to the open item below that deferred
  the dock's guard meters as "a *second* rendering of the same facts" — the objection
  was to a second rendering, and the fix is that there is now one.
- **One refusal.** A new `RefusalFlash.tsx`, so being told no looks the same on both
  pages.
- **One prefs shape.** `ChartReadingPrefs` extracted; `SimPrefs = ChartReadingPrefs &
  ReplayOnly`, `LiveChartKnobs = ChartReadingPrefs`, one loader parameterised over the
  validators that already exist. **Keys unchanged**, so nothing stored migrates.
- **One keymap.** A `usePaneKeys` hook; `LiveChart`'s three keys (q/w/s) widen to the
  replay's 1–8 bar sizes and Shift+1–4 focus. The transport keys stay replay-only —
  there is no clock to drive on a live tape.
- **The transport auto-hides while a position is open.** The row is for scrubbing,
  and scrubbing is the one thing that must not happen with size on.

### The commit sequence

Each of these is shippable on its own:

1. this doc
2. `replay_account.py` + `GET /replays/account` (readout only) + `tests/test_replay_account.py`
3. flags + the `reviewed` status + the review PATCH + the clean-sitting auto-pass
4. the account surface, read-only (hook, recap row, pinned note, countdown chip)
5. enforcement — `accountStop`/`accountRefusal`, auto-flatten and force-finish, the
   refusal chain, loud 429s
6. the create gates + epoch mint + the cause endpoint + the stale sweep
7. review mode — Simulator, `ReviewPanel`, the `ReplayHistory` entry point
8. the blown flow — autopsy card, cause-of-death form
9. `tools/browser/accountcheck.mjs`
10. parity: `GuardMeters` replaces `Discipline` on the replay
11. parity: `GuardMeters` + `RefusalFlash` on Live
12. parity: the shared prefs shape and loader
13. parity: the hotkeys
14. parity: transport auto-hide in position

Backend commits carry pytest. Replay-UI commits carry `typecheck && build` plus a
browser check. **Commits 11 and 13 touch `/charts/live`, which is manual-test-only by
standing rule** — they get typecheck and build and are flagged for a hand test, never
scripted.

### The risks worth writing down

1. The two timestamp families (the rule is boxed above). This is the one that
   silently produces plausible wrong numbers rather than an error.
2. Client refusals ship in commit 5 and server gates in commit 6, so for one commit
   the only thing stopping a refused create is the browser. The 429/409 is surfaced
   loudly through the existing flush-error path rather than swallowed.
3. Stale `active` attempts hiding losses — the lazy sweep (D7).
4. Reopening a `reviewed` attempt regresses it. Accepted: the review is wiped and
   re-required on the next finish, and the derivation is stateless, so nothing else
   has to know.
5. Countdown clock skew — every countdown runs off the view's `now`.
6. `FAST_TRADE_MS` will exist in TypeScript *and* Python. Cross-reference comments
   both ways; a threshold that drifts would make the flag disagree with the strip
   that reports it.
7. The equity walk is O(attempts) per call. Fine at this scale; memoise on directory
   mtime only if it ever shows up.

---

## Open items, not phases

- **The guardrails refused every entry on NQ — half resolved.** This instance's
  configured levels are `stop_ticks_min = 100` against `max_risk_usd = 250` at $5/tick —
  100 ticks × $5 = $500, so no size satisfied both, and the refusal message pointed at
  the stop floor rather than at the contradiction. The code *defaults*
  (`lib/guardRules.ts`: 40-tick floor, $250 ceiling) are consistent; the configured ones
  are not. They are consistent for MNQ.
  **The replay no longer enforces the stop floor at all** (`lib/guardRules.ts`, and the
  header there says why): the floor is a finding about where a stop should sit, and
  refusing a 50-tick stop in practice locked the one place cheap enough to re-measure it.
  The ceiling, the target floor and the dollar ceiling all still bind, so a 50-tick stop
  on 1 NQ now places at $250 exactly. **Live is untouched** — `routing.py` still refuses
  under the floor, so the configured contradiction is still live and still undecided:
  whether the ceiling should scale with the routed contract, or whether the floor is
  wrong for NQ.
- **Saved layouts.** Layout + per-pane bucketing + indicators is a workspace, and
  workspaces want names. The shape would be the existing `chartPrefs` blob with a list
  around it.
- ~~**The dock's guard meters**~~ — **taken up by phase 11.** The objection stands and
  is what shapes the fix: three meters *beside* `Discipline` would be a second rendering
  of the same facts, so `GuardMeters` **replaces** it rather than joining it, and the
  numbers stop being Live-only because phase 10 gives the replay an account to read them
  off.
- **The armed-tool banner is still in the canvas.** It is per pane and it belongs there,
  but with the rail also lit there are now two places saying "measuring". That reads fine
  today; if it starts to feel like noise, the banner is the one to drop.
- **`/charts/live` is unverified.** See phase 7 — three specific things to test by hand.
  Phase 8 changed what you will *see* there too (the identity block and the dock), so
  the look wants a glance as well as the behaviour.
- **The legend restyle reaches the Journal and Lab charts.** `IndicatorLegend` is shared
  with `CandlestickChart`, so its rows lost their chips everywhere, not only on Charts.
  Judged right — one legend, one look, and those charts float it over candles for the
  same reason — but it was not asked for, and `.ws-charts` would fence it if wanted.
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
- **Aim at `fx ≈ 0.78` for anything that needs the crosshair to move**, not just for
  clicks. Pane 0's legend is open by default and covers the middle of a narrow pane; a
  pointer that lands on it still focuses the pane (the legend is inside it) but moves no
  crosshair — which reads as a broken link rather than a wrong aim.
- **Programmatic crosshairs fire no event.** `setCrosshairPosition` passes `skipEvent`,
  so nothing hung off `subscribeCrosshairMove` will run for one. Assert on what the page
  *paints*, not on the subscription firing.
- **`smoke.mjs`'s "visible range survives the recolour" is flaky.** It compares an
  ink *silhouette* across two different background baselines (charcoal vs black),
  so a pixel sitting inside the `isBg` tolerance on one and outside it on the
  other flips a column. Seen failing once in four runs with no code between the
  passes and the failure. Re-run before believing it; a real regression there
  would also fail the round-trip assertion beside it, which shares the probe.
- **Never run two browser checks at once.** They drive the same dev server and
  the same `localStorage`, and the second one's setup lands in the middle of the
  first one's assertions. (And never stash a file a running check is reading.)
- **A paused replay shows you nothing you just placed.** The fill model charges a
  gesture lag (`lib/fillModel`): an order reaches the market ~250ms of *tape* time
  after the click, and on a paused replay that moment never arrives — so the order
  is neither filled nor working, it is simply not in the simulation yet, and it
  reads exactly like a gesture the page ignored. Any check that pauses before
  placing has to step the tape (`.`) before it reads the result. This is what made
  `panecheck`'s order assertions go red after the fill model gained the lag, and
  the failure looks like a broken gesture rather than a timing rule.
- **A check that trades must stub `POST /replays`, or it writes practice nobody
  sat.** The recorder opens a real attempt on the first fill, which puts a sitting
  into `data/replays` and the journal — and then the account's own create gate
  409s the *next* run for an hour, so the check fails for reasons that have
  nothing to do with what it is testing. `panecheck` and `accountcheck` both
  answer the recorder with a plausible attempt record and write nothing.
- **Stub `/live/routing` outright rather than fetching and patching it** if there
  is no Rithmic session up. `route.fetch()` on it reaches the broker and hangs
  until Playwright's 30s route timeout kills the run. `panecheck` patches (it has
  historically run with a session); `accountcheck` answers synthetically, and the
  page only reads `guards` and `replay_guardrails` off it.
- **A hidden duplicate is still in the DOM.** The in-canvas rail is not rendered when a
  page rail exists precisely because `display: none` left a second
  `button[data-tip^="…"]` and broke a strict-mode locator in `smoke.mjs`.
