import { ChartToolButton, ChartToolSep } from "./ChartToolButton";
import { CHART_TOOLS, type ChartToolId, type ChartToolState } from "../../lib/chartTools";

/**
 * The terminal's tool rail — one column, outside every canvas.
 *
 * WHY IT MOVED OUT. There used to be one of these floating *inside* each chart
 * (`.chart-tools`, and it is still there for pages that draw a single chart).
 * With four panes that is four rails, each covering the top-left of its own
 * candles and each arming only itself — but a tool is a mode of the terminal,
 * not a property of one chart. So there is one rail, and it acts on the pane the
 * pointer last touched.
 *
 * THE PANE IT ACTS ON IS NAMED ON IT. A rail with no addressee is worse than no
 * rail: arming ⚓ and then wondering which of four charts is waiting for a click
 * is exactly the confusion the focus ring exists to prevent, and the rail is the
 * control that most needs to say what it is pointing at.
 *
 * It is a remote control, not an owner: the arming, the pointer handling, the
 * drawings and the mutual exclusion between tools all stay in ReplayChart, and
 * this reaches them through the imperative handle (see lib/chartTools).
 */
export function ChartToolRail({
  state,
  paneLabel,
  pinned,
  onPinnedChange,
  onArm,
  onClearAvwap,
  onDeleteSelected,
  onClearDrawings,
}: {
  /** The focused pane's tools, as that pane reports them. */
  state: ChartToolState;
  /** Which pane this is currently aimed at ("2"), or absent with one pane on
   *  screen — where there is nothing to disambiguate and the label would be a
   *  permanent answer to a question nobody asked. */
  paneLabel?: string;
  /** Whether the rail reserves a column or floats over the tape. Pinned is the
   *  default and the honest one for four panes — 38px off 1920 to stop covering
   *  four charts' worth of candles. Floating is the old in-canvas behaviour back
   *  as a choice: on one pane the column is a straight loss, and some people
   *  would rather see the tape under the buttons. */
  pinned: boolean;
  onPinnedChange: (v: boolean) => void;
  onArm: (id: ChartToolId | null) => void;
  onClearAvwap: () => void;
  onDeleteSelected: () => void;
  onClearDrawings: () => void;
}) {
  const at = paneLabel ? ` — pane ${paneLabel}` : "";
  const hasSel = state.hasRangeSel || state.hasHlineSel;
  return (
    <div className={`chart-rail${pinned ? "" : " floating"}`} role="toolbar" aria-label="Chart tools">
      {/* Disarm. The keyboard has had Esc for this all along, but Esc is not
          discoverable and a rail with no way back to "just pointing" reads as a
          rail you can get stuck in. Lit when nothing is armed, so the rail
          always has exactly one lit button. */}
      <ChartToolButton
        icon="⌖"
        label="Cursor"
        on={state.armed == null}
        onClick={() => onArm(null)}
        title={`Cursor — nothing armed, clicks read the chart (Esc)${at}`}
      />
      <ChartToolSep />
      {CHART_TOOLS.map((t) => {
        const on = state.armed === t.id;
        // ＋Order needs a session behind it; the rest only need a chart.
        const dead = t.id === "order" && !state.canOrder;
        return (
          <ChartToolButton
            key={t.id}
            icon={t.icon}
            label={on ? t.armedLabel : t.label}
            on={on}
            disabled={dead}
            onClick={() => onArm(on ? null : t.id)}
            title={dead ? "Load a session first" : `${on ? t.armedTip : t.tip}${at}`}
          />
        );
      })}
      {/* Below the hairline: the tools that take things away. They come and go
          with what is on the focused pane, so they live at the foot of the rail
          where appearing doesn't move anything above them. */}
      {(state.hasAvwap || hasSel || state.drawings > 1) && <ChartToolSep />}
      {state.hasAvwap && (
        <ChartToolButton
          icon={<span className="chart-tool-pair">⚓✕</span>}
          label="Clear VWAP"
          onClick={onClearAvwap}
          title={`Remove the anchored VWAP${at}`}
        />
      )}
      {hasSel && (
        <ChartToolButton
          icon="🗑"
          label="Delete"
          onClick={onDeleteSelected}
          title={`Remove what is selected (Del)${at}`}
        />
      )}
      {state.drawings > 1 && (
        <ChartToolButton
          icon="🧹"
          label="Clear all"
          onClick={onClearDrawings}
          title={`Remove every fixed-range profile and price line${at}`}
        />
      )}
      {/* The pin, at the foot and pushed there by a spacer — it is a property of
          the rail itself rather than one of the tools, so it sits apart from them
          and stays put as the "take it away" group above comes and goes. */}
      <div className="chart-rail-gap" />
      <ChartToolButton
        icon="📌"
        label={pinned ? "Unpin the rail" : "Pin the rail"}
        on={pinned}
        onClick={() => onPinnedChange(!pinned)}
        title={
          pinned
            ? "Pinned — the rail keeps its own column beside the charts. Unpin to float it over the tape."
            : "Floating — the rail lays over the tape and you can see the chart behind it. Pin to give it a column."
        }
      />
    </div>
  );
}
