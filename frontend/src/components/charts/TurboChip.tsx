import { TURBO_MULT } from "../../hooks/useTurbo";
import { palette } from "../../theme";

/**
 * What the tape is actually running at while Ctrl is held.
 *
 * The transport's <select> can't say it — it shows the ladder value, which is
 * the thing you set and not the thing that is happening — and a chart that
 * silently runs ten times faster than the number next to it is the kind of
 * disagreement you only notice after you've traded off it. Occupies no width
 * when idle so nothing in the bar moves when it appears.
 */
export function TurboChip({ on, speed }: { on: boolean; speed: number }) {
  if (!on) return null;
  return (
    <span
      title="Ctrl held — release to go back to the set speed"
      style={{
        fontFamily: "monospace",
        fontSize: 11,
        padding: "1px 5px",
        borderRadius: 3,
        background: palette.card,
        color: palette.orange,
        whiteSpace: "nowrap",
      }}
    >
      ⏩ {speed * TURBO_MULT}×
    </span>
  );
}
