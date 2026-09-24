import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSimulatorDays, useSimulatorSession } from "../../hooks/useSimulator";
import type { SimDay } from "../../hooks/useSimulator";
import { attemptIdOf, fetchReplayAttemptDetail } from "../../hooks/useReplays";
import {
  decodeTape,
  ReplayEngine,
  type SessionPayload,
  type Tape,
} from "../../lib/replayEngine";
import { replaySource, type TapeSource } from "../../lib/tapeSource";
import { useTurbo } from "../../hooks/useTurbo";
import { TurboChip } from "./TurboChip";
import { resolveBracketUsd } from "../../lib/bracketUsd";
import {
  DEFAULT_SIM_PREFS,
  SIM_SPEEDS,
  loadSimPrefs,
  saveSimPrefs,
} from "../../lib/simPrefs";
import { fmtClock, fmtPts, fmtUsd, orderView, posLine, simSig, tradeMark } from "../../lib/simViews";
import { loadFillModel, type FillCfg } from "../../lib/fillModel";
import { MICRO_RATIO } from "../../lib/contracts";
import {
  newLog,
  newSim,
  runSim,
  stepSim,
  workingOrders,
  type Log,
  type OrderRec,
  type OrderType,
  type Position,
  type Side,
  type SimState,
} from "../../lib/replaySim";
import {
  loadDynamicSwingVwapParams,
  loadModernVwapParams,
  saveDynamicSwingVwapParams,
  saveModernVwapParams,
} from "../../lib/chartPrefs";
import type { ModernVwapParams } from "../../lib/modernVwap";
import type { DsvParams } from "../../lib/dynamicSwingVwap";
import {
  DEFAULT_TIMEFRAME_ID,
  showsSeconds,
  timeframeById,
  useTimeframeOptions,
} from "../../lib/timeframes";
import type { FilterScope } from "../../lib/queryKeys";
import type { TradeRow } from "../../lib/types";
import { palette } from "../../theme";
import {
  ReplayChart,
  type ReplayChartHandle,
  type TicketDraft,
  type TypedOrder,
} from "./ReplayChart";
import type { IndicatorSettingsMap } from "./IndicatorLegend";
import { dynamicSwingVwapKnobs, modernVwapKnobs } from "./indicatorKnobs";
import { QuickDock } from "./QuickDock";
import { TicketKnobs } from "./TicketKnobs";
import { REVIEW_LEAD_MS } from "./ReviewPanel";
import type { TradeMarkView } from "./TradesPrimitive";

// Replaying a journal day off its own tick tape — the built-in answer to the
// question a screen recording used to answer, and a better one: this is the
// market you traded rather than a video of your screen, so it scrubs, zooms and
// carries every layer the chart stack has.
//
// Playback, plus a ticket you can practise on. No grading — the day's grade
// lives in DayJournalForm and the per-trade intent in TradeDetail.
//
// The orders placed here are *ephemeral*. They run through the same fill model
// a replay sitting does (lib/fillModel, lib/replaySim — the stored commission,
// spread, queue and gesture latency), so what a practice trade books is
// comparable to what a sitting would have booked, and none of it is written
// anywhere: no attempt, no account, no journal mirror, no review owed. Reading
// your own day back and asking "what if I had taken that" is a question worth
// being able to ask cheaply, and an answer worth nothing on a record.
//
// The ▶ in the trades table uses the same machinery on the day's own trades:
// it arms the journal's entry and exit as practice gestures and plays them out,
// so the question "how did that trade go" is answered by watching it go rather
// than by reading its two prices (see `armTrade`).
//
// Practice resets when the session does — a different day, or a different
// contract on the same day — and a ▶ replaces it, since one trade at a time is
// what that gesture means. Collapsing the panel keeps it, because collapsing
// only hides the panel and throwing a position away for that would be a
// surprise.
//
// Two notes worth keeping:
//
//   * The tape is the *full* contract (NQ) even on the days the journal traded
//     the micro (MNQ). Their prices are identical, so the trade marks land where
//     they really did; only the money differs. That difference is carried on the
//     position itself: an armed trade is stamped with the contract the day was
//     traded in, and the sim prices it accordingly.
//   * `drawingsKey` shares the Simulator's namespace on purpose. It is the same
//     chart of the same session, so a fixed-range profile drawn on one day in
//     one place should be there in the other. Not a bug to "fix".

// --- the day's trades, in the shape the clock can gate --------------------

interface PreparedTrade {
  row: TradeRow;
  entryMs: number;
  exitMs: number;
}

/** The journal's local stamps are tz-**aware** (`2026-08-14T09:42:13-04:00`),
 *  and the tape's clock is display-zone wall time rather than UTC. So the
 *  offset is dropped rather than applied: a plain `Date.parse` would resolve
 *  the true instant and land the mark four or five hours from the bar it
 *  belongs to. `/day/{day}` omits the UTC columns anyway. */
function localMs(s: string | null | undefined): number | null {
  if (!s) return null;
  const t = Date.parse(s.slice(0, 19) + "Z");
  return Number.isFinite(t) ? t : null;
}

export function entryMsOf(t: TradeRow): number | null {
  return t.avg_entry == null ? null : localMs(t.entry_ts_local);
}

function prepare(trades: TradeRow[]): PreparedTrade[] {
  const out: PreparedTrade[] = [];
  for (const row of trades) {
    // A null average is an import that never resolved a fill price. There is no
    // honest place to draw it, so it is left off the chart entirely.
    if (row.avg_entry == null || row.avg_exit == null) continue;
    const entryMs = localMs(row.entry_ts_local);
    const exitMs = localMs(row.exit_ts_local);
    if (entryMs == null || exitMs == null) continue;
    out.push({ row, entryMs, exitMs });
  }
  return out.sort((a, b) => a.exitMs - b.exitMs);
}

/** One finished journal trade as the chart's trade layer wants it.
 *
 *  Deliberately not `simViews.tradeMark`: that one's input is a simulated
 *  `LoggedTrade`, and widening it to also accept an ATAS import would make one
 *  function answer to two different notions of a trade. `r` is null because an
 *  import records no risk, and the reason is "manual" because the other four
 *  are facts about the sim's own exit engine.
 *
 *  `entryTime`/`exitTime` are bar times in epoch **seconds**, which is why this
 *  has to be rebuilt whenever the bucketing changes. */
