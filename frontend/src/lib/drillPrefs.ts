// Backtest mode's own settings — the three that are about the *drill* rather
// than about the chart.
//
// Its own key rather than fields on `sim.prefs`, and the split is on purpose.
// Everything a drill shares with a replay it genuinely shares: the same ticket,
// the same fill model and its costs, the same bar sizes, layouts, indicators and
// studies. A drill is the same chart with the same money on it; what differs is
// which model you are exercising and where you are thrown in.
//
// Three fields, and each of them has to persist for a *campaign* rather than a
// sitting — you drill one model over dozens of reps, and re-picking it every
// rep would be the first thing to get skipped.
//
// See docs/backtest-mode-plan.md D5 for why the window is configurable at all.

const KEY = "sim.drill";

export interface DrillPrefs {
  /** The model every trade in a rep is booked against. Null is the un-started
   *  state: backtest mode refuses to draw without one, because the binding is
   *  the whole difference between this and a blind replay. */
  modelId: number | null;
  /** The ET wall-clock window the drop is drawn from, "HH:MM".
   *
   *  The default excludes the last hour of RTH so every rep has at least an
   *  hour of runway to the close — and because the last hour is a different
   *  animal (settlement flows, position squaring) that most models have nothing
   *  to say about. Narrowing it further is how you drill an afternoon-only
   *  model where it lives; it is also you telling yourself where the setup is,
   *  which is why the drop histogram records what was actually drawn from. */
  dropFrom: string;
  dropTo: string;
}

export const DEFAULT_DRILL_PREFS: DrillPrefs = {
  modelId: null,
  dropFrom: "09:30",
  dropTo: "15:00",
};

const HHMM = /^\d{1,2}:\d{2}$/;

const time = (v: unknown, fallback: string): string =>
  typeof v === "string" && HHMM.test(v) ? v : fallback;

export function loadDrillPrefs(): DrillPrefs {
  const d = DEFAULT_DRILL_PREFS;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...d };
    const s = JSON.parse(raw) as Partial<Record<keyof DrillPrefs, unknown>>;
    return {
      // A model that has since been archived or deleted still loads: the picker
      // is what notices, and silently unbinding here would drop a campaign's
      // model on a page that had no reason to mention it.
      modelId: typeof s.modelId === "number" && Number.isFinite(s.modelId) ? s.modelId : d.modelId,
      dropFrom: time(s.dropFrom, d.dropFrom),
      dropTo: time(s.dropTo, d.dropTo),
    };
  } catch {
    return { ...d };
  }
}

export function saveDrillPrefs(p: DrillPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* private mode, quota — a lost preference is not worth an error path */
  }
}

/** Minutes past midnight, for a "HH:MM" that has already been validated. */
export function minutesOf(hhmm: string): number {
  const [h, m] = hhmm.split(":");
  return Number(h) * 60 + Number(m);
}

/** The inverse: ET minutes past midnight back to "HH:MM". */
export function hhmm(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** Is the window one a drop can actually be drawn from?
 *
 *  Equal bounds are legal and mean "always drop here" — a fixed-time drill is a
 *  reasonable thing to want, and refusing it would be arithmetic pretending to
 *  be a rule. Inverted bounds are not. */
export function windowOk(p: DrillPrefs): boolean {
  return minutesOf(p.dropFrom) <= minutesOf(p.dropTo);
}
