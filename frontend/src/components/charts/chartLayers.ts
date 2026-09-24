import type { IndicatorKey } from "./IndicatorLegend";

/**
 * What this chart's own layers are called, and the order they are offered in.
 *
 * These names used to exist only as literals inside `ReplayChart`'s legend
 * labels, which was fine while the legend was the only place a layer was named.
 * The topbar catalogue needs them too — and it needs them *before the data
 * exists*, since you can switch a layer on for a session that hasn't reached it
 * yet — so a label built from `present` could not serve.
 *
 * One table rather than two lists. The legend's labels are built from these names
 * (`${LAYER_NAME.bigTrades} · >${bigLots} lots`), so the catalogue and the legend
 * cannot drift into calling the same layer two things — the same argument
 * `LayoutPicker` makes for deriving its icons from the placement table instead of
 * drawing them by hand.
 *
 * The *readout* stays at the push site. A layer's label is not a name: it quotes
 * what the row is currently showing — the threshold a count was counted at, how
 * many days went into a composite, what share of the session a gate called
 * trending. That is a fact about this pane at this moment and belongs where it is
 * computed. The name is the part that is always true.
 */

/** The subset of `IndicatorKey` this chart can draw. The rest of the union
 *  belongs to the journal charts (`CandlestickChart`) — an ATR pane, the key
 *  levels, the four 1-minute EMAs — and listing them here would offer a switch
 *  for something that cannot appear. */
export type ReplayLayerKey = Extract<
  IndicatorKey,
  | "vwapGlobex"
  | "vwapNy"
  | "vwapWeekly"
  | "vwapAnchored"
  | "modernVwap"
  | "modernVwapSignals"
  | "dynamicSwingVwap"
  | "developingProfileGlobex"
  | "developingProfileNy"
  | "developingProfileWeekly"
  | "developingVpNy"
  | "developingVpNyNodes"
  | "initialBalance"
  | "ibExtensions"
  | "volumeProfile"
  | "volumeShelf"
  | "volumeShelfBoxes"
  | "bigTrades"
  | "cvd"
  | "cvdOsc"
  | "volRuler"
  | "compositeProfile"
  | "compositeNodes"
  | "sweepBursts"
  | "absorption"
  | "replayTrades"
  | "externalChart"
  | "htfTrend"
  | "rankedZones"
  | "econEvents"
  | "gexLevels"
>;

export const LAYER_NAME: Record<ReplayLayerKey, string> = {
  vwapGlobex: "VWAP · Globex",
  vwapNy: "VWAP · NY",
  vwapWeekly: "VWAP · Weekly",
  vwapAnchored: "VWAP · Anchored",
  modernVwap: "Modern VWAP",
  modernVwapSignals: "Modern VWAP signals",
  dynamicSwingVwap: "Dynamic Swing VWAP",
  developingProfileGlobex: "Developing VA · Globex",
  developingProfileNy: "Developing VA · NY",
  developingProfileWeekly: "Developing VA · Weekly",
  developingVpNy: "Developing VP · NY session",
  developingVpNyNodes: "NY nodes",
  initialBalance: "Initial Balance",
  ibExtensions: "IB extensions",
  volumeProfile: "Volume profile",
  volumeShelf: "Volume shelves",
  volumeShelfBoxes: "Shelf boxes",
  bigTrades: "Big trades",
  cvd: "CVD",
  cvdOsc: "CVD oscillator",
  volRuler: "Vol ruler",
  compositeProfile: "Composite VP",
  compositeNodes: "Composite nodes",
  sweepBursts: "Sweep bursts",
  absorption: "Absorption",
  replayTrades: "Trades",
  externalChart: "External chart",
  htfTrend: "HTF trend",
  rankedZones: "Ranked S/R zones",
  econEvents: "Economic events",
  gexLevels: "Gamma levels",
};

/**
 * The order the catalogue offers them in — the legend's own order, which is
 * curated: the bars themselves at a coarser period, then the four anchored
 * VWAPs, then the value areas, then the session's shape, then what the tape did,
 * then the panes, then your own fills. Flat, not sub-grouped: it is twenty-seven
 * rows you already know by sight from the legend, and a third level of headers
 * inside a 320px popover buys nothing.
 */
export const REPLAY_LAYERS: readonly ReplayLayerKey[] = [
  "externalChart",
  "htfTrend",
  "econEvents",
  "gexLevels",
  "vwapGlobex",
  "vwapNy",
  "vwapWeekly",
  "vwapAnchored",
  "modernVwap",
  "modernVwapSignals",
  "dynamicSwingVwap",
  "developingProfileGlobex",
  "developingProfileNy",
  "developingProfileWeekly",
  "developingVpNy",
  "developingVpNyNodes",
  "initialBalance",
  "ibExtensions",
  "volumeProfile",
  "volumeShelf",
  "volumeShelfBoxes",
  "rankedZones",
  "bigTrades",
  "cvd",
  "cvdOsc",
  "volRuler",
  "compositeProfile",
  "compositeNodes",
  "sweepBursts",
  "absorption",
  "replayTrades",
];

/**
 * One layer as the topbar catalogue sees it, published by the pane that draws it.
 *
 * `available` is the thing the legend expresses by simply having no row: the
 * layer cannot draw yet, because the session has not reached it (the weekly VWAP
 * with no seed, CVD before the tape tags an aggressor, the IB before the hour is
 * up). The catalogue lists it anyway — switching something on ahead of the tape
 * is legitimate — and says so, because "I turned it on and nothing happened" is
 * otherwise indistinguishable from a bug.
 */
export interface LayerState {
  key: ReplayLayerKey;
  on: boolean;
  available: boolean;
}
