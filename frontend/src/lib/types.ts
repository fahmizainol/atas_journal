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
  date_min: string | null;
  date_max: string | null;
  tags: string[];
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
}
