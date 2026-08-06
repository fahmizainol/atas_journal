// Lean lightweight-charts wrapper for the Trade Simulator. Unlike the shared
// CandlestickChart (which paints a fully-precomputed session once via setData),
// this one is streaming-native: the page's playback loop feeds it `applyStep`
// each frame and it `.update()`s only the changed tail. Imperative by design —
// the ref handle is how the playback loop talks to it without a React render per
// frame.
//
// It carries the same layers and the same tools as a strategy chart, because the
// point of the replay is to practise the read you'd make there: the developing
// anchored VWAP bands (Globex, NY, and the seeded weekly) with the two
// developing value areas, the Initial Balance, a viewport volume profile, and
// the ⚓ / fixed-range-profile / ruler tools. Prior sessions can be drawn to the
// left as context — the same tape, so they profile like any other bar, but
// nothing develops over them. Two layers exist only here, and both are reading
// aids rather than signals: the multi-session composite over those context days
// (see lib/compositeProfile — frozen at the prior close by construction, since
// the engine develops nothing over them), and the tape-event bands the engine
// publishes (see TapeEvent). Everything is fed from the tape (see
// replayEngine), so a level here is the level the engine would have traded —
// nothing is reconstructed from bars. Two consequences worth knowing:
//
//   - the ⚓ VWAP's σ is tick-derived here, where CandlestickChart's is
//     bar-derived; this one matches the sim engine, that one matches the
//     journal's Databento bars;
//   - the IB *develops*. A strategy chart only draws a completed window; a
//     replay is a session in progress and watching the hour form is the point.

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { compositePalette, ibPalette, palette, profilePalette, vwapPalette } from "../../theme";
import type {
  BandPt,
  Bar,
  BigTrade,
  IbBox,
  ProfilePt,
  Snapshot,
  StepResult,
  Tape,
  TapeEvent,
} from "../../lib/replayEngine";
import type { TapeRange } from "../../lib/volumeProfile";
import type { VwapPoint } from "../../lib/chartTypes";
import {
  IndicatorLegend,
  type IndicatorKey,
  type IndicatorSettingsMap,
  type LegendItem,
} from "./IndicatorLegend";
import { ChartToolButton } from "./ChartToolButton";
import {
  loadIndicatorVisibility,
  saveIndicatorVisibility,
  type IndicatorVisibility,
} from "../../lib/chartPrefs";
import { VwapBandPrimitive } from "./VwapBandPrimitive";
import { VolumeProfilePrimitive } from "./VolumeProfilePrimitive";
import { RangeProfilePrimitive } from "./RangeProfilePrimitive";
import { RulerPrimitive } from "./RulerPrimitive";
import { PositionPrimitive, type PosHit, type PositionData } from "./PositionPrimitive";
import {
  OrdersPrimitive,
  type OrderHit,
  type WorkingOrderView,
} from "./OrdersPrimitive";
import { TradesPrimitive, type TradeMarkView } from "./TradesPrimitive";
import { BigTradePrimitive } from "./BigTradePrimitive";
import { CompositeProfilePrimitive } from "./CompositeProfilePrimitive";
import { DevelopingProfilePrimitive } from "./DevelopingProfilePrimitive";
import { EventBandPrimitive } from "./EventBandPrimitive";
import { DEFAULT_BIG_LOTS } from "../../lib/replayEngine";
import {
  LiveTapeProfile,
  computeTapeProfile,
  profileNodes,
  type ProfileNodes,
  type VolumeProfile,
} from "../../lib/volumeProfile";
import {
  buildComposite,
  type Composite,
  type CompositeRule,
} from "../../lib/compositeProfile";
import { COARSE_POINTER, byPointer } from "../../lib/pointer";

const VOL_UP = "rgba(33,192,122,0.5)";
const VOL_DOWN = "rgba(245,69,95,0.5)";

/** How tall one row of the volume profile is, in points — two ticks on NQ. The
 *  tape can be read at its own tick, but four rows to the point draw as a comb;
 *  pairing them keeps the shape of a shelf without smoothing it into one. Every
 *  profile on this chart uses it: the viewport one and each fixed-range one. */
const PROFILE_BIN = 0.5;

/** The open position as the page knows it. The chart fills in the rest of what
 *  the overlay needs (tick size, $/point, the mark price) from the tape and the
 *  playback it is already being fed. */
export interface PositionLine {
  side: "long" | "short";
  size: number;
  entry: number;
  stop: number | null;
  target: number | null;
  /** Bar time (epoch seconds) of the entry — where the risk/reward zones start. */
  entryTime: number;
}

/** The bracket a chart-placed order is measured with — the page's ticket, shown
 *  inside the long-press menu so a whole order can be built without opening it. */
export interface TicketDraft {
  size: number;
  stopTicks: number;
  targetTicks: number;
}

/** An order chosen outright rather than inferred from which side of the market
 *  was clicked: the long-press menu names the type and the side. */
export interface TypedOrder {
  price: number;
  type: "limit" | "stop";
  side: "long" | "short";
}

export interface ReplayChartHandle {
  /** Hand over the decoded session (or null on unload). Clears every hand-drawn
   *  tool — a new day is a new chart, and a profile dragged over yesterday means
   *  nothing today. `keepTools` is for the one swap that isn't a new day: the
   *  same session with more context days glued in front of it. */
  setTape(
    tape: Tape | null,
    opts?: {
      keepTools?: boolean;
      /** The context days' stretches on this tape, oldest first — what the
       *  composite is built over. Empty when no prior days are drawn. */
      contextRanges?: TapeRange[];
    },
  ): void;
  /** Re-cut the context days without re-handing the tape — the composite span
   *  changing (RTH, or Globex too) is the same days measured differently. A
   *  no-op when they are the spans already in hand. */
  setContextRanges(ranges: TapeRange[]): void;
  /** Repaint everything as of a clock. How the viewport is treated:
   *  - `true` (default) — snap to the tail at a fixed zoom. A load: nobody has
   *    chosen a zoom yet, so the replay opens on the last bars.
   *  - `"follow"` — keep the user's zoom and track the playhead. A seek: the
   *    clock moved, so the view goes with it, but at the bar spacing they set.
   *  - `false` — leave the view where it is. A re-anchor or a threshold change:
   *    nothing moved through time, so neither does the viewport. */
  setSnapshot(s: Snapshot, opts?: { reframe?: boolean | "follow" }): void;
  applyStep(r: StepResult): void;
  setPosition(p: PositionLine | null): void;
  /** The limit orders still working, with their levels resolved to prices. */
  setOrders(orders: WorkingOrderView[]): void;
  /** Every trade closed so far this replay — the full list as of the clock, so
   *  a rewind simply hands back the shorter one. */
  setTrades(trades: TradeMarkView[]): void;
  /** Drop any measurement on the chart. The page calls this when the bar grid
   *  changes under it: a ruler reads "n bars", and those bars are gone. */
  clearRuler(): void;
}

interface Props {
  /** Fixed pixel height. Omit to fill the parent box, which is what the
   *  Simulator does — the page hands the chart whatever viewport is left over
   *  after its controls, and the chart's own autoSize keeps up with it. */
  height?: number;
  /** The ⚓ tool moved (a bar time in epoch seconds) or was cleared. The page
   *  owns the engine, so it relays this to `ReplayEngine.setAnchor` and hands
   *  back a fresh snapshot — the chart never touches the tape itself. */
  onAnchorChange?: (barTime: number | null) => void;
  /** A stop or target was dragged to a new price and released. Fires once, on
   *  release: the page's trade log records *when* a bracket moved, and a stamp
   *  per mouse-move would be noise. */
  onBracketChange?: (b: { stop: number | null; target: number | null }) => void;
  /** The ✕ on the position chip was clicked — close at market. */
  onFlatten?: () => void;
  /** A working order's resting price (or one of its bracket legs) was dragged
   *  and released. Same contract as `onBracketChange`: the chart has already
   *  drawn it where it landed and clamped it somewhere it couldn't fill on the
   *  spot, so the page only has to record *when* it moved. */
  onOrderMove?: (o: {
    id: number;
    price: number;
    stop: number | null;
    target: number | null;
  }) => void;
  /** The ✕ on a working order's chip was clicked. */
  onOrderCancel?: (id: number) => void;
  /** Space was held and a mouse button clicked at a price. Which button, not
   *  which order — what a click *means* is a trading decision and belongs to the
   *  page; the chart only knows where the pointer was and which side it came
   *  down on. */
  onPlaceOrder?: (price: number, button: "left" | "right") => void;
  /** An order built in the long-press menu, where the type and the side were
   *  named rather than derived. Distinct from `onPlaceOrder` on purpose: that
   *  one hands over a gesture for the page to interpret, this one hands over a
   *  decision the user already made. */
  onPlaceTyped?: (o: TypedOrder) => void;
  /** The page's order ticket, so the menu can show and edit it in place. */
  ticket?: TicketDraft;
  onTicketChange?: (t: TicketDraft) => void;
  /** The mark, for the menu's own use: which of the four order types a price can
   *  legally be is a question about where the market is. The overlays get theirs
   *  from the playback (see `mark`), which never re-renders — this one has to. */
  mark?: number;
  /** Whether there is anything to place an order into yet. False before the
   *  replay is ready, and then the gesture goes dead rather than swallowing
   *  clicks. An open position is *not* a reason to refuse: the position is
   *  netted, so a further order scales it, takes size off it, or flips it. */
  canPlaceOrders?: boolean;
  /** Strip the calendar off the time axis — the labels and the crosshair read as
   *  a wall clock and nothing else. For the page's blind replay: a session runs
   *  through midnight, so the axis would otherwise name the day at the boundary,
   *  and the crosshair names it wherever you point. */
  hideDates?: boolean;
  /** Name seconds on the time axis and in the crosshair. For bars shorter than a
   *  minute — a 30s bar, or a tick bar, which is sub-minute most of the time —
   *  where an hh:mm axis would label several bars identically. */
  secondsAxis?: boolean;
  /** The lot threshold the big-trade marks were derived at. The engine decides
   *  which sweeps exist; the chart needs the number too, to scale the bubbles
   *  from it and to say on the legend row what "big" currently means. */
  bigLots?: number;
  /** How the context days are grouped into one composite profile — "off" draws
   *  none. Unlike the layers above, this one is computed here rather than in the
   *  engine: it is a fact about the days *before* the replay, which the engine
   *  deliberately develops nothing over. */
  composite?: CompositeRule;
  /** Prominence floor for the composite's HVN/LVN nodes, as a share of its
   *  tallest hump. Zero leaves the node reader off. */
  nodeProm?: number;
  /** Strength floor for the tape-event bands, in units of each kind's own
   *  threshold. Zero leaves the layer off. */
  eventStrength?: number;
  /** The knobs above, as the page offers them back to the user — hung off the
   *  "…" on the legend row each one belongs to. The chart doesn't own any of
   *  this state (it arrives as the props above and leaves through these
   *  callbacks); it only knows which row each knob goes on. */
  indicatorSettings?: IndicatorSettingsMap;
}

type BandKey = "mid" | "u1" | "l1" | "u2" | "l2";
const BAND_KEYS: BandKey[] = ["mid", "u1", "l1", "u2", "l2"];
type ProfKey = "vah" | "val" | "poc";
const PROF_KEYS: ProfKey[] = ["vah", "val", "poc"];

/** The bar a time sits on (or the nearest one). Bars are strictly ascending, so
 *  it is a plain binary search — used to hold a viewport across a `setData` that
 *  changed how many bars come *before* the ones already on screen. */
const idxOfTime = (bars: Bar[], time: number): number => {
  let lo = 0;
  let hi = bars.length - 1;
  if (hi < 0) return -1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time < time) lo = mid + 1;
    else hi = mid;
  }
  return lo;
};

/** How many of each kind of event clear the floor — the legend rows say so, and
 *  a layer with nothing on the chart doesn't get a toggle. */
const countEvents = (events: TapeEvent[]): { sweep: number; absorb: number } => {
  let sweep = 0;
  let absorb = 0;
  for (const e of events) {
    if (e.kind === "sweep") sweep++;
    else absorb++;
  }
  return { sweep, absorb };
};

/** A (fractional, possibly out-of-range) logical index, clamped onto a real bar. */
const clampIdx = (bars: Bar[], at: number): number =>
  bars.length ? Math.max(0, Math.min(bars.length - 1, Math.round(at))) : -1;

/** One fixed-range profile the user has drawn, bounded by bar times. */
interface RangeSel {
  id: number;
  from: number;
  to: number;
}
/** What a press grabbed: a fresh drag, an edge to resize, or the body to move. */
type DragMode = "new" | "left" | "right" | "move";
/** How close (px) the pointer must be to an edge to grab it rather than the body.
 *  Wider for a fingertip — see lib/pointer. */
const HANDLE_PX = byPointer(6, 20);
/** How far a press has to travel before it counts as a drag rather than a click.
 *  A finger never holds still, so the mouse's 5px would turn most taps into
 *  zero-width drags. */
const DRAG_SLOP = byPointer(5, 12);
/** How long a press has to stay put before it means "put the order ＋ here"
 *  rather than "pan". Long enough not to fire on a flick, short enough that you
 *  don't wonder whether the chart heard you. */
const LONG_PRESS_MS = 420;
/** Room the anchor keeps from the top and bottom edges, so what hangs off it
 *  stays on screen: just the ＋, or the whole menu. */
const ANCHOR_MARGIN = byPointer(16, 20);
const MENU_MARGIN = byPointer(108, 132);
/** Where the ticket goes when it opens. On a mouse it hangs off the ＋, centred
 *  on the price — a small panel on a big screen, next to the level it is for.
 *  On a fingertip it docks to the foot of the chart instead: the same panel on a
 *  phone is a slab sitting on the one price you summoned it to read, and the
 *  hand that opened it is over the rest. The ＋ stays on the axis either way, so
 *  which level the ticket belongs to is still on screen. */
const DOCK_MENU = COARSE_POINTER;

/** The four resting orders, and which side of the mark each one may sit on. A
 *  bid rests under the market and an offer over it; a stop is the other way
 *  round, because it is the order you have to be run through to fill. */
const ORDER_KINDS: {
  type: "limit" | "stop";
  side: "long" | "short";
  label: string;
  above: boolean;
}[] = [
  { type: "limit", side: "long", label: "Buy Limit", above: false },
  { type: "limit", side: "short", label: "Sell Limit", above: true },
  { type: "stop", side: "long", label: "Buy Stop", above: true },
  { type: "stop", side: "short", label: "Sell Stop", above: false },
];

/** The long-press ticket. Everything an order needs — which of the four it is,
 *  how big, and how far its stop and target sit from the fill — in one panel
 *  hanging off the price it was summoned at, so a trade can be built without
 *  the pointer ever leaving the chart. */