function toMark(p: PreparedTrade, barAt: (ms: number) => number): TradeMarkView {
  return {
    id: p.row.trade_no,
    side: p.row.direction === "Short" ? "short" : "long",
    size: p.row.max_contracts,
    entryTime: barAt(p.entryMs),
    entryPrice: p.row.avg_entry as number,
    exitTime: barAt(p.exitMs),
    exitPrice: p.row.avg_exit as number,
    pnl: p.row.net_pnl,
    r: null,
    reason: "manual",
  };
}

/** Practice trades share the chart's trade layer with the journal's, so their
 *  ids are moved out of the way first. Both count from one, and the layer keys
 *  by id — journal trade #1 and practice trade #1 are two different trades. */
const PRACTICE_ID_BASE = 1_000_000;

// --- which contract this day replays on -----------------------------------

/** The mini a micro is traded against, so a micro day replays on the tape that
 *  exists. The inverse of MICRO_ROOTS in src/journal/config.py, and a table
 *  rather than a leading-`M` strip because that rule is wrong for at least one
 *  listed micro (RTY's is M2K, so the strip would yield `2K`). */
const MINI_OF: Record<string, string> = { MNQ: "NQ", MES: "ES" };

/** The journal's instrument label as a tick-store root: `MNQU6@CME` is recorded
 *  as NQ. Mirrors src/journal/config.py `root_symbol()` — strip the venue, then
 *  the futures month letter and year digits — and then folds micro to mini. */
function rootOf(instrument: string | null | undefined): string | null {
  if (!instrument) return null;
  const sym = instrument.split("@")[0].trim().toUpperCase();
  if (!sym) return null;
  const root = /^([A-Z]+?)[FGHJKMNQUVXZ]\d{1,2}$/.exec(sym)?.[1] ?? sym;
  return MINI_OF[root] ?? root;
}

/** Whether this instrument is the *micro* of the contract its tape is kept
 *  under — MNQ against an NQ tape. `rootOf` folds the two together so the tape
 *  can be found at all; this asks the question that fold erases, because what a
 *  practice fill in it is worth is a tenth of what the tape's own contract is. */
function isMicro(instrument: string | null | undefined): boolean {
  const sym = (instrument ?? "").split("@")[0].trim().toUpperCase();
  if (!sym) return false;
  const root = /^([A-Z]+?)[FGHJKMNQUVXZ]\d{1,2}$/.exec(sym)?.[1] ?? sym;
  return root in MINI_OF;
}

function pickSymbol(
  days: SimDay[] | undefined,
  date: string,
  instrument: string | null,
): string | null {
  const onDay = (days ?? []).filter((d) => d.date === date);
  if (onDay.length === 0) return null;
  if (onDay.length === 1) return onDay[0].symbol;
  const want = rootOf(instrument);
  return (want && onDay.find((d) => d.root === want)?.symbol) ?? onDay[0].symbol;
}

// --- provider -------------------------------------------------------------

interface DayReplayCtx {
  date: string;
  tz: string;
  symbol: string | null;
  daysLoading: boolean;
  hasTape: boolean;
  /** Whether the day was traded in the micro of the tape's contract. An armed
   *  trade is stamped with it, so the practice money matches the money. */
  micro: boolean;
  trades: TradeRow[];
  /** Whether the panel is showing. */
  open: boolean;
  /** Whether it has ever been shown — the panel is mounted on first open and
   *  kept mounted after, never mounted-but-hidden from the start. A chart under
   *  `display:none` measures 0×0, and a snapshot pushed into a zero-width time
   *  scale frames against nothing. */
  mounted: boolean;
  toggle(): void;
  /** Open the panel if needed, rewind to just before this trade's entry, arm the
   *  trade itself on the practice sitting, and play. */
  seekToTrade(t: TradeRow): void;
  pending: { row: TradeRow; nonce: number } | null;
}

const Ctx = createContext<DayReplayCtx | null>(null);

/** Whether the replay is showing, remembered across days and across reloads.
 *
 *  Not a per-day flag: whether you read the tape back is how you work through
 *  the calendar, not a property of one date. Somebody who opens the replay on
 *  every day they look at should not have to open it on every day they look
 *  at. */
const OPEN_KEY = "day.replay.open";

