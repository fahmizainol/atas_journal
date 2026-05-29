// Scope-keyed query keys: TanStack Query refetches whenever the filter scope
// changes. The scope object is serialized into the key.

export interface FilterScope {
  view: string;
  instruments: string[];
  start: string | null;
  end: string | null;
  tags: string[];
  tz: string;
}

export function scopeParams(scope: FilterScope): Record<string, unknown> {
  return {
    view: scope.view,
    instruments: scope.instruments,
    start: scope.start,
    end: scope.end,
    tags: scope.tags,
    tz: scope.tz,
  };
}

export const qk = {
  meta: ["meta"] as const,
  filters: (scope: FilterScope) => ["filters", scope.view, scope.tz] as const,
  metrics: (scope: FilterScope) => ["metrics", scope] as const,
  equityCurve: (scope: FilterScope) => ["equity-curve", scope] as const,
  dailyPnl: (scope: FilterScope) => ["daily-pnl", scope] as const,
  distribution: (scope: FilterScope) => ["distribution", scope] as const,
  edges: (scope: FilterScope) => ["edges", scope] as const,
  trades: (scope: FilterScope) => ["trades", scope] as const,
  trade: (scope: FilterScope, no: number) => ["trade", no, scope] as const,
  note: (tradeKey: string) => ["note", tradeKey] as const,
  excursion: (no: number) => ["excursion", no] as const,
  bars: (params: Record<string, unknown>) => ["bars", params] as const,
  tradeChart: (scope: FilterScope, no: number, tf: string) =>
    ["trade-chart", no, tf, scope] as const,
  dayChart: (scope: FilterScope, date: string, tf: string) =>
    ["day-chart", date, tf, scope] as const,
  calendar: (scope: FilterScope) => ["calendar", scope] as const,
  day: (scope: FilterScope, date: string) => ["day", date, scope] as const,
  statisticsFiles: ["statistics-files"] as const,
  statistics: (file: string) => ["statistics", file] as const,
  aiTrade: (tradeKey: string) => ["ai-trade", tradeKey] as const,
  aiPeriod: (scope: FilterScope) => ["ai-period", scope] as const,
  settings: (key: string) => ["settings", key] as const,
};
