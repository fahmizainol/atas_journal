import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { WORKSPACES, workspaceForPath } from "../lib/workspaces";

// Navigation for the pages that draw no shell chrome. The Charts workspace
// hands its topbar and tab strip back to the chart (see the `chrome` flag in
// lib/workspaces), so the way to every other page has to live somewhere on the
// page itself — this is that somewhere.
//
// A popover rather than a drawer: it is nine links. A drawer would need a scrim,
// a transition and a body-scroll lock to show a list that fits in 200px.
//
// Deliberately NOT the data sidebar's ☰. That one opens import/timezone controls
// over ATAS exports, which a chart page has no use for (both of them hardcode
// their timezone), so the glyph is free to mean navigation here without two
// meanings competing on one screen.
export function NavMenu() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const active = workspaceForPath(pathname);

  // Esc and outside-press close, both in the capture phase — the same reasoning
  // as IndicatorLegend's settings panel: this listener is registered when the
  // menu opens, long after the chart's tools and the page's key handlers
  // registered theirs, so a bubble-phase listener would run last and the tape
  // would act on the Escape first. Capture puts it first regardless of when it
  // was added, and stopping propagation there is what "the menu took this
  // Escape" means.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    };
    // pointerdown rather than click so it closes on the press, before that
    // gesture can turn into a drag on the chart underneath.
    const onDown = (e: PointerEvent) => {
      const el = e.target as HTMLElement | null;
      if (el?.closest?.("[data-nav-menu]")) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pointerdown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onDown, true);
    };
  }, [open]);

  const go = (to: string) => {
    setOpen(false);
    // Keep the querystring across the hop so the Journal's FilterBar scope
    // survives leaving a chart, exactly as the shell's workspace switch does.
    navigate({ pathname: to, search });
  };

  return (
    <div className="nav-menu" data-nav-menu>
      <button
        type="button"
        className="nav-menu-btn"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Go to another page"
        title="Go to another page"
      >
        ☰
      </button>
      {open && (
        <div className="nav-menu-pop" role="menu">
          {WORKSPACES.map((ws) => (
            <div key={ws.id} className="nav-menu-group">
              <div className="nav-menu-head">{ws.label}</div>
              {ws.tabs.map((t) => {
                const here =
                  t.to === "/" ? pathname === "/" : pathname === t.to || pathname.startsWith(t.to + "/");
                return (
                  <button
                    key={t.to}
                    type="button"
                    role="menuitem"
                    className={`nav-menu-item${here ? " here" : ""}`}
                    onClick={() => go(t.to)}
                  >
                    {t.label}
                    {here && <span aria-hidden>✓</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
      {/* The active workspace is worth saying out loud for a screen reader: with
          no tab strip on the page there is nothing else announcing where you
          are. */}
      <span className="sr-only">Currently in {active.label}</span>
    </div>
  );
}