function loadOpen(): boolean {
  try {
    return window.localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

function saveOpen(v: boolean): void {
  try {
    window.localStorage.setItem(OPEN_KEY, v ? "1" : "0");
  } catch {
    /* private mode, a full quota — the panel still works, it just forgets */
  }
}

/** Null outside a provider rather than a throw — the cell renders in tables
 *  that have no replayer above them (the /trades page), and a dash there is the
 *  honest answer. */
export function useDayReplay(): DayReplayCtx | null {
  return useContext(Ctx);
}

export function DayReplayProvider({
  scope,
  date,
  trades,
  instrument,
  rememberOpen = true,
  children,
}: {
  scope: FilterScope;
  date: string;
  trades: TradeRow[];
  instrument: string | null;
  /** The day page remembers whether the replay is showing (see OPEN_KEY). A
   *  standalone embedding (TradeReplay) has no slot, so its arming gesture must
   *  not write that flag and force the day page's panel open next visit. */
  rememberOpen?: boolean;
  children: React.ReactNode;
}) {
  const root = rootOf(instrument);
  const { data: days, isLoading: daysLoading } = useSimulatorDays(root);
  const symbol = useMemo(
    () => pickSymbol(days?.days, date, instrument),
    [days, date, instrument],
  );

  const [open, setOpen] = useState(loadOpen);
  // Mounted from the start when it opens showing: the reason mounting waits for
  // the first open is that a chart under `display:none` measures 0×0, and a
  // panel that comes up open is never hidden.
  const [mounted, setMounted] = useState(open);
  const [pending, setPending] = useState<{ row: TradeRow; nonce: number } | null>(null);
  const nonceRef = useRef(0);

  // A new day keeps the panel as you left it — only the queued seek is dropped,
  // since the trade it names belongs to the day you just left.
  useEffect(() => setPending(null), [date]);

  const toggle = useCallback(() => {
    setOpen((v) => {
      if (!v) setMounted(true);
      if (rememberOpen) saveOpen(!v);
      return !v;
    });
  }, [rememberOpen]);

  const seekToTrade = useCallback((row: TradeRow) => {
    setMounted(true);
    setOpen(true);
    if (rememberOpen) saveOpen(true);
    nonceRef.current += 1;
    setPending({ row, nonce: nonceRef.current });
  }, [rememberOpen]);

  const value = useMemo<DayReplayCtx>(
    () => ({
      date,
      tz: scope.tz,
      symbol,
      daysLoading,
      hasTape: symbol != null,
      micro: isMicro(instrument),
      trades,
      open,
      mounted,
      toggle,
      seekToTrade,
      pending,
    }),
    [
      date,
      scope.tz,
      symbol,
      instrument,
      daysLoading,
      trades,
      open,
      mounted,
      toggle,
      seekToTrade,
      pending,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// --- the seek cell --------------------------------------------------------

export function TradeReplayCell({ trade }: { trade: TradeRow }) {
  const ctx = useDayReplay();
  const entryMs = entryMsOf(trade);
  if (!ctx || !ctx.hasTape || entryMs == null)
    return <span className="section-cap">—</span>;
  return (
    <button
      type="button"
      className="btn-xs"
      title="Replay this trade — from just before the entry, with the trade on"
      onClick={(e) => {
        e.stopPropagation(); // the row's own click expands the trade detail
        ctx.seekToTrade(trade);
      }}
    >
      ▶
    </button>
  );
}

// --- the collapsible slot -------------------------------------------------

/** The toggle and the panel's home, as the page hangs them: mounted on first
 *  open and hidden rather than torn down after, so collapsing keeps the engine,
 *  the decoded tape and the hand-drawn tools alive — but a panel nobody has
 *  opened yet costs nothing at all.
 *
 *  Pinned to the top of the viewport while it is open (see `.day-replay-sticky`
 *  in index.css). The whole point of the replay is to read it *against* the
 *  day's trades table and the journal form, and both of those are below it —
 *  scrolling to one of them used to scroll the chart off the screen. */
export function DayReplaySlot({ height }: { height?: number }) {
  const ctx = useDayReplay();
  if (!ctx) return null;
  return (
    <>
      <div style={{ margin: "6px 0 4px" }}>
        <button
          type="button"
          className={ctx.open ? "active" : ""}
          disabled={!ctx.hasTape && !ctx.daysLoading}
          onClick={ctx.toggle}
          title={
            ctx.hasTape
              ? "Show / hide this day's tape replay"
              : "No tick tape on disk for this day"
          }
        >
          {ctx.open ? "▾ Hide replay" : "▸ Show replay"}
        </button>
      </div>
      {ctx.mounted && (
        <div
          className={ctx.open ? "day-replay-sticky" : undefined}
          style={{ display: ctx.open ? undefined : "none" }}
        >
          <DayReplayPanel height={height} />
        </div>
      )}
    </>
  );
}

// --- the panel ------------------------------------------------------------

export function DayReplayPanel({
  height = 520,
  defaultTfId = DEFAULT_TIMEFRAME_ID,
  caption,
  restartTrade,
}: {
  height?: number;
  /** The bar the panel opens on. The day page keeps the chart stack's default;
   *  the trade-detail embedding opens on 500t, the bar a single trade's few
   *  minutes are actually legible on. */
  defaultTfId?: string;
  caption?: React.ReactNode;
  /** When the panel exists for one trade (TradeReplay), the transport offers
   *  Recall's ⟲ Restart: the ▶ gesture again — rewind to just before this
   *  trade's entry, arm it, play. The day page leaves it unset; its restart is
   *  the ▶ on whichever row you mean. */
  restartTrade?: TradeRow;
}) {
  const ctx = useDayReplay();
  const date = ctx?.date ?? "";
  const symbol = ctx?.symbol ?? null;
  const session = useSimulatorSession(symbol, date || null, ctx?.tz ?? "America/New_York");
  const data = session.data ?? null;

  const chartRef = useRef<ReplayChartHandle | null>(null);
  const engineRef = useRef<ReplayEngine | null>(null);
  const tapeRef = useRef<Tape | null>(null);
  const sessionRef = useRef<SessionPayload | null>(null);
  const sourceRef = useRef<TapeSource | null>(null);
  const clockRef = useRef<number>(NaN);
  const playingRef = useRef(false);
  const speedRef = useRef(1);
  // Hold Ctrl to run ten times whatever the ladder is set to.
  const turbo = useTurbo();
  const lastTsRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  // How many trades are currently drawn — the marks are rebuilt on the count
  // changing, not every frame. -1 forces the next sync to rebuild.
  const marksRef = useRef(-1);
  const lastPubRef = useRef(0);
  const sessionKeyRef = useRef<string | null>(null);
  const doneNonceRef = useRef(0);

  // The practice sitting: a log of what you did, the simulation it implies, and
  // the running signature that says whether the last step resolved anything.
  // None of it leaves this component — see the note at the top of the file.
  const logRef = useRef<Log>(newLog());
  const simRef = useRef<SimState>(newSim());
  const sigRef = useRef("");
  const openRef = useRef<Position | null>(null);
  const idRef = useRef(1);

  const [playing, setPlaying] = useState(false);
  // Seeded from the Simulator's saved prefs and written back through them:
  // "the speed you watch at" is one per-user setting (simPrefs' own words), so
  // this panel, the day page's and a sitting all stay on the knob you last set
  // anywhere. Read-modify-write on save — saveSimPrefs stores the whole
  // object, and a partial would wipe the ticket the Simulator keeps there.
  const [speed, setSpeed] = useState(() => loadSimPrefs().speed);
  speedRef.current = speed;
  const [clockMs, setClockMs] = useState(0);
  const [lastPrice, setLastPrice] = useState(NaN);
  const [tfId, setTfId] = useState(defaultTfId);
  const [ready, setReady] = useState(false);
  const [openPos, setOpenPos] = useState<Position | null>(null);
  const tf = timeframeById(tfId);
  // Including the ones typed into the Charts workspace's picker: this replays a
  // tape the same way, so the vocabulary is the same vocabulary.
  const tfOptions = useTimeframeOptions();

  // The bracket a practice order is measured with, starting at the same numbers
  // a replay sitting starts at. Read through a ref inside the placement path so
  // editing it doesn't rebuild every order callback.
  const [rawTicket, setTicket] = useState<TicketDraft>({
    size: DEFAULT_SIM_PREFS.size,
    stopTicks: DEFAULT_SIM_PREFS.stopTicks,
    targetTicks: DEFAULT_SIM_PREFS.targetTicks,
    stopUsd: DEFAULT_SIM_PREFS.stopUsd,
    targetUsd: DEFAULT_SIM_PREFS.targetUsd,
  });

  // Modern VWAP's parameters, from the sticky-global store the other charts
  // without a run config of their own read (lib/chartPrefs) — how you have the
  // indicator set is a statement about the indicator, not about this session.
  const [mvParams, setMvParams] = useState(loadModernVwapParams);
  const patchMv = useCallback((patch: Partial<ModernVwapParams>) => {
    setMvParams((prev) => {
      const next = { ...prev, ...patch };
      saveModernVwapParams(next);
      return next;
    });
  }, []);
  // The Zeiierman line beside it, on the same sticky-global store.
  const [dsvParams, setDsvParams] = useState(loadDynamicSwingVwapParams);
  const patchDsv = useCallback((patch: Partial<DsvParams>) => {
    setDsvParams((prev) => {
      const next = { ...prev, ...patch };
      saveDynamicSwingVwapParams(next);
      return next;
    });
  }, []);
  const indicatorSettings = useMemo<IndicatorSettingsMap>(() => {
    const mv = modernVwapKnobs(mvParams, patchMv);
    return {
      modernVwap: { title: "Modern VWAP", fields: mv.line },
      modernVwapSignals: { title: "Modern VWAP signals", fields: mv.signals },
      dynamicSwingVwap: {
        title: "Dynamic Swing VWAP",
        fields: dynamicSwingVwapKnobs(dsvParams, patchDsv),
      },
    };
  }, [mvParams, patchMv, dsvParams, patchDsv]);

  // What a fill costs: the replay's own stored model, so a practice number is
  // comparable with a sitting's. Loaded once — this panel offers no way to edit
  // it, and re-reading it mid-day would re-price trades already booked.
  const [fills] = useState(loadFillModel);
  const tickSize = data?.tick_size ?? 0.25;
  const pointValue = data?.point_value ?? 20;
  const tickUsd = tickSize * pointValue;
  /** The ticket with any dollar-pinned leg resolved to the distance it comes to
   *  on this day's contract — what every order path below reads, and read
   *  through a ref inside the placement path so editing it doesn't rebuild every
   *  order callback. */
  const qc = useQueryClient();
  const ticket = resolveBracketUsd(rawTicket, tickUsd);
  const ticketRef = useRef(ticket);
  ticketRef.current = ticket;
  const fillCfg = useMemo<FillCfg>(
    () => ({ ...fills, pointValue, tickSize }),
    [fills, pointValue, tickSize],
  );

  const prepared = useMemo(() => prepare(ctx?.trades ?? []), [ctx?.trades]);
  const preparedRef = useRef(prepared);
  preparedRef.current = prepared;

  const barAt = useCallback(
    (ms: number) => engineRef.current?.barTimeAt(ms) ?? Math.floor(ms / 1000),
    [],
  );

  /** Hand the trade layer both sets at once: the journal's trades that had
   *  *finished* by this clock, and every practice trade booked so far.
   *
   *  One channel, so they are one push. An in-flight journal trade is left off
   *  because the layer encodes win or loss from the P&L, and half a trade would
   *  render an outcome that hasn't happened; a practice trade only exists once
   *  it is closed, so the same rule needs no gate. */
  const paintTrades = useCallback(
    (clock: number) => {
      if (!engineRef.current) return;
      const list = preparedRef.current;
      let n = 0;
      while (n < list.length && list[n].exitMs <= clock) n++;
      marksRef.current = n;
      const journal = list.slice(0, n).map((p) => toMark(p, barAt));
      const practice = simRef.current.trades.map((t) => {
        const m = tradeMark(t, barAt);
        return { ...m, id: m.id + PRACTICE_ID_BASE };
      });
      chartRef.current?.setTrades(journal.concat(practice));
    },
    [barAt],
  );

  /** The frame loop's cheaper door onto the above: the journal marks change on
   *  their count changing, not sixty times a second. */
  const syncMarks = useCallback(
    (clock: number) => {
      const list = preparedRef.current;
      let n = 0;
      while (n < list.length && list[n].exitMs <= clock) n++;
      if (n === marksRef.current) return;
      paintTrades(clock);
    },
    [paintTrades],
  );

  /** Hand a fresh simulation to the chart and the dock. */
  const publishSim = useCallback(
    (st: SimState, clock: number) => {
      simRef.current = st;
      sigRef.current = simSig(st);
      openRef.current = st.open;
      // The stepper appends to the trades array and moves the position object in
      // place, so React is handed copies or it sees the same reference and skips
      // the render.
      setOpenPos(st.open ? { ...st.open } : null);
      const views = workingOrders(st).map((o) => orderView(o, clock, st.open, pointValue));
      chartRef.current?.setPosition(st.open ? posLine(st.open, barAt, pointValue) : null);
      chartRef.current?.setOrders(views);
      paintTrades(clock);
    },
    [barAt, paintTrades, pointValue],
  );

  // Re-derive the whole practice sitting from its log. Every gesture goes
  // through here: they are rare enough that one pass over the tape costs
  // nothing, and it means there is no second, optimistic path that could
  // disagree with what a later scrub produces.
  const rebuild = useCallback(
    (clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      publishSim(runSim(tape, logRef.current, clock, fillCfg), clock);
    },
    [fillCfg, publishSim],
  );
  // Called from the engine effect, which must not re-run when this changes.
  const rebuildRef = useRef(rebuild);
  rebuildRef.current = rebuild;

  const append = useCallback(
    (next: Log) => {
      logRef.current = next;
      rebuild(clockRef.current);
    },
    [rebuild],
  );

  // Fold a played tick range into the running simulation, and only re-render on
  // the step where something actually resolved.
  const advanceSim = useCallback(
    (from: number, to: number, clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      const st = simRef.current;
      stepSim(tape, logRef.current, st, from, to, clock, fillCfg);
      if (simSig(st) !== sigRef.current) publishSim(st, clock);
    },
    [fillCfg, publishSim],
  );

  /** The mark: the last print the replay has reached. */
  const markPrice = useCallback((): number => {
    const v = engineRef.current?.lastPriceValue() ?? NaN;
    return Number.isFinite(v) ? v : NaN;
  }, []);

  /** Place a practice order. `at` is the price its bracket is measured from —
   *  the mark for a market order, the resting price for a limit or a stop.
   *
   *  No discipline layer here, unlike a replay sitting: nothing is at stake and
   *  nothing is recorded, so there is no account to protect and no habit to
   *  hold to. The ticket is read off a ref so editing it doesn't rebuild the
   *  whole placement path. */
  const placeOrder = useCallback(
    (type: OrderType, side: Side, price: number | null, at: number) => {
      const eng = engineRef.current;
      if (!eng) return;
      const { size, stopTicks, targetTicks } = ticketRef.current;
      const dir = side === "long" ? 1 : -1;
      const rec: OrderRec = {
        id: idRef.current++,
        type,
        side,
        size,
        ms: clockRef.current,
        idx: eng.cursorIndex(),
        price,
        stop: stopTicks > 0 ? at - dir * stopTicks * tickSize : null,
        target: targetTicks > 0 ? at + dir * targetTicks * tickSize : null,
        // Resolved to prices at the gesture, so a rebuild reproduces the ladder
        // as it was rather than under whatever the ticket says later.
        trail: null,
        edits: [],
        cancelMs: null,
      };
      const log = logRef.current;
      append({ ...log, orders: [...log.orders, rec] });
    },
    [append, tickSize],
  );

  const placeMarket = useCallback(
    (side: Side) => {
      if (!ready) return;
      const px = markPrice();
      if (!Number.isFinite(px)) return;
      placeOrder("market", side, null, px);
    },
    [markPrice, placeOrder, ready],
  );

  /** Rest an order at a price, held one tick clear of the mark on the side its
   *  type belongs on — a marketable resting order would fill on the next print
   *  at a price better than the market, which is not a thing the tape can do. */
  const placeResting = useCallback(
    (price: number, side: Side, type: "limit" | "stop") => {
      if (!ready) return;
      const mk = markPrice();
      if (!Number.isFinite(mk) || !Number.isFinite(price)) return;
      const px = Math.round(price / tickSize) * tickSize;
      const above = type === "stop" ? side === "long" : side === "short";
      const rest = above ? Math.max(px, mk + tickSize) : Math.min(px, mk - tickSize);
      placeOrder(type, side, rest, rest);
    },
    [markPrice, placeOrder, ready, tickSize],
  );

  /** Space + click on the chart, at a price. Which order that is falls out of
   *  the geometry: the left button places the passive order at that price — a
   *  bid under the market, an offer over it — and the right button the one that
   *  has to be run through, a sell stop under and a buy stop over. */
  const placeAt = useCallback(
    (price: number, button: "left" | "right") => {
      const mk = markPrice();
      if (!Number.isFinite(mk)) return;
      const below = price < mk;
      const passive = button === "left";
      const side: Side = passive === below ? "long" : "short";
      placeResting(price, side, passive ? "limit" : "stop");
    },
    [markPrice, placeResting],
  );

  /** The chart's long-press ticket, where the type and the side were named
   *  outright rather than inferred from where the click landed. */
  const placeTyped = useCallback(
    (o: TypedOrder) => placeResting(o.price, o.side, o.type),
    [placeResting],
  );

  const cancelOrder = useCallback(
    (id: number) => {
      const log = logRef.current;
      append({
        ...log,
        orders: log.orders.map((o) =>
          o.id === id && o.cancelMs == null ? { ...o, cancelMs: clockRef.current } : o,
        ),
      });
    },
    [append],
  );

  // A level was dragged on the chart — a working order's resting price or either
  // bracket leg. The chart has already drawn it where it landed and clamped it
  // somewhere it couldn't fill on the spot, so this only records *when* it moved.
  const editOrder = useCallback(
    (id: number, next: { price: number | null; stop: number | null; target: number | null }) => {
      const log = logRef.current;
      append({
        ...log,
        orders: log.orders.map((o) =>
          o.id === id ? { ...o, edits: [...o.edits, { ms: clockRef.current, ...next }] } : o,
        ),
      });
    },
    [append],
  );

  // The open position's bracket was dragged. Its own channel in the log rather
  // than an edit on the order that opened the position: with several fills
  // making up one position, "the stop" belongs to the position.
  const moveBracket = useCallback(
    (b: { stop: number | null; target: number | null }) => {
      if (!openRef.current) return;
      const log = logRef.current;
      append({ ...log, brackets: [...log.brackets, { ms: clockRef.current, ...b }] });
    },
    [append],
  );

  const moveOrder = useCallback(
    (o: { id: number; price: number; stop: number | null; target: number | null }) =>
      editOrder(o.id, { price: o.price, stop: o.stop, target: o.target }),
    [editOrder],
  );

  const closeManual = useCallback(() => {
    if (!openRef.current) return;
    const log = logRef.current;
    append({ ...log, closes: [...log.closes, { ms: clockRef.current }] });
  }, [append]);

  /** Everything off: the position at the last print, and every order still
   *  working with it. One append rather than a close followed by n cancels, so
   *  a seek either lands before it and nothing came off, or after it and
   *  everything did — half a flatten is not a state to sit in. */
  const closeAll = useCallback(() => {
    const live = new Set(workingOrders(simRef.current).map((o) => o.id));
    const hadPos = openRef.current != null;
    if (!hadPos && live.size === 0) return;
    const log = logRef.current;
    const ms = clockRef.current;
    append({
      ...log,
      orders: live.size
        ? log.orders.map((o) => (live.has(o.id) ? { ...o, cancelMs: ms } : o))
        : log.orders,
      closes: hadPos ? [...log.closes, { ms }] : log.closes,
    });
  }, [append]);

  /** The bracket a trade actually went out with, or null when there is no
   *  record of one.
   *
   *  A replay sitting's trades are a mirror of its log, so the order that opened
   *  one is still on disk with the legs it carried — and those are what the ▶
   *  must arm, not whatever the ticket happens to say now. Arming from the
   *  ticket made the panel run a bracket the trade never had: a default 50-tick
   *  stop on a trade placed with 66 stops out on a print the real stop stood a
   *  point clear of, and the replay then disagreed with the journal it was
   *  drawn from.
   *
   *  Which order opened the position is the last one placed on that side at or
   *  before the fill — the log stores no trade→order link, and a later order or
   *  one on the other side cannot be the one that filled here.
   *
   *  Both shapes of leg are passed through as recorded. For a market order the
   *  distances are what the engine uses (they re-hang off *this* fill, as the
   *  live plant does); a resting order carries none and its prices are the
   *  levels it was drawn against. Either way the record decides, not this. */
  const placedLegs = useCallback(
    async (row: TradeRow, entryMs: number) => {
      const id = attemptIdOf(row.source_file);
      if (!id) return null;
      let orders: OrderRec[];
      try {
        orders = (await fetchReplayAttemptDetail(qc, id)).log?.orders ?? [];
      } catch {
        return null; // The sitting's files are gone; the ticket still replays it.
      }
      const side: Side = row.direction === "Short" ? "short" : "long";
      let opener: OrderRec | null = null;
      for (const o of orders) {
        if (o.side !== side || o.ms > entryMs) continue;
        if (!opener || o.ms > opener.ms) opener = o;
      }
      return opener;
    },
    [qc],
  );

  /** Put the journal's own trade on as a practice fill, so the ▶ replays the
   *  trade rather than only the tape it happened on: a market order that lands
   *  where your entry landed, and a flatten that lands where your exit did.
   *
   *  Both gestures are stamped a latency *early*. Nothing you do reaches the
   *  market at the instant you did it — the fill model holds every gesture for
   *  `latencyMs` and resolves it against the tape as it stood then — so placing
   *  the order that much before the recorded fill is what makes the fill land on
   *  the print your fill landed on. The price is still the tape's, crossed and
   *  charged like any other: this is what the trade would have cost you here,
   *  not a replay of the numbers the journal already holds.
   *
   *  It *replaces* the practice sitting rather than adding to it. One trade at a
   *  time is what the ▶ means, and a position left over from the last one would
   *  net against this one instead of standing beside it.
   *
   *  The bracket is the one the trade was placed with, read back off its own
   *  sitting (`placedLegs`). A trade with no sitting behind it — an imported
   *  broker export — has no recorded bracket to read, and falls back to the
   *  ticket as it stands, measured from the recorded entry. Either leg can get
   *  there before the exit does; the flatten then finds a flat position and does
   *  nothing, exactly as a rewind past a trade does.
   *
   *  What it does *not* carry is the management: the drags recorded against the
   *  open position (`log.brackets`) and any trail the order went out with are
   *  left off, so this is the bracket as placed rather than the bracket as
   *  worked. A trade you pulled the target in on comes off where you first put
   *  it, not where you ended up. */
  const armTrade = useCallback(
    async (row: TradeRow) => {
      const entryMs = entryMsOf(row);
      if (entryMs == null) return;
      const placed = await placedLegs(row, entryMs);
      const eng = engineRef.current;
      if (!eng) return;
      const lag = fillCfg.latencyMs;
      const side: Side = row.direction === "Short" ? "short" : "long";
      const dir = side === "long" ? 1 : -1;
      const { stopTicks, targetTicks } = ticketRef.current;
      const at = row.avg_entry as number;
      const exitMs = localMs(row.exit_ts_local);
      const rec: OrderRec = {
        id: idRef.current++,
        type: "market",
        side,
        size: Math.max(1, row.max_contracts || 1),
        ms: entryMs - lag,
        idx: eng.cursorIndex(),
        price: null,
        stop: placed ? placed.stop : stopTicks > 0 ? at - dir * stopTicks * tickSize : null,
        target: placed ? placed.target : targetTicks > 0 ? at + dir * targetTicks * tickSize : null,
        stopTicks: placed?.stopTicks,
        targetTicks: placed?.targetTicks,
        trail: null,
        edits: [],
        cancelMs: null,
        micro: ctx?.micro ?? false,
      };
      logRef.current = {
        orders: [rec],
        closes: exitMs == null ? [] : [{ ms: exitMs - lag }],
        brackets: [],
      };
      rebuild(clockRef.current);
    },
    [ctx?.micro, fillCfg.latencyMs, placedLegs, rebuild, tickSize],
  );

  const stop = useCallback(() => {
    playingRef.current = false;
    setPlaying(false);
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    lastTsRef.current = null;
    if (Number.isFinite(clockRef.current)) setClockMs(clockRef.current);
  }, []);

  /** The playing clock and the mark at ~10 Hz. The rAF loop must not re-render
   *  React sixty times a second to move a range input. */
  const publishClock = useCallback((clock: number, price: number) => {
    const now = performance.now();
    if (now - lastPubRef.current < 100) return;
    lastPubRef.current = now;
    setClockMs(clock);
    if (Number.isFinite(price)) setLastPrice(price);
  }, []);

  const frame = useCallback(
    (ts: number) => {
      const eng = engineRef.current;
      const src = sourceRef.current;
      if (!playingRef.current || !eng || !src) return;
      const last = lastTsRef.current ?? ts;
      lastTsRef.current = ts;
      const { clock, atEnd } = src.clockFor(clockRef.current, ts - last, speedRef.current * turbo.mult.current);
      const r = eng.advance(clock);
      chartRef.current?.applyStep(r);
      clockRef.current = clock;
      // The ticks just played are the ticks a working order fills against, so
      // the simulation is stepped over exactly the range the chart drew.
      advanceSim(r.fromIdx, r.toIdx, clock);
      syncMarks(clock);
      publishClock(clock, r.lastPrice);
      if (atEnd) {
        stop();
        return;
      }
      rafRef.current = requestAnimationFrame(frame);
    },
    [advanceSim, publishClock, stop, syncMarks],
  );

  const play = useCallback(() => {
    const s = sessionRef.current;
    if (!s || !engineRef.current || playingRef.current) return;
    if (clockRef.current >= s.session_end_ms) return;
    playingRef.current = true;
    setPlaying(true);
    lastTsRef.current = null;
    rafRef.current = requestAnimationFrame(frame);
  }, [frame]);

  const seekTo = useCallback(
    (ms: number) => {
      const eng = engineRef.current;
      const s = sessionRef.current;
      if (!eng || !s) return;
      stop();
      // Floored at the session start, not the RTH open: the journal holds
      // pre-open entries, and they have to stay reachable.
      const c = Math.max(s.session_start_ms, Math.min(s.session_end_ms, ms));
      chartRef.current?.setSnapshot(eng.snapshotTo(c), { reframe: "follow" });
      clockRef.current = c;
      setClockMs(c);
      setLastPrice(eng.lastPriceValue());
      // The tape can't run backwards, so a seek re-derives the practice sitting
      // at the new clock rather than unwinding it — which is also what makes a
      // rewind un-happen anything you did inside the skipped stretch, exactly as
      // it does in a replay sitting.
      rebuild(c);
    },
    [rebuild, stop],
  );

  /** Hand the chart everything it needs from scratch. Idempotent, because the
   *  chart can rebuild under us (a remount throws the surface away) and says so
   *  through `onReady` rather than leaving us to guess. */
  const publish = useCallback(() => {
    const eng = engineRef.current;
    const tape = tapeRef.current;
    if (!eng || !tape) return;
    chartRef.current?.setTape(tape, { keepTools: true });
    chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: true });
    marksRef.current = -1;
    // The position, the working orders and both sets of trade marks — a rebuilt
    // surface has none of them.
    publishSim(simRef.current, clockRef.current);
  }, [publishSim]);

  // Build the engine: on the session arriving, and again on a re-bucketing.
  useEffect(() => {
    if (!data) {
      setReady(false);
      return;
    }
    const tape = decodeTape(data);
    const eng = new ReplayEngine(tape, data, timeframeById(tfId));
    engineRef.current = eng;
    tapeRef.current = tape;
    sessionRef.current = data;
    sourceRef.current = replaySource(data.session_end_ms);

    // A timeframe change keeps the clock where it was — the same moment, cut
    // into different bars. A different session starts at its own default.
    const key = `${data.symbol}|${data.date}`;
    const same = sessionKeyRef.current === key;
    sessionKeyRef.current = key;
    // A different session is a different practice sitting. Nothing carries over
    // — the log's fills are indices into a tape that no longer exists.
    if (!same) {
      logRef.current = newLog();
      simRef.current = newSim();
      sigRef.current = "";
      openRef.current = null;
      idRef.current = 1;
      setOpenPos(null);
    }
    clockRef.current =
      same && Number.isFinite(clockRef.current)
        ? Math.max(data.session_start_ms, Math.min(data.session_end_ms, clockRef.current))
        : data.default_start_ms;
    setClockMs(clockRef.current);
    setReady(true);
    publish();
    setLastPrice(eng.lastPriceValue());
    return () => stop();
  }, [data, tfId, publish, stop]);

  // A fill costing something different re-derives the sitting under the new
  // rules — the log is what you did, and what it costs is priced again.
  useEffect(() => {
    if (ready) rebuildRef.current(clockRef.current);
  }, [fillCfg, ready]);

  // The day's trades changed under us (an attempt switch re-cuts them).
  useEffect(() => {
    marksRef.current = -1;
    syncMarks(clockRef.current);
  }, [prepared, syncMarks]);

  // A tab in the background still runs rAF in some browsers, and a replay
  // nobody is watching is pure main thread.
  useEffect(() => {
    const onVis = () => {
      if (document.hidden) stop();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [stop]);

  useEffect(() => stop, [stop]);

  // A ▶ in the trades table. It can land before the panel has ever mounted, so
  // it waits here for the engine rather than being pushed through a ref.
  const pending = ctx?.pending ?? null;
  useEffect(() => {
    if (!pending || !ready) return;
    if (doneNonceRef.current === pending.nonce) return;
    doneNonceRef.current = pending.nonce;
    const ms = entryMsOf(pending.row);
    if (ms == null) return;
    seekTo(ms - REVIEW_LEAD_MS);
    // Arming can need a round trip for the trade's own bracket, so the play
    // waits on it: starting first would run the seconds before the entry with
    // no order on, and on a cached attempt there is nothing to wait for anyway.
    let live = true;
    void armTrade(pending.row).then(() => {
      if (live) play();
    });
    return () => {
      live = false;
    };
  }, [pending, ready, seekTo, armTrade, play]);

  if (!ctx) return null;
  if (ctx.daysLoading) return <div className="notice">Looking for this day's tape…</div>;
  if (!symbol)
    return (
      <div className="notice">
        No tick tape on disk for {date} — nothing to replay.
      </div>
    );

  const s = sessionRef.current;
  const canPlay = ready && s != null;
  // What the open position is worth at the last print. Recomputed on render
  // rather than pushed, because the mark it reads is already state. A position
  // in the micro moves the same prices for a tenth of the money, and which
  // contract it is in is carried on the position itself.
  const openPnl =
    openPos && Number.isFinite(lastPrice)
      ? (lastPrice - openPos.entryPrice) *
        (openPos.side === "long" ? 1 : -1) *
        (openPos.micro ? pointValue / MICRO_RATIO : pointValue) *
        openPos.size
      : 0;

  return (
    <div className="panel">
      <div className="sim-transport">
        <button
          type="button"
          className={playing ? "active" : ""}
          disabled={!canPlay}
          onClick={() => (playing ? stop() : play())}
        >
          {playing ? "⏸ Pause" : "▶ Play"}
        </button>
        <button
          type="button"
          disabled={!canPlay}
          onClick={() => s && seekTo(s.session_start_ms)}
          title="Back to the start of the tape"
        >
          ⏮
        </button>
        {restartTrade && (
          <button
            type="button"
            data-trade-replay-restart
            disabled={!canPlay}
            onClick={() => ctx?.seekToTrade(restartTrade)}
            title="Replay the trade — back to five seconds before the entry, with the trade on again"
          >
            ⟲ Restart
          </button>
        )}
        <button
          type="button"
          disabled={!canPlay}
          onClick={() => s && seekTo(s.rth_open_ms)}
          title="Jump to the RTH open"
        >
          Open
        </button>
        <span className="sim-lbl">Speed</span>
        <select
          value={speed}
          disabled={!canPlay}
          title="Hold Ctrl to run 10× this"
          onChange={(e) => {
            const v = Number(e.target.value);
            speedRef.current = v;
            setSpeed(v);
            saveSimPrefs({ ...loadSimPrefs(), speed: v });
          }}
        >
          {SIM_SPEEDS.map((v) => (
            <option key={v} value={v}>
              {v}×
            </option>
          ))}
        </select>
        <TurboChip on={turbo.on} speed={speed} />
        <input
          type="range"
          className="sim-scrub"
          disabled={!canPlay}
          min={s?.session_start_ms ?? 0}
          max={s?.session_end_ms ?? 1}
          step={1000}
          value={clockMs}
          onChange={(e) => seekTo(Number(e.target.value))}
        />
        <span className="sim-clock" style={{ fontFamily: "monospace", minWidth: 78 }}>
          {fmtClock(clockMs)}
        </span>
      </div>
      {/* Positioned, because the order window floats inside it — QuickDock
          measures itself against its offset parent. The height is capped against
          the viewport as well as the caller's number: a sticky panel taller than
          the window can't stay pinned, which is the behaviour it exists for. */}
      <div
        data-day-replay
        style={{ height: `min(${height}px, 58vh)`, minHeight: 0, position: "relative" }}
      >
        <ReplayChart
          ref={chartRef}
          symbol={symbol}
          tapeContract={symbol}
          tz={ctx?.tz ?? "America/New_York"}
          tfLabel={tf.label}
          tfOptions={tfOptions}
          onTfChange={setTfId}
          secondsAxis={showsSeconds(tf)}
          canPlaceOrders={canPlay}
          mark={lastPrice}
          ticket={ticket}
          onTicketChange={setTicket}
          onPlaceOrder={placeAt}
          onPlaceTyped={placeTyped}
          onOrderMove={moveOrder}
          onOrderCancel={cancelOrder}
          onBracketChange={moveBracket}
          onFlatten={closeManual}
          modernVwap={mvParams}
          dynamicSwingVwap={dsvParams}
          indicatorSettings={indicatorSettings}
          drawingsKey={`${symbol}|${date}`}
          onReady={publish}
        />
        {/* The same order window the replay has, on the same fill model — but
            nothing here is written down. See the note at the top of the file. */}
        <QuickDock>
          <TicketKnobs ticket={ticket} onChange={setTicket} tickUsd={tickUsd} />
          {openPos && (
            <>
              <span className="sim-quick-pos">
                <span style={{ color: openPos.side === "long" ? palette.green : palette.red }}>
                  {openPos.side === "long" ? "LONG" : "SHORT"} ×{openPos.size}
                </span>
                <span style={{ color: palette.muted }}>@ {fmtPts(openPos.entryPrice)}</span>
                {Number.isFinite(lastPrice) && (
                  <b style={{ color: openPnl >= 0 ? palette.green : palette.red }}>
                    {fmtUsd(openPnl)}
                  </b>
                )}
              </span>
              <button
                type="button"
                className="sim-quick-btn flat"
                onClick={closeAll}
                title="Flatten at market and cancel everything still working"
              >
                Close
              </button>
            </>
          )}
          <button
            type="button"
            className="sim-quick-btn sell"
            onClick={() => placeMarket("short")}
            disabled={!canPlay}
            title="Sell at market"
          >
            <span>SELL</span>
            <b>{Number.isFinite(lastPrice) ? fmtPts(lastPrice) : "—"}</b>
          </button>
          <button
            type="button"
            className="sim-quick-btn buy"
            onClick={() => placeMarket("long")}
            disabled={!canPlay}
            title="Buy at market"
          >
            <span>BUY</span>
            <b>{Number.isFinite(lastPrice) ? fmtPts(lastPrice) : "—"}</b>
          </button>
        </QuickDock>
      </div>
      {session.isLoading && <div className="notice">Loading the tape…</div>}
      {session.isError && (
        <div className="notice">
          Couldn't load this session's tape
          {session.error instanceof Error ? ` — ${session.error.message}` : ""}.
        </div>
      )}
      <div className="section-cap" style={{ marginTop: 6 }}>
        {caption ?? (
          <>
            The day's own tick tape, replayed. Each trade appears as it closed — scrub to the end
            to see them all. ▶ in the table below rewinds to five seconds before that entry, puts
            the trade itself on, and plays it out hands-off: it enters where you entered, carries
            the stop and target you placed it with — not what you dragged them to — and comes off at
            your exit or on a bracket, whichever the tape reaches first. You can also trade it
            yourself, with the same buttons and the same
            fill model a replay sitting uses. Either way the orders are practice only: nothing is
            recorded, and they are gone when the day is.
          </>
        )}
      </div>
    </div>
  );
}

// --- one trade, standalone ------------------------------------------------

/** Fire the ▶ gesture once, as soon as the tape is found: rewind to just
 *  before the entry, arm the trade, play. A component rather than an effect in
 *  TradeReplay so it can read the provider it sits inside. */
function ArmOnMount({ trade }: { trade: TradeRow }) {
  const ctx = useDayReplay();
  const done = useRef(false);
  useEffect(() => {
    if (done.current || !ctx?.hasTape) return;
    done.current = true;
    ctx.seekToTrade(trade);
  }, [ctx, trade]);
  return null;
}

/** The day replayer scoped to one trade — what the trade detail shows instead
 *  of a static reconstruction: the same panel, the same practice sim, but the
 *  provider carries only this trade and the ▶ has already been pressed. Opens
 *  on the tick bar because one trade's few minutes are illegible on the day
 *  page's default. `rememberOpen` is off: arming here must not force the day
 *  page's panel open on its next visit. */
export function TradeReplay({
  scope,
  trade,
  height,
}: {
  scope: FilterScope;
  trade: TradeRow;
  height?: number;
}) {
  const trades = useMemo(() => [trade], [trade]);
  return (
    <DayReplayProvider
      scope={scope}
      date={String(trade.entry_ts_local).slice(0, 10)}
      trades={trades}
      instrument={trade.instrument}
      rememberOpen={false}
    >
      <ArmOnMount trade={trade} />
      <DayReplayPanel
        height={height}
        defaultTfId="500t"
        restartTrade={trade}
        caption={
          <>
            This trade on its day's own tape, from five seconds before the entry: it enters where
            you entered, carries the stop and target you placed it with — not what you dragged them
            to afterwards — and comes off at your exit or on a bracket, whichever the tape reaches
            first. Scrub or replay it as often as you like — the orders are practice only, and
            nothing is recorded.
          </>
        }
      />
    </DayReplayProvider>
  );
}
