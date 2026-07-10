import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTradeDetail } from "../hooks/useTrades";
import { useExcursion } from "../hooks/useCharts";
import { useVideo } from "../hooks/useVideo";
import type { FilterScope } from "../lib/queryKeys";
import { KpiGrid } from "./KpiGrid";
import { JournalForm } from "./JournalForm";
import { ReconstructionChart } from "./charts/ReconstructionChart";
import { TradeAnalysis } from "./ai/TradeAnalysis";
import { fmt, fmtDateTime, fmtInt } from "../lib/format";
import { toneOf } from "../theme";
import type { Card } from "./KpiCard";
import type { VideoData } from "../lib/types";

function fmtOffset(s: number): string {
  const t = Math.max(0, Math.floor(s));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

export function TradeDetail({
  scope,
  tradeNo,
  showRecording = true,
  jumpToOffset = null,
}: {
  scope: FilterScope;
  tradeNo: number;
  showRecording?: boolean;
  jumpToOffset?: { offsetS: number; nonce: number } | null;
}) {
  const { data, isLoading } = useTradeDetail(scope, tradeNo);
  // Heavy section (chart + excursion-backed AI) is hidden by default and only
  // fetched when revealed — keeps expanding a trade row instant. Excursion
  // loads Databento bars, which is the slow part on a cold cache.
  const [showChart, setShowChart] = useState(false);
  const { data: exc } = useExcursion(scope, tradeNo, showChart);
  const { data: videoData, isLoading: isVideoLoading } = useVideo(
    showRecording ? data?.trade.source_file ?? null : null,
  );
  if (isLoading || !data) return <div className="notice">Loading trade…</div>;

  const t = data.trade;
  const row1: Card[] = [
    { label: "Direction", value: t.direction },
    { label: "Contracts", value: fmtInt(t.max_contracts) },
    { label: "Net PnL", value: fmt(t.net_pnl), tone: toneOf(t.net_pnl) },
    { label: "Avg entry", value: fmt(t.avg_entry, false) },
    { label: "Avg exit", value: fmt(t.avg_exit, false) },
  ];
  const row2: Card[] = [
    { label: "Entry", value: fmtDateTime(t.entry_ts_local) },
    { label: "Exit", value: fmtDateTime(t.exit_ts_local) },
    { label: "Hold", value: `${(t.duration_s / 60).toFixed(1)}m` },
  ];
  const journal = (
    <JournalForm
      // The logical trade owns the journal entry, in either view.
      tradeKey={t.logical_trade_key}
      initialNote={data.note}
      initialTags={data.tags}
      initialSetups={data.setups}
      initialConfluences={data.confluences}
      initialModelId={data.model_id}
      initialRulesMet={data.rules_met}
    />
  );

  return (
    <div>
      <div className="section-title">
        Trade #{t.trade_no} — {fmtDateTime(t.entry_ts_local)}
      </div>
      {showRecording && (
        <TradeRecordingPanel
          sourceFile={t.source_file}
          tradeKey={t.trade_key}
          entryDate={t.entry_ts_local.slice(0, 10)}
          videoData={videoData}
          isLoading={isVideoLoading}
          jumpToOffset={jumpToOffset}
        />
      )}
      {showRecording ? (
        <div className="trade-detail-summary-grid">
          <div>
            <KpiGrid cards={row1} template="repeat(3, 1fr)" className="kpi-compact" />
            <KpiGrid cards={row2} template="repeat(3, 1fr)" className="kpi-compact" />
          </div>
          {journal}
        </div>
      ) : (
        <>
          <KpiGrid cards={row1} template="repeat(5, 1fr)" className="kpi-compact" />
          <KpiGrid cards={row2} template="repeat(3, 1fr)" className="kpi-compact" />
        </>
      )}
      <div style={{ margin: "10px 0 4px" }}>
        <button
          type="button"
          className={showChart ? "active" : ""}
          onClick={() => setShowChart((s) => !s)}
          title="Show / hide the reconstruction chart + AI analysis (hidden = not loaded, keeps expand instant)"
        >
          {showChart ? "▾ Hide chart & analysis" : "▸ Show chart & analysis"}
        </button>
      </div>
      {showChart && (
        <>
          <ReconstructionChart scope={scope} tradeNo={tradeNo} />
          {/* Render once excursion has resolved so the AI panel doesn't flash
              its "needs excursion data" notice while bars are still loading. */}
          {exc !== undefined && (
            <TradeAnalysis
              tradeKey={t.trade_key}
              scope={scope}
              hasExcursion={!!exc?.available && !!exc?.has_data}
            />
          )}
        </>
      )}
      {!showRecording && journal}
    </div>
  );
}

function TradeRecordingPanel({
  sourceFile,
  tradeKey,
  entryDate,
  videoData,
  isLoading,
  jumpToOffset,
}: {
  sourceFile: string;
  tradeKey: string;
  entryDate: string;
  videoData: VideoData | undefined;
  isLoading: boolean;
  jumpToOffset: { offsetS: number; nonce: number } | null;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const seekAndPlay = (offsetS: number) => {
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = Math.max(0, offsetS);
    void el.play().catch(() => {});
  };

  const video = videoData?.video ?? null;
  const bookmark = videoData?.bookmarks.find((b) => b.trade_key === tradeKey);
  const streamUrl =
    bookmark && video?.exists && video.playable
      ? `/api/videos/stream?source_file=${encodeURIComponent(sourceFile)}#t=${Math.floor(bookmark.offset_s)}`
      : `/api/videos/stream?source_file=${encodeURIComponent(sourceFile)}`;

  useEffect(() => {
    if (!jumpToOffset || !video?.exists || !video.playable) return;
    seekAndPlay(jumpToOffset.offsetS);
  }, [jumpToOffset?.nonce, jumpToOffset?.offsetS, video?.exists, video?.playable]);

  if (isLoading) return <div className="notice">Loading recording…</div>;

  if (!video) {
    return (
      <div className="panel" style={{ marginTop: 10 }}>
        <div className="section-title" style={{ marginTop: 0 }}>Recording</div>
        <div className="section-cap">
          No recording is linked for this trade's attempt yet. Link or scan recordings from{" "}
          <Link to={`/calendar/${entryDate}`}>the day review</Link>.
        </div>
      </div>
    );
  }

  return (
    <div className="panel" style={{ marginTop: 10 }}>
      <div className="section-title" style={{ marginTop: 0 }}>Recording</div>
      <div className="section-cap" style={{ marginBottom: 8 }}>
        Attempt file <code>{sourceFile}</code>
        {" · "}
        <Link to={`/calendar/${entryDate}`}>open day review</Link>
      </div>
      {!video.exists && (
        <div className="notice neg">
          Linked file not found at <code>{video.path}</code>.
        </div>
      )}
      {video.exists && !video.playable && (
        <div className="notice">
          Recording is linked, but this format cannot play in the browser: <code>{video.path}</code>
        </div>
      )}
      {video.exists && video.playable && (
        <>
          {bookmark ? (
            <div className="section-cap" style={{ marginBottom: 6 }}>
              Trade bookmark at <strong>{fmtOffset(bookmark.offset_s)}</strong>
              {bookmark.origin === "synced" ? " (auto-synced)" : " (manual)"}.
            </div>
          ) : (
            <div className="section-cap" style={{ marginBottom: 6 }}>
              Recording is linked, but this trade has no bookmark yet. Mark or auto-sync it from the day review.
            </div>
          )}
          <video
            ref={videoRef}
            src={streamUrl}
            controls
            autoPlay={!!jumpToOffset}
            preload="metadata"
            onLoadedMetadata={() => {
              if (jumpToOffset) seekAndPlay(jumpToOffset.offsetS);
            }}
            style={{
              width: "100%",
              maxHeight: "min(72vh, 720px)",
              background: "#000",
              borderRadius: 6,
              display: "block",
              objectFit: "contain",
            }}
          />
        </>
      )}
    </div>
  );
}
