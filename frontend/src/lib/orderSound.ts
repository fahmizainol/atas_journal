// The noises a trading surface makes.
//
// Two moments deserve one: an order going out, and an order coming back filled.
// They are the two things you must not have to look away from the chart to know
// — the whole reason a platform beeps at all is that the eye is on price and the
// order confirmation happens somewhere else on the screen.
//
// TWO SOURCES, ON PURPOSE.
//
//   placed / filled → the platform's own samples, in public/sounds, in two packs
//     you switch between from the chart top bar: `tones` (Quantower's
//     OrderCreated/OrderFilled) and `voice` (ATAS saying the words). These are
//     the sounds a futures trader already has a reflex for, and a reflex is the
//     entire value of an audio cue: a noise you have to *learn* is a noise you
//     look at the screen to interpret, which is what it was supposed to save.
//     Which of the two suits depends on how much else is talking at you, so it
//     is a setting rather than a decision made here. See the note next to the
//     files for where they came from.
//
//   win / loss → synthesised, below. No platform ships a sound that says which
//     way a close went, because no platform decides that for you; these are two
//     tones going opposite directions, and the point is only that a stop and a
//     target are distinguishable with your eyes elsewhere. Shared by both packs:
//     there is no spoken recording of "that one paid".
//
// The synth is also the fallback for the first two. A missing or undecodable
// file leaves the cue audible rather than silent — the failure mode of "the fill
// sound stopped working" is that you stop trusting the ones you do hear.
//
// WHAT COUNTS AS AN EVENT is decided by `FillCues`, below, and deliberately not
// by the callers: both chart pages already re-derive their whole blotter on
// every change (see `replaySim` — there is no incremental fill path to hook), so
// "a fill happened" is a *diff between two published states*, not something
// anybody hands us. One watcher, fed a normalised mark, keeps the two pages from
// growing two different answers to that question.

import { loadSoundOn, loadSoundPack, saveSoundOn, saveSoundPack, type SoundPack } from "./chartPrefs";
import type { SimState } from "./replaySim";
import type { BrokerOrder, BrokerPosition, BrokerTrade } from "./routingTypes";

export type { SoundPack };

/**
 * `placed`   — an order left this process (rested on the tape, or reached the
 *              broker). Deliberately quiet: it happens on every gesture,
 *              including the ones you immediately cancel.
 * `filled` / `limitFilled` — size came on, and whether it came on passively.
 *              Split because the recorded packs split it: being filled on a
 *              resting bid is a different event from paying the offer, and it is
 *              the one you did not have to be at the keyboard for.
 * `stopFilled` — size came off because a stop was hit. Named rather than folded
 *              into `loss` for the same reason: it is the exit nobody chose.
 * `win` / `loss` — size came off any other way, and whether it paid.
 * `canceled` — a working order was pulled.
 * `changed`  — a resting price or a bracket leg was dragged somewhere new. The
 *              quietest of all of them; it fires on every landed drag.
 * `connectionLost` — the feed dropped. The one cue here that is not about an
 *              order, and the one you are least likely to be looking at the
 *              screen for.
 * `alert`    — the tape crossed a hand-drawn price line. Not about an order
 *              either: it is the level you asked to be told about, so it may be
 *              the only cue that fires on a session you never trade.
 */
export type Cue =
  | "placed"
  | "filled"
  | "limitFilled"
  | "stopFilled"
  | "win"
  | "loss"
  | "canceled"
  | "changed"
  | "connectionLost"
  | "alert";

/** One note: frequency in Hz, offset from the cue's start in seconds, length in
 *  seconds, and peak gain. */
interface Note {
  hz: number;
  at: number;
  dur: number;
  gain: number;
  type: OscillatorType;
}

// Rising two-tone: the platform convention for "this is now on".
const FILL_TONES: Note[] = [
  { hz: 740, at: 0, dur: 0.06, gain: 0.09, type: "sine" },
  { hz: 1110, at: 0.055, dur: 0.09, gain: 0.09, type: "sine" },
];
// Falling, and lower. Not a buzzer: being stopped out is an ordinary event and
// the sound that marks it should not be a punishment.
const DOWN_TONES: Note[] = [
  { hz: 466, at: 0, dur: 0.07, gain: 0.07, type: "sine" },
  { hz: 349, at: 0.065, dur: 0.13, gain: 0.07, type: "sine" },
];

