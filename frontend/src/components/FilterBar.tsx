import { useState } from "react";
import { useFilters } from "../hooks/useFilters";
import { useFiltersData } from "../hooks/useMeta";

const MODE_LABELS: Record<string, string> = {
  live: "Live",
  replay: "Replay",
  backtest: "Backtest",
  all: "All",
};

// Top filter bar — mirrors the bordered container in app.py: view radio, session
// mode, instrument multiselect, date range, model + tag multiselects, archive
// toggle. All state is in the URL.
//
// Nine fields is a comfortable two rows on a desktop and the entire first screen
// of a phone, so on a narrow viewport the bar collapses to a single summary line
// (see the Journal mobile block in index.css). The toggle is rendered
// unconditionally and hidden off-mobile, and the `collapsed` class only means
// anything inside that media query — so the desktop bar is untouched by this and
// there is no width-watching state to keep in sync with the stylesheet.
export function FilterBar() {
  const [open, setOpen] = useState(false);
  const {
    scope, setView, setInstruments, setAccounts, setDates, setTags,
    setMode, setModels, setIncludeArchived,
  } = useFilters();
  const { data } = useFiltersData(scope);

  const instruments = data?.instruments ?? [];
  const accounts = data?.accounts ?? [];
  const tags = data?.tags ?? [];
  const models = data?.models ?? [];
  const modes = [...(data?.modes ?? ["live", "replay", "backtest"]), "all"];

  const multi = (e: React.ChangeEvent<HTMLSelectElement>): string[] =>
    Array.from(e.target.selectedOptions, (o) => o.value);

  // What the collapsed bar says it is doing. Only the scope-narrowing choices
  // earn a word: an empty multiselect means "everything", which is the default
  // and not worth a chip. The two that are always set (view + session mode) lead,
  // because they are the two that change what the numbers below actually mean.
  const summary = [
    MODE_LABELS[scope.mode] ?? scope.mode,
    scope.view === "logical" ? "Logical" : "ATAS rows",
    scope.instruments.length ? `${scope.instruments.length} instr` : null,
    scope.accounts.length ? `${scope.accounts.length} acct` : null,
    scope.models.length ? `${scope.models.length} model` : null,
    scope.tags.length ? `${scope.tags.length} tag` : null,
    scope.includeArchived ? "archived" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className={`filter-bar${open ? "" : " collapsed"}`}>
      <button
        type="button"
        className="filter-bar-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span>{open ? "▾" : "▸"} Filters</span>
        <span className="filter-bar-summary">{summary}</span>
      </button>
      {/* field-wide: a button group, not a control — half a phone's width wraps
          these into two ragged rows that read as two separate pickers. */}
      <div className="field field-wide">
        <label>Trade view</label>
        <div className="radio-group">
          <button
            className={scope.view === "logical" ? "active" : ""}
            onClick={() => setView("logical")}
          >
            Logical
          </button>
          <button
            className={scope.view === "atas" ? "active" : ""}
            onClick={() => setView("atas")}
          >
            ATAS rows
          </button>
        </div>
      </div>

      {/* Single-select on purpose: live is real money, replay and backtest are
          practice, and one number over all three means nothing. */}
      <div className="field field-wide">
        <label>Session</label>
        <div className="radio-group">
          {modes.map((m) => (
            <button
              key={m}
              className={scope.mode === m ? "active" : ""}
              onClick={() => setMode(m)}
              title={
                m === "all"
                  ? "Blend every session mode — rarely what you want."
                  : `Only ${m} sessions`
              }
            >
              {MODE_LABELS[m] ?? m}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label>Instrument</label>
        <select
          multiple
          value={scope.instruments.length ? scope.instruments : instruments}
          onChange={(e) => {
            const sel = multi(e);
            setInstruments(sel.length === instruments.length ? [] : sel);
          }}
          size={1}
        >
          {instruments.map((i) => (
            <option key={i} value={i}>
              {i}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Account</label>
        <select
          multiple
          value={scope.accounts.length ? scope.accounts : accounts}
          onChange={(e) => {
            const sel = multi(e);
            setAccounts(sel.length === accounts.length ? [] : sel);
          }}
          size={1}
        >
          {accounts.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>From</label>
        <input
          type="date"
          min={data?.date_min ?? undefined}
          max={data?.date_max ?? undefined}
          value={scope.start ?? data?.date_min ?? ""}
          onChange={(e) => setDates(e.target.value || null, scope.end ?? data?.date_max ?? null)}
        />
      </div>
      <div className="field">
        <label>To</label>
        <input
          type="date"
          min={data?.date_min ?? undefined}
          max={data?.date_max ?? undefined}
          value={scope.end ?? data?.date_max ?? ""}
          onChange={(e) => setDates(scope.start ?? data?.date_min ?? null, e.target.value || null)}
        />
      </div>

      <div className="field">
        <label>Model</label>
        <select
          multiple
          value={scope.models}
          onChange={(e) => setModels(multi(e))}
          size={1}
        >
          {models.length === 0 && <option disabled>(none)</option>}
          {models.map((m) => (
            <option key={m.id} value={String(m.id)}>
              {m.name}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Tags</label>
        <select
          multiple
          value={scope.tags}
          onChange={(e) => setTags(multi(e))}
          size={1}
        >
          {tags.length === 0 && <option disabled>(none)</option>}
          {tags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Archive</label>
        <button
          type="button"
          className={scope.includeArchived ? "active" : ""}
          onClick={() => setIncludeArchived(!scope.includeArchived)}
          title="Include pre-cutover sessions. They're archived, not deleted — out of the default statistics, still browsable."
        >
          {scope.includeArchived ? "Included" : "Excluded"}
        </button>
      </div>
    </div>
  );
}
