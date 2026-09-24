// How many prior sessions a bar wants drawn behind it.
//
// Context days exist for two reasons that disagree, which is why this is a rule
// and not a constant. *Levels* — yesterday's high, the shelf the week has been
// sat on — want at least one day behind you at any bar size, and one is usually
// enough. *Bar count* only bites at the slow end: an hourly with a single day in
// front of it has twenty-three candles on it, which is not a chart. So the rule
// is a floor of one day, raised until the bar has enough candles to read.
//
// Owned here rather than in either page's prefs because /charts/replay and
// /charts/live ask the same question of the same tape store, and the answer is a
// property of the *bucketing*, not of the page. Each page still keeps its own
// overrides (simPrefs, chartPrefs) — the two are deliberately allowed to be
// looking at different settings — but the default they depart from is one rule,
// so a bar means the same amount of history wherever you meet it.

import type { Timeframe } from "./timeframes";

/** Prior sessions the chart can carry as context. Each one is a whole tape — a
 *  few MB and about a million prints — so this is a short list rather than a
 *  box, and the steps get coarse past a working week.
 *
 *  20 is here for the 4h bar and effectively only for it: at ~5.75 bars a day
 *  nothing shorter puts a hundred candles on the screen. It is not a setting to
 *  reach for on a fast bar, where it buys nothing and costs ~20M prints.
 *
 *  One list, shared. Live used to carry a near-copy that differed by a single
 *  entry, which quietly made "prior days" mean two different things depending on
 *  which page you were reading. */
export const HISTORY_DAY_OPTIONS = [0, 1, 2, 3, 5, 10, 20];

/** Prints in an ordinary NQ session, near enough — the same ~1M/day figure the
 *  history controls have always been costed against. Only ever used to turn a
 *  tick bar into a candle count, and it is deciding between options that sit 2×
 *  apart, so the slop is not load-bearing. */
const PRINTS_PER_DAY = 1_000_000;

/** A context day, open to close: the 18:00 Globex open the evening before to the
 *  16:00 close. Not 6.5h — the days drawn to the left carry their overnight, and
 *  that is candles on the screen. */
const DAY_MS = 23 * 3_600_000;

/** Candles a bar wants behind it before the context reads as a chart rather than
 *  a handful of marks. A legibility judgement, not a measurement — it is the
 *  number the default table was drawn from, and moving it moves that table. */
const MIN_CONTEXT_BARS = 200;

/** Roughly how many candles one context day draws at this bucketing. */
export function barsPerDay(tf: Timeframe): number {
  return tf.kind === "tick" ? PRINTS_PER_DAY / tf.ticks : DAY_MS / tf.ms;
}

/**
 * The days this bar opens on when you have not said otherwise.
 *
 * The smallest offered count that clears `MIN_CONTEXT_BARS`, floored at one day
 * so the levels reason is always served, and capped at the largest option — 4h
 * cannot reach 200 candles inside any history this page will hold, and the
 * honest answer there is "as much as we offer" rather than a longer list.
 *
 * Every built-in bar falls out of this, which is the point: a typed `6m` gets
 * the same treatment as the `5m` button beside it, instead of landing on
 * whatever a hand-written table happened to omit.
 *
 *     500t 30s 1m 2m 3m 5m  →  1 day
 *     15m                   →  3 days
 *     1h                    →  10 days
 *     4h                    →  20 days
 */
export function defaultHistoryDays(tf: Timeframe): number {
  const per = barsPerDay(tf);
  for (const n of HISTORY_DAY_OPTIONS) {
    if (n >= 1 && n * per >= MIN_CONTEXT_BARS) return n;
  }
  return HISTORY_DAY_OPTIONS[HISTORY_DAY_OPTIONS.length - 1];
}

/** What a page has been told to use instead of the default, keyed by timeframe
 *  id. Overrides only — an untouched bar is absent rather than stored at its
 *  default, so a change to the rule above reaches every bar nobody has an
 *  opinion about. */
export type HistoryDayOverrides = Record<string, number>;

/** The days one bar asks for: what you chose for it, or what the rule says. */
export function historyDaysForTf(tf: Timeframe, over: HistoryDayOverrides): number {
  const v = over[tf.id];
  return v != null && HISTORY_DAY_OPTIONS.includes(v) ? v : defaultHistoryDays(tf);
}

/**
 * Which of the bars on screen governs the context, and how many days that is.
 *
 * Whichever asks for most. There is one tape, so days cannot be per pane: an
 * hourly pane beside a 1m pane pulls the hourly's days and the 1m draws them
 * too — which costs the 1m nothing, since the context is decoded once and it was
 * only ever a question of how far left the chart reaches.
 *
 * The governing bar comes back with the number because the control that edits
 * this has to say which bar it is editing — and, since the same control offers
 * to reset it, which bar the reset would be about.
 *
 * Ties go to the earliest in the list, which callers pass main-bar-first: with
 * two panes on 15m, "Prior days" is about the bar you are trading off. The one
 * exception is an override, which wins a tie over a bar that is merely following
 * the rule: if you pull the hourly down to the 1m's single day, the number on
 * screen is the hourly's doing and the reset has to still be able to reach it.
 */
export function governingHistory(
  tfs: readonly Timeframe[],
  over: HistoryDayOverrides,
): { tf: Timeframe; days: number } {
  let best = { tf: tfs[0], days: historyDaysForTf(tfs[0], over) };
  for (const tf of tfs.slice(1)) {
    const days = historyDaysForTf(tf, over);
    if (days > best.days || (days === best.days && over[tf.id] != null && over[best.tf.id] == null)) {
      best = { tf, days };
    }
  }
  return best;
}

/** Clamp a stored map back to something this module recognises. Unknown bar ids
 *  are kept rather than dropped — a `45s` you have since forgotten from the
 *  picker is still a bar you had an opinion about, and re-typing it should not
 *  also mean re-choosing its history. Values that are not on the list go, since
 *  the select could not show them. */
export function sanitizeHistoryDayOverrides(raw: unknown): HistoryDayOverrides {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: HistoryDayOverrides = {};
  for (const [id, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === "number" && HISTORY_DAY_OPTIONS.includes(v)) out[id] = v;
  }
  return out;
}

/** Set or clear one bar's override. Clearing is `null` and removes the key
 *  rather than writing the default in, so the bar goes back to following the
 *  rule instead of being pinned at whatever the rule said today. */
export function withHistoryOverride(
  over: HistoryDayOverrides,
  tfId: string,
  days: number | null,
): HistoryDayOverrides {
  const next = { ...over };
  if (days == null) delete next[tfId];
  else next[tfId] = days;
  return next;
}
