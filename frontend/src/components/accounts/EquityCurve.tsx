// One account's equity across every rep it has taken, life by life.
//
// Moved off the history page, where it was a running net over whatever tab you
// happened to be on — a curve with no zero that mattered. Here it has one: an
// account starts at a number and dies at a number, and the interesting thing a
// curve can show is where a life ended and the next one opened again from the
// top. So the series breaks at each life rather than running through, and the
// start line is drawn because it is the level every life begins on.
//
// Still a hand-rolled polyline rather than a charting library, for the reason it
// always was: a few dozen points with no axes to speak of, and the one library
// this app loads draws price on a time scale — which this is not. The x axis is
// rep order.

import { palette } from "../../theme";

export interface CurveRep {
  net_usd: number;
}

export interface CurveLife {
  reps: number;
  outcome: "blown" | "passed" | "live";
}

const W = 640;
const H = 110;

/** Each life's equity series, starting at `start` and adding rep nets in order.
 *
 *  The lives carry rep *counts* rather than ids, so the split is positional —
 *  which is exactly how the server walked them (`replay_account.lives`), in the
 *  same order over the same rows. */
export function segments(reps: CurveRep[], lives: CurveLife[], start: number): number[][] {
  const out: number[][] = [];
  let i = 0;
  for (const life of lives) {
    const series = [start];
    let eq = start;
    for (let n = 0; n < life.reps && i < reps.length; n++, i++) {
      eq += reps[i].net_usd;
      series.push(eq);
    }
    out.push(series);
  }
  return out;
}

export function EquityCurve({
  reps,
  lives,
  start,
  floor,
}: {
  reps: CurveRep[];
  lives: CurveLife[];
  start: number;
  /** The floor the current life stands on. Drawn as the level a life is lost at,
   *  though it moves — see `replay_account.walk`. Labelled as the *current* one
   *  so it is not read as a constant the whole history was judged against. */
  floor: number;
}) {
  const segs = segments(reps, lives, start);
  const points = segs.flat();
  if (points.length < 2) return null;

  const lo = Math.min(floor, ...points);
  const hi = Math.max(start, ...points);
  const span = hi - lo || 1;
  const total = points.length - 1;
  const y = (v: number) => H - ((v - lo) / span) * H;

  let cursor = 0;
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ width: "100%", height: H }}
      data-equity-curve
    >
      <line x1={0} x2={W} y1={y(start)} y2={y(start)} stroke={palette.grid} strokeWidth={1} />
      <line
        x1={0}
        x2={W}
        y1={y(floor)}
        y2={y(floor)}
        stroke={palette.red}
        strokeWidth={1}
        strokeDasharray="4 4"
        opacity={0.5}
      />
      {segs.map((series, li) => {
        const at = cursor;
        cursor += series.length - 1;
        const pts = series
          .map((v, n) => `${(((at + n) / total) * W).toFixed(1)},${y(v).toFixed(1)}`)
          .join(" ");
        const life = lives[li];
        return (
          <polyline
            key={li}
            points={pts}
            fill="none"
            stroke={
              life.outcome === "passed"
                ? palette.green
                : life.outcome === "blown"
                  ? palette.red
                  : palette.muted
            }
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          />
        );
      })}
    </svg>
  );
}
