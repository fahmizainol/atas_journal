// Charts → Live. The same chart surface as Replay, over a tape still arriving.
//
// Almost nothing here is new. The engine, the chart, the view mappers and the
// trade simulation are the ones Replay uses; what differs is the three things
// lib/tapeSource named — where the clock comes from, whether the tape ends, and
// what you may do to it — plus how the ticks get in: a poll that appends into a
// GrowableTape, rather than one decode of a finished session.
//
// WHAT THIS PAGE MAY NOT DO. `liveSource` reports canSeek / canRewind /
// canSetSpeed / canStepBar false, and every one of those is load-bearing:
//
//   - No seek and no rewind, so `truncateLog` is never imported here at all. A
//     rewind un-does actions taken after a point in time, which is coherent only
//     when the tape can be re-taken. A live fill really happened, and un-happening
//     it would be a lie about what the session did. The blotter is append-only,
//     and that is the whole difference between practising and keeping a record.
//   - No speed: the clock is the last print, not a playback rate.
//   - No step-bar: `nextBarClockMs()` reads forward on a tick timeframe, and the
//     print that would complete the forming bar has not happened yet.
//
// SHADOW SIGNALS ARE NOT ORDERS. The right-hand panel says where each registered
// strategy *would have* signalled — prefix re-runs of the same `run_session` the
// backtest calls, so live cannot disagree with the backtest. Nothing on this page
// can route an order, and nothing about it should be shaped as though it might
// (docs/live-shadow-plan.md § Phase 7).
//
// THE FEED IS SIMULATED, AND SAYS SO. Until Phase 5 there is no live tick source
// in this project, so the source is a cached Databento day replayed at wall-clock
// speed. The banner names the day and the multiplier for as long as it runs: a
// surface that looked live while showing last February would be the single most
// expensive thing this page could get wrong.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ReplayChart, type ReplayChartHandle } from "../components/charts/ReplayChart";
import { TimeframeControl } from "../components/charts/TimeframeControl";
import { ChartTopBar } from "../components/charts/ChartTopBar";
import { SimIndicators } from "../components/charts/SimIndicators";
import type { WorkingOrderView } from "../components/charts/OrdersPrimitive";
import { useSimulatorDays } from "../hooks/useSimulator";
import {
  setLiveModes,
  startFakeFeed,
  startRithmicFeed,
  stopFeed,
  useLiveHeader,
  useLiveSignals,
  useLiveStatus,
  useLiveTape,
} from "../hooks/useLive";
import type { GrowableTape } from "../lib/growableTape";
import {
  sessionPayloadFor,
  type LiveBackfill,
  type LiveSignals,
  type ShadowStrategy,
} from "../lib/liveTypes";
import { ReplayEngine, type IbBox, type RangeBox, type Tape } from "../lib/replayEngine";
import { liveSource } from "../lib/tapeSource";
import { showsSeconds, timeframeById, TIMEFRAMES } from "../lib/timeframes";
import {
  newLog,
  newSim,
  SimLadder,
  stepSim,
  workingOrders,
  type Log,
  type OrderRec,
  type OrderType,
  type Position,
  type Side,
  type SimState,
  type Trade,
} from "../lib/replaySim";
import {
  fmtClock,
  fmtPts,
  fmtUsd,
  orderView,
  posLine,
  simSig,
  tradeMark,
} from "../lib/simViews";
import { palette } from "../theme";

const TZ = "New York";
const SPEEDS = [1, 5, 15, 60, 300, 900];