// Kept quiet on purpose. These play over whatever else is on — a livestream, the
// desk's own audio — and a cue that makes you reach for the volume is a cue you
// end up muting for good.
//
// Every cue has an entry, including the ones the recorded packs cover, because
// this table is also the fallback. Where a pack draws a distinction the tones
// cannot (a limit fill against any other fill), both map to the same notes
// rather than inventing a difference nobody would learn.
const CUES: Record<Cue, Note[]> = {
  // A single soft tick, short enough that a burst of them (space+click along a
  // level ladder) reads as typing rather than as an alarm.
  placed: [{ hz: 620, at: 0, dur: 0.045, gain: 0.05, type: "triangle" }],
  filled: FILL_TONES,
  limitFilled: FILL_TONES,
  stopFilled: DOWN_TONES,
  loss: DOWN_TONES,
  // Brighter, and up again — a close that paid.
  win: [
    { hz: 988, at: 0, dur: 0.06, gain: 0.08, type: "sine" },
    { hz: 1319, at: 0.055, dur: 0.11, gain: 0.08, type: "sine" },
  ],
  // The tick, undone: same shape as `placed` but going down, so pulling an order
  // sounds like the opposite of putting one out.
  canceled: [
    { hz: 620, at: 0, dur: 0.035, gain: 0.045, type: "triangle" },
    { hz: 440, at: 0.04, dur: 0.05, gain: 0.045, type: "triangle" },
  ],
  // The quietest thing in the file. A drag is a small adjustment you are making
  // deliberately, with your eyes on the level — the sound is a receipt, not news.
  changed: [{ hz: 880, at: 0, dur: 0.025, gain: 0.03, type: "sine" }],
  // The one cue allowed to be more than a tick. Three tones walking down, longer
  // and louder than anything else here, because it is the only one that means
  // the screen you are watching has stopped being true.
  connectionLost: [
    { hz: 660, at: 0, dur: 0.12, gain: 0.1, type: "sine" },
    { hz: 550, at: 0.13, dur: 0.12, gain: 0.1, type: "sine" },
    { hz: 415, at: 0.26, dur: 0.24, gain: 0.1, type: "sine" },
  ],
  // Two identical high pings — a doorbell, not a fill. Level with the fill pair
  // in loudness (you drew the line to be told), but flat where every order cue
  // moves, so the ear files it as "the market reached something" rather than
  // "something happened to a position". No recorded pack has a word for it, so
  // both packs play this.
  alert: [
    { hz: 1175, at: 0, dur: 0.09, gain: 0.09, type: "sine" },
    { hz: 1175, at: 0.14, dur: 0.09, gain: 0.09, type: "sine" },
  ],
};

/** Which cue's recording stands in when a pack has none of its own. Only the
 *  fill split needs it: a pack that never recorded "limit filled" should play
 *  what it says for a fill, not drop to a synthesised tone while its neighbours
 *  are spoken. */
const SAMPLE_ALIAS: Partial<Record<Cue, Cue>> = { limitFilled: "filled" };

/** A recorded pack: which file each cue plays, and how far to pull it down.
 *
 *  The gains are not taste, they are measurement. Both sets of files were cut
 *  hot (peaking −1 to −4dBFS), and the two are not hot in the same way: the
 *  tonal pair is a transient with air around it (RMS 0.089) while a spoken cue
 *  is 0.7s of continuous voice (RMS 0.21), so playing them at the same gain
 *  would make the voice roughly three times as loud. Each gain is set so the
 *  pack lands at the same RMS as the synthesised cues, which is the level the
 *  whole file was tuned to: audible over a livestream, not worth reaching for
 *  the volume over. */
interface Pack {
  gain: number;
  urls: Partial<Record<Cue, string>>;
}

