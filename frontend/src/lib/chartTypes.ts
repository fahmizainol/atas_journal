export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface VwapPoint {
  time: number;
  upper: number;
  middle: number;
  lower: number;
}

export interface ChartMarker {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: "circle" | "square" | "arrowUp" | "arrowDown";
  color: string;
  text?: string;
}

export interface PriceLineSpec {
  price: number;
  color: string;
  title: string;
}

export interface TradeRect {
  entry_time: number;
  exit_time: number;
  entry_price: number;
  exit_price: number;
  net_pnl: number;
  profitable: boolean;
}

export interface Excursion {
  available: boolean;
  has_data?: boolean;
  mfe_usd?: number;
  mae_usd?: number;
  exit_efficiency?: number | null;
}

export interface TradeChartData {
  available: boolean;
  bars?: Bar[];
  vwap?: VwapPoint[];
  markers?: ChartMarker[];
  price_lines?: PriceLineSpec[];
  levels?: PriceLineSpec[];
  trade_rect?: TradeRect | null;
  excursion?: Omit<Excursion, "available">;
}

export interface DayChartData {
  available: boolean;
  instrument?: string;
  bars?: Bar[];
  vwap?: VwapPoint[];
  markers?: ChartMarker[];
  levels?: PriceLineSpec[];
  trades?: TradeRect[];
}
