import { useEffect, useMemo, useRef, useState } from "react";
import {
  catalogue,
  defaultInputs,
  loadCatalogue,
  nextStudyId,
  type StudyEntry,
  type StudySpec,
} from "../../lib/studies";
import { LAYER_NAME, REPLAY_LAYERS, type LayerState, type ReplayLayerKey } from "./chartLayers";

/** How many community hits are drawn. The catalogue is ~415 entries and an empty
 *  search is a browse, not a query — enough rows to scroll through and find out
 *  what is in there, few enough that typing stays instant. */
const MAX_HITS = 120;

/**
 * The indicator catalogue — the ƒ on the Charts topbar.
 *
 * Add and remove, nothing else. What is *on* the chart, what it is set to and
 * what it managed to draw all live on the pane's own legend, where the layers
 * already lived — this is only the way in. That split is the whole design: one
 * place to summon something, one place to read and tune what you summoned, and
 * a community study behaves exactly like one of ours once it is on.
 *
 * Three sections, searched together but never mixed, in descending order of how
 * much we know about what they draw: this app's own layers, each with a measured
 * reason to exist and a study page behind it; then the Pine we transcribed
 * ourselves (see lib/pineStudy); then 415 borrowed ones, 317 of them machine
 * ports of community Pine nobody here has read. Reading a number off the wrong
 * side of those lines is the one real risk this feature carries, so they are
 * drawn rather than implied by an alphabet.
 *
 * The two sections behave differently, because the things do. You cannot have
 * two Globex VWAPs — an app layer is a toggle, on this pane or not. You can have
 * three RSIs at three lengths — a study is an add.
 *
 * Everything here acts on the **focused pane**, the same rule `TimeframeControl`
 * follows: the bar's controls act on the chart you are working in.
 */
