import { useFilters } from "../hooks/useFilters";
import { useFiltersData } from "../hooks/useMeta";

// Top filter bar — mirrors the bordered container in app.py: view radio,
// instrument multiselect, date range, tag multiselect. All state is in the URL.
export function FilterBar() {
  const { scope, setView, setInstruments, setDates, setTags } = useFilters();
  const { data } = useFiltersData(scope);

  const instruments = data?.instruments ?? [];
  const tags = data?.tags ?? [];

  const multi = (e: React.ChangeEvent<HTMLSelectElement>): string[] =>
    Array.from(e.target.selectedOptions, (o) => o.value);

  return (
    <div className="filter-bar">
      <div className="field">
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

      <div className="field">
        <label>Instrument</label>
        <select
          multiple
          value={scope.instruments.length ? scope.instruments : instruments}
          onChange={(e) => {
            const sel = multi(e);
            setInstruments(sel.length === instruments.length ? [] : sel);
          }}
          size={Math.min(3, Math.max(1, instruments.length))}
        >
          {instruments.map((i) => (
            <option key={i} value={i}>
              {i}
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
        <label>Tags</label>
        <select
          multiple
          value={scope.tags}
          onChange={(e) => setTags(multi(e))}
          size={Math.min(3, Math.max(1, tags.length || 1))}
        >
          {tags.length === 0 && <option disabled>(none)</option>}
          {tags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
