// How many charts, and where — the control that replaced the split toggle.
//
// The toggle was a boolean because there were two arrangements. With six, the
// honest control is a picture of each one: nobody reads "left3" off a label, and
// a dropdown of names would make you translate a word into a shape every time.
//
// The icons are DERIVED FROM THE PLACEMENT TABLE rather than drawn by hand. An
// icon that says 2x2 over a layout that renders three panes is exactly the kind
// of drift a second hand-maintained table invites, and this way the picture is
// the same data the grid is built from — get one wrong and both are wrong
// together, which is at least visible.

import { useEffect, useRef, useState } from "react";
import { LAYOUTS, LAYOUT_IDS, type LayoutId, type PanePlace } from "../../lib/paneLayout";

/** One pane's rectangle inside a 10x10 icon.
 *
 *  Grid lines 1..2 are the first track, 3..4 the second, and 1..4 is both plus
 *  the divider between them — the same reading the grid does, at icon scale.
 *  0.8 of a unit is left between the two tracks so the split is legible at 30px.
 */
function rect(p: PanePlace): { x: number; y: number; w: number; h: number } {
  const span = (t: readonly [number, number]): [number, number] =>
    t[0] === 1 && t[1] === 4 ? [0, 10] : t[0] === 1 ? [0, 4.6] : [5.4, 4.6];
  const [x, w] = span(p.col);
  const [y, h] = span(p.row);
  return { x, y, w, h };
}

function LayoutIcon({ id }: { id: LayoutId }) {
  return (
    <svg width="26" height="19" viewBox="0 0 10 10" aria-hidden focusable="false">
      {LAYOUTS[id].place.map((p, i) => {
        const r = rect(p);
        return <rect key={i} x={r.x} y={r.y} width={r.w} height={r.h} rx="0.7" />;
      })}
    </svg>
  );
}

export function LayoutPicker({
  value,
  onChange,
}: {
  value: LayoutId;
  onChange: (id: LayoutId) => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // A press anywhere else, or Escape, puts it away. Registered only while it is
  // open so a closed picker costs the page nothing.
  useEffect(() => {
    if (!open) return;
    const off = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", off);
    document.addEventListener("keydown", key, true);
    return () => {
      document.removeEventListener("pointerdown", off);
      document.removeEventListener("keydown", key, true);
    };
  }, [open]);

  return (
    <div className="chart-layout" ref={box}>
      <button
        type="button"
        className={`chart-layout-btn${open ? " open" : ""}`}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
        title={`Layout — ${LAYOUTS[value].title.toLowerCase()}`}
      >
        <LayoutIcon id={value} />
      </button>
      {open && (
        <div className="chart-layout-menu" role="menu">
          {LAYOUT_IDS.map((id) => (
            <button
              key={id}
              type="button"
              role="menuitemradio"
              aria-checked={id === value}
              className={id === value ? "on" : undefined}
              title={LAYOUTS[id].title}
              onClick={() => {
                onChange(id);
                setOpen(false);
              }}
            >
              <LayoutIcon id={id} />
              <span>{LAYOUTS[id].label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
