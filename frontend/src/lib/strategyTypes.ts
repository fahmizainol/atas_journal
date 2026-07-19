// Strategies: the *automated* strategy research workbench (journal.sim).
// A strategy is a coded trading idea; a run is an immutable (config + engine
// version) artifact. Not the manual "Models" feature and not the `backtest`
// session mode (a manually replayed ATAS session). See src/journal/sim/.

/** The wire shape of a run's config. One strategy family, one config class on
 * the server (rules.SimConfig for the bounces, rules.FadeConfig for the fades)
 * — this type is their union, so every family-specific knob is optional and
 * only the knobs every family shares are required. The run form never reads
 * these fields by name anyway: it renders from config_schema. */
export interface SimConfig {
  instrument: string;
  contract: string;
  start_date: string;
  end_date: string;
  ticks_per_bar: number;
  entry_open: string;
  entry_close: string;
  flat_by: string;
  // --- bounce only: the acceptance candle arms the setup ---
  acceptance_min_ticks?: number;
  acceptance_require_green?: boolean;
  acceptance_cap_at_dev2?: boolean;
  // --- fade only: the stretch past dev1 arms the setup ---
  /** Which side of dev1 the arming stretch runs to. "beyond": the overextension
   * out of the channel, sold on the return to the band. "inside": the band
   * broken back into the channel, sold on the retest of it. Only the stretch
   * flips — the trade is a fade at dev1 toward the mid either way. */
  arm_stretch_side?: "beyond" | "inside";
  /** Arms on a print more than this far past dev1 (on arm_stretch_side of it);
   * edge-triggered, so a re-arm needs price back within this distance of the
   * band first. */
  arm_extension_ticks?: number;
  /** Only arm a stretch whose approach printed at/past the VWAP mid since the
   * last fill. */
  arm_require_mid_cross?: boolean;
  /** A bar close beyond dev2 disarms the armed, unfilled setup. */
  arm_cap_at_dev2?: boolean;
  entry_variant: "A" | "B";
  entry_stop_offset_ticks: number;
  /** Variant A: rest the limit this many ticks in front of dev1 so the return
   * fills before it reaches the band. 0 = on dev1. The server rejects a value
   * above acceptance_min_ticks (bounce) / arm_extension_ticks (fade). */
  entry_limit_offset_ticks: number;
  stop_ticks: number;
  target: string;
  target_rr: number | null;
  /** Bounce: N consecutive bar closes back below the developing VAH exit at market. 0 = off. */
  exit_below_vah_bars?: number;
  /** Fade: N consecutive closes re-accepted back beyond dev1 exit at market. 0 = off. */
  invalidate_beyond_dev1_bars?: number;
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
  /** Bounce: N consecutive closes past the VWAP mid disarm the setup. */
  invalidate_below_mid_bars?: number;
  rearm_after_exit: boolean;
  /** Stand down for the session once realized net P&L is this far in the red. 0 = off. */
  daily_loss_stop?: number;
  /** Extend the daily loss stop to the open trade: flatten it at market once
   * realized net plus the open position marked to price reaches the stop. Needs
   * daily_loss_stop set. */
  daily_loss_exit_open?: boolean;
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
  /** DAILY P&L, annualized (x sqrt(252)) — not per trade. Flat weekdays inside the
   * strategy's span count as 0, so a rare setup cannot flatter itself by hiding its
   * idle weeks. This is the Sharpe you can compare to one quoted anywhere else. */
  sharpe?: number;
  /** Sharpe with only losing DAYS in the denominator: upside volatility is not
   * risk. Reads higher than Sharpe by construction. */
  sortino?: number;
  recovery_factor?: number | "inf";
  band_width_median_ticks?: number;
  band_width_min_ticks?: number;
  exit_reasons?: Record<string, number>;
  vetoed?: { count: number; net_pnl: number; by_gate: Record<string, number> };
  /** Entries the engine went blind to because a position was already open —
   * the capacity cost of the one-trade-at-a-time state machine. Absent on runs
   * that predate the missed artifact (re-run to populate). ``net_pnl`` is the
   * standalone sum of every ghost and overstates the cost: the ghosts overlap
   * real positions (up to ``max_concurrent`` deep), so you could never have held
   * them all. ``realizable_net``/``realizable_count`` keep the real book fixed and
   * count only the ghosts that fit its gaps — the money actually left on the
   * table. The realizable fields are absent on runs made before this split. */
  missed?: {
    count: number;
    net_pnl: number;
    realizable_net?: number;
    realizable_count?: number;
    max_concurrent?: number;
  };
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
   * "dev1" = the fade's mirror: re-accepted back beyond dev1 (invalidate_beyond_dev1_bars).
   * "trail" = stopped out on a ratcheted stop (breakeven or better), not the initial one.
   * "panic" = the flow-shock market exit: the tape ran panic_exit_delta contracts
   * against the trade inside the panic window.
   * "uw_stop" = the underwater tighten: still red at underwater_stop_after_s, so the
   * stop was pulled in to underwater_stop_ticks behind the entry and then hit.
   * "daily_loss" = the daily-loss flatten: realized net plus the open trade marked
   * to price reached the daily loss stop, so the position left at market
   * (daily_loss_exit_open). */
  exit_reason:
    | "target"
    | "stop"
    | "time"
    | "vah"
    | "dev1"
    | "trail"
    | "panic"
    | "uw_stop"
    | "daily_loss";
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
  /** Per-trade tags, keyed by trade_no as a string. Only tagged trades appear. */
  trade_tags: Record<string, string[]>;
  /** Every distinct tag used across this strategy's runs — autocomplete source. */
  tag_vocab: string[];
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

// ---- gate-robustness audit (GET .../runs/{id}/gate-audit) -------------------
// The scorecard from docs/research/gate-robustness.md computed for one run's
// confluence stack. Variant runs are resolved by config hash server-side; a
// variant that was never run arrives as state "missing" with its ready-to-POST
// config so the panel can launch it through the normal create-run endpoint.

export interface GateAuditVariant {
  run_id: string;
  state: "done" | "running" | "error" | "missing";
  config: SimConfig;
  label?: string;
  net?: number;
  pf?: number;
  maxdd?: number;
  trades?: number;
  net_ex_top20?: number;
}

export type GateVerdict = "real" | "partial" | "weak" | "fail" | "unscored";

export interface GateAuditGate {
  gate: string;
  params: Record<string, unknown>;
  verdict: GateVerdict;
  /** null = the test's inputs (variant runs / ghost ledger) don't exist yet. */
  checks: {
    tail: boolean | null;
    halves: boolean | null;
    plateau: boolean | null;
    selection: boolean | null;
  };
  marginal: {
    off_trades: number;
    off_net: number;
    off_pf: number;
    off_maxdd: number;
    off_sharpe: number;
    d_net: number;
    d_pf: number;
    d_maxdd: number;
    d_sharpe: number;
  } | null;
  months: { months: number; better: number; p: number } | null;
  bootstrap: { delta: number; ci_lo: number; ci_hi: number; blocks: number } | null;
  tail: { d_net_ex_top20: number; d_net_winsor_p95: number; off_ex_top20: number } | null;
  halves: { h1_label: string; h2_label: string; d_h1: number; d_h2: number } | null;
  selection: {
    n_universe: number;
    n_kept: number;
    win_pctile: number;
    mean_r_pctile: number;
  } | null;
  cohort: {
    n_ghost: number;
    ghost_net: number;
    auc: number;
    p: number;
    kept_stop: number;
    ghost_stop: number;
  } | null;
  /** Ghost dollars positive while the in-stack marginal is positive too — the
   * composition-gate mirage (gx_overhang, twice): never read ghost net as the
   * gate's value. */
  mirage: boolean;
  off: GateAuditVariant;
  neighbors: GateAuditVariant[];
}

export interface GateAudit {
  run_id: string;
  baseline: {
    trades: number;
    net: number;
    pf: number;
    maxdd: number;
    sharpe: number;
    net_ex_top20: number;
  };
  /** False when this run predates the per-veto `gates` column — the ghost-frame
   * tests (selection/cohort) are unavailable and render as em-dashes. */
  has_ghost_frame: boolean;
  gates: GateAuditGate[];
}
