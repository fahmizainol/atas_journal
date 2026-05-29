import { useState } from "react";
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

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!data || data.months.length === 0)
    return <div className="notice">No trades to display.</div>;

  // If a day is selected, prefer the month containing it.
  let activeIdx = monthIdx;
  if (date) {
    const dt = new Date(date + "T00:00:00");
    const found = data.months.findIndex(
      (m) => m.year === dt.getFullYear() && m.month === dt.getMonth() + 1,
    );
    if (found >= 0) activeIdx = found;
  }
  const month = data.months[Math.min(activeIdx, data.months.length - 1)];

  return (
    <div>
      <div className="section-title">Monthly PnL calendar</div>
      <div className="field" style={{ maxWidth: 280, marginBottom: 12 }}>
        <label>Month</label>
        <select value={activeIdx} onChange={(e) => setMonthIdx(Number(e.target.value))}>
          {data.months.map((m, i) => (
            <option key={`${m.year}-${m.month}`} value={i}>
              {m.label}
            </option>
          ))}
        </select>
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