const PACKS: Record<SoundPack, Pack> = {
  // Quantower's OrderCreated / OrderFilled: a soft double-blip and a short
  // confirmation chime. What most platforms sound like. It records only those
  // two events, so the rest of the cues are the synthesised ones above — which
  // is the right way round: the events it has no word for are the ones a tone
  // says perfectly well.
  tones: {
    gain: 0.55,
    urls: { placed: "/sounds/order-placed.wav", filled: "/sounds/order-filled.wav" },
  },
  // ATAS saying what happened. Unmistakable across a room and impossible to
  // confuse with the market's own noise — but 0.7s of speech is a long time to
  // be talked at when you are clicking along a level ladder, which is exactly
  // why this is a choice and not the default.
  //
  // The female voice throughout, except `limitFilled`: ATAS never recorded that
  // one for her, so it is the male voice's, pulled to the female set's RMS at
  // conversion time (see demo/resample_cue.py). One cue in a second voice is
  // odd; the alternative was the only fill you don't have to be at the keyboard
  // for sounding like every other fill.
  voice: {
    gain: 0.25,
    urls: {
      placed: "/sounds/voice/order-placed.wav",
      filled: "/sounds/voice/order-filled.wav",
      limitFilled: "/sounds/voice/limit-filled.wav",
      stopFilled: "/sounds/voice/stop-filled.wav",
      canceled: "/sounds/voice/order-canceled.wav",
      changed: "/sounds/voice/order-changed.wav",
      connectionLost: "/sounds/voice/connection-lost.wav",
    },
  },
};

let ctx: AudioContext | null = null;
let armed = false;
let on = loadSoundOn();
let pack = loadSoundPack();
/** Decoded buffers, keyed `pack:cue` — both packs can be resident at once, which
 *  is what makes switching between them instant enough to compare them. */
const decoded = new Map<string, AudioBuffer>();
/** One promise per pack, so a second request joins the first download rather
 *  than starting another. Resolves when that pack has finished trying. */
const loads = new Map<SoundPack, Promise<void>>();

/** Whether cues are currently audible. */
export function soundOn(): boolean {
  return on;
}

/** Which recorded pack is in use. */
export function soundPack(): SoundPack {
  return pack;
}

/** Mute or unmute, for this page and every one after it. Silent in itself — the
 *  caller decides whether the change deserves an acknowledgement, and on the
 *  chart bar it does (see `previewCue`). */
export function setSoundOn(next: boolean): void {
  on = next;
  saveSoundOn(next);
}

/** Switch packs, for this page and every one after it. Starts the new pack
 *  downloading; it does not wait, because nothing is being played yet. */
export function setSoundPack(next: SoundPack): void {
  pack = next;
  saveSoundPack(next);
  const c = audio();
  if (c) void loadSamples(c, next);
}

/**
 * Say what the current setting sounds like, in answer to a press.
 *
 * The one cue worth waiting on. Everywhere else a late sound is worse than no
 * sound — a chime that lands a second after the fill is a lie about when it
 * happened — but this one *is* the answer to the button, so it waits for both
 * things that could delay it: the context resuming (the press may be the very
 * gesture that unlocks audio) and the pack finishing its download (pressing
 * "voice" and hearing the fallback beep would be a lie about what you selected).
 */
export function previewCue(): void {
  if (!on) return;
  const c = audio();
  if (!c) return;
  const resumed = c.state === "suspended" ? Promise.resolve(c.resume()) : Promise.resolve();
  void Promise.all([resumed, loadSamples(c, pack)]).then(() => playCue("placed"));
}

/**
 * The audio context, created on first use.
 *
 * Browsers refuse to start one outside a user gesture, and a fill is not a
 * gesture — it arrives from a frame loop or a poll. So the *first* cue of a page
 * may well be dropped, and the fix is not to retry it (a sound that arrives late
 * is worse than one that never came) but to make sure the context is running by
 * the time the second one is due: `armAudio` resumes it on the next click or
 * keypress, and on these pages there is always one — you loaded a session, you
 * pressed Play, you placed the order.
 */
