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
}

import type { Num } from "./format";

export interface Metrics {
  trades: number;
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
  trade_key: string;
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
  setups?: string[]; // attached by GET /trades for table badges
}

// A master-list entry (setup or confluence): the canonical name + its editable
// description, independent of any trade. Managed from the Setups/Confluences tabs.
export interface TaxonomyItem {
  name: string;
  description: string;
}

export interface ConfluenceStat {
  name: string;
  trades: number;
  win_rate: number;
  net_pnl: number;
}

export interface SetupStat {
  name: string;
  metrics: Metrics;
  confluences: ConfluenceStat[];
}

// Confluences tab: the inverse pivot of SetupStat, plus lift vs baseline.
export interface Lift {
  win_rate_delta: number;
  expectancy_delta: Num;
  without_win_rate: number;
  without_expectancy: Num;
  without_trades: number;
}

export interface ConfluenceLeaderStat {
  name: string;
  metrics: Metrics;
  lift: Lift;
  setups: ConfluenceStat[]; // same {name, trades, win_rate, net_pnl} breakdown shape
}

export interface StackBucket {
  count: number;
  label: string;
  trades: number;
  win_rate: number;
  expectancy: Num;
  net_pnl: number;
}

export interface ConfluencesResponse {
  baseline: Metrics;
  confluences: ConfluenceLeaderStat[];
  stacking: StackBucket[];
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
  setups?: string[];
  confluences?: string[];
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
