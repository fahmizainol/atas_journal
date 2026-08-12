# Browser harness

Drives the real app in a real browser and reads the chart canvas back.

```bash
pnpm dev                              # repo root — API on :8000, SPA on :5173
node tools/browser/smoke.mjs          # all checks
node tools/browser/smoke.mjs appearance
node tools/browser/smoke.mjs --headed # watch it happen
```

Screenshots land in `shots/` (gitignored) whichever way a check goes — on a
failure the picture is usually the answer.

## Why it exists

The charts are canvas. `lightweight-charts` and every primitive in
`frontend/src/components/charts/` — the VWAP bands, the volume profiles, the
composite, the orders, the event bands — draw pixels, and the DOM holds nothing
about them. So no amount of `tsc`, unit testing or reading tells you whether a
band rendered, rendered in the right place, or rendered at all. A browser reading
the canvas back is the only observable there is.

It is a harness, not a test suite: it exists so a change to chart code can be
looked at, not so CI can go red. Nothing depends on it staying green.

## Setup

```bash
cd tools/browser && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 pnpm install
```

Its own package, deliberately: Playwright is a tool for looking at the app, not a
dependency of it, and the root `node_modules` is linked against an older pnpm
store. It drives the system Chrome (`channel: "chrome"`) rather than downloading
a second browser — hence `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD`.

## What the checks assert

Coarse things a screenshot would also show — "the surface is `#000`", "the canvas
is not blank", "nothing threw". Exact-pixel comparison is the fragile thing to
assert about a canvas; shape is the useful one.

Two probes in `lib.mjs` carry most of the weight:

- **`bg`** — the modal colour of the price canvas, i.e. the surface. Modal rather
  than a corner pixel: the corner lands on a grid line about half the time, and a
  check that reports the grid colour half the time is worse than no check. (It
  did exactly that on the first run.)
- **`silhouette`** — first and last non-background row per sampled column. The
  cheap way to ask "is the same thing still drawn in the same place". This is what
  catches a lost zoom: chart appearance is applied through `applyOptions`
  precisely so it does *not* re-run the build effect, because a rebuild resets the
  visible range — and losing the range you spent a minute framing because you
  changed the background is the regression worth a check.

## The split pane

```bash
node tools/browser/panecheck.mjs      # the Replay split pane, driven end to end
```

`panecheck` presses the top bar's split button rather than a debug route, then
checks the things a second chart can quietly get wrong: that it draws at all,
that it draws a *different* bucketing from the trading pane, that the divider
resizes it, that pane/size/bucketing survive a reload, that it never writes over
the shared indicator preferences, that the order dock stays on the trading pane,
and — the one the whole design rests on — that a pane repainting only on its own
bar close does not silently fall behind the tape.

## Measuring, rather than checking

Four scripts here answer "what does this cost" instead of "did it draw". They
came out of asking whether `/charts` could hold more than one chart at a time.

```bash
node renderbench.mjs     # fps + frame percentiles + long tasks, idle vs playing
                         #   --panes=N seeds the split before measuring
node renderprofile.mjs   # CPU sampling profile, self time rolled up by file
node renderbudget.mjs    # what % of the main thread one playing chart occupies
node renderscale.mjs     # the same, across four chart widths
```

**Point them at a production build or throw the numbers away.** On the dev
server ~40% of frame time is React's `jsxDEV` and `validatePropertiesInDevelopment`
— overhead that ships to nobody. The same page measured 33 fps on `:5173` and
56 fps built. `vite preview` ignores `server.proxy`, so serving a build needs a
config of its own that proxies `/api` to `:8000`.

Two things to know before reading a result:

- **fps answers almost nothing.** A page pinned at 60 fps may be 10% busy or 97%
  busy, and only the second one is out of room — which is what `renderbudget`
  reads (Chrome's own `TaskDuration`) and what `renderbench` cannot see.
- **This harness rasterizes in software** (`ANGLE … SwiftShader driver` — check
  it with `WEBGL_debug_renderer_info` if a number looks wrong), so paint costs
  more here than on a real machine. `ScriptDuration` is the honest half: JS is
  the part a GPU never takes away.

- **Busy% saturates.** One playing chart already sits at 92-100% of the main
  thread, so above that the metric cannot tell two configurations apart — added
  load turns into dropped frames instead of a bigger number. Past that point,
  read fps and long-task counts. Both scripts exist because neither is
  sufficient alone.

What they found, on a 1.17M-print glued tape: the `ReplayEngine` is cheap and
scales linearly (a full-day `snapshotTo` ~60ms, a mid-session seek ~9ms, an
`advance()` frame 0.006ms, ~1MB of derived state — the tape itself is shared by
reference). One playing `ReplayChart` is ~62% of the main thread in JS, flat with
width — 1504px and 498px measure the same.

That last figure does *not* extrapolate. A second live pane measured 58.5 → 59.5
fps when its `applyStep` is gated on `StepResult.newBar`, and 58.5 → 44.5 fps
when it repaints every frame. Most of one pane's 62% is page-level work shared
across panes rather than duplicated per chart, so the gate — not the pane count —
is what decides whether a split-pane `/charts` is affordable.

## Adding one

Add an entry to `checks` in `smoke.mjs` returning `[label, ok]` pairs. Note that
no `CandlestickChart` in this app is reachable by URL alone — each hangs off a
selection (a session row in the Interactions Lab, a trade on a strategy page, a
draft), so a check has to click its way in. Prefer "whatever sorts first" over a
hardcoded date or trade number, which rots as the data moves.