export function LiveChart() {
  const statusQ = useLiveStatus();
  const status = statusQ.data;
  const gen = status?.running ? (status.gen ?? null) : null;
  const headerQ = useLiveHeader(gen, TZ);
  const header = headerQ.data ?? null;
  const signalsQ = useLiveSignals(gen);


  const [tfId, setTfId] = useState("t500");
  const tf = useMemo(() => timeframeById(tfId), [tfId]);
  const tfRef = useRef(tf);
  tfRef.current = tf;

  // --- imperative refs (the frame loop reads these, never React state) ------
  const chartRef = useRef<ReplayChartHandle>(null);
  const engineRef = useRef<ReplayEngine | null>(null);
  const tapeRef = useRef<GrowableTape | null>(null);
  const clockRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const idRef = useRef(1);
  // Made once, and never re-made: it closes over the tape *ref*, not over a tape,
  // so a session swap needs no new source. The clock is the newest print — never
  // the wall clock, which on a quiet market would run the chart past its data.
  const sourceRef = useRef(
    liveSource(() => {
      const t = tapeRef.current;
      return t && t.n > 0 ? t.t[t.n - 1] : null;
    }),
  );

  // The action log and the simulation derived from it. Append-only: there is no
  // truncate path on this page at all — by construction, not by discipline.
  const logRef = useRef<Log>(newLog());
  const simRef = useRef<SimState>(newSim());
  const sigRef = useRef("");
  const openRef = useRef<Position | null>(null);
  // Snapshots of the rebuild fold, so a click in the afternoon doesn't re-walk
  // the morning. A cache over `runSim`, checked rather than trusted — see
  // `SimLadder`. Built once and kept: a new one per render would throw the
  // session's snapshots away every time the HUD ticked.
  const ladderRef = useRef<SimLadder | null>(null);
  if (ladderRef.current === null) ladderRef.current = new SimLadder();
  const ladder = ladderRef.current;

  // --- display state --------------------------------------------------------
  const [ready, setReady] = useState(false);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [openPos, setOpenPos] = useState<Position | null>(null);
  const [working, setWorking] = useState<WorkingOrderView[]>([]);
  const [setupOpen, setSetupOpen] = useState(false);
  // Open and pinned by default, unlike the replay's ticket: this rail carries a
  // feed of what the shelf believed, and a signal you have to remember to go and
  // look at is the failure mode the whole shadow stack exists to avoid.
  //
  // Not on a phone, though — pinned there it is a column the tape cannot spare,
  // and unpinned it covers most of the chart it is about.
  const narrow = () => window.matchMedia("(max-width: 640px)").matches;
  const [signalsOpen, setSignalsOpen] = useState(() => !narrow());
  const [railPinned, setRailPinned] = useState(() => !narrow());
  const [indicators, setIndicators] = useState(true);
  const [size, setSize] = useState(1);
  const [stopTicks, setStopTicks] = useState(40);
  const [targetTicks, setTargetTicks] = useState(80);
  // The IB/range boxes ride along so the day-scale indicator strip can read
  // them: they are already tracked in geoRef for the chart's own overlays, and
  // going through the throttled HUD is what keeps the strip off a render per
  // frame — the same arrangement the Simulator uses.
  const [hud, setHud] = useState<{
    clockMs: number;
    lastPrice: number;
    openPnl: number;
    ib: IbBox | null;
    range: RangeBox | null;
  }>({ clockMs: 0, lastPrice: NaN, openPnl: 0, ib: null, range: null });
  const lastHudRef = useRef(0);
  const geoRef = useRef<{ ib: IbBox | null; range: RangeBox | null }>({ ib: null, range: null });

  // How much of the foot of the chart the market buttons are using, published as
  // --chart-floor so anything the chart parks down there (the order ticket, on a
  // fingertip) sits above them rather than under them.
  const dockRef = useRef<HTMLDivElement>(null);
  const [floor, setFloor] = useState(0);
  useEffect(() => {
    const el = dockRef.current;
    if (!el) return;
    const read = () => setFloor(el.getBoundingClientRect().height);
    const ro = new ResizeObserver(read);
    ro.observe(el);
    read();
    return () => ro.disconnect();
  }, []);

  const tickSize = header?.tick_size ?? 0.25;
  const pointValue = header?.point_value ?? 20;

  // --- helpers --------------------------------------------------------------
  const markPrice = useCallback((): number => {
    const v = engineRef.current?.lastPriceValue() ?? NaN;
    return Number.isFinite(v) ? v : NaN;
  }, []);

  const barAt = useCallback(
    (ms: number) => engineRef.current?.barTimeAt(ms) ?? Math.floor(ms / 1000),
    [],
  );

  const openPnl = useCallback(
    (lastPrice: number): number => {
      const o = openRef.current;
      if (!o || !Number.isFinite(lastPrice)) return 0;
      const dir = o.side === "long" ? 1 : -1;
      return (lastPrice - o.entryPrice) * dir * pointValue * o.size;
    },
    [pointValue],
  );

  const pushHud = useCallback(
    (lastPrice: number, clockMs: number, force = false) => {
      const now = performance.now();
      if (!force && now - lastHudRef.current < 80) return;
      lastHudRef.current = now;
      setHud({ clockMs, lastPrice, openPnl: openPnl(lastPrice), ...geoRef.current });
    },
    [openPnl],
  );

  const publish = useCallback(
    (st: SimState, clock: number) => {
      simRef.current = st;
      sigRef.current = simSig(st);
      openRef.current = st.open;
      const views = workingOrders(st).map((o) => orderView(o, clock, st.open));
      // Copies: the stepper appends to `trades` and mutates the position object
      // in place, so React would see the same reference and skip the render.
      setTrades(st.trades.slice());
      setOpenPos(st.open && { ...st.open });
      setWorking(views);
      chartRef.current?.setPosition(st.open ? posLine(st.open, barAt) : null);
      chartRef.current?.setOrders(views);
      chartRef.current?.setTrades(st.trades.map((t) => tradeMark(t, barAt)));
    },
    [barAt],
  );

  /**
   * Re-derive the blotter from the log. Every user action goes through here,
   * exactly as on the replay page: one pass over the tape per action, so there
   * is no second, optimistic code path that a later pass could disagree with.
   *
   * The pass resumes from the ladder's newest sound snapshot rather than from
   * the first order, which is what keeps it flat as the session grows — a live
   * tape would otherwise have every afternoon drag re-walking the morning. The
   * contract above is untouched: a snapshot is the same fold, paused (see
   * `SimLadder`), and it is dropped rather than trusted whenever the tape or the
   * consumed part of the log has moved under it.
   */
  const rebuild = useCallback(
    (clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      publish(ladder.run(tape, logRef.current, clock, pointValue), clock);
    },
    [ladder, publish, pointValue],
  );

  const advanceSim = useCallback(
    (from: number, to: number, clock: number) => {
      const tape = tapeRef.current;
      if (!tape) return;
      const st = simRef.current;
      stepSim(tape, logRef.current, st, from, to, clock, pointValue);
      if (simSig(st) !== sigRef.current) publish(st, clock);
    },
    [pointValue, publish],
  );

  // --- the frame loop -------------------------------------------------------
  // Runs for as long as the page is mounted. `atEnd` is never true on a live
  // source, so nothing stops it — when the feed is quiet the clock doesn't move
  // and `advance()` has nothing to apply, which costs one comparison a frame.
  const frame = useCallback(() => {
    const eng = engineRef.current;
    if (eng) {
      const { clock } = sourceRef.current.clockFor(clockRef.current, 0, 1);
      if (clock > clockRef.current) {
        const r = eng.advance(clock);
        chartRef.current?.applyStep(r);
        geoRef.current = { ib: r.ib, range: r.range };
        clockRef.current = clock;
        advanceSim(r.fromIdx, r.toIdx, clock);
        pushHud(r.lastPrice, clock);
      }
    }
    rafRef.current = requestAnimationFrame(frame);
  }, [advanceSim, pushHud]);

  useEffect(() => {
    rafRef.current = requestAnimationFrame(frame);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [frame]);

  // --- the tape -------------------------------------------------------------
  // A new accumulation: every row index the page was holding described a tape
  // that no longer exists, so the engine, the log and the blotter all go with it.
  const onReset = useCallback((tape: GrowableTape) => {
    tapeRef.current = tape;
    engineRef.current = null;
    clockRef.current = 0;
    logRef.current = newLog();
    // Belt and braces: the ladder already drops everything when the tape it was
    // handed is not the one it snapshotted, and an empty log fails the prefix
    // check regardless. Said out loud because the snapshots are the one thing
    // here that outlives a session without being visible on the page.
    ladder.reset();
    simRef.current = newSim();
    sigRef.current = simSig(simRef.current);
    openRef.current = null;
    idRef.current = 1;
    setTrades([]);
    setOpenPos(null);
    setWorking([]);
    setReady(false);
  }, [ladder]);

  const onAppend = useCallback(
    (tape: GrowableTape) => {
      if (!header || tape.n === 0 || engineRef.current) return;
      // The session's first print is where everything the engine develops
      // starts, and it is only knowable once that print has landed — which is
      // why the header ships `session_start_ms` as null until then.
      const payload = sessionPayloadFor(header, tape.t[0]);
      const eng = new ReplayEngine(tape as Tape, payload, tfRef.current);
      engineRef.current = eng;
      chartRef.current?.setTape(tape as Tape);
      const clock = tape.t[tape.n - 1];
      const snap = eng.snapshotTo(clock);
      chartRef.current?.setSnapshot(snap);
      geoRef.current = { ib: snap.ib, range: snap.range };
      clockRef.current = clock;
      setReady(true);
      pushHud(snap.lastPrice, clock, true);
      // Every later block is picked up by the frame loop, which reads the tape's
      // newest print through `liveSource` and folds the new ticks in.
    },
    [header, pushHud],
  );

  const tapeState = useLiveTape({
    enabled: !!gen && !!header,
    gen,
    tz: TZ,
    tickSize,
    pointValue,
    onReset,
    onAppend,
  });

  // A timeframe change is a re-derivation of the same tape, exactly as on the
  // replay page: the log and therefore every fill are untouched, because fills
  // come off tick indices and those don't know what a bar is.
  useEffect(() => {
    const eng = engineRef.current;
    if (!eng) return;
    eng.setTimeframe(tf);
    chartRef.current?.clearRuler();
    const snap = eng.snapshotTo(clockRef.current);
    chartRef.current?.setSnapshot(snap);
    geoRef.current = { ib: snap.ib, range: snap.range };
    rebuild(clockRef.current);
  }, [rebuild, tf]);

  // --- trading --------------------------------------------------------------
  const append = useCallback(
    (next: Log) => {
      logRef.current = next;
      rebuild(clockRef.current);
    },
    [rebuild],
  );

  /** Place an order. `at` is the price its bracket is measured from — the mark
   *  for a market order, the resting price for a limit or a stop. */
  const placeOrder = useCallback(
    (type: OrderType, side: Side, price: number | null, at: number) => {
      const eng = engineRef.current;
      if (!eng) return;
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
        // No ladder on this surface: the trail is a replay-practice knob, and a
        // live ticket has enough on it.
        trail: null,
        edits: [],
        cancelMs: null,
      };
      const log = logRef.current;
      append({ ...log, orders: [...log.orders, rec] });
    },
    [append, size, stopTicks, targetTicks, tickSize],
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
   *  at a price better than the market, which the tape cannot do. */
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

  /** Space + click at a price: the left button places the passive order there (a
   *  bid under the market, an offer over it), the right button the one that has
   *  to be run through. The side flips as you cross the market, which is the
   *  point — you click the level and the platform works out the order type. */
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

  // The open position's bracket, dragged. Its own channel in the log rather than
  // an edit on the order that opened the position: with several fills making up
  // one position, "the stop" belongs to the position, not to any of them.
  const moveBracket = useCallback(
    (b: { stop: number | null; target: number | null }) => {
      if (!openRef.current) return;
      const log = logRef.current;
      append({ ...log, brackets: [...log.brackets, { ms: clockRef.current, ...b }] });
    },
    [append],
  );

  /** Everything off: the position at the last print, and every order working
   *  with it — one append, so half a flatten is not a state this can sit in. */
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
    pushHud(markPrice(), ms, true);
  }, [append, markPrice, pushHud]);

  // q / w / s, the same three keys as the replay page — the muscle memory is the
  // point, so it does not get its own bindings here.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      const k = e.key.toLowerCase();
      if (k !== "q" && k !== "w" && k !== "s") return;
      e.preventDefault();
      if (k === "q") closeAll();
      else placeMarket(k === "w" ? "long" : "short");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeAll, placeMarket]);

  const net = trades.reduce((a, t) => a + t.pnl, 0);

  if (!status?.running) {
    return <NoSession onStarted={() => void statusQ.refetch()} />;
  }

  return (
    // `--sim-fill-h` is what gives `.sim-page` a definite height. Without it the
    // page is content-sized, `.sim-body`'s `flex: 1` has no height to take a
    // share of, and the chart collapses to whatever the signal rail happens to
    // be tall — growing as the rail fills, which is not a chart, it's a symptom.
    <div
      className={`sim-page${railPinned ? " pinned" : ""}`}
      style={{ "--chart-floor": `${floor}px` } as React.CSSProperties}
    >
      <ChartTopBar
        title={
          status.symbol
            ? `${status.symbol} · ${status.closed ? status.date ?? "closed" : "live"}`
            : "No feed"
        }
        onTitle={() => setSetupOpen((o) => !o)}
        titleOpen={setupOpen}
        right={
          <>
            <span className="sim-topbar-num" title="Session clock">
              {fmtClock(hud.clockMs)}
            </span>
            <span className="sim-topbar-num" title="Last print">
              {Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}
            </span>
          </>
        }
      >
        <TimeframeControl
          value={tfId}
          onChange={setTfId}
          options={TIMEFRAMES.map((t) => ({ key: t.id, label: t.label }))}
          // The tick bar (unique to a tape-driven chart), the default, and the
          // two the research vocabulary is written in. 30s/2m/3m/1h go behind ⋯.
          primary={["500t", "1m", "5m", "15m"]}
          compact
        />
      </ChartTopBar>

      {setupOpen && (
        <button
          type="button"
          className="sim-setup-backdrop"
          aria-label="Close ticket settings"
          onClick={() => setSetupOpen(false)}
        />
      )}

      {/* Ticket sizing — the pre-trade configuration, behind the title exactly
          as the Simulator's session setup is. Everything you touch while
          watching (timeframe, clock, mark) is up in the bar; everything you set
          once is in here. */}
      <div className={`sim-setup${setupOpen ? " open" : ""}`}>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Size
          <input
            type="number"
            min={1}
            value={size}
            onChange={(e) => setSize(Math.max(1, Number(e.target.value) || 1))}
            style={{ width: 72 }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Stop (ticks)
          <input
            type="number"
            min={0}
            value={stopTicks}
            onChange={(e) => setStopTicks(Math.max(0, Number(e.target.value) || 0))}
            style={{ width: 72 }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: palette.muted }}>
          Target (ticks)
          <input
            type="number"
            min={0}
            value={targetTicks}
            onChange={(e) => setTargetTicks(Math.max(0, Number(e.target.value) || 0))}
            style={{ width: 72 }}
          />
        </label>
        <span style={{ alignSelf: "flex-end", paddingBottom: 6, fontSize: 12, color: palette.muted }}>
          {trades.length} closed · net {fmtUsd(net)}
          {openPos ? ` · open ${fmtUsd(hud.openPnl)}` : ""}
        </span>
      </div>

      <div className="sim-body">
        <div className="sim-chart-card">
          <div className="sim-chart">
            <ReplayChart
              ref={chartRef}
              onBracketChange={moveBracket}
              onFlatten={closeAll}
              onOrderMove={(o) =>
                editOrder(o.id, { price: o.price, stop: o.stop, target: o.target })
              }
              onOrderCancel={cancelOrder}
              onPlaceOrder={placeAt}
              onPlaceTyped={(o) => placeResting(o.price, o.side, o.type)}
              ticket={{ size, stopTicks, targetTicks }}
              onTicketChange={(t) => {
                setSize(t.size);
                setStopTicks(t.stopTicks);
                setTargetTicks(t.targetTicks);
              }}
              mark={hud.lastPrice}
              canPlaceOrders={ready}
              secondsAxis={showsSeconds(tf)}
            />
            {/* The same day-scale strip the replay carries. It was written for a
                tape that grows print by print, which is what a live session is —
                the IB reads "forming" until the window closes and the range
                budget says "budget at IB close" until there is a denominator, so
                a partial day degrades honestly rather than guessing. The context
                it needs (ADR(14), the tercile edges) already comes down on
                /live/session; nothing here read it before. */}
            <SimIndicators
              context={header?.context}
              ib={hud.ib}
              range={hud.range}
              open={indicators}
              onToggle={() => setIndicators((v) => !v)}
            />
            {/* Market orders under the thumb, as on the replay. Both sides quote
                the last print, and that is not a stand-in for a bid/ask — this
                page fills off the same tape the chart draws. */}
            <div ref={dockRef} className="sim-quick">
              {openPos && (
                <button type="button" className="sim-quick-btn flat" onClick={closeAll} title="Flatten (q)">
                  Close
                </button>
              )}
              <button
                type="button"
                className="sim-quick-btn sell"
                onClick={() => placeMarket("short")}
                disabled={!ready}
                title="Sell at market (s)"
              >
                <span>SELL</span>
                <b>{Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}</b>
              </button>
              <button
                type="button"
                className="sim-quick-btn buy"
                onClick={() => placeMarket("long")}
                disabled={!ready}
                title="Buy at market (w)"
              >
                <span>BUY</span>
                <b>{Number.isFinite(hud.lastPrice) ? fmtPts(hud.lastPrice) : "—"}</b>
              </button>
            </div>
          </div>
        </div>

        {/* The same rail the replay carries, doing the same job: open the panel,
            and pin it to a column when you want the width spent on it. */}
        <div className="sim-rail">
          <button
            type="button"
            className={`sim-rail-btn${signalsOpen ? " on" : ""}`}
            onClick={() => setSignalsOpen((o) => !o)}
            aria-pressed={signalsOpen}
            title={signalsOpen ? "Hide the shadow signals" : "Show the shadow signals"}
          >
            ▤
          </button>
          {signalsOpen && (
            <button
              type="button"
              className={`sim-rail-btn${railPinned ? " on" : ""}`}
              onClick={() => setRailPinned((p) => !p)}
              aria-pressed={railPinned}
              title={
                railPinned
                  ? "Unpin — let the feed lay over the tape"
                  : "Pin — give the feed its own column beside the tape"
              }
            >
              📌
            </button>
          )}
          {working.length > 0 && (
            <span className="sim-rail-badge" title={`${working.length} working`}>
              {working.length}
            </span>
          )}
        </div>
        <SignalPanel data={signalsQ.data} working={working.length} open={signalsOpen} />
      </div>

      {/* The status strip is a footer: it is state you glance at, not something
          you act on continuously, and above the chart it was pushing the tape
          down by a row you were not reading. */}
      <FeedBanner
        source={status.source ?? "fake"}
        symbol={status.symbol ?? "—"}
        date={status.date ?? "—"}
        speed={status.speed ?? 1}
        feedRunning={!!status.feed_running}
        closed={!!status.closed}
        rows={tapeState.rows || (status.rows ?? 0)}
        recording={!!status.recording}
        signals={status.signals !== false}
        canRecord={status.can_record !== false}
        unrecorded={status.unrecorded_rows ?? 0}
        onModes={async (m) => {
          await setLiveModes(m);
          void statusQ.refetch();
          void signalsQ.refetch();
        }}
        backfilling={!!status.feed_status?.backfilling}
        timing={status.feed_status?.timing}
        backfills={status.feed_status?.backfills}
        harvested={status.feed_status?.harvested}
        feedError={status.feed_status?.error}
        error={tapeState.error}
        onStop={async () => {
          await stopFeed();
          void statusQ.refetch();
        }}
      />
    </div>
  );
}