function OrderMenu({
  price,
  mark,
  tick,
  ticket,
  docked,
  onTicket,
  onPlace,
  onNudge,
  onClose,
}: {
  price: number;
  mark: number;
  tick: number;
  ticket: TicketDraft;
  /** Docked at the foot of the chart rather than hung off the ＋ — the same
   *  controls, laid out wide and shallow because that is the shape of the strip
   *  it now has to fit in. */
  docked: boolean;
  onTicket: (t: TicketDraft) => void;
  onPlace: (o: TypedOrder) => void;
  onNudge: (d: number) => void;
  onClose: () => void;
}) {
  const known = Number.isFinite(mark);
  const field = (
    key: keyof TicketDraft,
    label: string,
    min: number,
    title: string,
  ) => (
    <label className="replay-omenu-f" title={title}>
      {label}
      <input
        type="number"
        min={min}
        // A bracket leg is optional, and zero is how the ticket says "not
        // attached" — shown as an empty box reading "none" rather than a 0,
        // which would look like a stop right on the fill. Clearing it by hand
        // says the same thing.
        value={ticket[key] === 0 ? "" : ticket[key]}
        placeholder={min === 0 ? "none" : undefined}
        onChange={(e) => onTicket({ ...ticket, [key]: Math.max(min, Number(e.target.value)) })}
      />
    </label>
  );
  return (
    <div className={`replay-omenu${docked ? " docked" : ""}`} role="menu">
      <div className="replay-omenu-kinds">
        {ORDER_KINDS.map((k) => {
          // Greyed rather than hidden: which two are available is the thing this
          // menu is teaching, and a menu that reshuffles under the pointer as the
          // tape crosses the price would be unusable.
          const ok = known && (k.above ? price > mark : price < mark);
          return (
            <button
              key={k.label}
              type="button"
              role="menuitem"
              className={`replay-okind ${k.side}`}
              disabled={!ok}
              onClick={() => onPlace({ price, type: k.type, side: k.side })}
              title={
                ok
                  ? `${k.label} at ${price.toFixed(2)}`
                  : `A ${k.label.toLowerCase()} has to sit ${k.above ? "above" : "below"} the market`
              }
            >
              {k.label}
            </button>
          );
        })}
      </div>
      {/* The sizing and the price: stacked when the panel hangs off the ＋, side
          by side when it is docked. The wrapper is the only thing the wide
          layout needs to turn one into the other. */}
      <div className="replay-omenu-foot">
        <div className="replay-omenu-row">
          {field("size", "Size", 1, "Contracts")}
          {field("stopTicks", "SL", 0, "Stop, in ticks from the fill — optional, leave empty for none")}
          {field("targetTicks", "TP", 0, "Target, in ticks from the fill — optional, leave empty for none")}
        </div>
        <div className="replay-omenu-px">
          <button type="button" onClick={() => onNudge(-tick)} title={`Down one tick (${tick})`}>
            ▾
          </button>
          <span>{price.toFixed(2)}</span>
          <button type="button" onClick={() => onNudge(tick)} title={`Up one tick (${tick})`}>
            ▴
          </button>
          <button type="button" className="replay-omenu-x" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>
      </div>
    </div>
  );
}

// One anchor's drawn state: the five σ lines, the shaded ±1σ→±2σ fill, and the
// points both are drawn from (kept here so the fill can be re-pointed each step).
interface Anchor {
  lines: Record<BandKey, ISeriesApi<"Line">>;
  band: VwapBandPrimitive;
  pts: VwapPoint[];
}

// The engine's compact band point → the shared VwapPoint the fill primitive and
// the journal charts speak.
const toVwapPoint = (p: BandPt): VwapPoint => ({
  time: p.time,
  middle: p.mid,
  upper1: p.u1,
  lower1: p.l1,
  upper2: p.u2,
  lower2: p.l2,
});

