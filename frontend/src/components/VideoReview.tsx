import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  useAddBookmark,
  useClearSynced,
  useDeleteBookmark,
  useDeleteVideo,
  useSaveVideo,
  useSyncTrades,
  useUpdateBookmark,
  useVideo,
} from "../hooks/useVideo";
import type { FilterScope } from "../lib/queryKeys";
import type { TradeRow, VideoBookmark, VideoData } from "../lib/types";

// ---------------------------------------------------------------------------
// Context: lets the trades table (rendered far from the player) drive the one
// shared <video> element — seek to a bookmark, or mark a trade at the playhead.
// ---------------------------------------------------------------------------
interface VideoReviewCtx {
  data: VideoData | undefined;
  hasPlayableVideo: boolean;
  seek: (offsetS: number) => void;
  markTrade: (trade: TradeRow) => void;
  bookmarkForTrade: (tradeKey: string) => VideoBookmark | undefined;
  isMarking: boolean;
}

const Ctx = createContext<VideoReviewCtx | null>(null);

export function useVideoReview(): VideoReviewCtx | null {
  return useContext(Ctx);
}

// Seconds -> "M:SS" / "H:MM:SS" for compact labels.
function fmtOffset(s: number): string {
  const t = Math.max(0, Math.floor(s));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

export function VideoReviewProvider({
  sourceFile,
  scope,
  children,
}: {
  sourceFile: string;
  scope: FilterScope;
  children: ReactNode;
}) {
  const { data } = useVideo(sourceFile);
  const addBookmark = useAddBookmark(sourceFile);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const video = data?.video ?? null;
  const hasPlayableVideo = !!video && video.exists && video.playable;

  const seek = (offsetS: number) => {
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = offsetS;
    el.focus(); // so arrow keys nudge from here for context
    void el.play().catch(() => {});
  };

  const markTrade = (trade: TradeRow) => {
    const el = videoRef.current;
    if (!el) return;
    addBookmark.mutate({
      offset_s: el.currentTime,
      trade_key: trade.trade_key,
      label: `Trade #${trade.trade_no}`,
    });
  };

  const bookmarkForTrade = (tradeKey: string) =>
    data?.bookmarks.find((b) => b.trade_key === tradeKey);

  return (
    <Ctx.Provider
      value={{
        data,
        hasPlayableVideo,
        seek,
        markTrade,
        bookmarkForTrade,
        isMarking: addBookmark.isPending,
      }}
    >
      <VideoPanel sourceFile={sourceFile} scope={scope} videoRef={videoRef} />
      {children}
    </Ctx.Provider>
  );
}

// ---------------------------------------------------------------------------
// The sticky player panel: link form when empty, else <video> + speed +
// bookmark list + scrub-bar dots.
// ---------------------------------------------------------------------------
function VideoPanel({
  sourceFile,
  scope,
  videoRef,
}: {
  sourceFile: string;
  scope: FilterScope;
  videoRef: React.MutableRefObject<HTMLVideoElement | null>;
}) {
  const { data, seek } = useContext(Ctx)!;
  const addBookmark = useAddBookmark(sourceFile);
  const deleteVideo = useDeleteVideo(sourceFile);
  const syncTrades = useSyncTrades(sourceFile, scope);
  const clearSynced = useClearSynced(sourceFile);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  // The speed + bookmarks side panel shows by default; toggle it off to give the
  // video the full panel width.
  const [asideHidden, setAsideHidden] = useState(false);
  const [duration, setDuration] = useState(0);
  const [speed, setSpeed] = useState(1);
  // Auto-compact: a 1px sentinel sits where the panel would start. The moment
  // it scrolls off the viewport, the panel is "stuck" — shrink the video so it
  // doesn't dominate the screen while you're reviewing trades.
  const [stuck, setStuck] = useState(false);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => setStuck(!entry.isIntersecting),
      { threshold: 0 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const video = data?.video ?? null;
  // Reset the playhead-derived UI when the attempt (and thus the video) changes.
  useEffect(() => {
    setDuration(0);
    setSpeed(1);
    setSyncMsg(null);
  }, [sourceFile]);

  if (!data) return null;

  if (!video) {
    return (
      <div className="panel" style={panelStyle}>
        <LinkForm sourceFile={sourceFile} />
      </div>
    );
  }

  const streamUrl = `/api/videos/stream?source_file=${encodeURIComponent(sourceFile)}`;
  const bookmarks = data.bookmarks;

  const setRate = (r: number) => {
    setSpeed(r);
    if (videoRef.current) videoRef.current.playbackRate = r;
  };

  const addFreeForm = () => {
    const el = videoRef.current;
    if (!el) return;
    const label = window.prompt("Bookmark label", "") ?? "";
    addBookmark.mutate({ offset_s: el.currentTime, label });
  };

  // Anchor = a hand-marked, trade-bound bookmark. Until one exists (and the
  // player has reported its duration) there's nothing to sync from.
  const hasAnchor = data.bookmarks.some((b) => b.trade_key && b.origin === "manual");
  const syncedCount = data.bookmarks.filter((b) => b.origin === "synced").length;
  const canSync = hasAnchor && duration > 0 && !syncTrades.isPending;

  const runSync = () => {
    if (!canSync) return;
    syncTrades.mutate(
      { duration_s: duration },
      {
        onSuccess: (r) => {
          const bits = [`Synced ${r.created} trade${r.created === 1 ? "" : "s"}`];
          if (r.skipped_out_of_range)
            bits.push(`${r.skipped_out_of_range} outside the recording`);
          if (r.skipped_existing) bits.push(`${r.skipped_existing} already marked`);
          setSyncMsg(bits.join(" · "));
        },
        onError: () => setSyncMsg("Sync failed — mark a trade and play the video first."),
      },
    );
  };

  const runClearSynced = () => {
    if (!window.confirm(`Remove ${syncedCount} auto-synced bookmark(s)? Manual marks stay.`))
      return;
    clearSynced.mutate(undefined, {
      onSuccess: (r) => setSyncMsg(`Cleared ${r.deleted} synced bookmark(s).`),
    });
  };

  return (
    <>
      <div ref={sentinelRef} aria-hidden style={{ height: 1 }} />
    <div className="panel" style={panelStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="section-title" style={{ margin: 0 }}>
          Recording{stuck ? " · compact" : ""}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {video.exists && video.playable && !collapsed && (
            <button
              type="button"
              className={asideHidden ? "" : "active"}
              onClick={() => setAsideHidden((h) => !h)}
              title="Show / hide the speed + bookmarks side panel"
            >
              {asideHidden ? "☰ Panel" : "✕ Panel"}
            </button>
          )}
          <button type="button" onClick={() => setCollapsed((c) => !c)} title="Collapse / expand the player">
            {collapsed ? "▸ Show" : "▾ Hide"}
          </button>
          <button
            type="button"
            className="btn-danger"
            onClick={() => {
              if (window.confirm("Unlink this recording and delete its bookmarks?")) deleteVideo.mutate();
            }}
            disabled={deleteVideo.isPending}
            title="Unlink the video and remove its bookmarks (the file on disk is untouched)."
          >
            Unlink
          </button>
        </div>
      </div>

      {!video.exists && (
        <div className="notice neg" style={{ margin: "8px 0" }}>
          Linked file not found at <code>{video.path}</code> — it may have moved. Relink below.
          <div style={{ marginTop: 8 }}><LinkForm sourceFile={sourceFile} /></div>
        </div>
      )}
      {video.exists && !video.playable && (
        <div className="notice" style={{ margin: "8px 0" }}>
          This file's format can't play in the browser (likely .mkv/.mov). Record or remux to
          <strong> .mp4 (H.264)</strong>: <code>ffmpeg -i in.mkv -c copy out.mp4</code>.
        </div>
      )}

      <div style={{ display: collapsed ? "none" : "block" }}>
        {video.exists && video.playable ? (
          <>
            {/* Two-column body: video on the left, controls + bookmarks on the
                right. The right column holds speed (HTML5 native controls
                don't expose a speed selector — only Chrome's right-click
                menu — so we surface it here), the free-form add button, and
                the mixed bookmark list. */}
            {/* Shared height cap for video + side panel: many bookmarks would
                otherwise stretch the aside (and the whole row) instead of
                scrolling within it. */}
            <div
              style={{
                display: "flex",
                gap: 12,
                alignItems: "flex-start",
                marginTop: 6,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <video
                  ref={videoRef}
                  src={streamUrl}
                  controls
                  preload="metadata"
                  onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
                  style={{
                    width: "100%",
                    // Full size at the top of the page; auto-shrink once the
                    // panel is sticky so it doesn't eat the screen while you
                    // scroll the trades table. Click-to-seek still works in
                    // compact mode; "Hide" removes it entirely.
                    maxHeight: stuck ? 450 : "min(82vh, 720px)",
                    background: "#000",
                    borderRadius: 6,
                    display: "block",
                    objectFit: "contain",
                  }}
                />
              </div>
              {!asideHidden && (
              <aside
                style={{
                  width: stuck ? 240 : 280,
                  // Cap matches the video's maxHeight so a long bookmark list
                  // scrolls within the panel rather than stretching the row.
                  maxHeight: stuck ? 450 : "min(82vh, 720px)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  minHeight: 0,
                }}
              >
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <span className="section-cap" style={{ marginRight: 2 }}>Speed</span>
                  {/* .radio-group is the styled selector for .active buttons
                      (matches the attempt-selector pattern in DayExplorer). */}
                  <div className="radio-group">
                    {[1, 1.5, 2].map((r) => (
                      <button
                        key={r}
                        type="button"
                        className={speed === r ? "active" : ""}
                        onClick={() => setRate(r)}
                      >
                        {r}×
                      </button>
                    ))}
                  </div>
                </div>
                <button type="button" className="btn-accent" onClick={addFreeForm}>
                  + Bookmark here
                </button>
                {/* Auto-sync: replay runs at 1×, so one hand-marked trade
                    anchors a bookmark for every other trade by timestamp.
                    Disabled until an anchor exists and the player knows its
                    duration. "Clear synced" only shows once synced rows exist. */}
                <button
                  type="button"
                  onClick={runSync}
                  disabled={!canSync}
                  title={
                    hasAnchor
                      ? "Place a bookmark for every trade, inferred from the marked one"
                      : "Mark a trade on the video first (that's the sync anchor)"
                  }
                >
                  {syncTrades.isPending ? "Syncing…" : "⤓ Auto-sync trades"}
                </button>
                {syncedCount > 0 && (
                  <button
                    type="button"
                    className="btn-danger"
                    onClick={runClearSynced}
                    disabled={clearSynced.isPending}
                    title="Remove auto-synced bookmarks (manual marks are kept)"
                  >
                    Clear synced ({syncedCount})
                  </button>
                )}
                {syncMsg && (
                  <div className="section-cap" style={{ margin: 0 }}>
                    {syncMsg}
                  </div>
                )}
                {/* Bookmark list scrolls within the side panel so a long list
                    doesn't push the video off-screen. */}
                <div
                  style={{
                    flex: 1,
                    minHeight: 0,
                    overflowY: "auto",
                    paddingRight: 4,
                  }}
                >
                  <BookmarkList sourceFile={sourceFile} bookmarks={bookmarks} onSeek={seek} />
                </div>
              </aside>
              )}
            </div>
            <ScrubBar bookmarks={bookmarks} duration={duration} onSeek={seek} />
          </>
        ) : (
          // No playable video yet — still surface bookmarks (e.g. after the
          // file was moved); they're keyed by source_file so they survive.
          <BookmarkList sourceFile={sourceFile} bookmarks={bookmarks} onSeek={seek} />
        )}
      </div>
    </div>
    </>
  );
}

const panelStyle: React.CSSProperties = {
  position: "sticky",
  top: 0,
  zIndex: 20,
  background: "var(--card)",
  marginBottom: 12,
};

// Thin marker strip beneath the video: a dot per bookmark, click to seek.
function ScrubBar({
  bookmarks,
  duration,
  onSeek,
}: {
  bookmarks: VideoBookmark[];
  duration: number;
  onSeek: (s: number) => void;
}) {
  if (!duration) return null;
  return (
    <div
      style={{
        position: "relative",
        height: 14,
        margin: "6px 2px 2px",
        borderTop: "1px solid var(--grid)",
      }}
    >
      {bookmarks.map((b) => {
        // Three states: solid accent = hand-marked trade, hollow accent =
        // auto-synced trade, muted = free-form bookmark.
        const synced = b.origin === "synced";
        const tradeBound = !!b.trade_key;
        return (
          <button
            key={b.id}
            type="button"
            onClick={() => onSeek(b.offset_s)}
            title={`${b.label || "bookmark"}${synced ? " (synced)" : ""} · ${fmtOffset(b.offset_s)}`}
            style={{
              position: "absolute",
              left: `${Math.min(100, (b.offset_s / duration) * 100)}%`,
              top: 2,
              transform: "translateX(-50%)",
              width: 10,
              height: 10,
              padding: 0,
              borderRadius: "50%",
              border: synced ? "1.5px solid var(--accent)" : "1px solid var(--bg)",
              cursor: "pointer",
              background: synced
                ? "var(--card)"
                : tradeBound
                  ? "var(--accent)"
                  : "var(--muted)",
            }}
          />
        );
      })}
    </div>
  );
}

// Mixed list: trade-bound ("Trade #N") and free-form, sorted by offset.
// Row is one click target — clicking anywhere seeks; double-click the label to
// rename inline; the × on the right deletes (stops propagation). No standing
// buttons means more rows fit and the panel stays scannable.
function BookmarkList({
  sourceFile,
  bookmarks,
  onSeek,
}: {
  sourceFile: string;
  bookmarks: VideoBookmark[];
  onSeek: (s: number) => void;
}) {
  const update = useUpdateBookmark(sourceFile);
  const del = useDeleteBookmark(sourceFile);
  const [editingId, setEditingId] = useState<number | null>(null);
  if (!bookmarks.length) {
    return <div className="section-cap" style={{ marginTop: 6 }}>No bookmarks yet. Scrub to a moment and use “+ Bookmark here”, or mark a trade from its row.</div>;
  }
  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 1, fontSize: "0.75em" }}>
      {bookmarks.map((b) => (
        <BookmarkRow
          key={b.id}
          b={b}
          editing={editingId === b.id}
          onStartEdit={() => setEditingId(b.id)}
          onCancelEdit={() => setEditingId(null)}
          onSeek={() => onSeek(b.offset_s)}
          onSave={(label) => {
            if (label !== b.label) update.mutate({ id: b.id, label });
            setEditingId(null);
          }}
          onDelete={() => del.mutate(b.id)}
        />
      ))}
    </div>
  );
}

function BookmarkRow({
  b,
  editing,
  onStartEdit,
  onCancelEdit,
  onSeek,
  onSave,
  onDelete,
}: {
  b: VideoBookmark;
  editing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSeek: () => void;
  onSave: (label: string) => void;
  onDelete: () => void;
}) {
  const [hover, setHover] = useState(false);
  const [draft, setDraft] = useState(b.label);
  useEffect(() => setDraft(b.label), [b.label, editing]);
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={() => !editing && onSeek()}
      title="Click to jump · double-click label to rename"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "2px 4px",
        borderRadius: 4,
        cursor: editing ? "text" : "pointer",
        background: hover ? "var(--card-border)" : "transparent",
      }}
    >
      <span
        style={{
          color: "var(--muted)",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
        }}
      >
        ▶ {fmtOffset(b.offset_s)}
      </span>
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={() => onSave(draft)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSave(draft);
            else if (e.key === "Escape") onCancelEdit();
          }}
          style={{ flex: 1, minWidth: 0, padding: "0 4px" }}
        />
      ) : (
        <span
          onDoubleClick={(e) => {
            e.stopPropagation();
            onStartEdit();
          }}
          style={{
            color: b.trade_key ? "var(--accent)" : "var(--text)",
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {/* ↻ flags an auto-synced marker — distinguishes it from a
              hand-placed trade mark, mirroring the hollow scrub-bar dot. */}
          {b.origin === "synced" && (
            <span title="auto-synced" style={{ opacity: 0.6, marginRight: 3 }}>↻</span>
          )}
          {b.label || (b.trade_key ? "(trade)" : "(bookmark)")}
        </span>
      )}
      {/* Borderless glyph; only visible on hover so it doesn't clutter the
          resting row. stopPropagation keeps the row's seek-on-click out. */}
      <span
        role="button"
        title="Delete bookmark"
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        style={{
          color: "var(--red)",
          opacity: hover ? 1 : 0,
          cursor: "pointer",
          padding: "0 2px",
          userSelect: "none",
        }}
      >
        ✕
      </span>
    </div>
  );
}