/**
 * Microseconds as something readable at a glance.
 *
 * Sub-millisecond keeps two decimals because that is the entire range the hop
 * lives in — 0.3ms rounded to "0ms" would say the leg is free, which is a
 * different claim from the one the measurement makes.
 */
function fmtUs(us: number): string {
  return us < 1000 ? `${(us / 1000).toFixed(2)}ms` : `${Math.round(us / 1000)}ms`;
}

/**
 * The banner that says what this surface is, so it can never be mistaken for
 * something it is not.
 *
 * Three states, and the third is why the source is read rather than assumed. A
 * **resumed** session has a whole tape and no feed behind it: the API restarted
 * mid-day and rebuilt what was recorded. It is not growing, and left labelled
 * "arriving" that reads as a quiet market rather than as a feed that needs
 * reconnecting.
 */
function FeedBanner(props: {
  source: string;
  symbol: string;
  date: string;
  speed: number;
  feedRunning: boolean;
  closed: boolean;
  rows: number;
  recording: boolean;
  signals: boolean;
  canRecord: boolean;
  unrecorded: number;
  onModes: (m: { record?: boolean; signals?: boolean }) => Promise<void>;
  backfilling?: boolean;
  timing?: Record<string, number>;
  backfills?: LiveBackfill[];
  harvested?: { date: string; skipped: boolean; rows: number; error?: string }[];
  feedError?: string | null;
  error: string | null;
  onStop: () => void;
}) {
  // Summed rather than listed: a long-running feed reconnects, and each
  // reconnect backfills the hole it left. What the banner owes the reader is
  // "how much of this tape was replayed rather than watched" and "did any of it
  // fail" — the per-range detail lives on /live/status.
  const filled = (props.backfills ?? []).reduce((a, b) => a + (b.rows || 0), 0);
  const backfillError = (props.backfills ?? []).find((b) => b.error)?.error;
  const caughtUp = (props.harvested ?? []).filter((h) => !h.skipped && h.rows).length;
  // Whichever legs have samples, rather than both or neither: a feed running on
  // rows that carry only Rithmic's stamp can say nothing about the hop, and
  // rendering a zero there would read as a perfect one.
  const t = props.timing ?? {};
  const timingParts: string[] = [];
  if (t.hop_p50_us !== undefined) timingParts.push(`hop ${fmtUs(t.hop_p50_us)}`);
  if (t.lag_p50_us !== undefined) timingParts.push(`lag ${fmtUs(t.lag_p50_us)}`);
  const live = props.source === "rithmic";
  const resumed = props.source === "resumed";
  const accent = live ? palette.green : palette.orange;
  const label = live ? "LIVE · RITHMIC" : resumed ? "RESUMED — NO FEED" : "SIMULATED FEED";
  return (
    <div
      className="live-status"
      style={{
        // The status colour, on the edge the strip now sits against.
        borderTop: `2px solid ${accent}`,
      }}
    >
      <strong style={{ letterSpacing: 0.4, color: accent }}>
        {label}
      </strong>
      <span >
        {props.symbol} · {props.date}
        {live ? "" : ` · ${props.speed}×`} · {props.rows.toLocaleString()} ticks
      </span>
      <span style={{ color: palette.muted }}>
        {props.backfilling
          ? // Said out loud because the tape is empty while this runs — a whole
            // session takes tens of seconds to replay, and a blank chart with a
            // green LIVE banner over it reads as a bug.
            "replaying the session so far…"
          : props.closed
            ? "session complete"
            : resumed
              ? "rebuilt from disk — reconnect to keep recording"
              : props.feedRunning
                ? "arriving"
                : "feed stopped"}
      </span>
      <ModeSwitches
        recording={props.recording}
        signals={props.signals}
        canRecord={props.canRecord}
        unrecorded={props.unrecorded}
        onModes={props.onModes}
      />
      {props.unrecorded > 0 && (
        // The hole, not the switch. Said in the banner rather than left to the
        // manifest because it is a property of the *day* now: the tape is in
        // memory and the chunks are what survives, so these prints are gone at
        // the next restart whatever happens next.
        <span style={{ color: palette.orange }}>
          ⚠ {props.unrecorded.toLocaleString()} tick
          {props.unrecorded === 1 ? "" : "s"} not written
        </span>
      )}
      {timingParts.length > 0 && (
        <span
          style={{ color: palette.muted, cursor: "help" }}
          title={
            "hop — the exchange's stamp to Rithmic's send stamp. Both ride in the " +
            "same message, so no local clock enters into it.\n\n" +
            "lag — arrival to publish inside the API, on the monotonic clock. It is " +
            "bounded below by the 100ms publish cadence, so a p50 near 50ms is the " +
            "cadence rather than a fault; a p90 far above 100ms means something (a " +
            "shadow pass, a sim sweep on the same cores) is holding the event loop.\n\n" +
            "There is no end-to-end figure, deliberately: it would need the host clock " +
            "against the exchange's, and this host measured a second off in a direction " +
            "that changes between runs — so the number would be reporting clock drift " +
            "while looking exactly like a feed measurement."
          }
        >
          {timingParts.join(" · ")}
        </span>
      )}
      {filled > 0 && (
        <span style={{ color: palette.muted }}>
          {filled.toLocaleString()} backfilled
        </span>
      )}
      {caughtUp > 0 && (
        // Earlier sessions, not this one — worth saying because it is what makes
        // the weekly anchor drawable after a week away, and it is otherwise
        // invisible work happening on a connection you opened to watch today.
        <span style={{ color: palette.muted }}>
          +{caughtUp} earlier session{caughtUp === 1 ? "" : "s"} filled
        </span>
      )}
      {backfillError && (
        <span style={{ color: palette.orange }}>
          ⚠ backfill: {backfillError} — the session begins where the feed
          connected
        </span>
      )}
      {props.feedError && (
        <span style={{ color: palette.red }}>⚠ feed: {props.feedError}</span>
      )}
      {props.error && (
        <span style={{ color: palette.red }}>⚠ {props.error}</span>
      )}
      <button style={{ marginLeft: "auto" }} onClick={props.onStop}>
        Stop
      </button>
    </div>
  );
}