export const ReplayChart = forwardRef<ReplayChartHandle, Props>(function ReplayChart(
  {
    height,
    onAnchorChange,
    onBracketChange,
    onFlatten,
    onOrderMove,
    onOrderCancel,
    onPlaceOrder,
    onPlaceTyped,
    ticket,
    onTicketChange,
    mark: markProp = NaN,
    canPlaceOrders = true,
    hideDates = false,
    secondsAxis = false,
    bigLots = DEFAULT_BIG_LOTS,
    composite = "off",
    nodeProm = 0,
    eventStrength = 0,
    indicatorSettings,
  },
  ref,
) {
  const elRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const gRef = useRef<Anchor | null>(null);
  const nRef = useRef<Anchor | null>(null);
  const aRef = useRef<Anchor | null>(null);
  const wkRef = useRef<Anchor | null>(null);
  const gProfRef = useRef<Record<ProfKey, ISeriesApi<"Line">> | null>(null);
  const nProfRef = useRef<Record<ProfKey, ISeriesApi<"Line">> | null>(null);

  // The open position overlay. The primitive is created here rather than in the
  // build effect because the imperative handle (which lives outside it) is what
  // feeds it; attaching to the series still happens in there.
  const posPrimRef = useRef<PositionPrimitive | null>(null);
  if (!posPrimRef.current) posPrimRef.current = new PositionPrimitive();
  const posRef = useRef<PositionData | null>(null);
  // The working orders, same arrangement: the page owns the log, the chart owns
  // what a drag is doing to a price until the pointer is released.
  const ordPrimRef = useRef<OrdersPrimitive | null>(null);
  if (!ordPrimRef.current) ordPrimRef.current = new OrdersPrimitive();
  const workingRef = useRef<WorkingOrderView[]>([]);
  // The closed trades, kept the same way — the page derives them from its log,
  // the chart only draws them.
  const tradesPrimRef = useRef<TradesPrimitive | null>(null);
  if (!tradesPrimRef.current) tradesPrimRef.current = new TradesPrimitive();
  const tradesRef = useRef<TradeMarkView[]>([]);
  const [tradeCount, setTradeCount] = useState(0);
  // The tape's own big trades. Same arrangement again — the engine derives them
  // from the tape, the chart only draws them — except the list is grown by the
  // playback tails rather than re-handed whole on every fill.
  const bigPrimRef = useRef<BigTradePrimitive | null>(null);
  if (!bigPrimRef.current) bigPrimRef.current = new BigTradePrimitive();
  const bigsRef = useRef<BigTrade[]>([]);
  const [bigCount, setBigCount] = useState(0);

  // The tape's events, kept the same way as the big trades — but merged on
  // (kind, idx) rather than by position, because either of the two open events
  // can grow while the other publishes past it.
  const evPrimRef = useRef<EventBandPrimitive | null>(null);
  if (!evPrimRef.current) evPrimRef.current = new EventBandPrimitive();
  const eventsRef = useRef<TapeEvent[]>([]);
  const evPosRef = useRef(new Map<string, number>());
  /** How many events of each kind are actually *drawn* — the list filtered by
   *  the strength floor, which is the number the legend has to quote. It moves
   *  when the list grows, when an open event grows past the floor, and when the
   *  floor itself changes, so it is recomputed rather than counted up. */
  const [evCount, setEvCount] = useState({ sweep: 0, absorb: 0 });

  // The composite over the context days. Frozen at the prior close by
  // construction — it is built from the history stretch, which the engine
  // develops nothing over — so it is rebuilt only when those days change, never
  // on a playback step.
  const compPrimRef = useRef<CompositeProfilePrimitive | null>(null);
  if (!compPrimRef.current) compPrimRef.current = new CompositeProfilePrimitive();
  /** The context days' stretches, as tick spans on the current tape — RTH, or
   *  Globex-and-RTH, as the page cut them. */
  const ctxRangeRef = useRef<TapeRange[]>([]);
  const compRef = useRef<Composite | null>(null);
  /** The viewport profile, which also carries the event marginal. Owned by the
   *  build effect; held here so the filtered list can reach it. */
  const vpRef = useRef<VolumeProfilePrimitive | null>(null);
  /** Nodes at the current prominence, cached: the reading changes only when the
   *  composite or the knob does, and both are rare. */
  const compNodesRef = useRef<ProfileNodes | null>(null);
  /** How many of the drawn bars are context — the span the histogram pins to. */
  const histCountRef = useRef(0);

  // The developing NY profile: the same distribution the NY VAH/POC/VAL lines
  // come from, drawn as a histogram in its own gutter. Recomputed off the tape
  // on each bar close rather than carried in the playback tails — a session's
  // histogram is thousands of rows, and shipping it per frame to redraw a gutter
  // would cost more than reading it back does.
  const devPrimRef = useRef<DevelopingProfilePrimitive | null>(null);
  if (!devPrimRef.current) devPrimRef.current = new DevelopingProfilePrimitive();
  /** Bar time of the session's first NY bar — the profile's left edge. NaN
   *  before the bell, and again after a rewind past it. */
  const nyStartRef = useRef(NaN);
  /** The NY value area as of the clock, straight off the engine — what the
   *  histogram shades by, so it and the VAH/POC/VAL lines can't disagree. */
  const nyVaRef = useRef<ProfilePt | null>(null);
  /** Sessions in the composite as drawn, 0 when there is none. The one thing
   *  about it React needs: under the balance rule the count is a *reading* (this
   *  is how long the auction has been running), so the legend says it. */
  const [compDays, setCompDays] = useState(0);
  /** Context days handed in, whatever the composite rule then made of them. The
   *  composite's row is drawn off *this* rather than off `compDays`, because the
   *  rule itself now lives on that row: gating the row on a composite being
   *  drawn would take the "off" switch away with the thing it switched off. */
  const [ctxDays, setCtxDays] = useState(0);
  // Mirrored for the build effect and the imperative handle, neither of which
  // re-runs when the prop changes.
  const bigLotsRef = useRef(bigLots);
  bigLotsRef.current = bigLots;
  const compositeRef = useRef(composite);
  compositeRef.current = composite;
  const nodePromRef = useRef(nodeProm);
  nodePromRef.current = nodeProm;
  const eventStrengthRef = useRef(eventStrength);
  eventStrengthRef.current = eventStrength;
  // Mark price, mirrored off the playback so the position chip's open P&L moves
  // with the tape without a React render per frame.
  const lastPriceRef = useRef<number>(NaN);

  // The bars as drawn, mirrored from the snapshots/tails so the tools can hit-test
  // and index into the tape. Bar objects are the engine's own (a forming bar keeps
  // mutating), which is exactly what the profile wants — its `i1` stays current.
  const barsRef = useRef<Bar[]>([]);
  const tapeRef = useRef<Tape | null>(null);
  const ibRef = useRef<IbBox | null>(null);

  const onAnchorRef = useRef(onAnchorChange);
  onAnchorRef.current = onAnchorChange;
  const onBracketRef = useRef(onBracketChange);
  onBracketRef.current = onBracketChange;
  const onFlattenRef = useRef(onFlatten);
  onFlattenRef.current = onFlatten;
  const onOrderMoveRef = useRef(onOrderMove);
  onOrderMoveRef.current = onOrderMove;
  const onOrderCancelRef = useRef(onOrderCancel);
  onOrderCancelRef.current = onOrderCancel;
  const onPlaceOrderRef = useRef(onPlaceOrder);
  onPlaceOrderRef.current = onPlaceOrder;

  // --- the long-press order ＋ ------------------------------------------------
  // A press held still on the tape puts a ＋ on the price axis at that price;
  // tapping the ＋ opens the ticket. Two steps rather than one because the first
  // is a gesture you can make by accident and the second never is — and because
  // the ＋ sitting on the axis, at the price, is the confirmation that the chart
  // heard the level you meant before you commit to an order at it.
  //
  // The price is the state; the pixel it sits at is not. A replay pans and
  // rescales continuously under it, so where the ＋ is drawn is re-derived from
  // the price every frame (see the effect below) rather than frozen at the
  // coordinate the press landed on.
  const [plusPrice, setPlusPrice] = useState<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const plusRef = useRef<number | null>(null);
  plusRef.current = plusPrice;
  const menuOpenRef = useRef(false);
  menuOpenRef.current = menuOpen;
  const anchorRef = useRef<HTMLDivElement>(null);
  const openPlus = (price: number, menu = false) => {
    setPlusPrice(price);
    setMenuOpen(menu);
  };
  const closePlus = () => {
    setPlusPrice(null);
    setMenuOpen(false);
  };
  const openPlusRef = useRef(openPlus);
  openPlusRef.current = openPlus;
  const closePlusRef = useRef(closePlus);
  closePlusRef.current = closePlus;

  // Push whatever `posRef` now holds at the overlay. Called from the handle when
  // the position changes, from the playback when the mark moves, and on every
  // frame of a bracket drag.
  const pushPos = () => posPrimRef.current?.setData(posRef.current);
  const pushOrders = () =>
    ordPrimRef.current?.setOrders(workingRef.current, tapeRef.current?.tickSize);
  const pushTrades = () =>
    tradesPrimRef.current?.setTrades(tradesRef.current, tapeRef.current?.tickSize);

  // Hand the composite to its primitive, pinned to the context bars it was
  // measured over. Cheap — the profile is already built and the nodes are cached
  // until the prominence knob moves — so it can be called from anywhere the
  // drawn bars change.
  const paintComposite = () => {
    const c = compRef.current;
    const bars = barsRef.current;
    const n = histCountRef.current;
    if (!c || n <= 0 || bars.length < n) {
      compPrimRef.current?.setData(null);
      setCompDays(0);
      return;
    }
    setCompDays(c.days);
    const prom = nodePromRef.current;
    if (prom > 0 && !compNodesRef.current) compNodesRef.current = profileNodes(c.profile, prom);
    compPrimRef.current?.setData({
      profile: c.profile,
      nodes: prom > 0 ? compNodesRef.current : null,
      from: bars[0].time,
      to: bars[n - 1].time,
      days: c.days,
    });
  };

  // Rebuild it from the tape. The expensive half — a scan of every context day's
  // RTH ticks, plus one more per day the balance rule considers — and the rare
  // one: a new session, a change to how many days are drawn, or a change of
  // rule. Never a playback step; the composite is frozen at the prior close.
  const rebuildComposite = () => {
    const tape = tapeRef.current;
    setCtxDays(ctxRangeRef.current.length);
    compRef.current =
      tape && ctxRangeRef.current.length
        ? buildComposite(
            tape.level,
            tape.size,
            ctxRangeRef.current,
            tape.tickSize,
            compositeRef.current,
          )
        : null;
    compNodesRef.current = null;
    paintComposite();
  };
  // The tape moved: re-price the open position off the new mark, and re-measure
  // how far the market still has to come to fill what's working.
  const mark = (v: number) => {
    if (!Number.isFinite(v) || v === lastPriceRef.current) return;
    lastPriceRef.current = v;
    ordPrimRef.current?.setMark(v);
    if (!posRef.current) return;
    posRef.current = { ...posRef.current, last: v };
    pushPos();
  };

  // Hide/show per indicator, sharing the journal charts' sticky preference: a
  // band hidden on a strategy chart comes up hidden here, and vice versa.
  const [vis, setVis] = useState<IndicatorVisibility>(loadIndicatorVisibility);
  const visRef = useRef(vis);
  visRef.current = vis;
  const applyRef = useRef<((v: IndicatorVisibility) => void) | null>(null);
  const toggle = (key: IndicatorKey) =>
    setVis((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      visRef.current = next;
      applyRef.current?.(next);
      saveIndicatorVisibility(next);
      return next;
    });
  // Force a layer on. Hide/show is a sticky global preference, so a layer hidden
  // on some other chart stays hidden here — fine for the fixed overlays, wrong
  // for one the user just asked for by hand (the ⚓).
  const reveal = (key: IndicatorKey) => {
    if (visRef.current[key]) return;
    const next = { ...visRef.current, [key]: true };
    visRef.current = next;
    applyRef.current?.(next);
    saveIndicatorVisibility(next);
    setVis(next);
  };
  const revealRef = useRef(reveal);
  revealRef.current = reveal;

  // Which layers have actually printed at the current clock — the Globex band is
  // absent on a day with no overnight tape, the NY band doesn't exist until the
  // bell, and the IB doesn't exist before it either. Tracked in a ref alongside
  // the state so the per-frame path only re-renders on the step where one comes
  // to life. A toggle for a line that can't draw is a lie.
  const emptyPresent = { bars: false, g: false, n: false, wk: false, gp: false, np: false, ib: false };
  const presentRef = useRef(emptyPresent);
  const [present, setPresent] = useState(emptyPresent);
  const syncPresent = (next: typeof emptyPresent) => {
    const p = presentRef.current;
    if ((Object.keys(next) as (keyof typeof next)[]).every((k) => p[k] === next[k])) return;
    presentRef.current = next;
    setPresent(next);
  };

  // --- hand-drawn tools -----------------------------------------------------
  // Everything below is mirrored into refs so the mouse handlers inside the build
  // effect can read it without becoming effect deps: arming a tool or moving a
  // profile must not rebuild the chart (that would lose zoom, scroll, and the
  // streamed history). React state exists only to render the toolbar.

  // Fixed-range profile. Ranges are stored as bar *times*, so a rewind that drops
  // their bars doesn't corrupt them — they simply stop drawing until the replay
  // reaches them again.
  const [armed, setArmed] = useState(false);
  const [ranges, setRanges] = useState<RangeSel[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const armedRef = useRef(false);
  const rangesRef = useRef<RangeSel[]>([]);
  const selectedRef = useRef<number | null>(null);
  const nextIdRef = useRef(1);
  const armApplyRef = useRef<((a: boolean) => void) | null>(null);
  const paintRef = useRef<(() => void) | null>(null);
  const paintDevRef = useRef<(() => void) | null>(null);

  // Ruler / measure tool.
  const [rulerArmed, setRulerArmed] = useState(false);
  const rulerArmedRef = useRef(false);
  const rulerApplyRef = useRef<((a: boolean) => void) | null>(null);
  // Returns whether it actually dismissed something, so the Escape handler can
  // tell a consumed key from an ignored one.
  const rulerClearRef = useRef<() => boolean>(() => false);

  // Anchored-VWAP tool. Unlike CandlestickChart's, the anchor lives in the engine
  // — so it keeps developing as the replay runs instead of being a fixed picture.
  const [avwapArmed, setAvwapArmed] = useState(false);
  const [avwapAnchor, setAvwapAnchor] = useState<number | null>(null);
  const avwapArmedRef = useRef(false);
  const avwapApplyRef = useRef<((a: boolean) => void) | null>(null);

  // Order placement is a held modifier rather than a tool: hold Space and click
  // a price. Nothing to arm and nothing to leave, which is what you want when
  // the decision to place is made in the second the tape gives you — the chart
  // trader's gesture, not a drawing tool's.
  const [spaceHeld, setSpaceHeld] = useState(false);
  const spaceRef = useRef(false);
  const spaceApplyRef = useRef<((on: boolean) => void) | null>(null);
  const canOrderRef = useRef(canPlaceOrders);
  canOrderRef.current = canPlaceOrders;
  const setSpace = (on: boolean) => {
    if (on === spaceRef.current || (on && !canOrderRef.current)) return;
    spaceRef.current = on;
    setSpaceHeld(on);
    spaceApplyRef.current?.(on);
  };
  const setSpaceRef = useRef(setSpace);
  setSpaceRef.current = setSpace;
  // Whether the pointer is over the chart. Space only becomes the placing
  // modifier when it is — otherwise pressing it after clicking Play would just
  // re-trigger that button, which is what a focused button does with Space and
  // is the right thing everywhere except here.
  const overRef = useRef(false);

  // The same gesture for a device with no keyboard and no second mouse button:
  // arm the tool, pick which channel, tap a price. It is a worse gesture than
  // Space+click — two taps of setup instead of none — so it does not replace it,
  // it sits beside it. Whichever hardware you have, one of them works.
  //
  // It disarms itself after placing, which Space deliberately doesn't. On a
  // mouse the modifier is held, so letting go *is* the disarm and there is never
  // a moment where the chart is quietly armed; a tool has no such moment, and a
  // chart left armed on a touchscreen turns the next stray tap into an order.
  const [orderArmed, setOrderArmed] = useState(false);
  const orderArmedRef = useRef(false);
  const orderApplyRef = useRef<((on: boolean) => void) | null>(null);
  // Which of the two channels a tap means. Named for the mouse buttons rather
  // than for limit/stop on purpose — see onPlaceOrder: what a click *means* is
  // the page's decision, and the mapping flips across the market.
  const [orderSide, setOrderSide] = useState<"left" | "right">("left");
  const orderSideRef = useRef<"left" | "right">("left");
  orderSideRef.current = orderSide;

  // One drag/click tool owns the pointer at a time.
  const armRulerRef = useRef<(v: boolean) => void>(() => {});
  const armAvwapRef = useRef<(v: boolean) => void>(() => {});
  const armOrderRef = useRef<(v: boolean) => void>(() => {});
  const arm = (v: boolean) => {
    if (v) {
      armRulerRef.current(false);
      armAvwapRef.current(false);
      armOrderRef.current(false);
    }
    armedRef.current = v;
    setArmed(v);
    armApplyRef.current?.(v);
  };
  const armRuler = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armAvwapRef.current(false);
      armOrderRef.current(false);
    }
    rulerArmedRef.current = v;
    setRulerArmed(v);
    rulerApplyRef.current?.(v);
  };
  armRulerRef.current = armRuler;
  const armAvwap = (v: boolean) => {
    if (v) {
      if (armedRef.current) arm(false);
      armRulerRef.current(false);
      armOrderRef.current(false);
    }
    avwapArmedRef.current = v;
    setAvwapArmed(v);
    avwapApplyRef.current?.(v);
  };
  armAvwapRef.current = armAvwap;
  const armOrder = (v: boolean) => {
    if (v) {
      if (!canOrderRef.current) return;
      if (armedRef.current) arm(false);
      armRulerRef.current(false);
      armAvwapRef.current(false);
    }
    orderArmedRef.current = v;
    setOrderArmed(v);
    orderApplyRef.current?.(v);
  };
  armOrderRef.current = armOrder;
  const clearAvwap = () => {
    setAvwapAnchor(null);
    onAnchorRef.current?.(null);
  };

  // Push whatever the refs now hold into both the chart and the toolbar. Called
  // once a drag settles, never mid-drag.
  const syncRanges = () => {
    setRanges([...rangesRef.current]);
    setSelected(selectedRef.current);
    paintRef.current?.();
  };
  const clearRanges = () => {
    rangesRef.current = [];
    selectedRef.current = null;
    syncRanges();
  };
  const deleteSelected = () => {
    if (selectedRef.current == null) return;
    rangesRef.current = rangesRef.current.filter((r) => r.id !== selectedRef.current);
    selectedRef.current = null;
    syncRanges();
  };
  const disarmRef = useRef<() => void>(() => {});
  const syncRef = useRef<() => void>(() => {});
  disarmRef.current = () => arm(false);
  syncRef.current = syncRanges;

  useEffect(() => {
    // Whether a key belongs to whatever the user is typing into rather than to
    // the chart.
    const busy = () => {
      const el = document.activeElement;
      return (
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement
      );
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        const armed =
          armedRef.current ||
          rulerArmedRef.current ||
          avwapArmedRef.current ||
          orderArmedRef.current;
        if (armedRef.current) arm(false);
        if (rulerArmedRef.current) armRulerRef.current(false);
        if (avwapArmedRef.current) armAvwapRef.current(false);
        if (orderArmedRef.current) armOrderRef.current(false);
        const dismissed = rulerClearRef.current(); // Esc also dismisses a finished measurement
        const hadPlus = plusRef.current != null;
        if (hadPlus) closePlusRef.current();
        // Mark the key spoken for when it actually cancelled something, so a
        // listener further out (the page's fullscreen exit) can stand down —
        // backing out of a tool shouldn't also tear down the whole view.
        if (armed || dismissed || hadPlus) e.preventDefault();
      }
      if (e.code === "Space" && overRef.current && !busy()) {
        // Space is the browser's page-scroll key (and a focused button's
        // trigger); here it is a held modifier, so take it before either. Auto-
        // repeat re-fires this while it's down — setSpace ignores the repeats.
        e.preventDefault();
        setSpaceRef.current(true);
      }
      // Don't hijack Delete while the user is typing somewhere on the page.
      if (
        (e.key === "Delete" || e.key === "Backspace") &&
        !busy() &&
        selectedRef.current != null
      ) {
        e.preventDefault();
        deleteSelected();
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") setSpaceRef.current(false);
    };
    // A key held while the window loses focus never delivers its keyup, and a
    // modifier stuck down would silently turn every later click into an order.
    const onBlur = () => setSpaceRef.current(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    applyRef.current?.(vis);
  }, [vis]);

  // Keep the ＋ on its price. lightweight-charts publishes no price-scale change
  // event, and the replay moves the scale every frame anyway, so this rides the
  // same clock the chart does. It only runs while something is summoned, and it
  // writes straight at the DOM — a React render per frame for a button that has
  // not changed would be the one expensive thing on the page.
  //
  // Also republishes the price axis' width, which the ＋ sits on and the menu
  // hangs off: it changes with the digits in the scale, not just with layout.
  useEffect(() => {
    if (plusPrice == null) return;
    let raf = 0;
    const paint = () => {
      raf = requestAnimationFrame(paint);
      const el = anchorRef.current;
      const s = candleRef.current;
      const c = chartRef.current;
      const host = elRef.current;
      if (!el || !s || !c || !host) return;
      const y = s.priceToCoordinate(plusPrice);
      // A price scrolled off the scale still has a menu attached to it, so the
      // anchor is clamped into view rather than hidden — the order it is about
      // to place is at the price, not at the pixel.
      // Only the undocked menu rides with the anchor and so needs the taller
      // margin; a docked one is at the foot of the chart and this row is back to
      // being just the ＋.
      const m = menuOpenRef.current && !DOCK_MENU ? MENU_MARGIN : ANCHOR_MARGIN;
      const h = host.clientHeight;
      const lo = Math.min(m, h / 2);
      const v = Math.max(lo, Math.min(h - lo, y ?? h / 2));
      // Centred on the price, not hung below it — with the menu open that centres
      // the menu, which is what the margin above was clamping for.
      el.style.transform = `translateY(calc(${v}px - 50%))`;
      el.style.setProperty("--axis-w", `${c.priceScale("right").width()}px`);
    };
    paint();
    return () => cancelAnimationFrame(raf);
  }, [plusPrice]);

  // Nothing to place into (no tape yet, or the replay hasn't started): put the
  // ticket away and drop any armed modifier, rather than leave a gesture up that
  // the next click would land on nothing.
  useEffect(() => {
    if (!canPlaceOrders) {
      closePlusRef.current();
      setSpaceRef.current(false);
      armOrderRef.current(false);
    }
  }, [canPlaceOrders]);

  // Bridge from the imperative handle into the build effect's closures — the
  // layers it repaints are all created (and destroyed) in there.
  const hooksRef = useRef<{
    reprofile: () => void;
    paint: () => void;
    syncIb: () => void;
    remakeRuler: (tape: Tape | null) => void;
    clearRuler: () => void;
  } | null>(null);

  useEffect(() => {
    if (!elRef.current) return;
    const chart = createChart(elRef.current, {
      // The library watches the container itself. Hand-rolling that watch is the
      // obvious thing and it does not work: resizing the chart re-lays-out the
      // element being observed, so Chrome treats it as a ResizeObserver feedback
      // loop and drops the *next* notification — the chart then tracks every
      // other resize and sits at a stale size in between. width/height below are
      // only the fallback the library uses if it can't observe at all.
      autoSize: true,
      width: elRef.current.clientWidth,
      height: height ?? elRef.current.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: palette.bg },
        textColor: palette.text,
        fontFamily: "Inter, sans-serif",
        fontSize: 9,
      },
      grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
      rightPriceScale: { borderColor: palette.grid },
      timeScale: {
        borderColor: palette.grid,
        timeVisible: true,
        secondsVisible: false,
        // The default, set explicitly because the replay's follow behaviour now
        // rests entirely on it: the range shifts with new bars only while the
        // last bar is on screen, so panning away parks the view where you left
        // it (see applyStep).
        shiftVisibleRangeOnNewBar: true,
      },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartRef.current = chart;

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: palette.green,
      downColor: palette.red,
      wickUpColor: palette.green,
      wickDownColor: palette.red,
      borderVisible: false,
    });
    candleRef.current = candle;

    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "" });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    volRef.current = vol;

    // One anchor = 5 lines (mid, ±1σ, ±2σ) plus the shaded ±1σ→±2σ fill, exactly
    // as the journal charts draw them (CandlestickChart.addVwap): same hues, same
    // dashed envelope, same wash — so a band read here reads the same there.
    const mkBand = (hue: { middle: string; band1: string; band2: string; fill: string }): Anchor => {
      const line = (color: string, key: BandKey) =>
        chart.addSeries(LineSeries, {
          color,
          lineWidth: key === "mid" ? 2 : 1,
          lineStyle: key === "mid" ? LineStyle.Solid : LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
      const band = new VwapBandPrimitive([], hue.fill);
      candle.attachPrimitive(band as any);
      return {
        lines: {
          mid: line(hue.middle, "mid"),
          u1: line(hue.band1, "u1"),
          l1: line(hue.band1, "l1"),
          u2: line(hue.band2, "u2"),
          l2: line(hue.band2, "l2"),
        } as Record<BandKey, ISeriesApi<"Line">>,
        band,
        pts: [],
      };
    };
    gRef.current = mkBand(vwapPalette.globex);
    nRef.current = mkBand(vwapPalette.ny);
    // The weekly anchor, in the same orange the strategy charts draw it in. It
    // is a *context* band here as it is there — nothing the replay does is
    // measured against it — and it only ever has points when the session shipped
    // a seed the week could be honestly built from.
    wkRef.current = mkBand(vwapPalette.weekly);
    // The ⚓ band is built empty and stays empty until the user anchors — no
    // create/destroy dance, the streaming path just starts finding points in it.
    aRef.current = mkBand(vwapPalette.anchored);

    // Developing value areas, one per anchor: VAH and VAL solid (they are the
    // levels the rules actually test against), POC dashed between them, each in
    // its anchor's colour. Deliberately not shaded bands — the VWAP envelope
    // already owns that visual, and stacking fills where the two areas overlap
    // (the whole setup) would be unreadable.
    const mkProfile = (pal: { edge: string; poc: string }) => {
      const line = (color: string, key: ProfKey) =>
        chart.addSeries(LineSeries, {
          color,
          lineWidth: 2,
          lineStyle: key === "poc" ? LineStyle.Dashed : LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
      return {
        vah: line(pal.edge, "vah"),
        val: line(pal.edge, "val"),
        poc: line(pal.poc, "poc"),
      } as Record<ProfKey, ISeriesApi<"Line">>;
    };
    gProfRef.current = mkProfile(profilePalette.globex);
    nProfRef.current = mkProfile(profilePalette.ny);

    // Initial Balance: high/low as flat segments from the bell to the live edge —
    // line series rather than price lines, because an IB doesn't exist over the
    // overnight candles and a full-pane line would draw it there. The extension
    // guides (±1×/1.5×/2× of the IB range) only appear once the hour completes,
    // and are excluded from autoscale: on a narrow-IB day they sit far outside
    // the traded range, and toggling them on must not crush the candles.
    const ibSeg = (color: string, guide: boolean) =>
      chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        lineStyle: guide ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        ...(guide ? { autoscaleInfoProvider: () => null } : {}),
      });
    const ibSeries = [ibSeg(ibPalette.line, false), ibSeg(ibPalette.line, false)];
    const ibExtSeries = [1, 1.5, 2].flatMap(() => [
      ibSeg(ibPalette.ext, true),
      ibSeg(ibPalette.ext, true),
    ]);

    // --- bar-grid helpers (the tools all speak bar times) --------------------
    const nearestIdx = (t: number): number => {
      const bars = barsRef.current;
      const last = bars.length - 1;
      if (last < 0) return -1;
      if (t <= bars[0].time) return 0;
      if (t >= bars[last].time) return last;
      let lo = 0;
      let hi = last;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (bars[mid].time === t) return mid;
        if (bars[mid].time < t) lo = mid + 1;
        else hi = mid - 1;
      }
      return t - bars[hi].time <= bars[lo].time - t ? hi : lo;
    };
    const nearestBar = (t: number): number => {
      const i = nearestIdx(t);
      return i < 0 ? t : barsRef.current[i].time;
    };

    // Every profile on this chart — the viewport-following one and each
    // fixed-range one — is a slice of bars, and a bar knows the ticks it was
    // built from, so they all resolve to a scan of the real tape.
    const profileFor = (i0: number, i1: number): VolumeProfile | null => {
      const bars = barsRef.current;
      const tape = tapeRef.current;
      if (!tape || i1 < i0 || i0 < 0 || i1 >= bars.length) return null;
      return computeTapeProfile(
        tape.level,
        tape.size,
        bars[i0].i0,
        bars[i1].i1,
        tape.tickSize,
        PROFILE_BIN,
      );
    };

    // The two profiles that have to keep up with the playhead read through their
    // own accumulator instead: the forming bar grows by a handful of ticks per
    // frame, and re-scanning the whole span for them would be the difference
    // between "redraw once a bar" and "redraw once a frame" (see LiveTapeProfile).
    // Same numbers as profileFor — the fast path only changes what it costs.
    const vpLive = new LiveTapeProfile();
    const devLive = new LiveTapeProfile();
    const liveProfileFor = (
      live: LiveTapeProfile,
      i0: number,
      i1: number,
    ): VolumeProfile | null => {
      const bars = barsRef.current;
      const tape = tapeRef.current;
      if (!tape || i1 < i0 || i0 < 0 || i1 >= bars.length) return null;
      return live.update(
        tape.level,
        tape.size,
        bars[i0].i0,
        bars[i1].i1,
        tape.tickSize,
        PROFILE_BIN,
      );
    };

    // Volume profile over whatever bars are on screen: the histogram itself is a
    // primitive (nothing native runs along the price axis), while POC/VAH/VAL are
    // price lines so they get axis labels and span the full pane for free.
    const vp = new VolumeProfilePrimitive(null);
    candle.attachPrimitive(vp as any);
    vpRef.current = vp;

    const VP_LINES = [
      { key: "poc", color: palette.gold, style: LineStyle.Solid, title: "POC" },
      { key: "vah", color: palette.blue, style: LineStyle.Dashed, title: "VAH" },
      { key: "val", color: palette.blue, style: LineStyle.Dashed, title: "VAL" },
    ] as const;
    let vpLines: IPriceLine[] = [];
    const syncProfileLines = (p: VolumeProfile | null) => {
      const on = visRef.current.volumeProfile && p != null;
      // A window with no traded range (every bar flat) has no profile to label.
      if (p && vpLines.length === 0) {
        vpLines = VP_LINES.map((spec) =>
          candle.createPriceLine({
            price: p[spec.key],
            color: spec.color,
            lineWidth: 1,
            lineStyle: spec.style,
            axisLabelVisible: true,
            title: spec.title,
          }),
        );
      }
      vpLines.forEach((line, i) =>
        line.applyOptions({
          ...(p ? { price: p[VP_LINES[i].key] } : {}),
          lineVisible: on,
          axisLabelVisible: on,
        }),
      );
    };

    // Re-profile on pan/zoom (and on the shift a new bar causes) so zooming into
    // a stretch profiles that stretch — and on every playback frame, so the
    // rightmost rows grow with the bar that is forming rather than jumping a
    // bar's worth of volume at its close. Logical range is fractional and can run
    // past the data on both ends, so clamp it back onto real bar indices.
    //
    // No from/to memo here: the span can be unchanged and the profile still
    // stale, because the last bar's tape keeps arriving. The accumulator answers
    // the "nothing new" case with the very profile it handed back last time, so
    // that comparison is the honest one to skip on.
    let lastVp: VolumeProfile | null = null;
    const reprofile = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range) return;
      const from = Math.max(0, Math.ceil(range.from));
      const to = Math.min(barsRef.current.length - 1, Math.floor(range.to));
      if (to < from) return;
      const p = liveProfileFor(vpLive, from, to);
      if (p === lastVp) return;
      lastVp = p;
      vp.setProfile(p);
      syncProfileLines(p);
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => reprofile());

    // --- The composite over the context days, and the events on the tape -----
    // Attached before the fixed-range tool so a profile you drew sits over them:
    // these two are the standing backdrop, that one is the question you are
    // asking right now.
    const compPrim = compPrimRef.current!;
    candle.attachPrimitive(compPrim as any);

    const devPrim = devPrimRef.current!;
    candle.attachPrimitive(devPrim as any);

    const evPrim = evPrimRef.current!;
    candle.attachPrimitive(evPrim as any);

    // --- Fixed-range profile: drag across the chart to profile just that slice ---
    const rangePrim = new RangeProfilePrimitive();
    candle.attachPrimitive(rangePrim as any);

    // --- Closed trades: entry arrow → exit dot, and what the leg paid --------
    // Attached before the two live overlays so a mark from an hour ago can never
    // be drawn over the position you are in now.
    // --- Big trades: the sweeps the tape had to work through the book --------
    // Attached first of the three mark layers, so it draws under them: what the
    // market did is context for what you did, never a cover over it.
    const bigPrim = bigPrimRef.current!;
    candle.attachPrimitive(bigPrim as any);
    bigPrim.setTrades(bigsRef.current, bigLotsRef.current);

    const tradesPrim = tradesPrimRef.current!;
    candle.attachPrimitive(tradesPrim as any);
    tradesPrim.setTrades(tradesRef.current, tapeRef.current?.tickSize);

    // --- Open position: entry / stop / target, zones, chips, axis labels ---
    // Drawn above everything else, and the only overlay whose lines can be
    // dragged (the mechanics are down in the pointer handlers).
    const posPrim = posPrimRef.current!;
    candle.attachPrimitive(posPrim as any);
    posPrim.setData(posRef.current);

    // --- Working orders: the resting limits, under the position overlay ------
    const ordPrim = ordPrimRef.current!;
    candle.attachPrimitive(ordPrim as any);
    ordPrim.setOrders(workingRef.current, tapeRef.current?.tickSize);

    // --- Ruler: drag between two points to measure the move between them ---
    // Recreated when the tape changes, because the tick size and $/point it
    // reports in are constructor arguments.
    let ruler = new RulerPrimitive(tapeRef.current?.tickSize, tapeRef.current?.pointValue);
    candle.attachPrimitive(ruler as any);
    const remakeRuler = (tape: Tape | null) => {
      candle.detachPrimitive(ruler as any);
      ruler = new RulerPrimitive(tape?.tickSize, tape?.pointValue);
      candle.attachPrimitive(ruler as any);
    };

    // Repaint every fixed-range profile from the ref. A range whose span isn't
    // fully on the chart draws nothing rather than being clamped onto the loaded
    // edge — on a rewind its bars genuinely haven't happened yet, and a profile
    // silently squashed onto the live edge would be a wrong number, not a missing
    // one. It comes back untouched when the replay reaches it again.
    const paint = () => {
      const bars = barsRef.current;
      const lastT = bars.length ? bars[bars.length - 1].time : -Infinity;
      const firstT = bars.length ? bars[0].time : Infinity;
      rangePrim.setData(
        rangesRef.current.map((r) => {
          const loaded = r.from >= firstT && r.to <= lastT;
          if (!loaded) return { id: r.id, from: r.from, to: r.to, profile: null };
          const i0 = nearestIdx(r.from);
          const i1 = nearestIdx(r.to);
          return { id: r.id, from: bars[i0].time, to: bars[i1].time, profile: profileFor(i0, i1) };
        }),
        selectedRef.current,
      );
    };
    paintRef.current = paint;

    // The developing NY profile, repainted on every playback frame rather than
    // once a bar: it is the distribution the session is building right now, so a
    // row that only thickens at the bar's close reads as the profile trailing
    // the candles. The span is the first NY bar to the live edge, which is
    // exactly the stretch the NY value area is accumulated over — so the
    // histogram and the VAH/POC/VAL lines are two views of one distribution and
    // cannot disagree, on any frame.
    let lastDev: VolumeProfile | null = null;
    let lastNodes: ProfileNodes | null = null;
    let lastProm = -1;
    const paintDev = () => {
      const bars = barsRef.current;
      const start = nyStartRef.current;
      if (!Number.isFinite(start) || !bars.length) {
        devPrim.setData(null);
        return;
      }
      const i0 = nearestIdx(start);
      const i1 = bars.length - 1;
      const profile = i0 >= 0 && i1 >= i0 ? liveProfileFor(devLive, i0, i1) : null;
      if (!profile) {
        devPrim.setData(null);
        return;
      }
      const va = nyVaRef.current;
      const prom = nodePromRef.current;
      // Re-read whenever the distribution moves rather than cached like the
      // composite's: this one changes with the tape, so the only thing to cache
      // against is the profile itself — which the accumulator hands back
      // unchanged when no tick has arrived (a paused replay, a repaint for some
      // other reason). It is one pass over a few hundred rows either way.
      if (profile !== lastDev || prom !== lastProm) {
        lastDev = profile;
        lastProm = prom;
        lastNodes = prom > 0 ? profileNodes(profile, prom) : null;
      }
      devPrim.setData({
        profile,
        va: va && { poc: va.poc, vah: va.vah, val: va.val },
        nodes: lastNodes,
        from: bars[i0].time,
      });
    };
    paintDevRef.current = paintDev;

    // --- IB overlay ---------------------------------------------------------
    // Redrawn whenever the box or the live edge moves (once per bar close, not
    // per frame): the segments have to keep reaching the right-hand edge as the
    // session runs.
    const syncIb = () => {
      const box = ibRef.current;
      const bars = barsRef.current;
      const blank = () => {
        for (const s of [...ibSeries, ...ibExtSeries]) s.setData([]);
      };
      if (!box || bars.length === 0) return blank();
      // Snap both endpoints onto the drawn grid: lightweight-charts unions the
      // time points of every series, so an off-grid stamp would open its own
      // empty column on the time scale.
      const a = nearestBar(box.start);
      const b = bars[bars.length - 1].time;
      const seg = (s: ISeriesApi<"Line">, from: number, price: number) =>
        s.setData(
          from >= b
            ? [{ time: b as Time, value: price }]
            : [
                { time: from as Time, value: price },
                { time: b as Time, value: price },
              ],
        );
      seg(ibSeries[0], a, box.high);
      seg(ibSeries[1], a, box.low);
      const range = box.high - box.low;
      const formed = nearestBar(box.formed);
      [1, 1.5, 2].forEach((m, k) => {
        // Guides are a statement about a *completed* hour: nothing to extend from
        // until the window closes.
        if (!box.complete) {
          ibExtSeries[k * 2].setData([]);
          ibExtSeries[k * 2 + 1].setData([]);
          return;
        }
        seg(ibExtSeries[k * 2], formed, box.high + m * range);
        seg(ibExtSeries[k * 2 + 1], formed, box.low - m * range);
      });
    };

    // --- pointer plumbing ---------------------------------------------------
    const host = elRef.current;
    const xOf = (e: MouseEvent | PointerEvent) => e.clientX - host.getBoundingClientRect().left;
    const yOf = (e: MouseEvent | PointerEvent) => e.clientY - host.getBoundingClientRect().top;
    const idxAtX = (x: number): number | null => {
      const last = barsRef.current.length - 1;
      if (last < 0) return null;
      const logical = chart.timeScale().coordinateToLogical(x);
      if (logical == null) return null;
      return Math.min(last, Math.max(0, Math.round(logical)));
    };
    // Ruler corners snap to the tick grid — a measurement in fractional ticks is
    // never what anyone wants.
    const priceAtY = (y: number): number | null => {
      const p = candle.coordinateToPrice(y);
      if (p == null) return null;
      const tick = tapeRef.current?.tickSize;
      return tick ? Math.round(p / tick) * tick : p;
    };
    const measureOf = (i1: number, p1: number, i2: number, p2: number) => {
      const bars = barsRef.current;
      return {
        t1: bars[i1].time,
        p1,
        t2: bars[i2].time,
        p2,
        bars: Math.abs(i2 - i1),
        seconds: Math.abs(bars[i2].time - bars[i1].time),
      };
    };

    // What's under the pointer, topmost (most recently drawn) first. Edges win
    // over bodies so a narrow profile is still resizable.
    const hitTest = (x: number): { id: number; mode: DragMode } | null => {
      const ts = chart.timeScale();
      for (let i = rangesRef.current.length - 1; i >= 0; i--) {
        const r = rangesRef.current[i];
        const a = ts.timeToCoordinate(nearestBar(r.from) as Time);
        const b = ts.timeToCoordinate(nearestBar(r.to) as Time);
        if (a == null || b == null) continue;
        const x1 = Math.min(a, b);
        const x2 = Math.max(a, b);
        if (Math.abs(x - x1) <= HANDLE_PX) return { id: r.id, mode: "left" };
        if (Math.abs(x - x2) <= HANDLE_PX) return { id: r.id, mode: "right" };
        if (x > x1 && x < x2) return { id: r.id, mode: "move" };
      }
      return null;
    };

    // Panning is a left-drag too, so it must be off whenever a left-drag means
    // something else: while a tool is armed, or while the pointer is over a
    // profile the user could grab. Only the *pressed-drag* gesture conflicts —
    // blanket `handleScroll: false` would also deaden the mouse wheel.
    let scrollOff = false;
    const setScroll = (off: boolean) => {
      if (off === scrollOff) return;
      scrollOff = off;
      chart.applyOptions({
        handleScroll: {
          mouseWheel: true,
          pressedMouseMove: !off,
          horzTouchDrag: !off,
          vertTouchDrag: !off,
        },
      });
    };
    const anyArmed = () =>
      armedRef.current ||
      rulerArmedRef.current ||
      avwapArmedRef.current ||
      orderArmedRef.current ||
      spaceRef.current;
    const setCursor = (a: boolean) => {
      if (elRef.current) elRef.current.style.cursor = a ? "crosshair" : "";
    };

    // Drag state, in bar-index space rather than time: the bar grid has gaps
    // (weekends, overnight), so shifting a range by a time delta would smear it.
    let drag: { mode: DragMode; id: number; anchorIdx: number; from: number; to: number } | null =
      null;
    let downX = 0;
    let downY = 0;
    // Which pointer owns the gesture. A mouse only ever has one, but a second
    // finger landing mid-drag would otherwise yank the thing being dragged to
    // wherever it touched down.
    let pressId: number | null = null;
    const isPress = (e: PointerEvent) => pressId == null || e.pointerId === pressId;
    // The long-press countdown. Armed on a press that lands on bare tape and
    // dropped the moment that press turns out to be anything else — a pan, a
    // flick, a tap — so the gesture costs the chart's existing ones nothing.
    let pressTimer: number | null = null;
    const clearPress = () => {
      if (pressTimer != null) window.clearTimeout(pressTimer);
      pressTimer = null;
    };
    // Android answers a held finger with a menu of its own — copy / share /
    // select — and it decides *later* than we do: our ＋ lands at 420ms, the
    // platform's callout at ~500ms, and by then the thing under the finger may be
    // the ＋ or the price beside it rather than the canvas. Those are siblings of
    // the chart, so the host's own contextmenu handler never sees the event and
    // the OS answers first.
    //
    // So the guard is on the document, in the capture phase, and armed for the
    // life of any press that started on this chart: inside that window every
    // contextmenu belongs to this gesture, whatever it happens to land on.
    let ctxGuard = false;
    let ctxTimer: number | null = null;
    const guardCtx = (on: boolean) => {
      if (ctxTimer != null) window.clearTimeout(ctxTimer);
      ctxTimer = null;
      if (on) {
        ctxGuard = true;
        return;
      }
      // Disarmed a beat after the finger lifts, not with it — on some builds the
      // callout arrives just after the pointerup that ended the press.
      ctxTimer = window.setTimeout(() => {
        ctxGuard = false;
        ctxTimer = null;
      }, 600);
    };
    const onDocCtx = (e: MouseEvent) => {
      if (ctxGuard) e.preventDefault();
    };
    // The ruler's anchor while a measurement is being drawn. Survives mouseup on
    // a no-move click, so both TV gestures work: press-drag-release and
    // click-move-click.
    let rulerDrag: { i1: number; p1: number } | null = null;
    // Which bracket leg is being dragged, if any.
    let posDrag: "stop" | "target" | null = null;
    // A held entry chip, waiting for the pull that decides which leg it becomes.
    // Only the legs the position hasn't got are in it: the gesture is the way to
    // attach a missing stop or target without a line to grab, so pulling toward
    // one that already exists is not a gesture at all.
    let legArm: { stop: boolean; target: boolean } | null = null;
    // Which working-order leg is being dragged. Dragging the resting price
    // carries its bracket along at the distances it was placed at (the stop is
    // a distance from the entry, not a level in its own right), so the offsets
    // are captured at grab time.
    let ordDrag: {
      id: number;
      leg: "price" | "stop" | "target";
      dStop: number | null;
      dTarget: number | null;
    } | null = null;

    // A bracket must stay on the side of the mark where it can still do its job:
    // a long's stop below the market, its target above. Dragging one past the
    // mark would be an order that fills the instant you let go, so it's clamped
    // one tick short instead — the same refusal TV makes.
    const clampBracket = (leg: "stop" | "target", price: number, p: PositionData): number => {
      const tick = tapeRef.current?.tickSize ?? 0.25;
      const mark = Number.isFinite(p.last) ? p.last : p.entry;
      const above = (p.side === "long") === (leg === "target");
      return above ? Math.max(price, mark + tick) : Math.min(price, mark - tick);
    };
    // "entry" gets the resize cursor too: what it advertises is the axis the
    // gesture ends up moving along, once the hold is done.
    const cursorFor = (h: PosHit) => (h === "close" ? "pointer" : "ns-resize");
    const ordCursorFor = (h: OrderHit) => (h.leg === "cancel" ? "pointer" : "ns-resize");

    // Where a working order's legs may sit. The resting price has to stay on the
    // side of the mark its type belongs on — a buy limit above the market would
    // fill instantly at a price better than the market, and a buy stop below it
    // likewise — so an order dragged across the market stops at the mark rather
    // than turning into the other type under your hand. Its bracket has to stay
    // on the side of the *entry* that lets it do its job.
    const clampOrder = (
      o: WorkingOrderView,
      leg: "price" | "stop" | "target",
      price: number,
    ): number => {
      const tick = tapeRef.current?.tickSize ?? 0.25;
      const long = o.side === "long";
      if (leg === "price") {
        const mk = Number.isFinite(lastPriceRef.current) ? lastPriceRef.current : o.price;
        const above = o.type === "stop" ? long : !long;
        return above ? Math.max(price, mk + tick) : Math.min(price, mk - tick);
      }
      const above = long === (leg === "target");
      return above ? Math.max(price, o.price + tick) : Math.min(price, o.price - tick);
    };

    const onDown = (e: PointerEvent) => {
      // Before anything else, and whatever this press turns out to be: from here
      // until it ends, the platform's own press-and-hold menu is not welcome.
      guardCtx(true);
      const x = xOf(e);
      const idx = idxAtX(x);
      if (idx == null) return;

      // Any press on the tape puts the order ＋ away. It is a transient thing you
      // summoned at a price, and the next thing you touch is either it or not it
      // — the ＋ and its menu are siblings of the canvas, not children, so a press
      // *on* them never reaches this handler. A press that then stays put re-arms
      // it at the new price, which is how you move it.
      if (plusRef.current != null) closePlusRef.current();

      // Space + click places an order — either button, and Space stays down so
      // you can place a second one without letting go.
      if (spaceRef.current && (e.button === 0 || e.button === 2)) {
        const p = priceAtY(yOf(e));
        if (p != null) onPlaceOrderRef.current?.(p, e.button === 2 ? "right" : "left");
        e.preventDefault();
        return;
      }
      // Same placement, reached by a tool instead of a modifier. Checked before
      // the button test below because a touch pointerdown reports button 0 —
      // which is right, but says nothing about which channel was chosen.
      if (orderArmedRef.current && e.button === 0) {
        const p = priceAtY(yOf(e));
        if (p != null) onPlaceOrderRef.current?.(p, orderSideRef.current);
        armOrderRef.current(false);
        e.preventDefault();
        return;
      }
      if (e.button !== 0) return;
      // A finger has no hover, so the press is the first this handler hears of
      // the pointer at all. Everything below decides whether to take the drag
      // off the chart's panning — on a mouse that decision was already made
      // during the hover that preceded this.
      pressId = e.pointerId;
      downX = x;
      downY = yOf(e);

      if (avwapArmedRef.current) {
        // Anchor the VWAP on the clicked bar and put the tool away. Un-hide the
        // layer first: its legend row only exists once an anchor is placed, so
        // hiding it once would otherwise make every later anchor land invisible,
        // and the tool read as dead.
        revealRef.current("vwapAnchored");
        const t = barsRef.current[idx].time;
        setAvwapAnchor(t);
        onAnchorRef.current?.(t);
        armAvwapRef.current(false);
        e.preventDefault();
        return;
      }

      if (rulerArmedRef.current) {
        if (rulerDrag) {
          // Second click of click-move-click: the measurement is done.
          rulerDrag = null;
          armRulerRef.current(false);
          e.preventDefault();
          return;
        }
        const p = priceAtY(downY);
        if (p == null) return;
        rulerDrag = { i1: idx, p1: p };
        ruler.setData(measureOf(idx, p, idx, p));
        e.preventDefault();
        return;
      }
      // Any plain press dismisses a finished measurement, like TV's ruler.
      if (ruler.data()) ruler.setData(null);

      if (armedRef.current) {
        const id = nextIdRef.current++;
        const t = barsRef.current[idx].time;
        rangesRef.current = [...rangesRef.current, { id, from: t, to: t }];
        selectedRef.current = id;
        drag = { mode: "new", id, anchorIdx: idx, from: idx, to: idx };
        e.preventDefault();
        paint();
        return;
      }

      // The open position outranks the fixed-range profiles: its lines are what
      // sits under the pointer most often once you're in a trade.
      const ph = posPrim.hit(x, downY);
      if (ph === "close") {
        onFlattenRef.current?.();
        e.preventDefault();
        return;
      }
      if (ph === "entry") {
        // Hold the position chip, then pull: the drag-out gesture for a leg that
        // has no line to grab yet. Armed the same way the order ＋ is — no
        // preventDefault, no taking the drag off panning — so until the countdown
        // finishes this is still an ordinary press, and a flick is still a pan.
        // (The primitive only offers this hit while a leg is missing, so reaching
        // here already means there is one to pull out.)
        clearPress();
        pressTimer = window.setTimeout(() => {
          pressTimer = null;
          const p = posRef.current;
          if (!p) return;
          legArm = { stop: p.stop == null, target: p.target == null };
          setScroll(true);
          host.style.cursor = "ns-resize";
          posPrim.setHover("entry");
          // Same reason as the ＋: a held finger may have selected something on
          // the way here, and its handles would sit over the chart.
          window.getSelection?.()?.removeAllRanges();
        }, LONG_PRESS_MS);
        return;
      }
      if (ph) {
        posDrag = ph;
        setScroll(true);
        host.style.cursor = "ns-resize";
        posPrim.setHover(ph);
        e.preventDefault();
        return;
      }

      const oh = ordPrim.hit(x, downY);
      if (oh) {
        const o = workingRef.current.find((v) => v.id === oh.id);
        if (oh.leg === "cancel") onOrderCancelRef.current?.(oh.id);
        else if (o) {
          ordDrag = {
            id: oh.id,
            leg: oh.leg,
            dStop: o.stop != null ? o.stop - o.price : null,
            dTarget: o.target != null ? o.target - o.price : null,
          };
          setScroll(true);
          host.style.cursor = "ns-resize";
          ordPrim.setHover(oh);
        }
        e.preventDefault();
        return;
      }

      const hit = hitTest(x);
      if (!hit) {
        // Clicking bare chart deselects — but let the chart pan as usual.
        if (selectedRef.current != null) {
          selectedRef.current = null;
          syncRef.current();
        }
        // Nothing else wanted this press, so it is a candidate for the order
        // gesture. Started rather than acted on, and without preventDefault or
        // taking the drag off panning: until the timer fires this is still an
        // ordinary press, and most of them are pans. If the finger travels or
        // lifts first, the countdown is dropped and nothing happened.
        if (canOrderRef.current) {
          clearPress();
          pressTimer = window.setTimeout(() => {
            pressTimer = null;
            const p = priceAtY(downY);
            if (p != null) openPlusRef.current(p);
            // If the platform got as far as selecting something on the way here,
            // drop it — otherwise the selection handles and their action bar stay
            // up over the chart even with the callout itself suppressed.
            window.getSelection?.()?.removeAllRanges();
          }, LONG_PRESS_MS);
        }
        return;
      }
      const r = rangesRef.current.find((v) => v.id === hit.id)!;
      selectedRef.current = hit.id;
      drag = { mode: hit.mode, id: hit.id, anchorIdx: idx, from: nearestIdx(r.from), to: nearestIdx(r.to) };
      e.preventDefault();
      if (hit.mode === "move") host.style.cursor = "grabbing";
      syncRef.current();
    };

    const onMove = (e: PointerEvent) => {
      if (!isPress(e)) return;
      const x = xOf(e);
      // A press that travels is a pan, not a long press. Same slop the ruler
      // uses, so "held still" means the same thing everywhere on this chart.
      if (pressTimer != null && Math.hypot(x - downX, yOf(e) - downY) > DRAG_SLOP) clearPress();
      if (posDrag) {
        // Live, primitive-only: the page hears about it on release, so a drag
        // costs no React render and no trip through the trade log.
        const p = posRef.current;
        const price = priceAtY(yOf(e));
        if (!p || price == null) return;
        const v = clampBracket(posDrag, price, p);
        posRef.current = { ...p, [posDrag]: v };
        pushPos();
        return;
      }
      if (legArm) {
        // Which leg a pull means is a matter of which side of the trade it goes
        // to, not of up and down: a long's stop sits under the market and a
        // short's over it, so "down" means SL on one and TP on the other. Nothing
        // happens until the pointer has committed to a direction — and a pull
        // toward a leg that already exists is left alone, so the held chip stays
        // armed for a pull the other way.
        const p = posRef.current;
        const y = yOf(e);
        const dy = y - downY;
        if (!p || Math.abs(dy) <= DRAG_SLOP) return;
        const leg = (dy < 0) === (p.side === "long") ? "target" : "stop";
        if (!legArm[leg]) return;
        // From here it is an ordinary bracket drag, and it ends like one: the
        // page hears about the new leg on release, through onBracketChange.
        legArm = null;
        posDrag = leg;
        posPrim.setHover(leg);
        const price = priceAtY(y);
        if (price != null) {
          posRef.current = { ...p, [leg]: clampBracket(leg, price, p) };
          pushPos();
        }
        return;
      }
      if (ordDrag) {
        // Same deal as a bracket drag: primitive-only until release.
        const price = priceAtY(yOf(e));
        const o = workingRef.current.find((v) => v.id === ordDrag!.id);
        if (!o || price == null) return;
        const v = clampOrder(o, ordDrag.leg, price);
        const next: WorkingOrderView =
          ordDrag.leg === "price"
            ? {
                ...o,
                price: v,
                stop: ordDrag.dStop != null ? v + ordDrag.dStop : null,
                target: ordDrag.dTarget != null ? v + ordDrag.dTarget : null,
              }
            : { ...o, [ordDrag.leg]: v };
        workingRef.current = workingRef.current.map((w) => (w.id === next.id ? next : w));
        pushOrders();
        return;
      }
      if (rulerDrag) {
        const idx = idxAtX(x);
        const p = priceAtY(yOf(e));
        if (idx == null || p == null) return;
        ruler.setData(measureOf(rulerDrag.i1, rulerDrag.p1, idx, p));
        return;
      }
      if (!drag) {
        // Idle hover: advertise what a grab here would do, and take the mouse away
        // from the chart's panning so the grab actually lands.
        //
        // Skipped for touch. A finger cannot hover, so anything that arrives here
        // from one is the tail of a gesture that has already ended; acting on it
        // would leave a leg lit up under nothing, and would flip panning off on
        // the strength of where a finger happened to lift.
        if (e.pointerType === "touch" || anyArmed()) return;
        const ph = posPrim.hit(x, yOf(e));
        posPrim.setHover(ph);
        const oh = ph ? null : ordPrim.hit(x, yOf(e));
        ordPrim.setHover(oh);
        if (ph || oh) {
          setScroll(true);
          host.style.cursor = ph ? cursorFor(ph) : ordCursorFor(oh!);
          return;
        }
        const hit = hitTest(x);
        setScroll(hit != null);
        host.style.cursor = !hit ? "" : hit.mode === "move" ? "grab" : "col-resize";
        return;
      }

      const idx = idxAtX(x);
      if (idx == null) return;
      const bars = barsRef.current;
      const last = bars.length - 1;
      const r = rangesRef.current.find((v) => v.id === drag!.id);
      if (!r) return;

      if (drag.mode === "move") {
        // Slide by whole bars, clamped so the range keeps its width at the edges.
        const width = drag.to - drag.from;
        let from = drag.from + (idx - drag.anchorIdx);
        from = Math.min(last - width, Math.max(0, from));
        r.from = bars[from].time;
        r.to = bars[from + width].time;
      } else {
        // Resizing: the grabbed edge follows the pointer, the other stays put, and
        // dragging one past the other just flips which is which.
        const fixed =
          drag.mode === "new" ? drag.anchorIdx : drag.mode === "left" ? drag.to : drag.from;
        r.from = bars[Math.min(idx, fixed)].time;
        r.to = bars[Math.max(idx, fixed)].time;
      }
      paint(); // primitive only — no React re-render mid-drag
    };

    const onUp = (e: PointerEvent) => {
      if (!isPress(e)) return;
      guardCtx(false);
      pressId = null;
      // Lifting before the countdown finishes is a tap, and a tap is not it.
      // (pointercancel lands here too, which is the case that matters: the OS
      // taking the pointer away must not leave a ＋ armed behind it.)
      clearPress();
      if (legArm) {
        // Held long enough to arm, then let go without pulling anywhere: no leg
        // was chosen, so there is nothing to commit.
        legArm = null;
        setScroll(false);
        host.style.cursor = "";
        posPrim.setHover(null);
        return;
      }
      if (posDrag) {
        const p = posRef.current;
        posDrag = null;
        setScroll(false);
        host.style.cursor = "";
        posPrim.setHover(null);
        if (p) onBracketRef.current?.({ stop: p.stop, target: p.target });
        return;
      }
      if (ordDrag) {
        const o = workingRef.current.find((v) => v.id === ordDrag!.id);
        ordDrag = null;
        setScroll(false);
        host.style.cursor = "";
        ordPrim.setHover(null);
        if (o) {
          onOrderMoveRef.current?.({ id: o.id, price: o.price, stop: o.stop, target: o.target });
        }
        return;
      }
      if (rulerDrag) {
        // A real drag ends the measurement here; a stationary click leaves the
        // anchor live so the pointer keeps stretching it (click-move-click).
        if (Math.hypot(xOf(e) - downX, yOf(e) - downY) >= DRAG_SLOP) {
          rulerDrag = null;
          armRulerRef.current(false);
        }
        return;
      }
      if (!drag) return;
      const wasNew = drag.mode === "new";
      const id = drag.id;
      const moved = Math.abs(xOf(e) - downX);
      drag = null;

      // A click with no real drag means "never mind" — don't leave a hairline
      // profile of a single bar behind.
      if (wasNew && moved < DRAG_SLOP) {
        rangesRef.current = rangesRef.current.filter((r) => r.id !== id);
        selectedRef.current = null;
      }
      if (wasNew) disarmRef.current();
      syncRef.current();
    };

    armApplyRef.current = (a: boolean) => {
      setScroll(anyArmed());
      setCursor(a);
    };
    // Below the handlers because both close over `rulerDrag`: disarming or
    // clearing must also drop an in-flight anchor, or the measurement would keep
    // chasing the pointer after Esc / toggling the tool off.
    rulerApplyRef.current = (a: boolean) => {
      setScroll(anyArmed());
      setCursor(a);
      if (a) ruler.setData(null); // re-arming starts a fresh measurement
      else rulerDrag = null;
    };
    rulerClearRef.current = () => {
      const had = rulerDrag != null || ruler.data() != null;
      rulerDrag = null;
      ruler.setData(null);
      return had;
    };
    // Arming the ⚓ tool just sets the crosshair and takes the pointer off panning
    // so the anchor click lands cleanly; the placement happens in onDown.
    avwapApplyRef.current = (a: boolean) => {
      setScroll(anyArmed());
      setCursor(a);
    };
    // Holding Space takes the left button away from panning (the click has to
    // land on a price, not drag the chart out from under it) and puts the
    // crosshair up so the price under the pointer is readable before you commit.
    spaceApplyRef.current = () => {
      setScroll(anyArmed());
      setCursor(anyArmed());
    };
    // The tool form of the same thing.
    orderApplyRef.current = () => {
      setScroll(anyArmed());
      setCursor(anyArmed());
    };

    // The browser's own menu never gets this surface. It would land on top of the
    // Space+click gesture (whose right half *is* a right-click), and on a
    // touchscreen it arrives from exactly the press-and-hold that summons the ＋
    // — so the one gesture would open two menus, ours under theirs.
    //
    // What replaces it: on a mouse, a right-click opens the order ticket outright
    // at the price under the pointer. The two-step ＋ exists because a fingertip
    // needs a confirmation of the level it hit and has no second button; a
    // right-click is already deliberate and already precise, and making it walk to
    // the axis for a second click would be a worse gesture, not a consistent one.
    const onCtx = (e: MouseEvent) => {
      e.preventDefault();
      if (spaceRef.current || orderArmedRef.current) return;
      if (COARSE_POINTER || !canOrderRef.current) return;
      const p = priceAtY(yOf(e));
      if (p != null) openPlusRef.current(p, true);
    };
    // Held state survives leaving and re-entering — the key really is still
    // down — so only entry and exit are tracked, not the modifier itself.
    const onEnter = () => {
      overRef.current = true;
    };
    const onLeave = () => {
      overRef.current = false;
    };

    // Pointer events rather than mouse events: one set of handlers that a finger,
    // a stylus and a mouse all arrive through, instead of a mouse-only chart with
    // touch bolted on beside it.
    //
    // The chart surface is JS-driven — lightweight-charts owns pan, pinch and
    // wheel, and the handlers here own everything drawn on top — so none of the
    // browser's own touch gestures were ever going to be right on it. Declaring
    // that once, up front, is also the only version that works: touch-action is
    // read when a gesture *starts*, so deciding at pointerdown that we want this
    // drag would already be a frame too late.
    host.style.touchAction = "none";
    // A press held on the tape is a gesture of ours now, and holding still on a
    // touchscreen is what the platforms reach for when they want to offer a
    // selection or a callout of their own. There is no text down here to select,
    // so nothing is being taken away — it just stops the OS answering first.
    host.style.userSelect = "none";
    host.style.webkitUserSelect = "none";
    // Safari-only, and not in the DOM typings — set by name rather than pretended
    // into the type.
    host.style.setProperty("-webkit-touch-callout", "none");
    host.addEventListener("pointerdown", onDown);
    host.addEventListener("contextmenu", onCtx);
    // Capture, and on the document rather than the chart: the event this one is
    // for may be aimed at something that only exists because the press summoned
    // it. See guardCtx.
    document.addEventListener("contextmenu", onDocCtx, true);
    host.addEventListener("pointerenter", onEnter);
    host.addEventListener("pointerleave", onLeave);
    // Move/up on the window, so a drag that leaves the chart still tracks and,
    // more importantly, still terminates. pointercancel matters more than it
    // looks: the OS takes the pointer away mid-drag for its own gestures (an
    // edge swipe, a notification pull) and without it the drag would never end.
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);

    applyRef.current = (v: IndicatorVisibility) => {
      const setBand = (a: Anchor | null, on: boolean) => {
        if (!a) return;
        // Lines and fill of an anchor hide as a unit — a naked wash (or naked
        // lines) would read as a different indicator.
        for (const k of BAND_KEYS) a.lines[k].applyOptions({ visible: on });
        a.band.setVisible(on);
      };
      setBand(gRef.current, v.vwapGlobex);
      setBand(nRef.current, v.vwapNy);
      setBand(aRef.current, v.vwapAnchored);
      setBand(wkRef.current, v.vwapWeekly);
      for (const k of PROF_KEYS) {
        gProfRef.current?.[k].applyOptions({ visible: v.developingProfileGlobex });
        nProfRef.current?.[k].applyOptions({ visible: v.developingProfileNy });
      }
      for (const s of ibSeries) s.applyOptions({ visible: v.initialBalance });
      for (const s of ibExtSeries) s.applyOptions({ visible: v.ibExtensions });
      vp.setVisible(v.volumeProfile);
      for (const l of vpLines)
        l.applyOptions({ lineVisible: v.volumeProfile, axisLabelVisible: v.volumeProfile });
      tradesPrim.setVisible(v.replayTrades);
      bigPrim.setVisible(v.bigTrades);
      compPrim.setVisible(v.compositeProfile, v.compositeNodes);
      devPrim.setVisible(v.developingVpNy, v.developingVpNyNodes);
      // The two event layers hide by dropping out of the filtered list, so that
      // the bands and the marginals can never show different sets.
      pushEvents();
    };
    applyRef.current(visRef.current);

    // Everything the imperative handle needs that lives inside this effect.
    hooksRef.current = { reprofile, paint, syncIb, remakeRuler, clearRuler: rulerClearRef.current };

    // Sizing is autoSize's job (see createChart) — it covers the window resize
    // and the cases there is no window resize for: the page's controls wrapping,
    // a panel opening, entering fullscreen.
    return () => {
      hooksRef.current = null;
      vpRef.current = null;
      applyRef.current = null;
      armApplyRef.current = null;
      rulerApplyRef.current = null;
      rulerClearRef.current = () => false;
      avwapApplyRef.current = null;
      spaceApplyRef.current = null;
      orderApplyRef.current = null;
      paintRef.current = null;
      paintDevRef.current = null;
      clearPress();
      if (ctxTimer != null) window.clearTimeout(ctxTimer);
      host.removeEventListener("pointerdown", onDown);
      host.removeEventListener("contextmenu", onCtx);
      document.removeEventListener("contextmenu", onDocCtx, true);
      host.removeEventListener("pointerenter", onEnter);
      host.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      chart.remove();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // How the two time labels read: the calendar on or off, and whether the clock
  // runs to seconds. Bar times are the ET wall clock carried on the UTC epoch
  // (see ReplayEngine), so a UTC read of them *is* the local time — which is also
  // why hiding the date is a formatting choice and nothing more: the same bars,
  // without the day written on them.
  //
  // Two labels give the day away, and they come back on by different routes.
  // A tick-mark formatter returning null means "use the default", so switching
  // the calendar back on is just that — but only while the default is the one we
  // want, and a sub-minute bar needs seconds the default axis won't print. So a
  // seconds axis always sets a formatter of its own. The crosshair's has no
  // escape hatch at all — the library only asks whether one is *set* — so turning
  // the calendar off means handing back a formatter that spells out what the
  // default already did (the same `dd MMM 'yy` + clock).
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const at = (t: unknown) => new Date((t as number) * 1000);
    const p2 = (n: number) => String(n).padStart(2, "0");
    const clock = (d: Date) =>
      `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}` +
      (secondsAxis ? `:${p2(d.getUTCSeconds())}` : "");
    const hhmm = (t: unknown) => clock(at(t));
    const dateTime = (t: unknown) => {
      const d = at(t);
      const mon = new Date(d.getUTCFullYear(), d.getUTCMonth(), 1).toLocaleString(undefined, {
        month: "short",
      });
      return `${p2(d.getUTCDate())} ${mon} '${p2(d.getUTCFullYear() % 100)}   ${clock(d)}`;
    };
    chart.applyOptions({
      timeScale: {
        secondsVisible: secondsAxis,
        tickMarkFormatter: hideDates || secondsAxis ? hhmm : () => null,
      },
      localization: { timeFormatter: hideDates ? hhmm : dateTime },
    });
  }, [hideDates, secondsAxis]);

  // A new threshold only reaches the marks through a rebuilt snapshot (the
  // engine decides which sweeps exist); the bubbles are scaled off it here, so
  // the primitive needs it as it changes rather than at build time.
  useEffect(() => {
    bigPrimRef.current?.setMinLots(bigLots);
  }, [bigLots]);

  // The event floor is a filter over marks the engine has already published, so
  // unlike the big-trade threshold it needs no rebuild at all.
  useEffect(() => {
    pushEvents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventStrength]);

  // A new rule is a new composite; a new prominence is the same composite read
  // again. Both leave the replay exactly where it stands — nothing here can
  // touch the clock, the tape or a fill.
  useEffect(() => {
    rebuildComposite();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [composite]);
  // One prominence, two readings: the composite's nodes are cached and dropped
  // here, the developing profile's are re-read as part of its repaint.
  useEffect(() => {
    compNodesRef.current = null;
    paintComposite();
    paintDevRef.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeProm]);

  // The one place the event filter lives: the strength floor and the two
  // per-kind toggles are applied here, and the same list then feeds the bands on
  // the candles, the marginal over each profile, and the legend's counts. Three
  // copies of one filter would be three places for them to disagree.
  const pushEvents = () => {
    const minSt = eventStrengthRef.current;
    const v = visRef.current;
    const list =
      minSt <= 0
        ? []
        : eventsRef.current.filter(
            (e) => e.st >= minSt && (e.kind === "sweep" ? v.sweepBursts : v.absorption),
          );
    evPrimRef.current?.setEvents(list);
    compPrimRef.current?.setEvents(list);
    vpRef.current?.setEvents(list);
    devPrimRef.current?.setEvents(list);
    const next = countEvents(list);
    setEvCount((c) => (c.sweep === next.sweep && c.absorb === next.absorb ? c : next));
  };

  // Grow the event list from a playback tail. Merged on kind + index rather than
  // by position: a burst and an absorption can both be open at once, so the
  // entry a tail updates is not always the last one.
  const mergeEvents = (tail: TapeEvent[]) => {
    if (!tail.length) return;
    const list = eventsRef.current;
    const pos = evPosRef.current;
    for (const e of tail) {
      const key = `${e.kind}:${e.idx}`;
      const at = pos.get(key);
      if (at == null) {
        pos.set(key, list.length);
        list.push(e);
      } else {
        list[at] = e;
      }
    }
    pushEvents();
  };

  // Grow the big-trade list from a playback tail. The tail's first entry is the
  // sweep that was still taking fills — matched by `idx`, not by time, because
  // a sweep that crosses a bar boundary changes the bar it belongs to.
  const mergeBigs = (tail: BigTrade[]) => {
    const list = bigsRef.current;
    for (const b of tail) {
      if (list.length && list[list.length - 1].idx === b.idx) list[list.length - 1] = b;
      else list.push(b);
    }
    if (tail.length) {
      bigPrimRef.current?.setTrades(list, bigLotsRef.current);
      setBigCount((n) => (n === list.length ? n : list.length));
    }
  };

  const bandData = (pts: BandPt[], key: BandKey) =>
    pts.map((p) => ({ time: p.time as Time, value: p[key] }));

  // Fold a playback tail into an anchor's fill points: the first tail entry
  // re-states the bar still forming (replace it), the rest are new (append).
  const mergeTail = (a: Anchor, tail: BandPt[]) => {
    for (const p of tail) {
      const pt = toVwapPoint(p);
      if (a.pts.length && a.pts[a.pts.length - 1].time === pt.time) a.pts[a.pts.length - 1] = pt;
      else a.pts.push(pt);
    }
    if (tail.length) a.band.setPoints(a.pts);
  };

  useImperativeHandle(ref, () => ({
    setTape(tape: Tape | null, opts?: { keepTools?: boolean; contextRanges?: TapeRange[] }) {
      tapeRef.current = tape;
      barsRef.current = [];
      histCountRef.current = 0;
      nyStartRef.current = NaN;
      nyVaRef.current = null;
      ibRef.current = null;
      lastPriceRef.current = NaN;
      posRef.current = null;
      pushPos();
      workingRef.current = [];
      pushOrders();
      tradesRef.current = [];
      setTradeCount(0);
      pushTrades();
      bigsRef.current = [];
      setBigCount(0);
      bigPrimRef.current?.setTrades([], bigLotsRef.current);
      eventsRef.current = [];
      evPosRef.current = new Map();
      pushEvents();
      // The days in front of the session are what the composite *is*, so a new
      // tape is always a new composite — including the one that is only a
      // context change, which is precisely when it has to be rebuilt.
      ctxRangeRef.current = opts?.contextRanges ?? [];
      rebuildComposite();
      // A new session is a new chart: nothing hand-drawn over the old one still
      // means anything. `keepTools` is the one exception — the same session with
      // more days glued in front of it is the same chart, and a profile drawn
      // over this morning still profiles this morning.
      if (!opts?.keepTools) {
        rangesRef.current = [];
        selectedRef.current = null;
        setRanges([]);
        setSelected(null);
        setAvwapAnchor(null);
        hooksRef.current?.clearRuler();
      }
      hooksRef.current?.remakeRuler(tape);
      hooksRef.current?.paint();
      paintDevRef.current?.();
    },
    setContextRanges(ranges: TapeRange[]) {
      // Identity, not contents: the page memoises them, so the same object is
      // the same cut of the same days — and re-summing five sessions of ticks to
      // land on the profile already on screen is the one cost worth avoiding
      // here.
      if (ctxRangeRef.current === ranges) return;
      ctxRangeRef.current = ranges;
      rebuildComposite();
    },
    clearRuler() {
      hooksRef.current?.clearRuler();
    },
    setSnapshot(s: Snapshot, opts?: { reframe?: boolean | "follow" }) {
      const candle = candleRef.current;
      const vol = volRef.current;
      if (!candle || !vol || !gRef.current || !nRef.current || !aRef.current || !wkRef.current)
        return;
      // The context days sit in front of the session's own bars and are drawn
      // exactly like them — they are the same tape, so a bar of Tuesday knows
      // the ticks it was built from and profiles like any other. Only the
      // session's bars ever grow, and they are the tail, which is what lets the
      // streaming path keep appending to this same array.
      const drawn = s.history.length ? s.history.concat(s.bars) : s.bars;
      const frame: "fit" | "follow" | "hold" =
        opts?.reframe === false ? "hold" : opts?.reframe === "follow" ? "follow" : "fit";
      // What the viewport is looking at, before the array under it is replaced —
      // both modes that keep the user's zoom have to read it here. Logical
      // ranges are indices into that array, so context days arriving at the
      // front would otherwise slide the view a thousand bars into Tuesday
      // without anyone touching the chart; `held` is remembered as a bar *time*
      // for that reason. A fit reframes anyway and doesn't ask.
      const held = frame === "fit" ? null : chartRef.current?.timeScale().getVisibleLogicalRange();
      const heldIdx = held ? clampIdx(barsRef.current, held.from) : -1;
      const heldAt = heldIdx >= 0 ? barsRef.current[heldIdx]?.time : undefined;
      // Where the playhead sat in the old array — a follow measures the room the
      // user left to the right of it, and puts the new one back at that spot.
      const prevLast = barsRef.current.length - 1;
      barsRef.current = drawn;
      histCountRef.current = s.history.length;
      candle.setData(
        drawn.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })),
      );
      vol.setData(
        drawn.map((b) => ({ time: b.time as Time, value: b.volume, color: b.close >= b.open ? VOL_UP : VOL_DOWN })),
      );
      const anchors: [Anchor, BandPt[]][] = [
        [gRef.current, s.gBand],
        [nRef.current, s.nBand],
        [aRef.current, s.aBand],
        [wkRef.current, s.wkBand],
      ];
      for (const [a, pts] of anchors) {
        for (const k of BAND_KEYS) a.lines[k].setData(bandData(pts, k));
        // A seek rebuilds the bands from scratch (including backwards, where the
        // fill must shrink), so hand the primitive a fresh array rather than
        // patching the one it holds.
        a.pts = pts.map(toVwapPoint);
        a.band.setPoints(a.pts);
      }
      const setProf = (
        lines: Record<ProfKey, ISeriesApi<"Line">> | null,
        pts: ProfilePt[],
      ) => {
        if (!lines) return;
        for (const k of PROF_KEYS)
          lines[k].setData(pts.map((p) => ({ time: p.time as Time, value: p[k] })));
      };
      setProf(gProfRef.current, s.gProfile);
      setProf(nProfRef.current, s.nProfile);
      // A seek rebuilds the NY value area from tick zero, so the bell is
      // wherever this snapshot says it is — including "not yet", after a rewind
      // to before it.
      nyStartRef.current = s.nProfile.length ? s.nProfile[0].time : NaN;
      nyVaRef.current = s.nProfile.length ? s.nProfile[s.nProfile.length - 1] : null;
      // A seek rebuilds the marks from tick zero, so this is a swap and not a
      // merge — a rewind hands back the shorter list.
      bigsRef.current = s.bigs;
      bigPrimRef.current?.setTrades(s.bigs, bigLotsRef.current);
      setBigCount((n) => (n === s.bigs.length ? n : s.bigs.length));
      // Same swap-not-merge rule for the events, and the position index goes
      // with them: after a rewind the entries an open event maps to are gone.
      eventsRef.current = s.events;
      evPosRef.current = new Map(s.events.map((e, i) => [`${e.kind}:${e.idx}`, i]));
      pushEvents();
      ibRef.current = s.ib;
      mark(s.lastPrice);
      hooksRef.current?.syncIb();
      hooksRef.current?.paint();
      paintDevRef.current?.();
      // The context bars only arrive with a snapshot, so this is where the
      // composite finds the stretch to pin its histogram to.
      paintComposite();
      syncPresent({
        bars: s.bars.length > 0,
        g: s.gBand.length > 0,
        n: s.nBand.length > 0,
        wk: s.wkBand.length > 0,
        gp: s.gProfile.length > 0,
        np: s.nProfile.length > 0,
        ib: s.ib != null,
      });
      // Frame the tail of the loaded history so the replay opens zoomed-in, not
      // fit to the whole (possibly overnight-spanning) session. Skipped when the
      // snapshot is a side effect of something else (a re-anchor) rather than a
      // move through time — the user's zoom is theirs.
      //
      // Framed in bar indices rather than in times, so it means the same thing on
      // every timeframe: the last 90 bars, plus a dozen bars of room on the right
      // to place an order into. A time-based range would be 90 minutes of 30s
      // bars or 90 hours of hourly ones.
      //
      // A seek follows instead (`"follow"`): the clock moved, so the view moves
      // with it — but at the bar spacing the user chose, not back to 90 bars.
      // Zoom out to read the morning, drag the scrubber, and the morning is
      // still the width you left it; only the playhead has moved. It is the same
      // thing playback already does when a new bar arrives at the right edge,
      // and a seek is playback with a bigger step.
      const chart = chartRef.current;
      const last = drawn.length - 1;
      const width = held ? held.to - held.from : 0;
      // A follow with nothing to preserve — the first snapshot of a session, or
      // a viewport the library hasn't laid out yet — is just a fit.
      const keepZoom = frame === "follow" && held && width > 0 && prevLast >= 0;
      if (!chart) {
        // No chart to frame.
      } else if (!keepZoom && frame !== "hold") {
        // Counted off the drawn array, so the context days stay off to the left
        // where they belong: the replay opens on the session, not on Tuesday.
        if (s.bars.length)
          chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, last - 90), to: last + 12 });
      } else if (keepZoom && held) {
        // The room past the newest bar is the user's too — an order gets placed
        // into it — so it is carried across rather than re-imposed. Capped at
        // half the viewport so a wide gap can't push the bars off screen, and
        // replaced by the default margin when the playhead was scrolled off to
        // the right entirely (nothing to preserve, and it has to come back).
        const raw = held.to - prevLast;
        const gap = Math.min(raw >= 0 ? raw : 12, width / 2);
        const to = last + gap;
        const from = to - width;
        chart
          .timeScale()
          .setVisibleLogicalRange(from < 0 ? { from: 0, to: width } : { from, to });
      } else if (held && heldAt != null) {
        // Same bars, new indices: put the range back on the bar it was on. The
        // shift is zero when nothing was prepended, which is every other
        // reframe:false caller (a re-anchor, a big-trade threshold).
        const shift = idxOfTime(drawn, heldAt) - heldIdx;
        if (shift)
          chart.timeScale().setVisibleLogicalRange({ from: held.from + shift, to: held.to + shift });
      }
      hooksRef.current?.reprofile();
    },
    applyStep(r: StepResult) {
      const candle = candleRef.current;
      const vol = volRef.current;
      if (!candle || !vol || !gRef.current || !nRef.current || !aRef.current || !wkRef.current)
        return;
      for (const b of r.barsTail) {
        candle.update({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close });
        vol.update({ time: b.time as Time, value: b.volume, color: b.close >= b.open ? VOL_UP : VOL_DOWN });
        // Same overwrite-or-append the series do with update(): a tail starts at
        // the still-forming bar, which is already the last one we hold.
        const bars = barsRef.current;
        if (bars.length && bars[bars.length - 1].time === b.time) bars[bars.length - 1] = b;
        else bars.push(b);
      }
      const anchors: [Anchor, BandPt[]][] = [
        [gRef.current, r.gTail],
        [nRef.current, r.nTail],
        [aRef.current, r.aTail],
        [wkRef.current, r.wkTail],
      ];
      for (const [a, tail] of anchors) {
        for (const k of BAND_KEYS) for (const p of tail) a.lines[k].update({ time: p.time as Time, value: p[k] });
        mergeTail(a, tail);
      }
      const stepProf = (
        lines: Record<ProfKey, ISeriesApi<"Line">> | null,
        tail: ProfilePt[],
      ) => {
        if (!lines) return;
        for (const k of PROF_KEYS)
          for (const p of tail) lines[k].update({ time: p.time as Time, value: p[k] });
      };
      stepProf(gProfRef.current, r.gProfTail);
      stepProf(nProfRef.current, r.nProfTail);
      // The first NY point the playback ever emits is the bell.
      if (!Number.isFinite(nyStartRef.current) && r.nProfTail.length)
        nyStartRef.current = r.nProfTail[0].time;
      if (r.nProfTail.length) nyVaRef.current = r.nProfTail[r.nProfTail.length - 1];
      mergeBigs(r.bigTail);
      mergeEvents(r.evTail);
      mark(r.lastPrice);

      // The IB's right edge is pinned to the bar grid, so it only needs touching
      // when a bar closes — not once a frame.
      const ibChanged =
        r.ib?.high !== ibRef.current?.high ||
        r.ib?.low !== ibRef.current?.low ||
        r.ib?.complete !== ibRef.current?.complete;
      ibRef.current = r.ib;
      if (r.newBar || ibChanged) hooksRef.current?.syncIb();
      // The two live profiles run every frame — they read the tape, not the bar
      // grid, and the ticks that just printed are already in the forming bar's
      // span. Both go through an accumulator, so a frame costs the ticks it
      // added and nothing more.
      hooksRef.current?.reprofile();
      paintDevRef.current?.();
      // A fixed-range profile is bounded by two bar times the user dragged out,
      // so only a new bar can change one (the range that ends at the live edge
      // gains the bar that just closed).
      if (r.newBar) hooksRef.current?.paint();

      syncPresent({
        bars: barsRef.current.length > 0,
        g: presentRef.current.g || r.gTail.length > 0,
        n: presentRef.current.n || r.nTail.length > 0,
        wk: presentRef.current.wk || r.wkTail.length > 0,
        gp: presentRef.current.gp || r.gProfTail.length > 0,
        np: presentRef.current.np || r.nProfTail.length > 0,
        ib: r.ib != null,
      });
      // Deliberately no scrollToRealTime() here. That call is unconditional — it
      // drags the view back to the right edge (at the default offset) on every
      // bar close, discarding whatever you had panned or zoomed to. The time
      // scale's own `shiftVisibleRangeOnNewBar` already follows the tape when
      // the last bar is visible and leaves the range alone when it isn't, which
      // is the behaviour we want: your view is yours.
    },
    setPosition(p: PositionLine | null) {
      const tape = tapeRef.current;
      posRef.current = p
        ? {
            ...p,
            last: lastPriceRef.current,
            tickSize: tape?.tickSize ?? 0.25,
            pointValue: tape?.pointValue ?? 20,
          }
        : null;
      pushPos();
    },
    setOrders(orders: WorkingOrderView[]) {
      workingRef.current = orders;
      pushOrders();
    },
    setTrades(trades: TradeMarkView[]) {
      tradesRef.current = trades;
      // The count is the only thing React needs from this (the legend row only
      // exists once something has been traded); the marks themselves go straight
      // to the primitive.
      setTradeCount((n) => (n === trades.length ? n : trades.length));
      pushTrades();
    },
  }));

  // Only layers that have actually printed get a row.
  // Read during render rather than mirrored into state: both are set once when a
  // session lands, and the page re-renders this component on every HUD push
  // anyway, so there is nothing here for state to buy.
  const tickSize = tapeRef.current?.tickSize ?? 0.25;
  const tkt: TicketDraft = ticket ?? { size: 1, stopTicks: 0, targetTicks: 0 };

  const legendItems: LegendItem[] = [];
  if (present.g)
    legendItems.push({ key: "vwapGlobex", label: "VWAP · Globex ±1σ ±2σ", color: vwapPalette.globex.middle });
  if (present.n)
    legendItems.push({ key: "vwapNy", label: "VWAP · NY ±1σ ±2σ", color: vwapPalette.ny.middle });
  if (present.wk)
    legendItems.push({ key: "vwapWeekly", label: "VWAP · Weekly ±1σ ±2σ", color: vwapPalette.weekly.middle });
  if (avwapAnchor != null)
    legendItems.push({ key: "vwapAnchored", label: "VWAP · Anchored ±1σ ±2σ", color: vwapPalette.anchored.middle });
  if (present.gp)
    legendItems.push({
      key: "developingProfileGlobex",
      label: "Developing VA · Globex VAH/POC/VAL",
      color: profilePalette.globex.edge,
    });
  if (present.np) {
    legendItems.push({
      key: "developingProfileNy",
      label: "Developing VA · NY VAH/POC/VAL",
      color: profilePalette.ny.edge,
    });
    // The same distribution as a histogram, in its own gutter. Its own row
    // because the levels and the shape are separately useful — and because this
    // one is where the event marginal lands.
    legendItems.push({
      key: "developingVpNy",
      label: `Developing VP · NY session (${PROFILE_BIN}pt rows)`,
      color: "#c4b5fd",
    });
    // The nodes it names, on the same switch as the composite's: the prominence
    // floor behind this row's "…" is what decides they exist at all. The row is
    // here even at zero, dimmed — that knob is the only way back on.
    legendItems.push({
      key: "developingVpNyNodes",
      label:
        nodeProm > 0
          ? `NY nodes · HVN/LVN at ${Math.round(nodeProm * 100)}% prominence`
          : "NY nodes · off",
      color: "#818cf8",
      dim: nodeProm === 0,
    });
  }
  if (present.ib) {
    legendItems.push({ key: "initialBalance", label: "Initial Balance · first 60m H/L", color: ibPalette.line });
    legendItems.push({ key: "ibExtensions", label: "IB extensions · 1×/1.5×/2×", color: ibPalette.ext });
  }
  // "(tick)" and not "(est.)": the replay profiles the real tape, never a
  // reconstruction spread across bar ranges — so the POC print is a price that
  // actually traded.
  if (present.bars)
    legendItems.push({
      key: "volumeProfile",
      label: `Volume profile · POC/VA (${PROFILE_BIN}pt rows)`,
      color: palette.gold,
    });
  // Once the tape has printed, whether or not any sweep has cleared the floor
  // yet: the threshold is on this row now, and a row that waited for a big trade
  // would be missing at exactly the moment you wanted to lower the bar. The
  // label carries the threshold, since that is what "big" currently means.
  if (present.bars)
    legendItems.push({
      key: "bigTrades",
      label: `Big trades · >${bigLots} lots · ${bigCount}`,
      color: palette.blue,
    });
  // The composite gets a row once there are days to build it from — the rule
  // itself sits on the row. How many days went in is a reading under the balance
  // rule (this is how long the auction has been running), so the label says it.
  if (ctxDays > 0) {
    legendItems.push({
      key: "compositeProfile",
      label:
        compDays > 0
          ? `Composite VP · ${compDays} prior session${compDays === 1 ? "" : "s"} · VAH/POC/VAL`
          : `Composite VP · off · ${ctxDays} prior day${ctxDays === 1 ? "" : "s"} loaded`,
      color: compositePalette.poc,
      dim: compDays === 0,
    });
    // The nodes read off it — only once there is a composite for them to be read
    // off. Their own switch is the prominence floor, which the NY-nodes row
    // above carries too, so nothing is stranded when this row isn't here.
    if (compDays > 0)
      legendItems.push({
        key: "compositeNodes",
        label:
          nodeProm > 0
            ? `Composite nodes · HVN/LVN at ${Math.round(nodeProm * 100)}% prominence`
            : "Composite nodes · off",
        color: compositePalette.hvn,
        dim: nodeProm === 0,
      });
  }
  // Same again for the events: the floor lives on these rows, so they are here
  // from the first bar, dimmed until it is raised off zero. The count is quoted
  // with the floor it was counted at — one without the other would be a
  // different number from what is on the chart.
  if (present.bars) {
    legendItems.push({
      key: "sweepBursts",
      label:
        eventStrength > 0
          ? `Sweep bursts · ≥${150 * eventStrength} lots · ${evCount.sweep}`
          : "Sweep bursts · off",
      color: palette.orange,
      dim: eventStrength === 0,
    });
    legendItems.push({
      key: "absorption",
      label:
        eventStrength > 0
          ? `Absorption · ≥${3 * eventStrength}× the session's own · ${evCount.absorb}`
          : "Absorption · off",
      color: palette.blue,
      dim: eventStrength === 0,
    });
  }
  // Only once something has been traded — a row for an empty session would be a
  // toggle for nothing.
  if (tradeCount > 0)
    legendItems.push({
      key: "replayTrades",
      label: `Trades · ${tradeCount} closed`,
      color: palette.green,
    });

  // Hang the page's knobs on whichever rows they belong to. Done in one pass at
  // the end rather than at each push: which row a setting goes on is the page's
  // statement, not this list's, and repeating the lookup twelve times above
  // would only make it easy to forget one.
  if (indicatorSettings)
    for (const it of legendItems) {
      const spec = indicatorSettings[it.key];
      if (spec) it.settings = spec;
    }

  // The ticket, built once and hung in one of two places below — off the ＋ on a
  // mouse, at the foot of the chart on a fingertip. Which one is a layout
  // decision (see DOCK_MENU); the panel itself is the same panel.
  const menuEl =
    plusPrice == null || !menuOpen ? null : (
      <OrderMenu
        price={plusPrice}
        mark={markProp}
        tick={tickSize}
        ticket={tkt}
        docked={DOCK_MENU}
        onTicket={(t) => onTicketChange?.(t)}
        onNudge={(d) =>
          setPlusPrice((p) => (p == null ? p : Math.round((p + d) / tickSize) * tickSize))
        }
        onPlace={(o) => {
          onPlaceTyped?.(o);
          closePlus();
        }}
        onClose={closePlus}
      />
    );

  return (
    <div style={{ position: "relative", width: "100%", height: height ?? "100%", minHeight: 0 }}>
      {/* What the two buttons mean while Space is down. The mapping flips across
          the market, so this is worth saying on screen rather than in a tooltip
          you can't read with a modifier held. */}
      {spaceHeld && (
        <div
          style={{
            position: "absolute",
            top: 8,
            left: 12,
            zIndex: 3,
            display: "flex",
            gap: 8,
            padding: "5px 9px",
            borderRadius: 6,
            background: "rgba(14, 17, 23, 0.86)",
            border: `1px solid ${palette.cardBorder}`,
            fontSize: 11,
            lineHeight: 1,
            pointerEvents: "none",
          }}
        >
          <span style={{ color: palette.muted }}>Click a price —</span>
          <span style={{ color: palette.blue }}>left: limit</span>
          <span style={{ color: palette.orange }}>right: stop</span>
        </div>
      )}
      {/* The tool's version of the same banner. The two channels are the two
          mouse buttons a touchscreen doesn't have, so here they are the choice
          itself rather than a caption about one — pick a side, then tap a price.
          Same corner as the Space banner, because it is the same message. */}
      {orderArmed && (
        <div className="replay-order-armed">
          <button
            type="button"
            className={`replay-side${orderSide === "left" ? " on" : ""}`}
            style={{ color: orderSide === "left" ? palette.blue : palette.muted }}
            onClick={() => setOrderSide("left")}
            aria-pressed={orderSide === "left"}
          >
            limit
          </button>
          <button
            type="button"
            className={`replay-side${orderSide === "right" ? " on" : ""}`}
            style={{ color: orderSide === "right" ? palette.orange : palette.muted }}
            onClick={() => setOrderSide("right")}
            aria-pressed={orderSide === "right"}
          >
            stop
          </button>
          <span style={{ color: palette.muted }}>— now tap a price</span>
        </div>
      )}
      <div className="chart-tools">
        {/* Only where the modifier isn't available. On a mouse Space+click is
            strictly the better gesture — nothing to arm, nothing left armed —
            and a button that duplicates it would just be a slower way in. */}
        {COARSE_POINTER && (
          <ChartToolButton
            icon="🧾"
            label="＋ Order"
            on={orderArmed}
            onClick={() => armOrder(!orderArmed)}
            disabled={!canPlaceOrders}
            title={
              canPlaceOrders
                ? orderArmed
                  ? "Pick limit or stop, then tap a price (Esc to cancel)"
                  : "Place an order — pick limit or stop, then tap a price. The desktop gesture is Space + click."
                : "Load a session first"
            }
          />
        )}
        <ChartToolButton
          icon="📊"
          label={armed ? "Drag a range…" : "Fixed range VP"}
          on={armed}
          onClick={() => arm(!armed)}
          title={
            armed
              ? "Drag across the chart to profile that range (Esc to cancel)"
              : "Fixed-range volume profile — drag across a range to profile it. Drag its edges to resize, its body to move, Del to remove."
          }
        />
        <ChartToolButton
          icon="📏"
          label={rulerArmed ? "Measuring…" : "Measure"}
          on={rulerArmed}
          onClick={() => armRuler(!rulerArmed)}
          title={
            rulerArmed
              ? "Drag (or click, move, click) between two points to measure (Esc to cancel)"
              : "Ruler — measure between two points: points/ticks/%, $ per lot, bars and time. Click the chart or press Esc to dismiss."
          }
        />
        <ChartToolButton
          icon="⚓"
          label={avwapArmed ? "Click a bar…" : "Anchored VWAP"}
          on={avwapArmed}
          onClick={() => armAvwap(!avwapArmed)}
          title={
            avwapArmed
              ? "Click a bar to anchor the VWAP there (Esc to cancel)"
              : "Anchored VWAP — click any bar to draw a VWAP + ±1σ/±2σ bands from that point forward. Keeps developing as the replay runs; σ is tick-derived, like the session anchors. Click again to re-anchor."
          }
        />
        {avwapAnchor != null && (
          <ChartToolButton
            icon="⚓✕"
            label="Clear VWAP"
            onClick={clearAvwap}
            title="Remove the anchored VWAP"
          />
        )}
        {selected != null && (
          <ChartToolButton
            icon="🗑"
            label="Delete"
            onClick={deleteSelected}
            title="Remove this profile (Del)"
          />
        )}
        {ranges.length > 1 && (
          <ChartToolButton
            icon="🧹"
            label="Clear all"
            onClick={clearRanges}
            title="Remove every fixed-range profile"
          />
        )}
      </div>
      <div ref={elRef} style={{ width: "100%", height: "100%", minHeight: 0 }} />
      {/* The long-press ＋, riding the price axis at the price it was summoned
          at, with the ticket hanging off its left. Deliberately a sibling of the
          canvas rather than something drawn into it: it is a menu with typed
          fields, and the press that dismisses it must not be a press the chart's
          own handlers ever see. Positioned by the effect above, not by React. */}
      {plusPrice != null && (
        <>
          <div
            ref={anchorRef}
            className={`replay-anchor${menuOpen && !DOCK_MENU ? " open" : ""}`}
            // The ＋ and the level beside it are the one thing on this chart the
            // platform can mistake for text: they appear under a finger that is
            // still held down, which is exactly when Android goes looking for
            // something to offer copy/select on.
            onContextMenu={(e) => e.preventDefault()}
          >
            {menuOpen && !DOCK_MENU && menuEl}
            {/* The level, readable before you commit to it — the point of making
                this two steps rather than one. An undocked menu prints it itself
                and takes this row's place; a docked one is at the other end of
                the chart, so the axis keeps saying which price it is for. */}
            {(!menuOpen || DOCK_MENU) && (
              <span className="replay-plus-px">{plusPrice.toFixed(2)}</span>
            )}
            <button
              type="button"
              className="replay-plus"
              onClick={() => setMenuOpen((o) => !o)}
              aria-expanded={menuOpen}
              aria-label={`Order at ${plusPrice.toFixed(2)}`}
              title={menuOpen ? "Close the ticket (Esc)" : `Order at ${plusPrice.toFixed(2)}`}
            >
              ＋
            </button>
          </div>
          {/* The docked home: the foot of the chart, clear of whatever the page
              has already parked down there (--chart-floor). Nothing about it is
              pinned to the price, so unlike the anchor it is positioned by CSS
              alone. */}
          {menuOpen && DOCK_MENU && (
            <div className="replay-omenu-dock" onContextMenu={(e) => e.preventDefault()}>
              {menuEl}
            </div>
          )}
        </>
      )}
      <IndicatorLegend items={legendItems} visibility={vis} onToggle={toggle} />
    </div>
  );
});