// Paste a path to link a recording to this attempt. Validation (file exists)
// happens server-side; a 404 surfaces as an error here.
function LinkForm({ sourceFile }: { sourceFile: string }) {
  const save = useSaveVideo(sourceFile);
  const [path, setPath] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (path.trim()) save.mutate({ path: path.trim() });
      }}
    >
      <div className="section-cap" style={{ marginBottom: 6 }}>
        Link this attempt’s recording — paste its path (Windows <code>C:\…</code> works under WSL):
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder={`C:\\Users\\you\\Videos\\${sourceFile.replace(/\.[^.]+$/, "")}.mp4`}
          style={{ flex: 1 }}
        />
        <button type="submit" className="btn-accent" disabled={save.isPending || !path.trim()}>
          {save.isPending ? "Linking…" : "Link"}
        </button>
      </div>
      {save.isError && (
        <div className="neg" style={{ marginTop: 6 }}>
          Couldn’t link — no readable file at that path. Check the path and try again.
        </div>
      )}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Trade-row cell: state-aware button — 🔖 Mark (unbound) → ▶ jump (bound).
// Rendered inside the trades table; no-ops gracefully when there's no video.
// ---------------------------------------------------------------------------
export function TradeVideoCell({ trade }: { trade: TradeRow }) {
  const ctx = useVideoReview();
  if (!ctx || !ctx.hasPlayableVideo) return <span className="section-cap">—</span>;
  // Defensive: without a trade_key we can't bind a bookmark to this row.
  // Should never happen now that the day endpoint returns trade_key, but
  // guarding here keeps a single bad row from rendering a broken Mark button.
  if (!trade.trade_key) return <span className="section-cap">—</span>;
  const bm = ctx.bookmarkForTrade(trade.trade_key);
  if (bm) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          ctx.seek(bm.offset_s);
        }}
        title="Jump to this trade in the video"
      >
        ▶ {fmtOffset(bm.offset_s)}
      </button>
    );
  }
  return (
    <button
      type="button"
      className="btn-accent"
      disabled={ctx.isMarking}
      onClick={(e) => {
        e.stopPropagation();
        ctx.markTrade(trade);
      }}
      title="Mark this trade at the current playhead"
    >
      🔖 Mark
    </button>
  );
}
