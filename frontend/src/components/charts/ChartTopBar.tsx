import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { NavMenu } from "../NavMenu";
import { CHARTS } from "../../lib/workspaces";
import {
  armAudio,
  previewCue,
  setSoundOn,
  setSoundPack,
  soundOn,
  soundPack,
} from "../../lib/orderSound";

/** Is native fullscreen actually available for a plain element?
 *
 *  iOS Safari says no — it only ever fullscreens a <video> — and the button has
 *  to be absent there rather than present and inert. Checked once at module
 *  load: the answer cannot change for the life of the document. */
const CAN_FULLSCREEN =
  typeof document !== "undefined" &&
  document.fullscreenEnabled === true &&
  typeof document.documentElement.requestFullscreen === "function";

/** What the order cues are set to: silent, the platform tones, or the spoken
 *  pack. The mute state and the pack choice as one setting, because that is how
 *  the button presents them. */
type CueMode = "off" | "tones" | "voice";

// Off first, so the cycle starts by turning something on rather than by
// silencing it, and quiet-to-loud after that.
const NEXT_CUE: Record<CueMode, CueMode> = { off: "tones", tones: "voice", voice: "off" };
const CUE_ICON: Record<CueMode, string> = { off: "🔇", tones: "🔊", voice: "🗣" };
const CUE_LABEL: Record<CueMode, string> = {
  off: "off",
  tones: "tones",
  voice: "spoken",
};

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
  // The sound switch lives here rather than on either page's setup panel for the
  // same reason the fullscreen button does: both chart pages make the same
  // noises, and a setting that has to be found twice gets set twice differently.
  //
  // One button, three states, because they are three points on one scale — how
  // much the app says out loud — rather than two independent settings. A mute
  // checkbox plus a pack dropdown would be two controls on a 36px bar to express
  // a choice you make by pressing until it sounds right, which is what the cycle
  // lets you do: every press plays the setting it just landed on.
  const [cue, setCue] = useState<CueMode>(() => (soundOn() ? soundPack() : "off"));

  // Audio cannot start outside a user gesture, and a fill is not one — so the
  // bar that is always on screen is what arms it (see lib/orderSound).
  useEffect(armAudio, []);

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
        <button
          type="button"
          className={`chart-topbar-btn${cue === "off" ? "" : " on"}`}
          onClick={() => {
            const next = NEXT_CUE[cue];
            setCue(next);
            setSoundOn(next !== "off");
            if (next !== "off") setSoundPack(next);
            // Hear what you just chose. Pressed through to "off" this is silent,
            // which is its own answer.
            previewCue();
          }}
          aria-pressed={cue !== "off"}
          aria-label={`Order sounds: ${CUE_LABEL[cue]}`}
          title={`Order sounds: ${CUE_LABEL[cue]} — click for ${CUE_LABEL[NEXT_CUE[cue]]}`}
        >
          {CUE_ICON[cue]}
        </button>
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
