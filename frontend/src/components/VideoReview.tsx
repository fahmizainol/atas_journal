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
  useDeleteBookmark,
  useDeleteVideo,
  useSaveVideo,
  useUpdateBookmark,
  useVideo,
} from "../hooks/useVideo";
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
  children,
}: {
  sourceFile: string;
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
      <VideoPanel sourceFile={sourceFile} videoRef={videoRef} />
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
  videoRef,
}: {
  sourceFile: string;
  videoRef: React.MutableRefObject<HTMLVideoElement | null>;
}) {
  const { data, seek } = useContext(Ctx)!;
  const addBookmark = useAddBookmark(sourceFile);
  const deleteVideo = useDeleteVideo(sourceFile);
  const [collapsed, setCollapsed] = useState(false);
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

  return (
    <>
      <div ref={sentinelRef} aria-hidden style={{ height: 1 }} />
    <div className="panel" style={panelStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="section-title" style={{ margin: 0 }}>
          Recording{stuck ? " · compact" : ""}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
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
        {video.exists && video.playable && (
          <>
            <video
              ref={videoRef}
              src={streamUrl}
              controls
              preload="metadata"
              onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
              style={{
                width: stuck ? "auto" : "100%",
                // Full size at the top of the page; auto-shrink to a thumbnail
                // once the panel is sticky so it doesn't eat the screen while
                // you scroll the trades table. Click-to-seek still works on
                // the thumbnail; "Show" expands manually if you prefer.
                maxHeight: stuck ? 180 : "min(50vh, 420px)",
                background: "#000",
                borderRadius: 6,
                display: "block",
              }}
            />
            <ScrubBar bookmarks={bookmarks} duration={duration} onSeek={seek} />
            <div style={{ display: "flex", gap: 6, alignItems: "center", margin: "8px 0", flexWrap: "wrap" }}>
              <span className="section-cap" style={{ marginRight: 4 }}>Speed</span>
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
              <button type="button" className="btn-accent" style={{ marginLeft: "auto" }} onClick={addFreeForm}>
                + Bookmark here
              </button>
            </div>
          </>
        )}
        <BookmarkList sourceFile={sourceFile} bookmarks={bookmarks} onSeek={seek} />
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
      {bookmarks.map((b) => (
        <button
          key={b.id}
          type="button"
          onClick={() => onSeek(b.offset_s)}
          title={`${b.label || "bookmark"} · ${fmtOffset(b.offset_s)}`}
          style={{
            position: "absolute",
            left: `${Math.min(100, (b.offset_s / duration) * 100)}%`,
            top: 2,
            transform: "translateX(-50%)",
            width: 10,
            height: 10,
            padding: 0,
            borderRadius: "50%",
            border: "1px solid var(--bg)",
            cursor: "pointer",
            background: b.trade_key ? "var(--accent)" : "var(--muted)",
          }}
        />
      ))}
    </div>
  );
}

// Mixed list: trade-bound ("Trade #N") and free-form, sorted by offset.
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
  if (!bookmarks.length) {
    return <div className="section-cap" style={{ marginTop: 6 }}>No bookmarks yet. Scrub to a moment and use “+ Bookmark here”, or mark a trade from its row.</div>;
  }
  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 2 }}>
      {bookmarks.map((b) => (
        <div key={b.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button type="button" onClick={() => onSeek(b.offset_s)} title="Jump to this moment" style={{ minWidth: 64, textAlign: "left" }}>
            ▶ {fmtOffset(b.offset_s)}
          </button>
          <span style={{ color: b.trade_key ? "var(--accent)" : "var(--text)", flex: 1 }}>
            {b.label || (b.trade_key ? "(trade)" : "(bookmark)")}
          </span>
          <button
            type="button"
            title="Rename"
            onClick={() => {
              const label = window.prompt("Bookmark label", b.label) ?? b.label;
              if (label !== b.label) update.mutate({ id: b.id, label });
            }}
          >
            ✎
          </button>
          <button type="button" className="btn-danger" title="Delete bookmark" onClick={() => del.mutate(b.id)}>
            ✕
          </button>
        </div>
      ))}
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
