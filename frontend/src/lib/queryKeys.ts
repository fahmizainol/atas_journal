// Scope-keyed query keys: TanStack Query refetches whenever the filter scope
// changes. The scope object is serialized into the key.

export interface FilterScope {
  view: string;
  instruments: string[];
  accounts: string[];
  start: string | null;
  end: string | null;
  tags: string[];
  tz: string;
  // A single session mode, defaulting to "live". Blending 1,246 replay trades
  // with 203 live ones is what made Overview meaningless; one mode at a time.
  mode: string;
  models: string[]; // model ids, as strings (they live in the query string)
  includeArchived: boolean;
}

export function scopeParams(scope: FilterScope): Record<string, unknown> {
  return {
    view: scope.view,
    instruments: scope.instruments,
    accounts: scope.accounts,
    start: scope.start,
    end: scope.end,
    tags: scope.tags,
    tz: scope.tz,
    // "all" is the UI's way of saying "no mode filter"; the API reads an absent
    // param the same way, and toQuery drops undefined.
    modes: scope.mode === "all" ? undefined : scope.mode,
    models: scope.models,
    include_archived: scope.includeArchived ? 1 : undefined,
  };
}

export const qk = {
  meta: ["meta"] as const,
  filters: (scope: FilterScope) => ["filters", scope.view, scope.tz] as const,
  metrics: (scope: FilterScope) => ["metrics", scope] as const,
  summaryExtras: (scope: FilterScope) => ["summary-extras", scope] as const,
  equityCurve: (scope: FilterScope) => ["equity-curve", scope] as const,
  dailyPnl: (scope: FilterScope) => ["daily-pnl", scope] as const,
  distribution: (scope: FilterScope) => ["distribution", scope] as const,
  edges: (scope: FilterScope) => ["edges", scope] as const,
  modelList: ["model-list"] as const,
  modelStats: (scope: FilterScope) => ["model-stats", scope] as const,
  backtestsOverview: (tz: string) => ["backtests-overview", tz] as const,
  backtestDetail: (id: number, tz: string) => ["backtest-detail", id, tz] as const,
  importFeed: ["import-feed"] as const,
  sessions: ["sessions"] as const,
  trades: (scope: FilterScope) => ["trades", scope] as const,
  trade: (scope: FilterScope, no: number) => ["trade", no, scope] as const,
  // Scope-free: a trade_key is a content hash, so the same key means the same
  // trade under every filter, and the review panel in /charts shares this cache
  // with the Trades page instead of refetching per view.
  tradeContext: (keys: string[]) => ["trade-context", [...keys].sort().join(",")] as const,
  // The bars half does take a scope — it needs the journal row to know the
  // instrument and the fills, and that lookup is scoped.
  tradeContextChart: (scope: FilterScope, key: string) =>
    ["trade-context-chart", key, scope] as const,
  note: (tradeKey: string) => ["note", tradeKey] as const,
  dayNote: (date: string) => ["day-note", date] as const,
  dayNotesAll: ["day-note", "all"] as const,
  excursion: (no: number) => ["excursion", no] as const,
  bars: (params: Record<string, unknown>) => ["bars", params] as const,
  dayChart: (scope: FilterScope, date: string, tf: string, sourceFile: string | null) =>
    ["day-chart", date, tf, sourceFile, scope] as const,
  calendar: (scope: FilterScope) => ["calendar", scope] as const,
  day: (scope: FilterScope, date: string, sourceFile: string | null) =>
    ["day", date, sourceFile, scope] as const,
  statisticsFiles: ["statistics-files"] as const,
  statistics: (file: string) => ["statistics", file] as const,
  aiTrade: (tradeKey: string) => ["ai-trade", tradeKey] as const,
  aiPeriod: (scope: FilterScope) => ["ai-period", scope] as const,
  settings: (key: string) => ["settings", key] as const,
  researchList: ["research-list"] as const,
  researchDoc: (slug: string) => ["research-doc", slug] as const,
  recallDeck: ["recall-deck"] as const,
  /** The flipped card's answer. Named here because a review saved from the card
   *  has to invalidate it, and that write lives in `useSaveTradeReview`. */
  recallBack: (tradeKey: string) => ["recall-back", tradeKey] as const,
};
