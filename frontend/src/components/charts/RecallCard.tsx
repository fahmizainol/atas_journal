import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSimulatorSession } from "../../hooks/useSimulator";
import type { RecallBack, RecallCardFront } from "../../hooks/useRecall";
import {
  decodeTape,
  ReplayEngine,
  type EventTuning,
  type SessionPayload,
  type Tape,
} from "../../lib/replayEngine";
import { replaySource, type TapeSource } from "../../lib/tapeSource";
import { useTurbo } from "../../hooks/useTurbo";
import { TurboChip } from "./TurboChip";
import { SIM_SPEEDS } from "../../lib/simPrefs";
import {
  loadDynamicSwingVwapParams,
  loadModernVwapParams,
  loadProfileKnobs,
  loadReplaySpeed,
  loadStudies,
  loadTapeKnobs,
  loadTimeframeId,
  saveDynamicSwingVwapParams,
  saveModernVwapParams,
  saveProfileKnobs,
  saveReplaySpeed,
  saveStudies,
  saveTapeKnobs,
  saveTimeframeId,
  type TapeKnobs,
} from "../../lib/chartPrefs";
import type { DsvParams } from "../../lib/dynamicSwingVwap";
import type { ModernVwapParams } from "../../lib/modernVwap";
import type { StudySpec } from "../../lib/studies";
import { fmtClock } from "../../lib/simViews";
import { showsSeconds, timeframeById, useTimeframeOptions } from "../../lib/timeframes";
import { buildChartKnobs } from "./indicatorKnobs";
import type { IndicatorSettingsMap } from "./IndicatorLegend";
import { ReplayChart, type ReplayChartHandle } from "./ReplayChart";
import type { TradeMarkView } from "./TradesPrimitive";

// One recall card's chart: the trade's own session, playable through a minute
// around the fill — and then, once flipped, playable to the close with the trade
// drawn on it.
//
// A stripped `DayReplayer`: same engine, same tape loader, same rAF transport,
// and the same zero write paths (no `useReplayAttempt`, no ticket, no practice
// sim; the unarmed-recorder property is structural here rather than a flag).
//
// **The front is bounded, and the bound is half a minute past the fill.**
// `replaySource(end)` stops the clock there and the scrubber's max is the same
// number, so there is no gesture that reaches past it. The card opens on that
// instant, with everything it will ever show already on screen, and ▶ replays the
// window: at the ceiling it rewinds `WINDOW_MS` and runs `WINDOW_PRE_MS` of
// approach, the fill, and `WINDOW_POST_MS` of what came of it, then stops on the
// bound again. Press it as often as you like; it is the same minute each time.
//
// The window used to stop dead on the fill. It runs past it now because the
// question a rep is asking is *what did that decision buy*, and the first
// half-minute is where a fill either goes with you or immediately does not — so
// it costs thirty seconds of the answer to make the rest of it worth reading.
// What is spent is deliberate and bounded: the tape goes no further.
//
// **Blindness on the front:** the axis dates are masked (`hideDates`, the
// drill's own mechanism), the title shows the tape's root only (a month letter
// dates the chart), the scrubber is relative so no epoch stamp appears in the
// DOM, and nothing marks the fill — no position line, no trade, no direction.
// You know a trade is in here, half a minute off the right edge; what you are
// not told is which way, how big, or what it did. The payload behind all of this
// carries no answer either (see api/routers/recall.py); masking a field that
// arrived in the response would be theatre.
//
// **The back plays out.** On flip the bound extends to the session end, the
// trade is drawn, and the tape seeks to five seconds before the entry and runs
// — the review's own gesture, for the same reason: the decision is in those
// seconds, not in the fill.
//
// **Both windows play themselves.** A card that opened stopped made every rep
// start with the same click, and the click is not the rep — so the front replays
// its minute as soon as the tape is decoded, and the flip replays the reveal. ⟲
// is the same gesture on demand: whichever window this side of the card owns,
// from its start, however far through it you are. ▶ is still the plain
// pause/resume (and still rewinds a window when pressed at the ceiling).
//
// The reveal used to force 1×. It doesn't: the speed is yours, it is the same
// sticky-global the reading knobs are (`chart.replaySpeed`), and a deck watched
// at 3× that dropped back to 1× on every flip was re-picking it forever.
// Nothing here writes `sim.prefs`.

