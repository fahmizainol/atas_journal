import { useMemo } from "react";
import type { SimTrade } from "../../lib/strategyTypes";

export interface TradeFilter {
  entryFrom: string; // "HH:MM" or ""
  entryTo: string;
  exitFrom: string;
  exitTo: string;
  reasons: string[]; // empty = all
  rMin: string; // raw input text; "" = unbounded
  rMax: string;
  tags: string[]; // empty = all; OR-matched — a trade passes if it has any of them
}

export const EMPTY_TRADE_FILTER: TradeFilter = {
  entryFrom: "",
  entryTo: "",
  exitFrom: "",
  exitTo: "",
  reasons: [],
  rMin: "",
  rMax: "",
  tags: [],
};

const isActive = (f: TradeFilter) =>
  f.entryFrom !== "" ||
  f.entryTo !== "" ||
  f.exitFrom !== "" ||
  f.exitTo !== "" ||
  f.reasons.length > 0 ||
  f.rMin !== "" ||
  f.rMax !== "" ||
  f.tags.length > 0;

// "HH:MM" from a local ISO timestamp; string compare is enough because both
// sides are zero-padded 24h clock.
const hhmm = (iso: string) => iso.slice(11, 16);

const inWindow = (t: string, from: string, to: string) =>
  (from === "" || t >= from) && (to === "" || t <= to);

// Tags live in a sidecar map (trade_no -> [tag]), not on the trade row, so the
// map is passed alongside. A trade with no entry in the map has no tags.
export function filterTrades(
  trades: SimTrade[],
  f: TradeFilter,
  tagsByNo: Record<string, string[]> = {},
): SimTrade[] {
  if (!isActive(f)) return trades;
  const rMin = f.rMin === "" ? null : Number(f.rMin);
  const rMax = f.rMax === "" ? null : Number(f.rMax);
  return trades.filter((t) => {
    if (!inWindow(hhmm(t.entry_ts_local), f.entryFrom, f.entryTo)) return false;
    if (!inWindow(hhmm(t.exit_ts_local), f.exitFrom, f.exitTo)) return false;
    if (f.reasons.length > 0 && !f.reasons.includes(t.exit_reason)) return false;
    if (rMin != null && !Number.isNaN(rMin) && t.r_multiple < rMin) return false;
    if (rMax != null && !Number.isNaN(rMax) && t.r_multiple > rMax) return false;
    if (f.tags.length > 0) {
      const tt = tagsByNo[String(t.trade_no)] ?? [];
      if (!f.tags.some((x) => tt.includes(x))) return false;
    }
    return true;
  });
}

interface Props {
  trades: SimTrade[]; // unfiltered — used to enumerate exit reasons + total count
  shown: number; // filtered count, for the "n of N" readout
  filter: TradeFilter;
  onChange: (f: TradeFilter) => void;
  tagOptions: string[]; // distinct tags present in this run — the chip row
}

// Client-side filter bar for a run's trade list. All state lives in the parent
// so it can filter the array before DataTable sees it; reasons are chips built
// from the reasons this run actually produced, not the full enum.
export function TradeFilterBar({ trades, shown, filter, onChange, tagOptions }: Props) {
  const reasons = useMemo(
    () => [...new Set(trades.map((t) => t.exit_reason))].sort(),
    [trades],
  );

  const set = (patch: Partial<TradeFilter>) => onChange({ ...filter, ...patch });
  const toggleReason = (r: string) =>
    set({
      reasons: filter.reasons.includes(r)
        ? filter.reasons.filter((x) => x !== r)
        : [...filter.reasons, r],
    });
  const toggleTag = (t: string) =>
    set({
      tags: filter.tags.includes(t)
        ? filter.tags.filter((x) => x !== t)
        : [...filter.tags, t],
    });

  const timePair = (
    label: string,
    fromKey: "entryFrom" | "exitFrom",
    toKey: "entryTo" | "exitTo",
  ) => (
    <div className="field">
      <label>{label}</label>
      <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
        <input
          type="time"
          value={filter[fromKey]}
          onChange={(e) => set({ [fromKey]: e.target.value })}
        />
        <span className="muted">–</span>
        <input
          type="time"
          value={filter[toKey]}
          onChange={(e) => set({ [toKey]: e.target.value })}
        />
      </div>
    </div>
  );

  return (
    <div className="filter-bar" style={{ minHeight: 0, marginTop: 8, marginBottom: 8 }}>
      {timePair("Entry", "entryFrom", "entryTo")}
      {timePair("Exit", "exitFrom", "exitTo")}
      <div className="field">
        <label>Why out</label>
        <div className="chip-row">
          {reasons.map((r) => (
            <button
              key={r}
              type="button"
              className={filter.reasons.includes(r) ? "active" : undefined}
              onClick={() => toggleReason(r)}
            >
              {r}
            </button>
          ))}
        </div>
      </div>
      {tagOptions.length > 0 && (
        <div className="field">
          <label>Tags</label>
          <div className="chip-row">
            {tagOptions.map((t) => (
              <button
                key={t}
                type="button"
                className={filter.tags.includes(t) ? "active" : undefined}
                onClick={() => toggleTag(t)}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="field">
        <label>R</label>
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <input
            type="number"
            step="0.25"
            placeholder="min"
            value={filter.rMin}
            onChange={(e) => set({ rMin: e.target.value })}
            style={{ width: 70 }}
          />
          <span className="muted">–</span>
          <input
            type="number"
            step="0.25"
            placeholder="max"
            value={filter.rMax}
            onChange={(e) => set({ rMax: e.target.value })}
            style={{ width: 70 }}
          />
        </div>
      </div>
      <div className="field" style={{ marginLeft: "auto", alignItems: "flex-end" }}>
        <span className="muted" style={{ fontSize: 12 }}>
          {shown} of {trades.length} trades
        </span>
        {isActive(filter) && (
          <button
            type="button"
            onClick={() => onChange(EMPTY_TRADE_FILTER)}
            style={{ padding: "4px 10px" }}
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}
