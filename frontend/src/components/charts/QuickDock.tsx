// The market-order window — the BUY/SELL pair (and, when a position is on, the
// chip and Close that belong with them) in one small box that floats over the
// chart and can be dragged anywhere on it.
//
// It parks itself at the foot of the tape, which is where it has always been and
// still the right default: the two buttons you can least afford to go looking
// for should be under the thumb. But *where* the chart is busy is a per-person,
// per-setup thing — a dock over the last hour of price action on one layout is
// out of the way on another — so the window moves, and remembers where it was
// put. That is the whole feature: same buttons, one box, draggable, sticky.
//
// Moving it changes what the page publishes as --chart-floor. Parked, this box
// owns the bottom strip and the ticket has to sit above it; floated, it does
// not, and the floor goes back to zero so the ticket and the day-scale strip
// get their height back. The measurement is done here rather than on the pages
// because only this component knows which of the two it is.

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { loadDockPos, saveDockPos, type DockPos } from "../../lib/chartPrefs";

interface QuickDockProps {
  children: ReactNode;
  /** Called with the height this window claims at the foot of the chart — its
   *  own height while parked, 0 once it has been dragged off the floor. */
  onFloorChange?: (px: number) => void;
}

/** Keep a position inside the chart it floats over. Applied on every drag frame
 *  *and* whenever the chart resizes, so a spot saved on a wide window can't
 *  strand the buttons off-screen on a narrow one — the whole box stays reachable
 *  whatever the layout does underneath it. */
function clampToParent(el: HTMLElement, pos: DockPos): DockPos {
  const parent = el.offsetParent as HTMLElement | null;
  if (!parent) return pos;
  const maxX = Math.max(0, parent.clientWidth - el.offsetWidth);
  const maxY = Math.max(0, parent.clientHeight - el.offsetHeight);
  return {
    x: Math.min(Math.max(pos.x, 0), maxX),
    y: Math.min(Math.max(pos.y, 0), maxY),
  };
}

export function QuickDock({ children, onFloorChange }: QuickDockProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<DockPos | null>(() => loadDockPos());
  const [dragging, setDragging] = useState(false);
  // The grab offset within the box, so the window doesn't jump its own corner to
  // the pointer on the first move, plus the chart's rect at grab time to convert
  // page coordinates into offsets from it.
  const grab = useRef<{ dx: number; dy: number; left: number; top: number } | null>(null);
  const posRef = useRef(pos);
  posRef.current = pos;

  const floorCb = useRef(onFloorChange);
  floorCb.current = onFloorChange;

  // Both observers below key off *whether* the window floats, never off where it
  // is: a drag moves it every frame, and re-attaching two ResizeObservers per
  // frame is work for nothing. Position is read through the ref instead.
  const floating = pos != null;

  // Height of the parked window, republished whenever it changes — the row is one
  // line or two depending on whether a position is open and how far its buttons
  // wrapped, so it is measured rather than declared. Floated, it claims nothing.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => floorCb.current?.(posRef.current ? 0 : el.getBoundingClientRect().height);
    const ro = new ResizeObserver(read);
    ro.observe(el);
    read();
    return () => {
      ro.disconnect();
      floorCb.current?.(0);
    };
  }, [floating]);

  // Re-clamp when the chart resizes under a floated window: a pane opening, the
  // rail pinning, or the browser narrowing all move the edges this box was put
  // inside of.
  useLayoutEffect(() => {
    const el = ref.current;
    const parent = el?.offsetParent as HTMLElement | null;
    if (!el || !parent || !floating) return;
    const fit = () => {
      const at = posRef.current;
      if (!at) return;
      const next = clampToParent(el, at);
      if (next.x !== at.x || next.y !== at.y) setPos(next);
    };
    const ro = new ResizeObserver(fit);
    ro.observe(parent);
    fit();
    return () => ro.disconnect();
  }, [floating]);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const el = ref.current;
    if (!el) return;
    // Everything interactive inside stays interactive: the drag only starts on
    // the window's own chrome — the grip, the padding, the gaps between buttons.
    // A market order is not something to fire by mis-aiming a drag.
    if ((e.target as HTMLElement).closest("button, input, select, textarea, a")) return;
    const parent = el.offsetParent as HTMLElement | null;
    if (!parent) return;
    const pr = parent.getBoundingClientRect();
    // Read the box where it actually is — parked it is centred by a transform,
    // and measuring the rendered rect is what makes the first drag frame
    // continuous instead of a jump to wherever the untransformed corner was.
    const er = el.getBoundingClientRect();
    grab.current = { dx: e.clientX - er.left, dy: e.clientY - er.top, left: pr.left, top: pr.top };
    el.setPointerCapture(e.pointerId);
    setDragging(true);
    setPos(clampToParent(el, { x: er.left - pr.left, y: er.top - pr.top }));
    e.preventDefault();
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const g = grab.current;
    const el = ref.current;
    if (!g || !el) return;
    setPos(clampToParent(el, { x: e.clientX - g.left - g.dx, y: e.clientY - g.top - g.dy }));
  }, []);

  const endDrag = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!grab.current) return;
    grab.current = null;
    setDragging(false);
    ref.current?.releasePointerCapture(e.pointerId);
    saveDockPos(posRef.current);
  }, []);

  // Double-click the chrome to put it back at the foot of the tape — the way out
  // of a position you regret, without a button that would cost width on a box
  // whose whole point is being small.
  const onDoubleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button, input, select, textarea, a")) return;
    setPos(null);
    saveDockPos(null);
  }, []);

  return (
    <div
      ref={ref}
      className={`sim-quick${pos ? " moved" : ""}${dragging ? " dragging" : ""}`}
      style={pos ? { left: pos.x, top: pos.y } : undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={onDoubleClick}
    >
      {/* A title bar, because that is what makes a box read as a window you can
          move. The buttons themselves cannot be the handle — a drag that starts
          on BUY must never become an order — so the handle has to be something
          you can see without being told it is there, which a hairline down the
          side was not. */}
      <div
        className="sim-quick-grip"
        title="Drag to move these buttons anywhere on the chart · double-click to put them back"
      >
        <span aria-hidden />
      </div>
      <div className="sim-quick-body">{children}</div>
    </div>
  );
}
