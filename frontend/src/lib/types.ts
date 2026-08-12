export interface Meta {
  has_data: boolean;
  databento_available: boolean;
  /** Whether any ticks are cached — what the chart components gate on. An API
   *  key and a drawable chart are different questions since the charts stopped
   *  fetching. */
  chart_ticks_available: boolean;
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

// The same cuts over one simulated run — with the two things the real book can't
// have: avg_r (a fixed-stop engine measures every trade in units of its own risk)
// and the cuts only the engine knows (which rule closed the trade, how wide the
// band was, which gate vetoed it).
export interface RunEdgeRow extends EdgeRow {
  avg_r: number;
}

export interface RunCut {
  name: string;
  label: string;
  // Was the bucket knowable at entry? A session block was; an exit reason was not.
  // Only a knowable cut is a filter you could have traded, so only a knowable cut
  // is scored — an outcome cut separates P&L perfectly by construction.
  knowable: boolean;
  // How often shuffled P&L separates the buckets this well. null = not scored.
  luck: number | null;
  holds: boolean;
  rows: RunEdgeRow[];
}

// One confluence's independent veto stats over the ghost ledger. Unlike the
// by_gate cut (which buckets each entry under the one gate that vetoed it alone,
// with stacked vetoes pooled), every gate that would have vetoed a trade is scored
// on it here — so rows overlap and `trades` sums to more than the vetoed total.
// `unique` is how many a gate caught alone (nothing else stood between the entry
// and a real fill) — the same count as that gate's bucket in the by_gate cut.
export interface ConfluenceRow extends RunEdgeRow {
  bucket: string; // the confluence / gate name
  unique: number;
}

// One outcome group's MFE/MAE profile (bucket = "All" | "Winners" | "Losers").
// mfe_r/mae_r are the median peak/trough in R; capture is the median fraction of
// the peak the exit actually booked (null when no trade in the group was ever in
// profit); reach_1r/heat_1r are the shares that reached +1R in favor / -1R against.
export interface ExcursionRow {
  bucket: string;
  trades: number;
  mfe_r: number;
  mae_r: number;
  capture: number | null;
  // Share ever in profit (mfe_r > 0) — the weaker sibling of reach_1r. On the
  // Losers row it keeps reach_1r honest: 0% reached +1R can sit next to ~100%
  // ever green, which is a give-back, not a never-worked.
  ever_green: number;
  reach_1r: number;
  heat_1r: number;
}

// One peak-MFE bucket of the losers (bucket = "Never green" … "1R+"). Answers
// "how far did the losers ever run in favor before turning" — never-worked at the
// bottom, give-backs the exit might have caught higher up. net_pnl is what that
// slice of losers cost.
export interface LoserGivebackBin {
  bucket: string;
  trades: number;
  share: number;
  net_pnl: number;
}

// The losers split by peak MFE, plus the headline share that were ever green.
// Empty buckets / zero losers on a run with no losers; absent on runs predating
// mfe_r (re-run to populate, same as the excursion profile).
export interface LoserGiveback {
  losers: number;
  ever_green: number;
  buckets: LoserGivebackBin[];
}

// One heat bucket of the winners (bucket = "No heat" … "1R+"), the mirror of
// LoserGivebackBin. Answers "how far underwater did the winners go before turning" —
// clean entries at the bottom, near-stopped survivors higher up. net_pnl is what
// that slice of winners made.
export interface WinnerHeatBin {
  bucket: string;
  trades: number;
  share: number;
  net_pnl: number;
}

// The winners split by peak heat (−mae_r), plus the headline share that took any
// heat at all. Empty buckets / zero winners on a run with no winners; absent on
// runs predating mae_r (re-run to populate).
export interface WinnerHeat {
  winners: number;
  took_heat: number;
  buckets: WinnerHeatBin[];
}

// One recovery-time bucket of the underwater winners (bucket = "< 30s" … "5m+") —
// how long they took to climb from their deepest heat back to breakeven. net_pnl is
// the green riding on the trades held through the red.
export interface WinnerRecoveryBin {
  bucket: string;
  trades: number;
  share: number;
  net_pnl: number;
}

// The underwater winners split by recovery time, plus the median. Needs the engine's
// recovery_s — absent on runs predating it (re-run to populate). Zero winners / no
// underwater winners -> empty buckets.
export interface WinnerRecovery {
  winners: number;
  median_recovery_s: number;
  buckets: WinnerRecoveryBin[];
}

// One collapse-time bucket of the green losers (bucket = "< 30s" … "5m+") — how long
// they held their peak profit before collapsing back through breakeven into the
// loss. The mirror of WinnerRecoveryBin. net_pnl is the loss riding on the slow ones.
export interface LoserCollapseBin {
  bucket: string;
  trades: number;
  share: number;
  net_pnl: number;
}

// The green losers split by collapse time, plus the median. Needs the engine's
// giveback_s — absent on runs predating it (re-run to populate). Zero losers / no
// green losers -> empty buckets.
export interface LoserCollapse {
  losers: number;
  median_giveback_s: number;
  buckets: LoserCollapseBin[];
}

// One dwell bucket (bucket = "< 1m" … "10m+") of EVERY trade split by total time
// underwater — win_rate is the fraction of that bucket that ended green, net_pnl the
// money in it. A win_rate that falls as the bucket grows is the "sitting red predicts
// the loss" signal.
export interface UnderwaterBin {
  bucket: string;
  trades: number;
  win_rate: number;
  net_pnl: number;
}

// Win rate as a function of time underwater, over every trade (not conditioned on
// outcome). never_underwater is the clean-entry ceiling to read the buckets against.
// Needs the engine's underwater_s — absent on runs predating it (re-run to populate).
export interface UnderwaterSurvival {
  trades: number;
  overall_win_rate: number;
  median_underwater_s: number;
  never_underwater: UnderwaterBin;
  buckets: UnderwaterBin[];
}

// One side (bucket = "Winners" | "Losers") of the win/loss distribution the
// bucket cuts blend together. best_r/best_pnl are the single most extreme trade on
// that side; top3_share is the fraction of the side's P&L its three most extreme
// trades carried (near 1 = a few outliers are the whole side); med_hold_s is the
// median hold in seconds.
export interface WinLossSide {
  bucket: string;
  trades: number;
  share: number;
  net_pnl: number;
  avg_pnl: number;
  avg_r: number;
  med_r: number;
  std_r: number;
  best_r: number;
  best_pnl: number;
  top3_share: number | null;
  med_hold_s: number;
}

// Book-level payoff geometry and sequence. profit_factor/payoff_ratio may be "inf"
// (a side was empty); max_drawdown is the deepest peak-to-trough of cumulative net.
export interface WinLossSummary {
  profit_factor: Num;
  payoff_ratio: Num;
  expectancy_r: number;
  max_win_streak: number;
  max_loss_streak: number;
  max_drawdown: number;
}

// Empty object ({}) for a run predating r_multiple — the panel hides the table.
export interface WinLossProfile {
  sides?: WinLossSide[];
  summary?: WinLossSummary;
}

// One R-bucket of the outcome distribution (bucket = "≤ -1R" … "> 3R"). The stop
// wall sits in the first bucket, the target spike in whichever holds the target R.
export interface RHistBin {
  bucket: string;
  trades: number;
  share: number;
  net_pnl: number;
}

// One entry-knowable feature's winner-vs-loser contrast. auc is P(a random winner's
// value > a random loser's) — 0.5 = no separation; luck is the permutation floor
// (null = not scored), holds = clears the multiple-testing bar.
export interface DiscriminatorRow {
  feature: string;
  unit: string;
  win_mean: number;
  loss_mean: number;
  auc: number;
  luck: number | null;
  holds: boolean;
}

// The whole discriminator block. Zero-variance features (a fixed stop, fixed size)
// are dropped server-side, so rows may be empty even on a full run.
export interface Discriminator {
  rows: DiscriminatorRow[];
  luck_bar: number;
  n_win: number;
  n_loss: number;
}

// One session's net, in booked order — the strip that shows day-level clustering.
export interface DailyDay {
  date: string;
  net: number;
}

// The book rolled up to sessions. top3_share is the fraction of net the three best
// days carried (null over an unprofitable book); worst_day is the number a dollar
// drawdown is built from.
export interface DailyConcentration {
  days: number;
  green_share: number;
  avg_day: number;
  med_day: number;
  best_day: number;
  best_date: string;
  worst_day: number;
  worst_date: string;
  top3_share: number | null;
  series: DailyDay[];
}

export interface RunEdgeScope {
  trades: number;
  net_pnl: number;
  // The MFE/MAE breakdown of this book. Empty for a run that predates the engine's
  // excursion columns — the panel reads that as "re-run to populate".
  excursions: ExcursionRow[];
  // The winners-vs-losers distribution: tails, payoff, hold split, streaks, drawdown.
  win_loss?: WinLossProfile;
  // The R-outcome histogram, the entry discriminator, and the daily concentration —
  // the deeper winner/loser reads. Empty/absent on runs predating r_multiple.
  r_hist?: RHistBin[];
  discriminator?: Discriminator;
  daily?: DailyConcentration;
  // The losers split by how far they ever ran in favor. Absent on runs predating mfe_r.
  loser_giveback?: LoserGiveback;
  // Of those green losers, how fast they collapsed back to breakeven. Absent pre-giveback_s.
  loser_collapse?: LoserCollapse;
  // The mirror: winners split by the heat they took before working. Absent pre-mae_r.
  winner_heat?: WinnerHeat;
  // Of those underwater winners, how fast they recovered. Absent pre-recovery_s.
  winner_recovery?: WinnerRecovery;
  // Win rate by total time underwater, over every trade. Absent pre-underwater_s.
  underwater_survival?: UnderwaterSurvival;
  cuts: RunCut[];
  // Present only on the vetoed scope; empty on runs with no gates (or older runs
  // whose ledger predates the full gate set, where it falls back to first-match).
  confluences?: ConfluenceRow[];
}

// traded = what the run took; vetoed = the ghost trades its gates cut; all = the
// run the gates were never in. null when that book is empty.
export type RunEdgeScopeName = "traded" | "vetoed" | "all";

export interface RunEdges {
  run_id: string;
  permutations: number;
  luck_bar: number;
  scopes: Record<RunEdgeScopeName, RunEdgeScope | null>;
  reference: {
    run_id: string;
    label: string;
    is_baseline: boolean;
    start: string;
    end: string;
    same_window: boolean;
    scopes: Record<RunEdgeScopeName, RunEdgeScope | null>;
  } | null;
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
