// Strategies: the *automated* strategy research workbench (journal.sim).
// A strategy is a coded trading idea; a run is an immutable (config + engine
// version) artifact. Not the manual "Models" feature and not the `backtest`
// session mode (a manually replayed ATAS session). See src/journal/sim/.

export interface SimConfig {
  instrument: string;
  contract: string;
  start_date: string;
  end_date: string;
  ticks_per_bar: number;
  entry_open: string;
  entry_close: string;
  flat_by: string;
  acceptance_min_ticks: number;
  acceptance_require_green: boolean;
  acceptance_cap_at_dev2: boolean;
  entry_variant: "A" | "B";
  entry_stop_offset_ticks: number;
  /** Variant A: rest the limit this many ticks in front of dev1 (above it on a long,
   * below it on a short) so the pullback fills before it reaches the band. 0 = on dev1.
   * The server rejects a value above acceptance_min_ticks. */
  entry_limit_offset_ticks: number;
  stop_ticks: number;
  target: string;
  target_rr: number | null;
  /** N consecutive bar closes back below the developing VAH exit at market. 0 = off. */
  exit_below_vah_bars: number;
  /** How far behind the best price seen the stop follows, and the trail's master
   * switch. 0 = off (fixed stop). The first ratchet lands on breakeven. */
  trail_stop_ticks: number;
  /** The grid the trailed stop rests on, measured from its first level: trail 50 /
   * step 25 scratches from +50, one step up from +75. 0 = one click per distance. */
  trail_step_ticks: number;
  /** Where the trail's first click lands beyond the entry, so a scratch clears the
   * round trip's commission instead of booking it. 0 = on the entry (gross breakeven). */
  trail_breakeven_ticks: number;
  /** Take the first click and no other: a breakeven stop, not a trail. The step is
   * then irrelevant. */
  trail_breakeven_only: boolean;
  min_band_width_ticks: number;
  invalidate_below_mid_bars: number;
  rearm_after_exit: boolean;
  contracts: number;
  commission_per_side: number;
  confluences: Confluences;
}

/** Veto-only gates. A section is inert with `enabled: false`, but still part of
 * the run's identity — flipping the flag is a different run. */
export interface Confluences {
  /** Entry must fill above the developing value-area high. */
  volume_profile?: { enabled?: boolean; min_ticks_above_vah?: number };
}

// --- the run form's blueprint -------------------------------------------------
// Served by GET /strategies/{slug} from journal.sim.schema, which is also what
// coerces and validates a posted config. The form renders itself from these
// rather than hard-coding the knob list, so a knob can't reach the engine without
// reaching the UI — and the bounds the form enforces are the same objects the
// server enforces.

export interface ConfigField {
  name: string;
  type: "int" | "float" | "bool" | "enum" | "date" | "time" | "str";
  group: string;
  label: string;
  default: unknown;
  help?: string;
  unit?: string;
  min?: number;
  max?: number;
  nullable?: boolean;
  /** 0 is the engine's sentinel for "off" on this knob. The form renders a
   * checkbox over it and writes 0 when unticked — the wire format keeps the
   * sentinel, so run identity is unchanged. */
  zero_means_off?: boolean;
  /** What to fill in when the knob is switched on (or its dependency enables it). */
  on_default?: number;
  choices?: { value: string; label: string }[];
  /** The engine only reads this knob when `field` holds `value`. The form disables
   * it otherwise — but still ships it, because the identity hash needs the whole
   * config and the engine ignores the value anyway. */
  depends_on?: { field: string; value: unknown };
}

export interface ConfigGroup {
  key: string;
  title: string;
  collapsed: boolean;
}

export interface ConfigSchema {
  groups: ConfigGroup[];
  fields: ConfigField[];
  /** One section per gate this strategy supports; each gate declares its own knobs. */
  confluences: { name: string; fields: ConfigField[] }[];
}

export interface SimMetrics {
  trades: number;
  net_pnl?: number;
  win_rate?: number;
  profit_factor?: number | "inf";
  expectancy?: number;
  max_drawdown?: number;
  avg_win?: number;
  avg_loss?: number;
  r_mean?: number;
  r_median?: number;
  r_best?: number;
  band_width_median_ticks?: number;
  band_width_min_ticks?: number;
  exit_reasons?: Record<string, number>;
  vetoed?: { count: number; net_pnl: number; by_gate: Record<string, number> };
}

export interface SimTrade {
  trade_no: number;
  session: string;
  direction: string;
  entry_ts_local: string;
  exit_ts_local: string;
  avg_entry: number;
  avg_exit: number;
  /** The stop as entered — what was risked, and what r_multiple is measured against. */
  stop_price: number;
  /** Where the trail had ratcheted the stop by the exit. Equal to stop_price on an
   * untrailed run; absent on runs made before trail_step_ticks existed. */
  final_stop_price?: number;
  target_price: number;
  /** "vah" = price was re-accepted back inside the value area (exit_below_vah_bars).
   * "trail" = stopped out on a ratcheted stop (breakeven or better), not the initial one. */
  exit_reason: "target" | "stop" | "time" | "vah" | "trail";
  points: number;
  r_multiple: number;
  band_width_ticks: number;
  duration_s: number;
  gross_pnl: number;
  commission: number;
  net_pnl: number;
}

export interface VetoedTrade extends SimTrade {
  gate: string;
}

export interface RunMeta {
  label: string;
  notes: string;
}

export interface RunState {
  status: "running" | "done" | "error";
  sessions_done: number;
  sessions_total: number;
  error: string | null;
  created_at: string;
  engine_version: string;
}

export interface StrategyRun {
  run_id: string;
  config: SimConfig;
  metrics: SimMetrics;
  trades: number;
  vetoed: number;
  meta: RunMeta;
  state: RunState;
}

export interface StrategySummary {
  slug: string;
  name: string;
  description: string;
  version: string;
  confluences: string[];
  /** Which tick segments the idea reads. "globex" also pulls the overnight
   * (18:00 ET → 09:30) segment, which anchors its VWAP and costs a second
   * Databento fetch per uncached session. */
  session: "rth" | "globex";
  run_count: number;
  baseline_run_id: string | null;
  baseline_metrics: SimMetrics | null;
}

export interface StrategyDetail extends StrategySummary {
  default_config: SimConfig;
  config_schema: ConfigSchema;
  runs: StrategyRun[];
}

export interface RunDetail {
  run_id: string;
  config: SimConfig;
  metrics: SimMetrics;
  trades: SimTrade[];
  vetoed_trades: VetoedTrade[];
  session_days: string[];
  meta: RunMeta;
  state: RunState;
}

export interface Preflight {
  sessions_total: number;
  uncached_sessions: number;
  uncached_days: string[];
  est_cost_usd: number | null;
  run_id: string;
  exists: boolean;
}

/** Baseline deltas are only honest when the runs saw the same data through the
 * same code: identical window AND identical engine version. */
export function comparableToBaseline(run: StrategyRun, base: StrategyRun): boolean {
  return (
    run.state.status === "done" &&
    base.state.status === "done" &&
    run.config.start_date === base.config.start_date &&
    run.config.end_date === base.config.end_date &&
    run.state.engine_version === base.state.engine_version
  );
}

/** Below this many trades a run's stats are noise; the UI badges it. */
export const THIN_SAMPLE = 30;
