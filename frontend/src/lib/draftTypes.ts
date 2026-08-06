// Draft strategies: study events materialized as pseudo-trades. A draft is
// NOT a backtest — no fills, slippage, sizing or commission — and the types
// carry the guardrail stats (skips, overlaps) so the pages can say so.

export interface DraftChecklist {
  split_half?: boolean;
  monthly_consistency?: boolean;
  engine_ab?: boolean;
}

export interface DraftMonth {
  n: number;
  sum_r: number;
}

export interface DraftSummary {
  n_events: number;
  n_trades: number;
  n_skipped: number;
  days_no_data: number;
  n_sessions: number;
  first_day: string | null;
  last_day: string | null;
  targets: number;
  stops: number;
  time_exits: number;
  win_rate: number | null;
  avg_r: number | null;
  total_r: number | null;
  total_points: number;
  // Every exit reason the source actually used. Race drafts fill the
  // target/stop/time triple above; passthrough drafts bring their own
  // vocabulary (engine trails, a transcript's spoken exits) and the triple
  // reads as three zeros for them.
  by_reason: Record<string, number>;
  overlapping_trades: number;
  by_month: Record<string, DraftMonth>;
}

export interface DraftListItem {
  slug: string;
  name: string;
  hypothesis: string;
  source_doc: string;
  direction: string;
  symbol: string;
  // null until the draft has been materialized once (opening it does that).
  summary: DraftSummary | null;
}

export interface DraftTrade {
  trade_no: number;
  day: string;
  direction: "Long" | "Short";
  entry_ts_utc: string;
  exit_ts_utc: string;
  avg_entry: number;
  avg_exit: number;
  stop_price: number;
  target_price: number;
  // Race drafts emit target/stop/time; passthrough drafts pass their source's
  // own vocabulary through untouched, so this is not a closed set.
  exit_reason: string;
  points: number;
  r_multiple: number;
  net_pnl: number;
  duration_s: number;
  band_width_ticks: number;
  is_rth: boolean;
  overlapped: boolean;
  // Optional columns a passthrough source may carry along (drafts.py copies
  // any extra parquet column verbatim).
  strategy?: string;
  entry_source?: string;
  stated_result?: string;
}

export interface DraftDetailData {
  // Every cached session in the draft's span, first→last trade day. The tape
  // renders all of them (not just trade days) so the weekly anchor reads
  // continuously and sessions sit adjacent without silent multi-day jumps.
  days: string[];
  slug: string;
  name: string;
  hypothesis: string;
  source_doc: string;
  notes: string;
  direction: string;
  symbol: string;
  race_sigma: number;
  horizon_min: number;
  query: string;
  checklist: DraftChecklist;
  run_id: string;
  summary: DraftSummary;
  trades: DraftTrade[];
}
