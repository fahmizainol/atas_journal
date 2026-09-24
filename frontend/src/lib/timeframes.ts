// The bar the Simulator draws.
//
// A timeframe here is a *bucketing rule over the tape*, not a data request. The
// session arrives as raw ticks (api/routers/simulator.py ships prints, never
// bars) and every layer the chart draws — the candles, both VWAP bands, both
// developing value areas, the IB — is built from them in the browser. So
// switching is a client-side rebuild through the path a seek already uses, and
// nothing about the replay's *trading* changes: fills are resolved against tick
// indices in lib/replaySim, so the same session traded on 30s and on 1h fills at
// exactly the same prints for exactly the same P&L. The timeframe is what you
// see, never what you get.
//
// It is also why a tick bar is possible at all: there is no bar feed here to ask
// one of.

import { useSyncExternalStore } from "react";

export type Timeframe =
  /** Closes on a wall-clock boundary. */
  | { id: string; label: string; kind: "time"; ms: number }
  /** Closes on a count of prints — the tape's own clock rather than the wall's. */
  | { id: string; label: string; kind: "tick"; ticks: number };

/** The ones with a permanent button. Ordered fastest-first, which is also how
 *  they sit in the picker and how the number keys are numbered. 500 prints runs
 *  ~25s on a normal NQ session, so it leads the sub-minute end.
 *
 *  Not the only ones that exist: anything `parseTimeframeId` accepts is a bar
 *  this chart can draw, and the picker's field is how you ask for one. 4h is on
 *  the list rather than left to that field because it is the bar the context
 *  rule stretches furthest for (lib/contextDays: 20 days), and a bucketing worth
 *  a special case in the history is worth a button. */
export const TIMEFRAMES: readonly Timeframe[] = [
  { id: "500t", label: "500t", kind: "tick", ticks: 500 },
  { id: "30s", label: "30s", kind: "time", ms: 30_000 },
  { id: "1m", label: "1m", kind: "time", ms: 60_000 },
  { id: "2m", label: "2m", kind: "time", ms: 120_000 },
  { id: "3m", label: "3m", kind: "time", ms: 180_000 },
  { id: "5m", label: "5m", kind: "time", ms: 300_000 },
  { id: "15m", label: "15m", kind: "time", ms: 900_000 },
  { id: "1h", label: "1h", kind: "time", ms: 3_600_000 },
  { id: "4h", label: "4h", kind: "time", ms: 14_400_000 },
];

export const DEFAULT_TIMEFRAME_ID = "1m";

const MAX_TICKS = 100_000;
const MAX_MS = 24 * 3_600_000;

/**
 * A bucketing written the way you would say it: "45s", "7m", "2h", "1500t", and
 * a bare number read as minutes (the convention every charting package shares).
 * This is what the picker's custom field takes, and it is also what makes the
 * built-in list above a *default* rather than a limit — a bucketing rule needs
 * nothing from a server, so there is no reason the eight we chose should be the
 * eight that exist.
 *
 * Null when the text isn't a bar: the caller shows that as a rejected field
 * rather than silently drawing something else. The ceilings are there so a
 * fat-fingered "999999h" cannot produce a chart with one bar on it.
 */
export function parseTimeframeId(raw: string): Timeframe | null {
  const m = /^(\d{1,6})\s*(t|s|m|h)?$/.exec(raw.trim().toLowerCase());
  if (!m) return null;
  const n = Number(m[1]);
  if (n <= 0) return null;
  const unit = m[2] ?? "m";
  if (unit === "t") return n <= MAX_TICKS ? { id: `${n}t`, label: `${n}t`, kind: "tick", ticks: n } : null;
  const ms = n * (unit === "s" ? 1_000 : unit === "m" ? 60_000 : 3_600_000);
  if (ms > MAX_MS) return null;
  return { id: `${n}${unit}`, label: `${n}${unit}`, kind: "time", ms };
}

/** Whether a stored string still names a bar. What the prefs loaders clamp on:
 *  before custom bucketings existed that meant "is it one of the eight", and a
 *  saved `45s` would have been thrown away on the next reload. */
