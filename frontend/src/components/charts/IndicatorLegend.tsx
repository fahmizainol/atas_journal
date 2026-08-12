import { useEffect, useState } from "react";
import { loadLegendOpen, saveLegendOpen } from "../../lib/chartPrefs";
import { IndicatorSettings, type IndicatorSettingsSpec } from "./IndicatorSettings";

// One union across both chart components, because the persisted visibility map
// is one map: a band hidden on a strategy chart comes up hidden on the replay,
// and vice versa. The cost of that sharing is that each component draws only a
// subset — the rows below marked "journal charts only" never appear on
// ReplayChart, and the "Simulator only" ones never on CandlestickChart — so an
// unfamiliar key here is not dead, it belongs to the other chart.
export type IndicatorKey =
  | "vwapGlobex"
  | "vwapNy"
  | "vwapWeekly"
  | "vwapAnchored"
  /** Journal charts only (CandlestickChart): ATR pane, key levels, the four
   *  1-minute EMAs, RSI pane, level touches and VA-snap marks. */
  | "atr"
  | "cvd"
  | "levels"
  | "initialBalance"
  | "ibExtensions"
  | "volumeProfile"
  | "developingProfileGlobex"
  | "developingProfileNy"
  | "ema9"
  | "ema20"
  | "ema50"
  | "ema200"
  | "rsi"
  | "touches"
  | "va_snaps"
  /** Simulator only: the marks left by trades closed during a replay. */
  | "replayTrades"
  /** Simulator only: the tape's own big trades — sweeps over the lot threshold. */
  | "bigTrades"
  /** Simulator only: the composite of the prior sessions drawn as context, and
   *  the HVN/LVN nodes read off it. Two keys, because the histogram and the
   *  levels answer different questions and you often want only the second. */
  | "compositeProfile"
  | "compositeNodes"
  /** Simulator only: the developing NY profile as a histogram, in its own
   *  gutter. Distinct from `developingProfileNy`, which is the VAH/POC/VAL lines
   *  the same distribution produces — you often want the levels without the
   *  shape, and occasionally the other way round. */
  | "developingVpNy"
  /** Simulator only: the HVN/LVN nodes read off that histogram, drawn as prices
   *  from the bell rightward. Its own key for the same reason the composite's
   *  nodes have one — these are the half that draws over the price action. */
  | "developingVpNyNodes"
  /** Simulator only: the two tape-event proxies (see replayEngine's TapeEvent).
   *  Separate keys because they stand for opposite halves of the same idea —
   *  size arriving, and size defending. */
  | "sweepBursts"
  | "absorption"
  /** Charts workspace only (ReplayChart): the bar-range vol pane — ATR line,
   *  developing median bar range, yesterday's median, all in ticks against the
   *  50-tick stop. See lib/volRuler. */
  | "volRuler"
  /** Charts workspace only: Modern VWAP [GBB] — the swing-anchored VWAP and its
   *  σ envelope, and the MR/TC triggers it names. Two keys because the line is a
   *  reference you may want without a layer of arrows over the tape, and every
   *  knob for both hangs off either row's "…". An unfalsified indicator: see
   *  lib/modernVwap and docs/research/modern-vwap.html before reading anything
   *  off it. */
  | "modernVwap"
  | "modernVwapSignals";

export interface LegendItem {
  key: IndicatorKey;
  label: string;
  color: string;
  /** The knobs behind this row's "…". Absent on a layer with nothing to tune —
   *  most of them — and the row then has no button at all. */
  settings?: IndicatorSettingsSpec;
  /** The layer isn't on the chart for a reason other than its eye: a setting of
   *  its own has it switched off. Drawn like a hidden row, because that is what
   *  it is; the row still exists so the setting that turned it off is somewhere
   *  you can reach. */
  dim?: boolean;
}

/** Settings by layer, as the page that owns those settings hands them over. */
export type IndicatorSettingsMap = Partial<Record<IndicatorKey, IndicatorSettingsSpec>>;

function EyeIcon({ open }: { open: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
      <circle cx="12" cy="12" r="3" />
      {!open && <line x1="2" y1="2" x2="22" y2="22" />}
    </svg>
  );
}

function LayersIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 2 2 7l10 5 10-5-10-5z" />
      <path d="M2 17l10 5 10-5" />
      <path d="M2 12l10 5 10-5" />
    </svg>
  );
}

function DotsIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
      <circle cx="5" cy="12" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="19" cy="12" r="1.8" />
    </svg>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.12s ease" }}
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

/** The header's panel is keyed like a row's so one piece of open/close logic
 *  covers both — it just isn't a layer. */
const APPEARANCE = "__appearance";
type PanelKey = IndicatorKey | typeof APPEARANCE;

