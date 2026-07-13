export interface Meta {
  has_data: boolean;
  databento_available: boolean;
  ai_available: boolean;
  models: string[];
  display_tzs: string[];
  default_tz: string;
}

export interface Filters {
  instruments: string[];
  accounts: string[];
  date_min: string | null;
  date_max: string | null;
  tags: string[];
  setups: string[];
  confluences: string[];
  modes: string[]; // session modes present in the DB: live | replay | backtest
  models: ModelOption[];
}

export interface ModelOption {
  id: number;
  name: string;
}

import type { Num } from "./format";

export interface Metrics {
  trades: number;
  longs: number;
  shorts: number;
  net_pnl: Num;
  gross_profit: Num;
  gross_loss: Num;
  profit_factor: Num;
  win_rate: number;
  wins: number;
  losses: number;
  avg_win: Num;
  avg_loss: Num;
  win_loss_ratio: Num;
  expectancy: Num;
  best_trade: Num;
  worst_trade: Num;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  max_drawdown: Num;
  recovery_factor: Num;
  sharpe: Num;
  sortino: Num;
  total_days: number;
  profit_days: number;
  loss_days: number;
  winning_days_pct: number;
  avg_trade_length_s: Num;
  total_commission: Num;
  view: string;
}

export interface SideStats {
  trades: number;
  net_pnl: number;
  win_rate: number;
}

export interface SummaryExtras {
  total_contracts: number;
  long: SideStats;
  short: SideStats;
  avg_mfe_usd: number | null;
  avg_mae_usd: number | null;
  avg_exit_efficiency: number | null;
  avg_atr_pts: number | null;
  avg_atr_usd: number | null;
  window_start: string | null;
  window_end: string | null;
}

export interface EquityPoint {
  ts: string;
  trade_no: number;
  pnl: number;
  equity: number;
  drawdown: number;
}

export interface DailyPnlPoint {
  date: string;
  net_pnl: number;
  trades: number;
}

export interface TradeRow {
  trade_no: number;
  trade_key: string; // view-local key (logical hash, or the ATAS lot's own)
  // The key every journal entry binds to. Identical to trade_key in logical
  // view; in ATAS view it points at the logical trade that absorbed this lot.
  logical_trade_key: string;
  source_file: string;
  instrument: string;
  direction: string;
  max_contracts: number;
  entry_ts_local: string;
  exit_ts_local: string;
  entry_ts_utc: string;
  exit_ts_utc: string;
  duration_s: number;
  avg_entry: Num;
  avg_exit: Num;
  net_pnl: number;
  comment: string;
  model_id: number | null; // effective model (own binding, or a backtest session's)
  session_mode: SessionMode; // the owning session's mode; drives the detail layout
  setups?: string[]; // attached by GET /trades for table badges
}

// --- Models: the live taxonomy, replacing setups + confluences -----------
export interface ModelRule {
  id: number;
  model_id: number;
  label: string;
  sort_order: number;
  active?: boolean;
}

export interface Model {
  id: number;
  name: string;
  description: string;
  archived: boolean;
  folder: string | null; // export drop-box slug under data/imports/backtest/
  target_sample: number | null; // backtest sample-size goal
  rules: ModelRule[];
}

export interface ComplianceBucket {
  label: "followed" | "partial" | "broke";
  trades: number;
  win_rate: number;
  expectancy: Num;
  net_pnl: number;
}

export interface Compliance {
  rules: number;
  buckets: ComplianceBucket[];
  unscored: number; // assigned to the model but never checked against its rules
}

export interface RuleStat {
  id: number;
  label: string;
  met_trades: number;
  met_expectancy: Num;
  met_win_rate: number;
  met_net_pnl: number;
  missed_trades: number;
  missed_expectancy: Num;
  missed_win_rate: number;
  missed_net_pnl: number;
}

export interface ModelStat {
  id: number;
  name: string;
  description: string;
  archived: boolean;
  metrics: Metrics;
  compliance: Compliance;
  rules: RuleStat[];
}

export interface ModelStatsResponse {
  models: ModelStat[];
  unassigned: Metrics; // off-model trades; models + unassigned == total
  total: Metrics;
}

// --- Sessions: one per ATAS export (source_file) --------------------------
export type SessionMode = "live" | "replay" | "backtest";