export function isTimeframeId(v: unknown): v is string {
  return typeof v === "string" && parseTimeframeId(v) !== null;
}

export function timeframeById(id: string): Timeframe {
  // The built-in first, so the same id keeps handing back the same object — a
  // parse would mint a new one on every render.
  return (
    TIMEFRAMES.find((t) => t.id === id) ??
    parseTimeframeId(id) ??
    TIMEFRAMES.find((t) => t.id === DEFAULT_TIMEFRAME_ID)!
  );
}

// ---- the bucketings you added yourself ----
//
// Kept here rather than in lib/chartPrefs with the other sticky-global chart
// settings for one reason: that module imports this one (for the default id and
// the list), so a store on the other side would be a cycle. Everything else
// about it follows the chartPrefs convention — a `chart.` key, clamped on read,
// and a write that survives a private-mode throw.
//
// A store rather than page state because two pickers show this list at once (the
// top bar's and every pane legend's), and a bar typed into one has to appear in
// the others without a reload.

const CUSTOM_KEY = "chart.customTimeframes";
/** Oldest drops out. A picker is a row of buttons, not a history. */
const MAX_CUSTOM = 8;

type TfOption = { key: string; label: string };

let cachedIds: string[] | null = null;
let cachedOptions: readonly TfOption[] | null = null;
const listeners = new Set<() => void>();

function customIds(): string[] {
  if (cachedIds) return cachedIds;
  let stored: unknown = null;
  try {
    stored = JSON.parse(localStorage.getItem(CUSTOM_KEY) ?? "[]");
  } catch {
    stored = null;
  }
  const seen = new Set(TIMEFRAMES.map((t) => t.id));
  const out: string[] = [];
  for (const v of Array.isArray(stored) ? stored : []) {
    const tf = typeof v === "string" ? parseTimeframeId(v) : null;
    if (!tf || seen.has(tf.id)) continue;
    seen.add(tf.id);
    out.push(tf.id);
  }
  cachedIds = out.slice(-MAX_CUSTOM);
  return cachedIds;
}

function writeCustom(ids: string[]): void {
  cachedIds = ids;
  cachedOptions = null;
  try {
    localStorage.setItem(CUSTOM_KEY, JSON.stringify(ids));
  } catch {
    // Private mode: the list still holds for this session.
  }
  for (const fn of listeners) fn();
}

function options(): readonly TfOption[] {
  if (!cachedOptions)
    cachedOptions = [...TIMEFRAMES, ...customIds().map(timeframeById)].map((t) => ({
      key: t.id,
      label: t.label,
    }));
  return cachedOptions;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** The built-ins plus yours, as `{key, label}` — what every picker takes. Here
 *  rather than re-mapped at each call site, because four components building the
 *  same array four times is four places for it to drift. */
export function useTimeframeOptions(): readonly TfOption[] {
  return useSyncExternalStore(subscribe, options, options);
}

/** Take a bucketing typed into a picker: remember it and hand it back so the
 *  caller can switch to it. Null when the text isn't a bar. A built-in comes
 *  back unstored — it already has a button. */
export function addCustomTimeframe(raw: string): Timeframe | null {
  const tf = parseTimeframeId(raw);
  if (!tf) return null;
  if (TIMEFRAMES.some((t) => t.id === tf.id)) return tf;
  const ids = customIds();
  if (!ids.includes(tf.id)) writeCustom([...ids, tf.id].slice(-MAX_CUSTOM));
  return tf;
}

export function removeCustomTimeframe(id: string): void {
  const ids = customIds();
  if (ids.includes(id)) writeCustom(ids.filter((x) => x !== id));
}

export function isCustomTimeframe(id: string): boolean {
  return customIds().includes(id);
}

/** Whether the time axis has to name seconds. Below a minute — and a tick bar is
 *  below one most of the time — an hh:mm axis labels several bars identically. */
export function showsSeconds(tf: Timeframe): boolean {
  return tf.kind === "tick" || tf.ms < 60_000;
}