function audio(): AudioContext | null {
  if (ctx) return ctx;
  const Ctor = window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    ctx = new Ctor();
  } catch {
    return null;   // no audio device / blocked — the charts still work
  }
  return ctx;
}

/** Fetch and decode one pack. Decoding does not need a running context — only
 *  playing does — so this can run the moment a chart page mounts, which is what
 *  makes the first fill of a session sound like the ones after it. Only the pack
 *  in use is fetched; the other one costs nothing until it is chosen.
 *
 *  Failures are swallowed on purpose, and the promise still resolves: a cue with
 *  no buffer falls through to the synthesised version in `playCue`, and a chart
 *  page is not the place to report that a wav 404'd. */
function loadSamples(c: BaseAudioContext, p: SoundPack): Promise<void> {
  let pending = loads.get(p);
  if (pending) return pending;
  pending = Promise.all(
    (Object.entries(PACKS[p].urls) as [Cue, string][]).map(([cue, url]) =>
      fetch(url)
        .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`${r.status}`))))
        .then((bytes) => c.decodeAudioData(bytes))
        .then((buf) => void decoded.set(`${p}:${cue}`, buf))
        .catch(() => {}),
    ),
  ).then(() => {});
  loads.set(p, pending);
  return pending;
}

/** Install the one-time gesture listener that unlocks playback, and start
 *  pulling the current pack down. Safe to call repeatedly; the listeners install
 *  once and remove themselves. */
export function armAudio(): void {
  if (armed) return;
  armed = true;
  const c = audio();
  if (c) void loadSamples(c, pack);
  const unlock = () => {
    const ac = audio();
    if (ac && ac.state === "suspended") void ac.resume();
  };
  window.addEventListener("pointerdown", unlock, { once: true, capture: true });
  window.addEventListener("keydown", unlock, { once: true, capture: true });
}

// Two cues at the same instant is a stack, not a chord: a flip books a trade and
// opens a position in one tick, and both firing would clip. The watcher already
// picks one event per change; this is the backstop for the paths that don't go
// through it.
let lastPlayed = 0;

export function playCue(cue: Cue): void {
  if (!on) return;
  const c = audio();
  if (!c) return;
  if (c.state === "suspended") {
    // Not yet unlocked. Ask, and drop this one rather than queue it.
    void c.resume();
    return;
  }
  const now = c.currentTime;
  if (now - lastPlayed < 0.05) return;
  lastPlayed = now;
  const alias = SAMPLE_ALIAS[cue];
  const buf = decoded.get(`${pack}:${cue}`) ?? (alias && decoded.get(`${pack}:${alias}`));
  if (buf) {
    const src = c.createBufferSource();
    const g = c.createGain();
    src.buffer = buf;
    g.gain.value = PACKS[pack].gain;
    src.connect(g).connect(c.destination);
    src.start(now);
    return;
  }
  // No sample for this cue, or it never arrived — synthesise it.
  for (const n of CUES[cue]) {
    const osc = c.createOscillator();
    const g = c.createGain();
    osc.type = n.type;
    osc.frequency.setValueAtTime(n.hz, now + n.at);
    // Ramped rather than switched, both ends: a gain that steps to its value
    // makes a click of its own, which on a 45ms cue is most of what you hear.
    g.gain.setValueAtTime(0.0001, now + n.at);
    g.gain.exponentialRampToValueAtTime(n.gain, now + n.at + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, now + n.at + n.dur);
    osc.connect(g).connect(c.destination);
    osc.start(now + n.at);
    osc.stop(now + n.at + n.dur + 0.02);
  }
}

/** The blotter reduced to what decides whether something happened, and which
 *  something it was: signed position, how many round trips are booked, what they
 *  have paid in total, and enough about the newest of each to name the event.
 *  Paper and broker state both fold to this. */