export interface Session {
  source_file: string;
  mode: SessionMode;
  account: string | null;
  model_id: number | null; // bound session-wide when mode is 'backtest'
  model_name: string | null;
  archived: boolean;
  note: string; // session-level journal (hypothesis, conditions, verdict)
  updated_at: string | null;
}

// --- Backtests: per-model monitoring + the auto-import watcher feed -------
export interface SlimMetrics {
  trades: number;
  net_pnl?: Num;
  win_rate?: number;
  expectancy?: Num;
  profit_factor?: Num;
  avg_win?: Num;
  avg_loss?: Num;
  max_drawdown?: Num;
}

export interface BacktestModelCard {
  id: number;
  name: string;
  description: string;
  archived: boolean;
  folder: string | null;
  target_sample: number | null;
  sessions: number;
  last_import: string | null;
  metrics: SlimMetrics;
}

export interface BacktestSessionRow {
  source_file: string;
  archived: boolean;
  note: string;
  imported_at: string | null;
  first_day: string | null;
  last_day: string | null;
  metrics: SlimMetrics;
}

export interface BacktestDetail {
  model: {
    id: number;
    name: string;
    description: string;
    archived: boolean;
    folder: string | null;
    target_sample: number | null;
  };
  metrics: Metrics;
  equity: EquityPoint[];
  distribution: number[];
  comparison: Record<SessionMode, SlimMetrics>;
  sessions: BacktestSessionRow[];
}

export interface ImportFeedEvent {
  seq: number;
  ts: string;
  kind: "imported" | "unknown_folder" | "error";
  file: string;
  folder?: string;
  mode?: SessionMode;
  model_id?: number | null;
  model_name?: string | null;
  counts?: { executions: number; journal: number; statistics: number };
  message?: string;
}

export interface ImportFeed {
  seq: number;
  last_scan_at: string | null;
  interval_s: number;
  events: ImportFeedEvent[];
}

export interface EdgeRow {
  bucket: string;
  trades: number;
  net_pnl: number;
  win_rate: number;
  expectancy: number;
}

export interface Edges {
  by_weekday: EdgeRow[];
  by_hold_time: EdgeRow[];
  by_direction: EdgeRow[];
  by_hour_kl: EdgeRow[];
  by_hour_et: EdgeRow[];
}

export interface Note {
  note: string;
  tags: string[];
  // Per-trade only; day notes omit these (optional keeps DayJournalForm valid).
  // setups/confluences are the archived era's badges — read-only in the UI now.
  setups?: string[];
  confluences?: string[];
  model_id?: number | null; // null = off-model
  rules_met?: number[]; // ids of the model's rules this trade satisfied
}

export interface VideoBookmark {
  id: number;
  source_file: string;
  offset_s: number;
  label: string;
  trade_key: string | null; // bound trade; null = free-form bookmark
  created_at: string;
  origin: "manual" | "synced"; // hand-placed/anchor vs auto-synced from trade ts
}

export interface TradeVideoBookmarkStatus {
  source_file: string;
  offset_s: number;
  label: string;
  origin: "manual" | "synced";
}

export interface TradeVideoStatus {
  source_file: string;
  has_video: boolean;
  exists: boolean;
  playable: boolean;
  bookmark: TradeVideoBookmarkStatus | null;
}

export interface TradeVideoStatusResponse {
  statuses: Record<string, TradeVideoStatus>;
}

export interface SyncResult {
  created: number;
  skipped_existing: number;
  skipped_out_of_range: number;
  pruned_orphans: number;
  anchor_trade_key: string;
}

export interface VideoInfo {
  path: string;
  duration_s: number | null;
  exists: boolean; // file present at the linked path
  playable: boolean; // extension a browser <video> can play
}

export interface ScanLinked {
  source_file: string;
  day: string; // ISO date of the replayed session
  attempt_no: number; // parsed from the export filename
  filename: string; // the recording that matched, e.g. 13-JUN-2026-01.mp4
}

export interface ScanResult {
  linked: ScanLinked[]; // attempts newly auto-linked this scan
  count: number;
}

export interface VideoData {
  video: VideoInfo | null; // null = no recording linked to this attempt
  bookmarks: VideoBookmark[];
}

export interface Reconcile {
  logical_net_pnl: number;
  atas_journal_pnl: number;
  difference: number;
  logical_trades: number;
  atas_rows: number;
}

export interface StatisticsDetail {
  pivot: { scopes: string[]; rows: Record<string, string>[] };
  ours: Metrics;
  reconcile: Reconcile;
}
