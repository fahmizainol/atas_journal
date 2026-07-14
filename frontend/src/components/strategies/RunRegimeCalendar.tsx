import { useRegimeRange } from "../../hooks/useRegime";
import { fmt } from "../../lib/format";
import { CLASS_LABEL, type RegimeDaySummary } from "../../lib/regimeTypes";
import { palette, regimePalette } from "../../theme";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// Monday-first month matrix. Same shape as the journal's CalendarHeatmap, kept
// separate on purpose: that grid tints by P&L, this one by regime, and one cell
// cannot honestly carry both scales. Here P&L is the badge, not the fill.
function buildWeeks(year: number, month: number): number[][] {
  const first = new Date(year, month - 1, 1);
  const offset = (first.getDay() + 6) % 7; // JS getDay is Sunday-first
  const cells: number[] = Array(offset).fill(0);
  for (let d = 1; d <= new Date(year, month, 0).getDate(); d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(0);
  const weeks: number[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

function monthsBetween(start: string, end: string): { year: number; month: number }[] {
  const out: { year: number; month: number }[] = [];
  const [sy, sm] = start.split("-").map(Number);
  const [ey, em] = end.split("-").map(Number);
  for (let y = sy, m = sm; y < ey || (y === ey && m <= em); m === 12 ? (m = 1, y++) : m++)
    out.push({ year: y, month: m });
  return out;
}

const iso = (y: number, m: number, d: number) =>
  `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;

// The month's net over the days the run actually traded. Days outside the run's
// window can't contribute — the grid draws them, but they aren't the run's.
function monthTotal(
  dayStats: Map<string, { net: number; wins: number; n: number }>,
  year: number,
  month: number,
  start: string,
  end: string,
) {
  const prefix = `${year}-${String(month).padStart(2, "0")}`;
  let net = 0;
  let trades = 0;
  for (const [date, s] of dayStats) {
    if (!date.startsWith(prefix) || date < start || date > end) continue;
    net += s.net;
    trades += s.n;
  }
  return { net, trades };
}

// The run's window as a regime calendar: every session coloured by what kind of
// day it was, with the run's own net for that day as a badge. This is the view
// that answers "does this model only work in one regime?" at a glance — and the
// one that shows the days it was flat *in* that regime, which a P&L calendar
// can't, because it has nothing to say about a day with no trades.
export function RunRegimeCalendar({
  symbol,
  start,
  end,
  dayStats,
  activeDay,
  onPick,
}: {
  symbol: string;
  start: string;
  end: string;
  dayStats: Map<string, { net: number; wins: number; n: number }>;
  activeDay: string | null;
  onPick: (day: string) => void;
}) {
  const { data, isLoading } = useRegimeRange(symbol, start, end);

  if (isLoading) return <div className="notice">Loading regime…</div>;
  if (!data) return null;

  const byDate = new Map<string, RegimeDaySummary>(data.days.map((d) => [d.date, d]));
  const skipped = new Set(data.skipped);
  const seen = [...new Set(data.days.map((d) => d.class))];

  return (
    <div style={{ marginTop: 12 }}>
      <div className="panel" style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
          {seen.map((c) => (
            <span key={c} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <span
                style={{
                  width: 12,
                  height: 12,
                  borderRadius: 3,
                  background: regimePalette.klass[c],
                  border: `1px solid ${palette.cardBorder}`,
                }}
              />
              {CLASS_LABEL[c]}
            </span>
          ))}
        </div>
        <div className="section-cap" style={{ marginTop: 6 }}>
          Cell colour is the day's regime (from the two anchored VWAPs at the close); the number is
          what this run made that day. Classes are <strong>provisional</strong> — read them as
          "this day looked like that day", not as a verdict. Hatched days had no overnight ticks, so
          only the NY anchor was measurable; empty cells have no cached ticks at all. Click a day to
          open its session.
        </div>
      </div>

      {monthsBetween(start, end).map(({ year, month }) => {
        const mt = monthTotal(dayStats, year, month, start, end);
        return (
          <div className="panel" key={`${year}-${month}`} style={{ marginBottom: 12 }}>
            <div
              className="section-cap"
              style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}
            >
              <span>
                {new Date(year, month - 1, 1).toLocaleString("en-US", {
                  month: "long",
                  year: "numeric",
                })}
              </span>
              {mt.trades > 0 && (
                <span>
                  net{" "}
                  <span className={mt.net >= 0 ? "pos" : "neg"} style={{ fontWeight: 700 }}>
                    {fmt(mt.net)}
                  </span>{" "}
                  · {mt.trades} trd
                </span>
              )}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 4 }}>
              {WEEKDAYS.map((w) => (
                <div
                  key={w}
                  className="muted"
                  style={{ textAlign: "center", fontSize: 11, padding: 4 }}
                >
                  {w}
                </div>
              ))}
              {buildWeeks(year, month).flatMap((week, wi) =>
                week.map((day, di) => {
                  if (day === 0) return <div key={`${wi}-${di}`} />;
                  const date = iso(year, month, day);
                  const reg = byDate.get(date);
                  const s = dayStats.get(date);
                  const inWindow = date >= start && date <= end;
                  const fill = reg ? regimePalette.klass[reg.class] : "transparent";
                  return (
                    <div
                      key={`${wi}-${di}`}
                      onClick={reg ? () => onPick(date) : undefined}
                      title={
                        reg
                          ? `${date} — ${CLASS_LABEL[reg.class]}${reg.partial ? " (no overnight)" : ""}`
                          : inWindow && skipped.has(date)
                            ? `${date} — no cached ticks`
                            : undefined
                      }
                      style={{
                        minHeight: 62,
                        borderRadius: 8,
                        padding: "6px 8px",
                        background: fill,
                        // Hatch marks the days whose regime is only half-measured —
                        // the eye must not read them as a confident call.
                        backgroundImage: reg?.partial
                          ? "repeating-linear-gradient(45deg, rgba(255,255,255,0.10) 0 4px, transparent 4px 8px)"
                          : undefined,
                        border: `1px solid ${date === activeDay ? palette.accent : palette.cardBorder}`,
                        opacity: inWindow ? 1 : 0.35,
                        cursor: reg ? "pointer" : "default",
                      }}
                    >
                      <div style={{ fontWeight: 700, fontSize: 12 }}>{day}</div>
                      {reg && (
                        <div
                          className={s ? (s.net >= 0 ? "pos" : "neg") : "muted"}
                          style={{ fontSize: 12, fontWeight: 600 }}
                        >
                          {s ? fmt(s.net) : "—"}
                        </div>
                      )}
                      {reg && s && (
                        <div className="muted" style={{ fontSize: 11 }}>
                          {s.n} trd
                        </div>
                      )}
                    </div>
                  );
                }),
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