export interface BookMark {
  /** Positive long, negative short, zero flat. */
  net: number;
  trades: number;
  /** Cumulative realised P&L. Only ever read as a difference — the sign of what
   *  the *newest* trades booked. */
  pnl: number;
  /** The order type that opened the position now on, or null when flat. What
   *  separates a limit fill from any other.
   *
   *  Honest about its limit: this is the *opening* fill's type, so a scale-in
   *  is announced as whatever got you in rather than as whatever added. Neither
   *  blotter records a per-fill order type — the paper position keeps one
   *  `openType`, and the broker reports a net quantity — and inventing one from
   *  which order happened to be working would be a guess dressed as a fact. The
   *  common case (place a limit, hear it fill) is the one it gets right. */
  openType: string | null;
  /** Why the newest booked trade came off: `stop`, `trail`, `target`, `reduce`,
   *  `manual`. Null when nothing is booked. Shared vocabulary between the paper
   *  engine and the server's fill pairing, which is what lets one watcher name
   *  the exit on either account. */
  lastReason: string | null;
}

/**
 * Turns a stream of blotter states into fill events.
 *
 * The contract that makes this safe on pages that re-derive from scratch: a
 * blotter that *appears* is not a blotter that *happened*. Restoring a resumed
 * sitting, seeking to a new clock and switching accounts all hand over a state
 * with trades already booked and possibly a position on — none of that is
 * happening now, and a page that announced it would beep six times on load. So
 * those paths call `sync()` when they are done re-deriving, and only the steps
 * *between* them make noise. A watcher that has never been synced adopts its
 * first mark in silence for the same reason.
 */
export class FillCues {
  private last: BookMark | null = null;

  /** Take this mark as the new baseline without sounding a thing. What every
   *  wholesale rebuild calls once it has finished re-deriving. */
  sync(now: BookMark): void {
    this.last = now;
  }

  /** Compare against the last mark, sound the difference, and keep it. */
  observe(now: BookMark): void {
    const was = this.last;
    this.last = now;
    if (!was) return;                       // adoption, not an event
    if (now.trades > was.trades) {
      // Size came off. One cue for the whole step even when several portions
      // booked at once — a scale-out that filled in three clips is one event to
      // the person watching, and its sign is what the three of them netted.
      //
      // A stop is named rather than scored. The ladder's own exit counts as one:
      // `trail` is a stop that moved itself, and being taken out on it is the
      // same experience as being taken out on the one you placed.
      const stopped = now.lastReason === "stop" || now.lastReason === "trail";
      playCue(stopped ? "stopFilled" : now.pnl - was.pnl >= 0 ? "win" : "loss");
      return;
    }
    // Anything else that moved the net position is size coming *on*: opening,
    // adding, or the entry half of a flip. (A reduction always books a trade, so
    // it was handled above.)
    if (now.net !== was.net) playCue(now.openType === "limit" ? "limitFilled" : "filled");
  }
}

/** The paper simulation as a mark. */
export function simMark(st: SimState): BookMark {
  const net = st.open ? (st.open.side === "long" ? st.open.size : -st.open.size) : 0;
  let pnl = 0;
  for (const t of st.trades) pnl += t.pnl;
  return {
    net,
    trades: st.trades.length,
    pnl,
    openType: st.open?.openType ?? null,
    lastReason: st.trades.length ? st.trades[st.trades.length - 1].reason : null,
  };
}

/** The broker's own state as a mark. Its position is already signed, and its
 *  round trips are paired server-side by the same netting rules — which is what
 *  lets one watcher serve both accounts.
 *
 *  `recent` is the broker's list of orders that are no longer working, newest
 *  first. The newest one with fills against it is what just filled, and its type
 *  is the only place a real account says whether you were hit on a resting order
 *  — the position itself reports a net quantity and nothing about how it got
 *  there. Omit it and a real limit fill is announced as an ordinary one, which
 *  is a smaller error than guessing. */
export function brokerMark(
  pos: BrokerPosition | null,
  trades: BrokerTrade[],
  recent: BrokerOrder[] = [],
): BookMark {
  let pnl = 0;
  for (const t of trades) pnl += t.pnl;
  return {
    net: pos?.net ?? 0,
    trades: trades.length,
    pnl,
    openType: recent.find((o) => o.filled > 0)?.type ?? null,
    lastReason: trades.length ? trades[trades.length - 1].reason : null,
  };
}
