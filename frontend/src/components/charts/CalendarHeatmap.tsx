import { useNavigate, useLocation } from "react-router-dom";
import { palette } from "../../theme";
import { fmt } from "../../lib/format";
import type { CalendarDay } from "../../hooks/useCalendar";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// Month grid (weeks x weekdays), Monday-first; each cell tints by day net PnL.
// Ports charts.calendar_fig to a plain CSS grid (cheaper + clickable).
function buildWeeks(year: number, month: number): (number | 0)[][] {
  const first = new Date(year, month - 1, 1);
  // JS getDay: 0=Sun..6=Sat -> Monday-first offset.
  const offset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month, 0).getDate();
  const cells: (number | 0)[] = Array(offset).fill(0);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(0);
  const weeks: (number | 0)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

function tint(pnl: number, maxAbs: number): string {
  if (maxAbs === 0) return palette.card;
  const ratio = Math.min(Math.abs(pnl) / maxAbs, 1);
  const base = pnl >= 0 ? "33,192,122" : "245,69,95";
  return `rgba(${base}, ${0.12 + ratio * 0.55})`;
}

export function CalendarHeatmap({
  year,
  month,
  days,
  selected,
}: {
  year: number;
  month: number;
  days: CalendarDay[];
  selected: string | null;
}) {
  const navigate = useNavigate();
  const { search } = useLocation();
  const byDay = new Map<number, CalendarDay>();
  for (const d of days) {
    const dt = new Date(d.date + "T00:00:00");
    if (dt.getFullYear() === year && dt.getMonth() + 1 === month) byDay.set(dt.getDate(), d);
  }
  const maxAbs = Math.max(1, ...[...byDay.values()].map((d) => Math.abs(d.net_pnl)));
  const weeks = buildWeeks(year, month);
  const monthTotal = [...byDay.values()].reduce((s, d) => s + d.net_pnl, 0);
  const iso = (day: number) =>
    `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;

  return (
    <div className="panel">
      <div className="section-cap">
        {new Date(year, month - 1, 1).toLocaleString("en-US", { month: "long", year: "numeric" })}{" "}
        — net {fmt(monthTotal)}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 4 }}>
        {WEEKDAYS.map((w) => (
          <div key={w} className="muted" style={{ textAlign: "center", fontSize: 11, padding: 4 }}>
            {w}
          </div>
        ))}
        {weeks.flatMap((week, wi) =>
          week.map((day, di) => {
            if (day === 0) return <div key={`${wi}-${di}`} />;
            const info = byDay.get(day);
            const date = iso(day);
            const isSel = selected === date;
            return (
              <div
                key={`${wi}-${di}`}
                onClick={info ? () => navigate({ pathname: `/calendar/${date}`, search }) : undefined}
                style={{
                  minHeight: 64,
                  borderRadius: 8,
                  padding: "6px 8px",
                  background: info ? tint(info.net_pnl, maxAbs) : "transparent",
                  border: `1px solid ${isSel ? palette.accent : palette.cardBorder}`,
                  cursor: info ? "pointer" : "default",
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 12 }}>{day}</div>
                {info && (
                  <>
                    <div style={{ fontSize: 12 }}>{fmt(info.net_pnl)}</div>
                    <div className="muted" style={{ fontSize: 11 }}>
                      {info.trades} trd
                    </div>
                  </>
                )}
              </div>
            );
          }),
        )}
      </div>
    </div>
  );
}
