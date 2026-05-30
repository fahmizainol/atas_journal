import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useFilters } from "../hooks/useFilters";
import { useCalendar } from "../hooks/useCalendar";
import { CalendarHeatmap } from "../components/charts/CalendarHeatmap";
import { DayExplorer } from "../components/DayExplorer";

export function Calendar() {
  const { scope } = useFilters();
  const { data, isLoading } = useCalendar(scope);
  const { date } = useParams();
  const [monthIdx, setMonthIdx] = useState(0);

  // When a day is opened (via link or by clicking a cell), jump to its month.
  // After that, the dropdown/arrows drive the view freely.
  useEffect(() => {
    if (!data || !date) return;
    const dt = new Date(date + "T00:00:00");
    const found = data.months.findIndex(
      (m) => m.year === dt.getFullYear() && m.month === dt.getMonth() + 1,
    );
    if (found >= 0) setMonthIdx(found);
  }, [date, data]);

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!data || data.months.length === 0)
    return <div className="notice">No trades to display.</div>;

  const activeIdx = Math.min(monthIdx, data.months.length - 1);
  const month = data.months[activeIdx];

  return (
    <div>
      <div className="section-title">Monthly PnL calendar</div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 8, maxWidth: 360, marginBottom: 12 }}>
        <button
          type="button"
          onClick={() => setMonthIdx(activeIdx + 1)}
          disabled={activeIdx >= data.months.length - 1}
          aria-label="Previous month"
        >
          ‹
        </button>
        <div className="field" style={{ flex: 1 }}>
          <label>Month</label>
          <select value={activeIdx} onChange={(e) => setMonthIdx(Number(e.target.value))}>
            {data.months.map((m, i) => (
              <option key={`${m.year}-${m.month}`} value={i}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => setMonthIdx(activeIdx - 1)}
          disabled={activeIdx <= 0}
          aria-label="Next month"
        >
          ›
        </button>
      </div>

      <CalendarHeatmap
        year={month.year}
        month={month.month}
        days={data.days}
        selected={date ?? null}
      />

      {date && <DayExplorer scope={scope} date={date} />}
      {!date && <div className="notice">Click a day in the calendar to explore its trades.</div>}
    </div>
  );
}
