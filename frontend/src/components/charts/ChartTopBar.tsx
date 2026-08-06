import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { NavMenu } from "../NavMenu";
import { CHARTS } from "../../lib/workspaces";

/** Is native fullscreen actually available for a plain element?
 *
 *  iOS Safari says no — it only ever fullscreens a <video> — and the button has
 *  to be absent there rather than present and inert. Checked once at module
 *  load: the answer cannot change for the life of the document. */
const CAN_FULLSCREEN =
  typeof document !== "undefined" &&
  document.fullscreenEnabled === true &&
  typeof document.documentElement.requestFullscreen === "function";

export type ChartTopBarProps = {
  /** What session this is. A node rather than a string because blind replay
   *  shows ▨▨▨▨ where the date goes, and Live shows a connection state. */
  title: ReactNode;
  /** Opens the page's setup panel. The title doubles as its trigger — the thing
   *  the panel configures is the thing you press to configure it. Omit on a page
   *  with no setup to open, and the title renders as plain text. */
  onTitle?: () => void;
  /** Whether that panel is currently out, for the caret and aria-expanded. */
  titleOpen?: boolean;
  /** Resident controls, immediately right of the title. In practice the
   *  timeframe picker: the one setting you change while reading rather than
   *  before starting. */
  children?: ReactNode;
  /** Page-specific cluster at the right end, before the Replay|Live switch.
   *  Live puts its connection dot and record/signal toggles here; Replay uses it
   *  for the history link. */
  right?: ReactNode;
};

/**
 * The one bar a chart page draws for itself.
 *
 * The Charts workspace renders no shell chrome (see `chrome` in lib/workspaces)
 * — 215px of topbar, tab strip and page padding was a fifth of a 1080p viewport
 * spent on navigation you look at once. This is what replaces it: ~36px holding
 * the way out, what you are looking at, the one control you change mid-read, and
 * the switch between the two chart pages.
 *
 * Everything else a chart page used to keep resident is summoned instead — the
 * setup row behind the title, the ticket and blotter behind the side rail. The
 * transport is the deliberate exception and stays in flow on Replay: it is the
 * instrument you drive a replay with, not chrome you occasionally want.
 *
 * Shared by Replay and Live so the two cannot drift. Page-specific content
 * arrives through slots rather than through a `page` discriminator, so neither
 * page has to know the other exists.
 */
export function ChartTopBar({ title, onTitle, titleOpen, children, right }: ChartTopBarProps) {
  const { search } = useLocation();
  const [isFull, setIsFull] = useState(false);

  useEffect(() => {
    if (!CAN_FULLSCREEN) return;
    // Esc leaves fullscreen without going through the button, so the icon has to
    // follow the document rather than a click count.
    const sync = () => setIsFull(document.fullscreenElement != null);
    document.addEventListener("fullscreenchange", sync);
    sync();
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  const toggleFull = useCallback(() => {
    // Whole document, not the chart element: the page already fills the viewport,
    // so the only pixels left to win are the browser's own tab and address bars.
    if (document.fullscreenElement) void document.exitFullscreen();
    else void document.documentElement.requestFullscreen().catch(() => {});
  }, []);

  return (
    <div className="chart-topbar">
      <NavMenu />
      {onTitle ? (
        <button
          type="button"
          className={`chart-topbar-title${titleOpen ? " open" : ""}`}
          onClick={onTitle}
          aria-expanded={titleOpen ?? false}
          title="Session setup"
        >
          {title}
          <span className="chart-topbar-caret" aria-hidden>
            {titleOpen ? "▴" : "▾"}
          </span>
        </button>
      ) : (
        <span className="chart-topbar-title static">{title}</span>
      )}

      {children}

      <div className="chart-topbar-end">
        {right}
        {/* The Replay|Live switch is the tab strip, reduced to the only two tabs
            this workspace has. Read off the shared config so a third chart page
            appears here without anyone remembering to add it. */}
        <nav className="chart-topbar-tabs" aria-label="Chart">
          {CHARTS.tabs.map((t) => (
            <NavLink key={t.to} to={{ pathname: t.to, search }} end={t.end}>
              {t.label}
            </NavLink>
          ))}
        </nav>
        {CAN_FULLSCREEN && (
          <button
            type="button"
            className={`chart-topbar-btn${isFull ? " on" : ""}`}
            onClick={toggleFull}
            aria-pressed={isFull}
            title={isFull ? "Leave fullscreen (Esc)" : "Fullscreen — hides the browser's own chrome"}
          >
            ⛶
          </button>
        )}
      </div>
    </div>
  );
}
