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

// A month is seven columns wide whatever the screen, so on a phone each cell is
// ~44px and "$1,234.56" does not fit in it. Both spellings are rendered and the
// stylesheet picks one (see .cal-pnl-wide / .cal-pnl-narrow): dropping the cents
// and thousands on a heatmap costs nothing — the cell's tint carries the
// magnitude, the number is only there to say roughly how much and which way.
function fmtCompact(pnl: number): string {
  const abs = Math.abs(pnl);
  const sign = pnl < 0 ? "-" : "";
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(abs >= 10000 ? 0 : 1)}k`;
  return `${sign}$${Math.round(abs)}`;
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
      <div className="cal-grid">
        {WEEKDAYS.map((w) => (
          <div key={w} className="muted cal-weekday">
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
                className={`cal-cell${info ? "" : " empty"}`}
                onClick={info ? () => navigate({ pathname: `/calendar/${date}`, search }) : undefined}
                style={{
                  // Data-driven, so it stays inline: the tint is a function of the
                  // day's PnL and the selection ring of the URL.
                  background: info ? tint(info.net_pnl, maxAbs) : "transparent",
                  borderColor: isSel ? palette.accent : palette.cardBorder,
                }}
              >
                <div className="cal-cell-head">
                  <span className="cal-day">{day}</span>
                  <span className="cal-flags">
                    {info?.has_video && (
                      <span title="A recording is linked to this day">🎥</span>
                    )}
                    {info && info.attempts > 1 && (
                      <span
                        className="muted cal-attempts"
                        title={`${info.attempts} replay attempts — showing the latest`}
                      >
                        ·{info.attempts}
                      </span>
                    )}
                  </span>
                </div>
                {info && (
                  <>
                    <div className="cal-pnl">
                      <span className="cal-pnl-wide">{fmt(info.net_pnl)}</span>
                      <span className="cal-pnl-narrow">{fmtCompact(info.net_pnl)}</span>
                    </div>
                    <div className="muted cal-trades">{info.trades} trd</div>
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