/** How far before the entry the reveal starts, matching `REVIEW_LEAD_MS`. */
const REVEAL_LEAD_MS = 5_000;

/** The front card's window, centred on the fill: half a minute of approach, half
 *  a minute of what came of it. `WINDOW_POST_MS` is also how far past the fill
 *  the front's ceiling sits, so the two halves are the same statement — the
 *  window is the bound. */
const WINDOW_PRE_MS = 30_000;
const WINDOW_POST_MS = 30_000;
const WINDOW_MS = WINDOW_PRE_MS + WINDOW_POST_MS;

/** The scrubber's resolution, and with it what counts as "at the ceiling". A
 *  range input snaps to its step, so dragging to the far right leaves the clock
 *  up to a step short of the bound — close enough that playing the remainder is
 *  indistinguishable from the button doing nothing. One step of tape is the
 *  smallest move this transport can even express. */
const SCRUB_STEP_MS = 1_000;

/** The journal's local stamps are tz-aware and the tape clock is display-zone
 *  wall time, so the offset is dropped rather than applied — `DayReplayer`'s
 *  `localMs`, for the same reason it gives. */
function localMs(s: string | null | undefined): number | null {
  if (!s) return null;
  const t = Date.parse(s.slice(0, 19) + "Z");
  return Number.isFinite(t) ? t : null;
}

interface Props {
  card: RecallCardFront;
  /** The answer, once fetched. Null while the front is up. */
  back: RecallBack | null;
  tz: string;
}

