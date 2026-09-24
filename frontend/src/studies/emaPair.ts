// Two EMAs as one study, so the pair can be read apart.
//
// The catalogue's own `EMA` is a fine moving average and a poor *pair*: adding
// it twice gives two instances of one export, which the package colours from one
// `plotConfig`, so a 9 and a 21 arrive as two lines in the identical blue. One
// study with two plots fixes that at the root — two lines, two default colours,
// two colour knobs, one row on the legend and one label carrying both lengths.
//
// Defaults are the app's own `emaPalette`, the lemon→bronze ramp the fixed EMA
// layer already draws in: one hue ordered by length, so the fast line is the
// bright one. A transcribed study has no reason to look foreign, and the colours
// are per-spec overridable now anyway.
//
// Lengths are in *bars*, like every length in Pine. On a 30s chart the pair that
// matches a 1m 9/21 is 18/42 — matching decay per unit of wall-clock rather than
// bar count, `(1 - a₃₀)² = 1 - a₁ₘ`.
//
// The crossover marks are the reason this is a script rather than a setting: the
// cross is a fact about the two lines together, which neither line can plot.

import { indicator, input, pineStudy, plot, plotshape, ta } from "../lib/pineStudy";
import { emaPalette } from "../theme";

export const emaPair = pineStudy("pine:emaPair", () => {
  indicator("EMA pair", { shorttitle: "EMA×2", overlay: true });

  const fastLen = input.int(9, "Fast length", { minval: 1 });
  const slowLen = input.int(21, "Slow length", { minval: 1 });
  const src = input.source("close", "Source");

  const fast = ta.ema(src, fastLen);
  const slow = ta.ema(src, slowLen);

  plot(fast, "Fast", { color: emaPalette.fast });
  plot(slow, "Slow", { color: emaPalette.slow });

  // Up-shapes become the up arrow and down-shapes the down arrow on the way out
  // (see pineStudy's SHAPE) — lightweight-charts draws four shapes and no
  // triangle, so the direction is what survives.
  plotshape(ta.crossover(fast, slow), "Fast over slow", {
    location: "belowbar",
    style: "triangleup",
    color: emaPalette.fast,
  });
  plotshape(ta.crossunder(fast, slow), "Fast under slow", {
    location: "abovebar",
    style: "triangledown",
    color: emaPalette.slow,
  });
});
