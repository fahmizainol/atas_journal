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

export type Timeframe =
  /** Closes on a wall-clock boundary. */
  | { id: string; label: string; kind: "time"; ms: number }
  /** Closes on a count of prints — the tape's own clock rather than the wall's. */
  | { id: string; label: string; kind: "tick"; ticks: number };

/** Ordered fastest-first, which is also how they sit in the picker. 500 prints
 *  runs ~25s on a normal NQ session, so it leads the sub-minute end. */
export const TIMEFRAMES: readonly Timeframe[] = [
  { id: "500t", label: "500t", kind: "tick", ticks: 500 },
  { id: "30s", label: "30s", kind: "time", ms: 30_000 },
  { id: "1m", label: "1m", kind: "time", ms: 60_000 },
  { id: "2m", label: "2m", kind: "time", ms: 120_000 },
  { id: "3m", label: "3m", kind: "time", ms: 180_000 },
  { id: "5m", label: "5m", kind: "time", ms: 300_000 },
  { id: "15m", label: "15m", kind: "time", ms: 900_000 },
  { id: "1h", label: "1h", kind: "time", ms: 3_600_000 },
];

export const DEFAULT_TIMEFRAME_ID = "1m";

export function timeframeById(id: string): Timeframe {
  return (
    TIMEFRAMES.find((t) => t.id === id) ??
    TIMEFRAMES.find((t) => t.id === DEFAULT_TIMEFRAME_ID)!
  );
}

/** Whether the time axis has to name seconds. Below a minute — and a tick bar is
 *  below one most of the time — an hh:mm axis labels several bars identically. */
export function showsSeconds(tf: Timeframe): boolean {
  return tf.kind === "tick" || tf.ms < 60_000;
}
