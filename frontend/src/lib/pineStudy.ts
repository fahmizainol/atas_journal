// Pine, running on our chart.
//
// The community catalogue (see lib/studies) is 317 machine ports of published
// PineScript, and this is the same road with our hands on it: `oakscriptjs`
// ships the v6 runtime those ports ride, and its `script` entry is the Pine
// *vocabulary* — `indicator()`, `input.int()`, `plot()`, `ta.ema()`, the bar
// sources — as callable JavaScript. So a Pine indicator comes across by being
// retyped in this dialect, near enough line for line, and this file is the
// twenty lines that turn the result into a `StudyModule` the rest of the chart
// already knows how to draw, tune, colour, hide and persist.
//
// What it is not: a parser. There is no path from pasted `.pine` text to a
// running study, here or in the package. Someone reads the Pine and writes the
// JS, and that person owns whether it is faithful.
//
// What will not come across at all, because the runtime has no such thing:
//
//   request.security()   no multi-timeframe — the single most common reason a
//                        published script cannot be ported
//   strategy.*           indicators only, never backtests
//   barstate.*, syminfo.*, timeframe.*, varip, table
//   line/box/label       they exist in oakscriptjs' root export but not in the
//                        `script` dialect, so structure drawings need another
//                        route than `plot`
//
// And the standing rule the 415 live under applies here unchanged, with one
// more turn of the screw: a transpiled indicator that is quietly wrong is the
// cheapest route to a fake edge, and one *we* transcribed is wrong in our own
// handwriting. These are a reading surface. Nothing in the sim or the strategy
// engine reads them.

import { executeScript } from "oakscriptjs/script";
import type { Bar } from "./chartTypes";
import type { StudyEntry, StudyMarker, StudyResult } from "./studies";

// The dialect itself, passed through so a study file has one import and the
// runtime stays behind this seam — the same rule lib/studies keeps for the
// community package. In Pine these are globals; here they are named imports,
// which is the only visible difference between a script and its source.
export {
  alertcondition,
  barcolor,
  bgcolor,
  close,
  color,
  eachBar,
  fill,
  high,
  hl2,
  hlc3,
  hlcc4,
  hline,
  indicator,
  input,
  low,
  math,
  na,
  nz,
  ohlc4,
  open,
  plot,
  plotchar,
  plotshape,
  Series,
  ta,
  volume,
} from "oakscriptjs/script";

/** Two bars, to make the script declare itself.
 *
 *  A Pine script has no separate manifest: its title, its inputs and its plots
 *  are whatever `indicator()`, `input.*()` and `plot()` happened to be called
 *  with while it ran. So the only way to read the configuration is to run it
 *  once — and the cheapest tape that gets a body past its first bar is two
 *  bars of nonsense. The values are thrown away; only the declarations are
 *  kept. (The community package probes with exactly this, for exactly this
 *  reason.) */
const PROBE_BARS: Bar[] = [
  { time: 1, open: 10, high: 12, low: 9, close: 11, volume: 100 },
  { time: 2, open: 11, high: 13, low: 10, close: 12, volume: 100 },
] as unknown as Bar[];

/** Pine's marker anchors, as lightweight-charts names them. */
const POSITION: Record<string, StudyMarker["position"]> = {
  abovebar: "aboveBar",
  belowbar: "belowBar",
  top: "aboveBar",
  bottom: "belowBar",
  absolute: "inBar",
};

/** Pine's twelve `plotshape` styles onto the four lightweight-charts actually
 *  draws — `SeriesMarkerShape` is `circle | square | arrowUp | arrowDown` and
 *  nothing else.
 *
 *  Collapsed by *direction* rather than by looks, because direction is the part
 *  a mark on a chart is read for: every upward shape becomes the up arrow, every
 *  downward one the down arrow, and the neutral shapes become a circle. The
 *  community package maps these one-to-one instead and hands the library names
 *  like `triangleUp` that it has no case for — which is a mark that silently
 *  does not draw. Worth diverging from it here. */
const SHAPE: Record<string, StudyMarker["shape"]> = {
  arrowup: "arrowUp",
  triangleup: "arrowUp",
  labelup: "arrowUp",
  arrowdown: "arrowDown",
  triangledown: "arrowDown",
  labeldown: "arrowDown",
  circle: "circle",
  cross: "circle",
  xcross: "circle",
  diamond: "circle",
  square: "square",
  flag: "square",
};

/**
 * One Pine body, as a catalogue entry.
 *
 * `body` is the script: a function that calls `indicator()` once and then
 * whatever `input`/`ta`/`plot` calls the indicator is made of. It is run twice
 * per draw cycle in total — once here to harvest what it declares, and once per
 * `calculate` against the real bars. It must be a pure function of the runtime's
 * globals, which is the same discipline Pine itself imposes: no state of its own
 * between runs, because the runtime rebinds `close` and friends on every call.
 *
 * @param key    Stable identity, stored in the saved spec. Prefixed `pine:` so a
 *               future community export of the same name can never resolve a
 *               spec that meant this one.
 */
export function pineStudy(key: string, body: () => void): StudyEntry {
  const probe = executeScript(body, PROBE_BARS, {});
  return {
    key,
    title: probe.metadata.title || key,
    short: probe.metadata.shortTitle || probe.metadata.title || key,
    overlay: !!probe.metadata.overlay,
    origin: "mine",
    mod: {
      metadata: probe.metadata,
      // Structurally ours already — oakscriptjs' `InputConfig` and `PlotConfig`
      // are the shapes lib/studies mirrors, which is why the settings panel and
      // the colour knobs need nothing added for these.
      inputConfig: probe.inputConfig,
      plotConfig: probe.plotConfig,
      hlineConfig: probe.hlineConfig,
      calculate(bars: Bar[], inputs?: Record<string, unknown>): StudyResult {
        const run = executeScript(body, bars, inputs ?? {});
        return {
          plots: run.result.plots,
          markers: (run.result.markers ?? []).map((m) => ({
            time: m.time,
            position: POSITION[m.location] ?? "aboveBar",
            shape: SHAPE[m.style] ?? "circle",
            color: m.color ?? "#94a3b8",
            text: m.text ?? m.char,
          })),
        };
      },
    },
  };
}