// TV-style on-chart indicator list: one row per indicator, click to hide/show,
// and a "…" on the rows that have something to tune. The whole list collapses
// behind its header so it can get out of the chart's way — handy on a phone,
// where the expanded list can cover much of the frame.
export function IndicatorLegend({
  items,
  visibility,
  onToggle,
  appearance,
  prefsPane,
}: {
  items: LegendItem[];
  visibility: Record<IndicatorKey, boolean>;
  onToggle: (key: IndicatorKey) => void;
  /** The chart's own colours, hung off the list's header rather than off a row —
   *  it is the one setting here that belongs to the whole chart instead of to a
   *  layer. Optional: a chart that doesn't own its surface omits it and the
   *  header keeps its old shape, no "…" at all. */
  appearance?: IndicatorSettingsSpec;
  /** Which pane's copy of the open/closed state to keep. See chartPrefs.paneKey. */
  prefsPane?: string;
}) {
  const [open, setOpen] = useState(() => loadLegendOpen(prefsPane));
  /** Which row has its settings panel out — `APPEARANCE` for the header's. One at
   *  a time: they overlay the rows below them, so two open panels would mostly be
   *  one panel hiding another. */
  const [settingsFor, setSettingsFor] = useState<PanelKey | null>(null);

  // Esc closes the panel, and does so *before* anything else on the page sees
  // the key. The convention elsewhere is a bubble-phase listener that marks the
  // key spoken for (`preventDefault`) so the outer ones stand down — but this
  // listener is registered when the panel opens, which is long after the chart's
  // tools and the page's fullscreen exit registered theirs, so it would run
  // last. Capture puts it first regardless of when it was added; stopping
  // propagation there is what "the panel took this Escape" means.
  useEffect(() => {
    if (!settingsFor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      setSettingsFor(null);
    };
    // A press anywhere outside the open row closes it — including on the chart
    // underneath, where a click is an order tool and closing first is the right
    // reading of the gesture. `pointerdown` rather than `click` so it closes on
    // the press, before that gesture turns into a drag.
    const onDown = (e: PointerEvent) => {
      const el = e.target as HTMLElement | null;
      if (el?.closest?.(`[data-ind-item="${settingsFor}"]`)) return;
      setSettingsFor(null);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pointerdown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onDown, true);
    };
  }, [settingsFor]);

  if (items.length === 0 && !appearance) return null;
  // A row switched off by its own setting doesn't count as shown, whatever its
  // eye says: the count is "how much of this is on the chart".
  const shown = items.filter((it) => visibility[it.key] && !it.dim).length;
  const setOpenPersist = (v: boolean) => {
    setOpen(v);
    saveLegendOpen(v, prefsPane);
    // Collapsing the list takes any open row panel with it — it is anchored to a
    // row that is about to stop existing. The header's own panel stays: its
    // anchor is the one thing collapsing leaves behind.
    if (!v && settingsFor !== APPEARANCE) setSettingsFor(null);
  };
  const appearanceOpen = settingsFor === APPEARANCE;
  return (
    <div className={`chart-legend${settingsFor ? " settings-open" : ""}`}>
      <div className="chart-legend-item" data-ind-item={APPEARANCE}>
        <button
          className={`chart-legend-row chart-legend-head${appearance ? " has-dots" : ""}`}
          onClick={() => setOpenPersist(!open)}
          aria-expanded={open}
          title={open ? "Hide the indicator list" : "Show the indicator list"}
        >
          <LayersIcon />
          <span>Indicators</span>
          <span className="chart-legend-count">
            {shown}/{items.length}
          </span>
          <Chevron open={open} />
        </button>
        {appearance && (
          <button
            className="chart-legend-dots"
            onClick={() => setSettingsFor(appearanceOpen ? null : APPEARANCE)}
            aria-expanded={appearanceOpen}
            title="Chart appearance — background and candle colours"
          >
            <DotsIcon />
          </button>
        )}
        {appearance && appearanceOpen && (
          <IndicatorSettings spec={appearance} onClose={() => setSettingsFor(null)} />
        )}
      </div>
      {open &&
        items.map((it) => {
          const on = visibility[it.key];
          const openHere = settingsFor === it.key;
          return (
            <div key={it.key} className="chart-legend-item" data-ind-item={it.key}>
              <button
                className={`chart-legend-row${on && !it.dim ? "" : " off"}${it.settings ? " has-dots" : ""}`}
                onClick={() => onToggle(it.key)}
                title={on ? `Hide ${it.label}` : `Show ${it.label}`}
              >
                <span className="chart-legend-swatch" style={{ background: it.color }} />
                <span>{it.label}</span>
                <EyeIcon open={on} />
              </button>
              {it.settings && (
                <button
                  className="chart-legend-dots"
                  onClick={() => setSettingsFor(openHere ? null : it.key)}
                  aria-expanded={openHere}
                  title={`Settings for ${it.settings.title}`}
                >
                  <DotsIcon />
                </button>
              )}
              {it.settings && openHere && (
                <IndicatorSettings spec={it.settings} onClose={() => setSettingsFor(null)} />
              )}
            </div>
          );
        })}
    </div>
  );
}
