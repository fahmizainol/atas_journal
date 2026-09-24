# box-whisker-series — vendored from lightweight-charts plugin-examples

Copied verbatim from TradingView's `lightweight-charts` repository, at
`plugin-examples/src/`:

    plugins/box-whisker-series/{box-whisker-series,options,renderer,sample-data}.ts
    helpers/dimensions/{positions,crosshair-width,candles,common}.ts

The directory layout is preserved because `renderer.ts` imports the helpers by
relative path (`../../helpers/dimensions/...`).

**Why vendored rather than depended on.** The plugin examples are published as
source to copy, not as an npm package — there is no `@tradingview/box-whisker`
to install. Copying is the intended usage.

**Licence.** Apache-2.0, © TradingView, Inc. The `lightweight-charts` NOTICE
applies; the research page that renders this keeps `attributionLogo: true`,
which is how the linking requirement is satisfied (see the `attributionLogo`
option docs — the licence asks for a link to https://www.tradingview.com/ on a
user-visible page, and the on-chart logo counts).

**Local changes.** None. If that stops being true, say so here — the value of
this directory is that it can be diffed against upstream.