export function StudyPicker({
  layers,
  onLayer,
  appLayers = REPLAY_LAYERS,
  specs,
  onSpecs,
  paneLabel,
}: {
  /** The focused pane's layers, as it published them. Empty before the first
   *  pane has reported, which is a few hundred ms at page load. */
  layers: LayerState[];
  onLayer: (key: ReplayLayerKey, on: boolean) => void;
  /** Which of the app's own layers this chart offers, in legend order.
   *
   *  Defaults to the replay chart's whole set. A chart that draws a subset says
   *  so — offering a layer it cannot draw is a switch that does nothing, which is
   *  worse than the layer being absent. Pass `[]` and the section disappears
   *  entirely: on a chart of a session that has already happened, every layer's
   *  data is already there, so the legend is a complete list and the only thing
   *  left for this panel to do is add a community study. */
  appLayers?: readonly ReplayLayerKey[];
  /** The focused pane's community studies. */
  specs: StudySpec[];
  onSpecs: (specs: StudySpec[]) => void;
  /** Which pane this is acting on, when there is more than one. Named on the
   *  panel because "add" is otherwise a gesture with an invisible target. */
  paneLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  /** Re-render when the catalogue lands. The module holds the cache; this is
   *  only how React hears about it. */
  const [cat, setCat] = useState<StudyEntry[] | null>(catalogue);
  const [loadError, setLoadError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // The 1.8 MB of ESM behind the community half arrives on the first open, not
  // with the page. A pane that came up holding saved studies has already asked
  // for it by then — `loadCatalogue` is a single shared import.
  useEffect(() => {
    if (!open || cat) return;
    let alive = true;
    loadCatalogue()
      .then((c) => alive && setCat(c))
      .catch((e) => alive && setLoadError(String((e as Error)?.message ?? e)));
    return () => {
      alive = false;
    };
  }, [open, cat]);

  // Esc and outside-press close, both in the capture phase — the same reasoning
  // as NavMenu's: this listener is registered when the popover opens, long after
  // the chart's tools and the page's key handlers registered theirs, so a
  // bubble-phase listener would run last and the tape would act on the Escape
  // first.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    };
    const onDown = (e: PointerEvent) => {
      const el = e.target as HTMLElement | null;
      if (el?.closest?.("[data-study-picker]")) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pointerdown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onDown, true);
    };
  }, [open]);

  // The search box takes the caret on open: the button is pressed to find
  // something, and between them the two sections are far too long to browse to.
  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  const byKey = useMemo(() => new Map(layers.map((l) => [l.key, l])), [layers]);
  const needle = query.trim().toLowerCase();

  /** Ours, in the legend's own order. Rendered from the static table rather than
   *  from what the pane published, so the list is complete and correctly ordered
   *  even before the first pane has reported. */
  const mine = useMemo(
    () => appLayers.filter((k) => !needle || LAYER_NAME[k].toLowerCase().includes(needle)),
    [appLayers, needle],
  );

  /** The catalogue, searched. One pass over the merged list, split by origin
   *  afterwards — the search is deliberately blind to which half a study came
   *  from, so typing "ema" finds ours and theirs in one gesture. */
  const found = useMemo(() => {
    if (!cat) return [];
    return needle
      ? cat.filter(
          (c) => c.title.toLowerCase().includes(needle) || c.short.toLowerCase().includes(needle),
        )
      : cat;
  }, [cat, needle]);

  /** Ours. Uncapped — there will never be enough of these to need a cap, and a
   *  "first 120 of" line under a list of four would read as a bug. */
  const pine = useMemo(() => found.filter((c) => c.origin === "mine"), [found]);
  const hits = useMemo(
    () => found.filter((c) => c.origin === "community").slice(0, MAX_HITS),
    [found],
  );

  const add = (entry: StudyEntry) => {
    onSpecs([...specs, { id: nextStudyId(specs), key: entry.key, inputs: defaultInputs(entry) }]);
    setQuery("");
  };

  return (
    <div className="study-pick" data-study-picker>
      <button
        type="button"
        className="chart-topbar-btn"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title="Indicators — this chart's layers and the community catalogue. What's on is listed on the chart itself."
      >
        ƒ
      </button>

      {open && (
        <div className="study-pop" role="dialog" aria-label="Indicators">
          <input
            ref={searchRef}
            className="study-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={cat ? `Search ${appLayers.length + cat.length} indicators…` : "Search…"}
            autoComplete="off"
            spellCheck={false}
          />

          <div className="study-results">
            {/* Skipped whole on a chart that offers no app layers here — see
                `appLayers`. A head with nothing under it reads as a load that
                failed. */}
            {appLayers.length > 0 && (
              <div className="study-head">
                This chart’s layers
                {paneLabel && <span className="study-head-pane">pane {paneLabel}</span>}
              </div>
            )}
            {mine.map((key) => {
              const st = byKey.get(key);
              const on = !!st?.on;
              // On, but the session hasn't reached what it draws — the weekly
              // VWAP with no seed, CVD before the tape tags an aggressor, the IB
              // before the hour is up. There is no legend row for it yet, and
              // "I switched it on and nothing happened" must not read as a bug.
              const waiting = on && st != null && !st.available;
              return (
                <button
                  key={key}
                  type="button"
                  className={`study-hit mine${on ? " on" : ""}`}
                  onClick={() => onLayer(key, !on)}
                  title={
                    on
                      ? `Stop drawing ${LAYER_NAME[key]} on this pane`
                      : `Draw ${LAYER_NAME[key]} on this pane`
                  }
                >
                  <span className="study-tick" aria-hidden>
                    {on ? "✓" : ""}
                  </span>
                  <span className="study-hit-title">{LAYER_NAME[key]}</span>
                  {waiting && <span className="study-hit-where">nothing to draw yet</span>}
                </button>
              );
            })}
            {appLayers.length > 0 && !mine.length && (
              <div className="study-empty">None of yours match.</div>
            )}

            {/* Ours, when there are any that match. No "nothing matches" line
                under this one: an empty section here means the search was aimed
                at the community half, which is the next thing on screen. */}
            {pine.length > 0 && (
              <div className="study-head">
                Pine, transcribed here
                <span
                  className="study-head-note"
                  title="Written against the same PineScript runtime the borrowed ones ride — see src/studies. Ours to get wrong, and not wired to the sim either."
                >
                  ours · unvalidated
                </span>
              </div>
            )}
            {pine.map((c) => (
              <button key={c.key} type="button" className="study-hit" onClick={() => add(c)}>
                <span className="study-hit-short">{c.short}</span>
                <span className="study-hit-title">{c.title}</span>
                <span className="study-hit-where">{c.overlay ? "overlay" : "pane"}</span>
              </button>
            ))}

            <div className="study-head">
              Community catalogue
              <span className="study-head-note" title="See docs/research/lwc-addons.html">
                borrowed · unvalidated
              </span>
            </div>
            {loadError && <div className="study-empty">Couldn’t load it: {loadError}</div>}
            {!cat && !loadError && <div className="study-empty">Loading…</div>}
            {cat && !hits.length && <div className="study-empty">Nothing matches.</div>}
            {hits.map((c) => (
              <button key={c.key} type="button" className="study-hit" onClick={() => add(c)}>
                <span className="study-hit-short">{c.short}</span>
                <span className="study-hit-title">{c.title}</span>
                <span className="study-hit-where">{c.overlay ? "overlay" : "pane"}</span>
              </button>
            ))}
            {cat && hits.length === MAX_HITS && (
              <div className="study-empty">
                First {MAX_HITS} of {cat.length - pine.length} — keep typing to narrow it.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