/**
 * The two switches, and the one order they may be thrown in.
 *
 * They are separate because they fail differently. Recording off is an absence
 * you can see — the tape stops being written and the banner counts what was
 * lost. The shelf running with nothing recorded is not an absence at all: ten
 * `gx_*` gate sites and the weekly seed read the session's earlier windows off
 * *disk*, not from the frame the runner injects, so they blind-fail-closed and
 * seven of the thirteen strategies veto everything without a word. On screen
 * that is indistinguishable from a morning where no setup formed.
 *
 * So recording cannot be switched off underneath a running shelf. The API
 * refuses it (422) and this disables the control and says why — turning the two
 * off in one click would be the page deciding to stop the shelf because you
 * asked about the disk, which is a bigger action than the one requested.
 *
 * The simulated feed is the exception, and not one: it records nothing by
 * design, but the day it replays is a *cached* day, so the windows the gates
 * read are already on disk and the shelf can honestly run over it.
 */
function ModeSwitches(props: {
  recording: boolean;
  signals: boolean;
  canRecord: boolean;
  unrecorded: number;
  onModes: (m: { record?: boolean; signals?: boolean }) => Promise<void>;
}) {
  const [busy, setBusy] = useState<"record" | "signals" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const send = async (which: "record" | "signals", m: { record?: boolean; signals?: boolean }) => {
    setBusy(which);
    setErr(null);
    try {
      await props.onModes(m);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // Blocked rather than hidden: the reason is the interesting part, and a
  // control that vanished would read as "this feed cannot do that".
  const recordLocked = props.canRecord && props.recording && props.signals;

  return (
    <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
      {props.canRecord ? (
        <ModeChip
          on={props.recording}
          label="recording"
          busy={busy === "record"}
          disabled={recordLocked}
          title={
            recordLocked
              ? "Switch the shadow shelf off first. Its Globex gates read this " +
                "session's earlier windows off disk — with nothing being written " +
                "they veto everything and say nothing about why, which looks " +
                "exactly like a morning with no setups in it."
              : props.recording
                ? "Writing every print to data/live/. Stopping leaves a permanent " +
                  "hole: the tape is in memory, the chunks are what survive."
                : props.unrecorded > 0
                  ? "Resume writing. The prints that arrived while this was off " +
                    "are not recoverable, and the day's manifest will say so."
                  : "Write every print to data/live/."
          }
          onClick={() => void send("record", { record: !props.recording })}
        />
      ) : (
        <span
          style={{ fontSize: 12, color: palette.muted, cursor: "help" }}
          title={
            "The simulated feed records nothing by design — its source is a cached " +
            "day, and recording it would manufacture a live day out of a replayed one."
          }
        >
          not recorded
        </span>
      )}
      <ModeChip
        on={props.signals}
        label="shadow shelf"
        busy={busy === "signals"}
        title={
          props.signals
            ? "Re-running every registered strategy over the day so far. Switching " +
              "off keeps what it last said on screen and stops the passes."
            : "Re-run the shelf over the day so far."
        }
        onClick={() => void send("signals", { signals: !props.signals })}
      />
      {err && <span style={{ fontSize: 12, color: palette.red }}>⚠ {err}</span>}
    </span>
  );
}

function ModeChip(props: {
  on: boolean;
  label: string;
  busy: boolean;
  disabled?: boolean;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={props.onClick}
      disabled={props.busy || props.disabled}
      title={props.title}
      style={{
        fontSize: 11,
        padding: "1px 7px",
        borderRadius: 10,
        color: props.on ? palette.green : palette.muted,
        borderColor: props.on ? palette.green : undefined,
        opacity: props.disabled ? 0.55 : 1,
      }}
    >
      {props.on ? "●" : "○"} {props.label}
    </button>
  );
}

/** Where the shelf would have signalled, on the day so far. */
function SignalPanel({
  data,
  working,
  open,
}: {
  data?: LiveSignals;
  working: number;
  open: boolean;
}) {
  const ran = data?.strategies.filter((s) => s.ran) ?? [];
  const fired = ran.filter((s) => s.trades.length > 0);
  // `.sim-panel` — the same box the replay's ticket and blotter live in, opened
  // and pinned from the same rail. It was briefly its own class on the theory
  // that a feed you keep in view is a different kind of thing from a form you
  // fill in; in practice that just meant a second set of rules to keep in step,
  // and the panel already does overlay-or-column.
  //
  // `min-height: 0` on the flex column and the scroll on the card are what stop a
  // long signal list from stretching the row and squashing the chart — the row's
  // height belongs to the page, not to this.
  return (
    <div className={`sim-panel${open ? " open" : ""}`}>
      <div className="panel" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {!data ? (
          <div style={{ fontSize: 12, color: palette.muted }}>Loading signals…</div>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>Shadow signals</h3>
              <span style={{ fontSize: 11, color: palette.muted }}>
                {data.enabled ? `${ran.length}/${data.strategies.length} run` : "off"}
              </span>
            </div>
            <p style={{ fontSize: 11, color: palette.muted, margin: "4px 0 10px" }}>
              Prefix re-runs of the same engine the backtest uses, each under its
              own baseline's config. Nothing here places an order.
            </p>

            {!data.enabled && (
              // The whole point of the flag. Without it a switched-off shelf and
              // a market that has offered nothing render identically, and the
              // one you are looking at changes what the blank list means.
              <div
                style={{
                  fontSize: 11,
                  color: palette.orange,
                  border: `1px solid ${palette.cardBorder}`,
                  borderRadius: 4,
                  padding: "6px 8px",
                  marginBottom: 10,
                }}
              >
                The shelf is switched off. Anything below is what it last said
                before that, not a reading of the day as it stands.
              </div>
            )}
            {data.enabled && !data.journalling && (
              <div style={{ fontSize: 11, color: palette.muted, marginBottom: 10 }}>
                Not journalled — these passes are on screen only, so there is
                nothing for the after-the-close prefix check to run against.
              </div>
            )}

            <div style={{ fontSize: 11, color: palette.muted, marginBottom: 10 }}>
              Regime {data.regime.class ?? "—"}
              {data.regime.texture ? ` · ${data.regime.texture}` : ""} · frozen{" "}
              {data.regime.frozen.length ? data.regime.frozen.join(", ") : "none yet"}
            </div>

            {fired.length === 0 && data.enabled && (
              <div style={{ fontSize: 12, color: palette.muted }}>
                No strategy has signalled yet today.
              </div>
            )}
            {fired.map((s) => (
              <StrategyRow key={s.slug} s={s} />
            ))}

            {data.skipped.length > 0 && (
              <details style={{ marginTop: 12, fontSize: 11, color: palette.muted }}>
                <summary>{data.skipped.length} not watched</summary>
                {data.skipped.map((k) => (
                  <div key={k.slug}>
                    {k.slug} — {k.reason}
                  </div>
                ))}
              </details>
            )}
            {working > 0 && (
              <div style={{ marginTop: 12, fontSize: 11, color: palette.muted }}>
                {working} of your own order{working === 1 ? "" : "s"} working
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function StrategyRow({ s }: { s: ShadowStrategy }) {
  const last = s.trades[s.trades.length - 1];
  const open = last && !last.exit_ts_utc;
  return (
    <div style={{ borderTop: `1px solid ${palette.cardBorder}`, padding: "8px 0" }}>
      <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
        <strong style={{ fontSize: 12 }}>{s.name}</strong>
        <span style={{ fontSize: 10, color: palette.muted }}>{s.version}</span>
        {open && <span style={{ fontSize: 10, color: palette.green }}>OPEN</span>}
      </div>
      <div style={{ fontSize: 11, color: palette.muted }}>
        {s.trades.length} signal{s.trades.length === 1 ? "" : "s"}
        {s.vetoed.length > 0 && ` · ${s.vetoed.length} vetoed`}
        {last && ` · last ${last.direction} @ ${fmtPts(last.avg_entry)}`}
        {last?.exit_reason && ` → ${last.exit_reason}`}
      </div>
      {s.error && <div style={{ fontSize: 11, color: palette.red }}>⚠ {s.error}</div>}
    </div>
  );
}

/**
 * No session running — start one, and say plainly what it is.
 *
 * The day list is the Simulator's: a fake feed's source is a cached Databento
 * session, so anything replayable is something the feed can present as today.
 */
function NoSession({ onStarted }: { onStarted: () => void }) {
  const daysQ = useSimulatorDays("NQ");
  const days = daysQ.data?.days ?? [];
  const [pick, setPick] = useState("");
  const [speed, setSpeed] = useState(60);
  const [startAt, setStartAt] = useState("09:25");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const chosen = days.find((d) => d.date === pick) ?? days[days.length - 1];

  return (
    <div className="page">
      <div className="panel" style={{ maxWidth: 720 }}>
        <h2 style={{ marginTop: 0 }}>Live</h2>
        <p className="muted">
          A strategy run over a session in progress reproduces exactly what the
          backtest reports for that stretch — that is the property the whole
          surface rests on, and it holds for either feed below.
        </p>

        <RithmicStart onStarted={onStarted} />

        <h3 style={{ marginBottom: 4 }}>Simulated feed</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          A cached Databento session replayed at wall-clock speed. It is not a
          market and records nothing, and the banner says so for as long as it
          runs. Use it to work on the surface when the market is shut.
        </p>

        <div style={{ display: "flex", gap: 10, alignItems: "end", marginTop: 12, flexWrap: "wrap" }}>
          <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
            Session
            <select value={chosen?.date ?? ""} onChange={(e) => setPick(e.target.value)}>
              {days.map((d) => (
                <option key={`${d.symbol}-${d.date}`} value={d.date}>
                  {d.date} · {d.symbol}
                  {d.has_overnight ? "" : " (no overnight)"}
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
            Speed
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {SPEEDS.map((s) => (
                <option key={s} value={s}>
                  {s}×
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
            Open at (ET)
            <input
              value={startAt}
              onChange={(e) => setStartAt(e.target.value)}
              style={{ width: 80 }}
            />
          </label>
          <button
            disabled={!chosen || busy}
            onClick={async () => {
              if (!chosen) return;
              setBusy(true);
              setErr(null);
              try {
                await startFakeFeed({
                  symbol: chosen.symbol,
                  date: chosen.date,
                  speed,
                  start_at: startAt,
                });
                onStarted();
              } catch (e) {
                setErr(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Starting…" : "Start simulated feed"}
          </button>
        </div>
        {err && <p style={{ color: palette.red, fontSize: 12 }}>{err}</p>}

        <p className="muted" style={{ fontSize: 13, marginTop: 24 }}>
          To practise against a finished session instead — with seek, rewind and
          speed — use <Link to="/charts/replay">Replay</Link>.
        </p>
      </div>
    </div>
  );
}

/**
 * Connect the real ticker plant.
 *
 * The contract field takes a RAW symbol and nothing else. That is not a UI
 * preference: a root sends `contract_for` to probe Databento — which a live path
 * must never do — and the on-disk roll map ends 2026-06-30 regardless. The API
 * rejects roots with a 422, so this is a hint rather than the guard.
 *
 * CONNECTING LATE IS NO LONGER A TRAP, AND IT USED TO BE. A session is 18:00 →
 * 18:00 ET, seven of the thirteen strategies declare `session="globex"`, and
 * their gates read the night off disk and blind-fail-closed when it is not
 * there — so connecting at nine in the morning produced a shadow day where those
 * seven vetoed everything and nothing said why, which looks exactly like "no
 * setup formed". The feed now replays the session from its open off Rithmic's
 * history plant before streaming, and records it, so the night is both on the
 * chart and on disk (`docs/live-shadow-plan.md § Tick replay`).
 *
 * What it does not cover: a contract that has already rolled off, which returns
 * nothing at any depth. Recording is still the only thing that makes a day
 * recoverable after that.
 */
function RithmicStart({ onStarted }: { onStarted: () => void }) {
  const [symbol, setSymbol] = useState("NQU6");
  const [record, setRecord] = useState(true);
  const [signals, setSignals] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Same rule as the running session's switches, applied before there is a
  // session to apply it to: the shelf reads this day's earlier windows off disk,
  // so it cannot honestly run over a feed that writes none of them. Unchecking
  // the recording therefore unchecks the shelf here, where nothing has started
  // and the pair is still a single choice about how to open the connection.
  const setRecording = (on: boolean) => {
    setRecord(on);
    if (!on) setSignals(false);
  };

  return (
    <div style={{ marginBottom: 28 }}>
      <h3 style={{ marginBottom: 4 }}>Rithmic feed</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        Market data only — the ticker plant, never the order plant, and nothing
        here can send an order. Recorded, every print goes to{" "}
        <code>data/live/</code>, which is what the Globex gates and the weekly
        seed read off disk — so the shelf can only run over a session that is
        being written. Live ticks never join the Databento corpus. Both switches
        can be thrown again while the session runs.
      </p>
      <div style={{ display: "flex", gap: 10, alignItems: "end", flexWrap: "wrap" }}>
        <label style={{ display: "grid", gap: 4, fontSize: 12 }}>
          Contract (raw)
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="NQU6"
            style={{ width: 100 }}
          />
        </label>
        <label style={{ display: "flex", gap: 5, alignItems: "center", fontSize: 12 }}>
          <input
            type="checkbox"
            checked={record}
            onChange={(e) => setRecording(e.target.checked)}
          />
          Record the tape
        </label>
        <label
          style={{
            display: "flex",
            gap: 5,
            alignItems: "center",
            fontSize: 12,
            opacity: record ? 1 : 0.55,
          }}
          title={
            record
              ? "Re-run every registered strategy over the day as it arrives."
              : "Needs the recording: the Globex gates read this session's earlier " +
                "windows off disk, and with nothing written they veto everything " +
                "without saying why."
          }
        >
          <input
            type="checkbox"
            checked={signals}
            disabled={!record}
            onChange={(e) => setSignals(e.target.checked)}
          />
          Run shadow signals
        </label>
        <button
          disabled={busy || symbol.trim().length < 4}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              await startRithmicFeed({ symbol: symbol.trim(), record, signals });
              onStarted();
            } catch (e) {
              setErr(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Connecting…" : record ? "Connect & record" : "Connect (no recording)"}
        </button>
      </div>
      {err && <p style={{ color: palette.red, fontSize: 12 }}>{err}</p>}
      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        Connecting mid-session is fine: the day is replayed from its 18:00 ET
        open before the live stream starts, so the night the Globex gates read is
        there whatever time you arrive. Needs the <code>RITHMIC_*</code> keys in{" "}
        <code>.env</code> — <code>uv run python demo/rithmic_smoke.py</code> is
        what proves them, and{" "}
        <code>demo/rithmic_history_probe.py</code> the replay behind them.
      </p>
    </div>
  );
}