export function RecallCard({ card, back, tz }: Props) {
  const session = useSimulatorSession(card.symbol, card.date, tz);
  const data = session.data ?? null;
  const revealed = back != null;

  const chartRef = useRef<ReplayChartHandle | null>(null);
  const engineRef = useRef<ReplayEngine | null>(null);
  const tapeRef = useRef<Tape | null>(null);
  const sessionRef = useRef<SessionPayload | null>(null);
  const sourceRef = useRef<TapeSource | null>(null);
  const clockRef = useRef<number>(NaN);
  const playingRef = useRef(false);
  const lastTsRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastPubRef = useRef(0);

  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(loadReplaySpeed);
  const speedRef = useRef(speed);
  // Hold Ctrl to run ten times whatever the ladder is set to.
  const turbo = useTurbo();
  const changeSpeed = useCallback((v: number) => {
    speedRef.current = v;
    setSpeed(v);
    saveReplaySpeed(v);
  }, []);
  const [clockMs, setClockMs] = useState(0);
  // Sticky like the reading knobs below, and for the same reason: a deck is a
  // sequence of cards, so a bucketing re-picked on every rep is re-picked
  // forever. `lib/chartPrefs` keeps it, so it survives the reload with them.
  const [tfId, setTfId] = useState(loadTimeframeId);
  const changeTf = useCallback((id: string) => {
    setTfId(id);
    saveTimeframeId(id);
  }, []);
  const tf = timeframeById(tfId);
  // Including the ones typed into the Charts workspace's picker — a card is the
  // same tape bucketed the same way, so it offers the same bars.
  const tfOptions = useTimeframeOptions();

  // ---- The reading knobs -------------------------------------------------
  //
  // A card is the same tape the Simulator draws, so it offers the same layers:
  // the big prints, the tape events, both swing VWAPs, the node reader and the
  // community studies. None of it is Recall's to own, and none of it is stored
  // per card — every one of these is a statement about how *you* read a chart,
  // which is why they come from the sticky-global stores in lib/chartPrefs that
  // the journal's charts already share. Nothing here touches `sim.prefs`.
  //
  // The composite is the one layer that isn't offered, and it isn't a choice: a
  // card loads the trade's own session and no days in front of it, so there is
  // nothing to composite (the chart drops the row itself, on `ctxDays`).
  const [tapeKnobs, setTapeKnobs] = useState<TapeKnobs>(loadTapeKnobs);
  const [nodeProm, setNodeProm] = useState(() => loadProfileKnobs().nodeProm);
  const [mvParams, setMvParams] = useState<ModernVwapParams>(loadModernVwapParams);
  const [dsvParams, setDsvParams] = useState<DsvParams>(loadDynamicSwingVwapParams);
  const [studies, setStudies] = useState<StudySpec[]>(() => loadStudies());

  const patchMv = useCallback((patch: Partial<ModernVwapParams>) => {
    setMvParams((prev) => {
      const next = { ...prev, ...patch };
      saveModernVwapParams(next);
      return next;
    });
  }, []);
  const patchDsv = useCallback((patch: Partial<DsvParams>) => {
    setDsvParams((prev) => {
      const next = { ...prev, ...patch };
      saveDynamicSwingVwapParams(next);
      return next;
    });
  }, []);
  const changeStudies = useCallback((next: StudySpec[]) => {
    setStudies(next);
    saveStudies(next);
  }, []);
  const changeNodeProm = useCallback((p: number) => {
    setNodeProm(p);
    saveProfileKnobs({ ...loadProfileKnobs(), nodeProm: p });
  }, []);

  /** Re-derive the tape under the new setting and hand the chart the result
   *  where it stands. Both of these are engine-side — the engine decides which
   *  sweeps and which events exist at all — so unlike a repaint knob they can't
   *  be applied by the chart alone, and `reframe: false` keeps the viewport you
   *  were reading. */
  const resnapshot = useCallback(() => {
    const eng = engineRef.current;
    if (!eng || !Number.isFinite(clockRef.current)) return;
    chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: false });
  }, []);

  const changeBigLots = useCallback(
    (lots: number) => {
      setTapeKnobs((prev) => {
        const next = { ...prev, bigLots: lots };
        saveTapeKnobs(next);
        return next;
      });
      engineRef.current?.setBigLots(lots);
      resnapshot();
    },
    [resnapshot],
  );

  const changeEvTuning = useCallback(
    (patch: Partial<EventTuning>) => {
      setTapeKnobs((prev) => {
        const next = { ...prev, eventTuning: { ...prev.eventTuning, ...patch } };
        saveTapeKnobs(next);
        return next;
      });
      engineRef.current?.setEventTuning(patch);
      resnapshot();
    },
    [resnapshot],
  );

  /** The three that only change how a selected event is drawn. */
  const patchTapeDrawing = useCallback((patch: Partial<TapeKnobs>) => {
    setTapeKnobs((prev) => {
      const next = { ...prev, ...patch };
      saveTapeKnobs(next);
      return next;
    });
  }, []);

  const indicatorSettings = useMemo<IndicatorSettingsMap>(
    () =>
      buildChartKnobs({
        bigLots: tapeKnobs.bigLots,
        onBigLots: changeBigLots,
        nodeProm,
        onNodeProm: changeNodeProm,
        modernVwap: { params: mvParams, onChange: patchMv },
        dynamicSwingVwap: { params: dsvParams, onChange: patchDsv },
        events: {
          tuning: tapeKnobs.eventTuning,
          labelSt: tapeKnobs.eventLabelSt,
          fillSweep: tapeKnobs.eventFillSweep,
          fillAbsorb: tapeKnobs.eventFillAbsorb,
          floorSweep: tapeKnobs.eventFloorSweep,
          floorAbsorb: tapeKnobs.eventFloorAbsorb,
          marginal: tapeKnobs.eventMarginal,
          onTuning: changeEvTuning,
          onLabelSt: (eventLabelSt) => patchTapeDrawing({ eventLabelSt }),
          onFillSweep: (eventFillSweep) => patchTapeDrawing({ eventFillSweep }),
          onFillAbsorb: (eventFillAbsorb) => patchTapeDrawing({ eventFillAbsorb }),
          onFloorSweep: (eventFloorSweep) => patchTapeDrawing({ eventFloorSweep }),
          onFloorAbsorb: (eventFloorAbsorb) => patchTapeDrawing({ eventFloorAbsorb }),
          onMarginal: (eventMarginal) => patchTapeDrawing({ eventMarginal }),
        },
      }),
    [
      tapeKnobs,
      changeBigLots,
      changeEvTuning,
      patchTapeDrawing,
      nodeProm,
      changeNodeProm,
      mvParams,
      patchMv,
      dsvParams,
      patchDsv,
    ],
  );

  const eventOverlay = useMemo(
    () => ({
      tuning: tapeKnobs.eventTuning,
      style: {
        labelSt: tapeKnobs.eventLabelSt,
        fillSweep: tapeKnobs.eventFillSweep,
        fillAbsorb: tapeKnobs.eventFillAbsorb,
      },
      floorSweep: tapeKnobs.eventFloorSweep,
      floorAbsorb: tapeKnobs.eventFloorAbsorb,
      marginal: tapeKnobs.eventMarginal,
    }),
    [tapeKnobs],
  );

  /** Read by the engine-build effect, which must not re-run when a knob moves —
   *  a knob change re-derives in place (above); rebuilding would re-decode the
   *  tape and throw away where you were. */
  const tapeKnobsRef = useRef(tapeKnobs);
  tapeKnobsRef.current = tapeKnobs;

  const startMs = data?.session_start_ms ?? 0;
  /** The blind ceiling: `WINDOW_POST_MS` past the fill, or the session's own end
   *  if the trade ran that close to the bell. Also where the card opens, so the
   *  two cannot drift apart. */
  const frontEndMs = Math.max(
    startMs,
    Math.min(data?.session_end_ms ?? card.cut_ms, card.cut_ms + WINDOW_POST_MS),
  );
  /** The right edge of what this card will draw. The window's far side while
   *  blind; the whole session once flipped. Every clamp, the tape source and the
   *  scrubber's max all read this one number. */
  const endMs = revealed ? (data?.session_end_ms ?? card.cut_ms) : frontEndMs;

  const stop = useCallback(() => {
    playingRef.current = false;
    setPlaying(false);
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    lastTsRef.current = null;
    if (Number.isFinite(clockRef.current)) setClockMs(clockRef.current);
  }, []);

  /** ~10 Hz, not 60: the rAF loop must not re-render React every frame to move
   *  a range input. */
  const publishClock = useCallback((clock: number) => {
    const now = performance.now();
    if (now - lastPubRef.current < 100) return;
    lastPubRef.current = now;
    setClockMs(clock);
  }, []);

  const frame = useCallback(
    (ts: number) => {
      const eng = engineRef.current;
      const src = sourceRef.current;
      if (!playingRef.current || !eng || !src) return;
      const last = lastTsRef.current ?? ts;
      lastTsRef.current = ts;
      const { clock, atEnd } = src.clockFor(clockRef.current, ts - last, speedRef.current * turbo.mult.current);
      chartRef.current?.applyStep(eng.advance(clock));
      clockRef.current = clock;
      publishClock(clock);
      if (atEnd) {
        stop();
        return;
      }
      rafRef.current = requestAnimationFrame(frame);
    },
    [publishClock, stop],
  );

  const seekTo = useCallback(
    (ms: number) => {
      const eng = engineRef.current;
      if (!eng) return;
      stop();
      const c = Math.max(startMs, Math.min(endMs, ms));
      chartRef.current?.setSnapshot(eng.snapshotTo(c), { reframe: "follow" });
      clockRef.current = c;
      setClockMs(c);
    },
    [endMs, startMs, stop],
  );

  const play = useCallback(() => {
    if (!engineRef.current || playingRef.current) return;
    // At the ceiling ▶ means "replay the window": rewind its whole length and
    // play forward through it. The front sits at the ceiling from its first frame
    // — it opens there — so without this the button never does anything until you
    // scrub back by hand, and a transport that has to be primed reads as broken.
    if (endMs - clockRef.current < SCRUB_STEP_MS) seekTo(endMs - WINDOW_MS);
    playingRef.current = true;
    setPlaying(true);
    lastTsRef.current = null;
    rafRef.current = requestAnimationFrame(frame);
  }, [endMs, frame, seekTo]);

  /** Where this side of the card's window starts. The front's is the minute
   *  around the fill, so it hangs off the ceiling; the back's is the reveal, so
   *  it hangs off the entry. One number, so the button, the flip and the
   *  card's own opening gesture cannot mean three different things by "the
   *  window". */
  const windowStartMs = useMemo(() => {
    const entryMs = back ? localMs(back.entry_ts_local) : null;
    return entryMs != null ? entryMs - REVEAL_LEAD_MS : endMs - WINDOW_MS;
  }, [back, endMs]);

  /** Rewind to that start and run it, wherever the clock stands. */
  const replayWindow = useCallback(() => {
    if (!engineRef.current) return;
    seekTo(windowStartMs);
    play();
  }, [play, seekTo, windowStartMs]);

  /** Read by the effects below, which must not re-run when the callback is
   *  rebuilt — the flip already has its own trigger and a new card has its. */
  const replayRef = useRef(replayWindow);
  replayRef.current = replayWindow;

  /** Hand the chart everything from scratch, as of the current clock. Idempotent
   *  — the surface can rebuild under us (`onReady`) and a timeframe change
   *  rebuilds the engine. */
  const publish = useCallback(() => {
    const eng = engineRef.current;
    const tape = tapeRef.current;
    if (!eng || !tape || !Number.isFinite(clockRef.current)) return;
    chartRef.current?.setTape(tape, { keepTools: true });
    chartRef.current?.setSnapshot(eng.snapshotTo(clockRef.current), { reframe: true });
    // The trade is drawn only on the back, and drawn whole: a back card that
    // made you play forward to find out where you got in would be coy about the
    // one thing it exists to answer.
    if (!back) {
      chartRef.current?.setTrades([]);
      return;
    }
    const entryMs = localMs(back.entry_ts_local);
    const exitMs = localMs(back.exit_ts_local);
    if (entryMs == null || exitMs == null) return;
    // The order behind the fill, when the trade came from a sitting: the bracket
    // it opened with, drawn where it stood, and — for a resting entry — the wait
    // before it filled. Reading the levels off the chart is a different question
    // from reading them off the panel: the panel says 21480, the chart says
    // *that far under the swing you were fading*.
    //
    // `rest_ms` is a duration, so the wait is placed off the fill the card has
    // already resolved. That is deliberate on the API's side — the log's clock
    // and the journal's are not the same one, and a duration needs neither.
    const ord = back.order;
    const mark: TradeMarkView = {
      id: back.trade_no,
      side: back.direction === "Short" ? "short" : "long",
      size: back.max_contracts,
      entryTime: eng.barTimeAt(entryMs),
      entryPrice: back.avg_entry,
      exitTime: eng.barTimeAt(exitMs),
      exitPrice: back.avg_exit,
      pnl: back.net_pnl,
      r: null,
      reason: ord?.exit_reason ?? "manual",
      stop: ord?.stop ?? null,
      target: ord?.target ?? null,
      rest:
        ord && ord.rest_price != null && ord.rest_ms != null
          ? { from: eng.barTimeAt(entryMs - ord.rest_ms), price: ord.rest_price }
          : null,
    };
    chartRef.current?.setTrades([mark]);
  }, [back]);

  const publishRef = useRef(publish);
  publishRef.current = publish;

  // Build the engine on the session arriving and on a re-bucketing only — a
  // flip must not re-decode a ~1M-tick tape it already holds.
  useEffect(() => {
    if (!data) return;
    const tape = decodeTape(data);
    const eng = new ReplayEngine(tape, data, timeframeById(tfId));
    // Before the first snapshot, so the opening frame is drawn at the settings
    // you left rather than at the engine's defaults and then re-derived.
    eng.setBigLots(tapeKnobsRef.current.bigLots);
    eng.setEventTuning(tapeKnobsRef.current.eventTuning);
    engineRef.current = eng;
    tapeRef.current = tape;
    sessionRef.current = data;
    // Opens on the window's far side: everything the front will ever show is
    // already drawn, and the question is the one at the hard right edge. ▶ is
    // what replays the minute that got there.
    clockRef.current = frontEndMs;
    setClockMs(frontEndMs);
    publishRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, tfId]);

  // The clock's ceiling moves with the phase, so the tape source is rebuilt
  // rather than reconfigured — it closes over its own end.
  useEffect(() => {
    sourceRef.current = replaySource(endMs);
  }, [endMs]);

  // The front plays itself, once per card: the rep is watching the minute, not
  // pressing a button to start watching it. Keyed on the card rather than folded
  // into the build effect above so that re-bucketing — which does rebuild the
  // engine — leaves the transport where you left it instead of lurching into
  // playback under a chart you were only re-reading.
  useEffect(() => {
    if (!data) return;
    replayRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.trade_key, data]);

  // The flip: draw the trade, then run the last seconds before the entry, which
  // is the review's own gesture.
  useEffect(() => {
    if (!back || !engineRef.current) return;
    publishRef.current();
    replayRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [back]);

  // A new card starts stopped, whatever the last one was doing.
  useEffect(() => () => stop(), [card.trade_key, stop]);

  if (session.isLoading) return <div className="notice">Loading the tape…</div>;
  if (session.isError || (!session.isLoading && !data))
    return (
      <div className="notice">
        No tick tape on disk for this card — it cannot be shown.
      </div>
    );

  const span = Math.max(1, endMs - startMs);
  return (
    // The tape, then the instrument that drives it. Under the chart rather than
    // over it because the row below the chart is the one your thumb is already
    // near — the rating buttons are the next thing down — and because a page
    // whose first row is a toolbar reads as a tool, not as a card.
    <div className="recall-stage">
      <div className="recall-chart" data-recall-chart>
        <ReplayChart
          ref={chartRef}
          tz={tz}
          symbol={revealed ? `${card.symbol} · ${card.date}` : card.root}
          tfLabel={tf.label}
          tfOptions={tfOptions}
          onTfChange={changeTf}
          secondsAxis={showsSeconds(tf)}
          hideDates={!revealed}
          onReady={publish}
          bigLots={tapeKnobs.bigLots}
          nodeProm={nodeProm}
          events={eventOverlay}
          modernVwap={mvParams}
          dynamicSwingVwap={dsvParams}
          studies={studies}
          onStudiesChange={changeStudies}
          indicatorSettings={indicatorSettings}
          // No `drawingsKey`, and that one *is* a blindness rule rather than an
          // omission: the tools you drew on this session elsewhere would come
          // back as levels only that day has, which names the day.
        />
      </div>
      <div className="sim-transport recall-transport" data-recall-transport>
        <button
          type="button"
          className={playing ? "active" : ""}
          data-recall-play
          onClick={() => (playing ? stop() : play())}
          title={
            !playing && clockMs >= endMs
              ? revealed
                ? "Rewind a minute and play it out"
                : "Replay the minute around the fill"
              : undefined
          }
        >
          {playing ? "⏸ Pause" : "▶ Play"}
        </button>
        {/* The window from its start, however far through it you are — the
            gesture the card opens with and the flip makes, on demand. */}
        <button
          type="button"
          data-recall-restart
          onClick={replayWindow}
          title={revealed ? "Replay from just before the entry" : "Replay the minute around the fill"}
        >
          ⟲ Restart
        </button>
        <span className="sim-lbl">Speed</span>
        <select
          value={speed}
          title="Hold Ctrl to run 10× this"
          onChange={(e) => changeSpeed(Number(e.target.value))}
        >
          {SIM_SPEEDS.map((v) => (
            <option key={v} value={v}>
              {v}×
            </option>
          ))}
        </select>
        <TurboChip on={turbo.on} speed={speed} />
        {/* Relative, so no epoch stamp — which is a date — reaches the DOM. */}
        <input
          type="range"
          className="sim-scrub"
          min={0}
          max={span}
          step={SCRUB_STEP_MS}
          value={Math.max(0, Math.min(span, clockMs - startMs))}
          onChange={(e) => seekTo(startMs + Number(e.target.value))}
        />
        <span className="sim-clock" style={{ fontFamily: "monospace", minWidth: 70 }}>
          {fmtClock(clockMs)}
        </span>
      </div>
    </div>
  );
}
