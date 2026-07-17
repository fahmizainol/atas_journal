import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { CandlestickChart } from "../components/charts/CandlestickChart";
import { DataTable } from "../components/DataTable";
import { KpiGrid } from "../components/KpiGrid";
import type { Card } from "../components/KpiCard";
import { useFilters } from "../hooks/useFilters";
import {
  useCreateRun,
  useDeleteRun,
  usePatchRunMeta,
  usePinBaseline,
  usePreflight,
  useRerunBaseline,
  useRunDayChart,
  useRunDetail,
  useRunTradeChart,
  useStrategyDetail,
} from "../hooks/useStrategies";
import { ConfigForm } from "../components/strategies/ConfigForm";
import { RegimePnlPanel } from "../components/strategies/RegimePnlPanel";
import { RunEdgesPanel } from "../components/strategies/RunEdgesPanel";
import { RunRegimeCalendar } from "../components/strategies/RunRegimeCalendar";
import { useRegimeDay } from "../hooks/useRegime";
import { CLASS_LABEL, type RegimeDay } from "../lib/regimeTypes";
import type { TradeRect } from "../lib/chartTypes";
import {
  describeDiff,
  diffConfig,
  draftFrom,
  suggestLabel,
  validate,
  type DraftConfig,
} from "../lib/configForm";
import { fmt, fmtInt, fmtPct } from "../lib/format";
import {
  comparableToBaseline,
  THIN_SAMPLE,
  type ConfigSchema,
  type Preflight,
  type RunDetail,
  type SimConfig,
  type SimMetrics,
  type SimTrade,
  type StrategyRun,
  type VetoedTrade,
} from "../lib/strategyTypes";

const hhmmss = (iso: string) => iso.slice(11, 19);
const tone = (n: number) => (n >= 0 ? "pos" : "neg");
// Noon, not midnight: midnight parses in the browser's tz and can shift the date.
const dayLabel = (d: string) =>
  new Date(`${d}T12:00:00`).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
const shortDate = (iso: string) => iso.slice(0, 10);

function kpiCards(m: SimMetrics): Card[] {
  if (!m.trades) return [{ label: "Trades", value: "0" }];
  return [
    { label: "Net P&L", value: fmt(m.net_pnl), tone: tone(m.net_pnl ?? 0), hero: true },
    { label: "Trades", value: fmtInt(m.trades) },
    { label: "Win rate", value: fmtPct(m.win_rate, 0) },
    { label: "Profit factor", value: fmt(m.profit_factor, false) },
    { label: "Expectancy", value: fmt(m.expectancy), tone: tone(m.expectancy ?? 0) },
    { label: "Max drawdown", value: fmt(m.max_drawdown), tone: "neg" },
    { label: "Mean R", value: fmt(m.r_mean, false), tone: tone(m.r_mean ?? 0) },
    { label: "Best R", value: fmt(m.r_best, false), tone: "pos" },
    // Daily and annualized — see SimMetrics.sharpe.
    { label: "Sharpe", value: fmt(m.sharpe, false), tone: tone(m.sharpe ?? 0) },
    { label: "Sortino", value: fmt(m.sortino, false), tone: tone(m.sortino ?? 0) },
  ];
}

// A sim chart draws BOTH anchored VWAPs — gray for Globex, purple for NY — but the
// engine only traded one of them. `vwap_anchor` says which, and the caption has to
// say so too: two envelopes on one chart with no note of which decided the entries
// is worse than one, because you'd read the trades against the wrong bands.
const isGlobexChart = (d: { vwap_anchor?: "globex" | "ny" }) => d.vwap_anchor === "globex";

const vwapCaption = (globex: boolean) =>
  globex
    ? "Tick candles from the 18:00 ET Globex open through the bell. The engine traded the gray Globex-anchored VWAP ±1σ (dev1) and ±2σ (dev2), computed from ticks — those are the bands the rules fired on. The purple NY-anchored VWAP is drawn for context only (it starts at the bell) and no rule reads it. The overnight bars feed the bands and the profile only; acceptance and the invalidations are read from RTH closes alone."
    : "Tick candles from the 18:00 ET Globex open through the bell, shown whenever the overnight ticks are cached. The engine traded the purple NY-anchored VWAP ±1σ (dev1) and ±2σ (dev2), computed from ticks — those are the bands the rules fired on, and only the RTH candles are the ones it closed on (the overnight leg is context, built separately so the session's own candle boundaries stay exactly as the engine saw them). The gray Globex-anchored VWAP is drawn for context only and no rule reads it. Toggle either off in the legend.";

// Chart for one simulated trade. Same CandlestickChart the journal uses for real
// trades — the sim just feeds it tick bars and tick-derived VWAP instead.
function RunTradeChart({ slug, runId, tradeNo }: { slug: string; runId: string; tradeNo: number }) {
  const { scope } = useFilters();
  const { data, isLoading } = useRunTradeChart(slug, runId, tradeNo, scope.tz || "");
  const rects = useMemo(() => (data?.trade_rect ? [data.trade_rect] : []), [data?.trade_rect]);

  if (isLoading) return <div className="notice">Loading chart…</div>;
  if (!data?.available || !data.bars?.length)
    return <div className="notice">No market data for this trade.</div>;

  return (
    <div className="panel">
      <CandlestickChart
        bars={data.bars}
        vwapGlobex={data.vwap_globex}
        vwapNy={data.vwap_ny}
        profileGlobex={data.profile_globex}
        profileNy={data.profile_ny}
        atrPoints={[]}
        cvd={data.cvd}
        markers={data.markers}
        priceLines={data.price_lines}
        levels={[]}
        tradeRects={rects}
        footprint={data.footprint}
        tickSize={data.tick_size}
        pointValue={data.point_value}
        height={560}
      />
      <div className="section-cap" style={{ marginTop: 6 }}>
        {vwapCaption(isGlobexChart(data))} Green circle = the signal that armed the setup (the
        acceptance candle on a bounce, the overextension print on a fade). Blue arrow = entry.
        Orange arrow = exit. Dashed lines = entry / exit / stop. Hover
        the shaded rectangle for the trade's stats.
        {((data.profile_ny?.length ?? 0) > 0 || (data.profile_globex?.length ?? 0) > 0) && (
          <>
            {" "}
            The developing value areas (VAH / VAL solid, POC dashed) show each bar's close-of-bar
            value area: fuchsia anchored at the NY open, icy-cyan at the Globex open. Both are drawn
            on every chart — whether a rule actually read one is the run's config, not the picture.
          </>
        )}
      </div>
    </div>
  );
}

// The day's regime, as KPI tiles. Read at the close, so these describe the
// session rather than predict it — the scatter panel's checkpoint picker is where
// the "was it knowable in time?" question gets asked.
function regimeCards(r: RegimeDay): Card[] {
  const k = r.checkpoints.eod;
  const pct = (v: number | null) => (v == null ? "—" : fmtPct(v * 100, 0));
  const num = (v: number | null, d = 1) => (v == null ? "—" : v.toFixed(d));
  return [
    {
      label: "Regime",
      value: CLASS_LABEL[r.class],
      sub: r.partial ? "no overnight — NY anchor only" : undefined,
      hero: true,
    },
    { label: "Above both VWAPs", value: pct(k.abr), sub: `below both ${pct(k.bbr)}` },
    {
      label: "Longest hold above both",
      value: k.longest_hold_min == null ? "—" : `${k.longest_hold_min}m`,
    },
    {
      label: "Quadrant transitions",
      value: k.quadrant_transitions_rate == null ? "—" : `${num(k.quadrant_transitions_rate)}/hr`,
      sub: "how much it churned between the anchors",
    },
    {
      label: "NY +1σ touch → hold",
      value: pct(k.ny_touch_hold_ratio),
      sub: "did the band act as a wall",
    },
    {
      label: "NY +1σ crossings",
      value: k.ny_band_cross_rate == null ? "—" : `${num(k.ny_band_cross_rate)}/hr`,
    },
    { label: "Upper-channel occupancy", value: pct(k.ny_upper_channel_occupancy) },
    {
      label: "Middle-band occupancy",
      value: pct(k.ny_middle_band_occupancy),
      sub: `Globex ${pct(k.gx_middle_band_occupancy)} — time inside ±1σ`,
    },
    {
      label: "Globex band wrap",
      value: pct(k.upper_wrap_occupancy),
      sub: `rescues broken NY +1σ ${pct(k.gx_upper_rescue_ratio)}`,
    },
    { label: "VWAP spread", value: k.norm_spread == null ? "—" : `${num(k.norm_spread, 2)}σ` },
  ];
}

// Whole-session chart with every trade of that day drawn. Sparser per trade than
// RunTradeChart (no marker text, no price lines) — the hover tooltip carries the
// numbers, and clicking a rectangle jumps to the by-trade view.
//
// The regime rides along: tiles above, and the per-minute quadrant ribbon in a
// strip under the candles. Both come from the (symbol, date) artifact, not from
// the run — so the same session reads identically whichever run you opened it in.
function RunDayChart({
  slug,
  runId,
  symbol,
  day,
  onTradeClick,
}: {
  slug: string;
  runId: string;
  symbol: string;
  day: string;
  onTradeClick: (r: TradeRect) => void;
}) {
  const { scope } = useFilters();
  const tz = scope.tz || "";
  const { data, isLoading } = useRunDayChart(slug, runId, day, tz);
  const { data: regime } = useRegimeDay(symbol, day, tz);

  if (isLoading) return <div className="notice">Loading session…</div>;
  if (!data?.available || !data.bars?.length)
    return <div className="notice">No market data for this day.</div>;

  return (
    <>
      {regime && (
        <div style={{ marginBottom: 12 }}>
          <KpiGrid cards={regimeCards(regime)} template="repeat(4, 1fr)" />
        </div>
      )}
      <div className="panel">
        {data.trades?.length === 0 && (
          <div className="notice" style={{ marginBottom: 8 }}>
            No trades this session — the setup never armed and filled.
          </div>
        )}
        <CandlestickChart
          bars={data.bars}
          vwapGlobex={data.vwap_globex}
          vwapNy={data.vwap_ny}
          profileGlobex={data.profile_globex}
          profileNy={data.profile_ny}
          atrPoints={[]}
          cvd={data.cvd}
          markers={data.markers}
          priceLines={[]}
          levels={[]}
          tradeRects={data.trades}
          footprint={data.footprint}
          regimeStates={regime?.ribbon}
          tickSize={data.tick_size}
          pointValue={data.point_value}
          height={560}
          onTradeClick={onTradeClick}
        />
        <div className="section-cap" style={{ marginTop: 6 }}>
          Every trade this session. Green circle = the arming signal, blue arrow = entry, orange arrow =
          exit, shaded rectangle = the holding period. Hover a rectangle for the trade's stats;
          click it to open that trade in the by-trade view.
          {isGlobexChart(data) && (
            <>
              {" "}
              The session starts at the 18:00 ET Globex open, where this strategy's gray VWAP is
              anchored — trades are still confined to RTH.
            </>
          )}
          {regime && (
            <>
              {" "}
              The strip under the candles is the regime ribbon: green where price closed above both
              anchored VWAPs, red below both, amber/blue where it sat between them (the churn the
              model dies on). Pre-bell bars are faint — only the Globex anchor exists yet.
            </>
          )}
        </div>
      </div>
    </>
  );
}

// Label + notes live in meta.json, outside the run's identity hash — rename
// after seeing the results without creating a "different" run.
function MetaEditor({ slug, runId, label, notes }: {
  slug: string;
  runId: string;
  label: string;
  notes: string;
}) {
  const patch = usePatchRunMeta(slug);
  const [l, setL] = useState(label);
  const [n, setN] = useState(notes);
  useEffect(() => {
    setL(label);
    setN(notes);
  }, [runId, label, notes]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
      <input
        className="field"
        value={l}
        placeholder="Label this run (e.g. “tighter stop, RR target”)"
        onChange={(e) => setL(e.target.value)}
        onBlur={() => l !== label && patch.mutate({ runId, label: l })}
      />
      <textarea
        className="field"
        rows={2}
        value={n}
        placeholder="Notes — why this tweak, what you saw, what to try next…"
        onChange={(e) => setN(e.target.value)}
        onBlur={() => n !== notes && patch.mutate({ runId, notes: n })}
      />
    </div>
  );
}

// The selected run's full config, collapsed by default so it doesn't cramp the
// page. Even collapsed, the knobs that differ from the baseline (or from the
// strategy defaults, when viewing the baseline itself) stay visible — those
// diffs are what make this run this run.
function ConfigSection({
  config,
  reference,
  referenceName,
}: {
  config: Record<string, unknown>;
  reference: Record<string, unknown>;
  referenceName: "baseline" | "defaults";
}) {
  const [open, setOpen] = useState(false);
  const js = (v: unknown) => JSON.stringify(v);
  const keys = Object.keys(config);
  const changed = keys.filter((k) => js(config[k]) !== js(reference[k]));

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <button type="button" className="btn-xs" onClick={() => setOpen((o) => !o)}>
          {open ? "Hide config" : "Show config"}
        </button>
        {!open && (
          <span className="muted" style={{ fontSize: 12, fontFamily: "monospace" }}>
            {changed.length === 0
              ? `identical to ${referenceName}`
              : `vs ${referenceName}: ${changed.map((k) => `${k}=${js(config[k])}`).join("  ")}`}
          </span>
        )}
      </div>
      {open && (
        <div className="panel" style={{ marginTop: 8, overflowX: "auto" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "max-content max-content 1fr",
              gap: "3px 18px",
              fontFamily: "monospace",
              fontSize: 12,
            }}
          >
            {keys.map((k) => {
              const diff = js(config[k]) !== js(reference[k]);
              return (
                <Fragment key={k}>
                  <span className={diff ? undefined : "muted"}>{k}</span>
                  <span style={diff ? { fontWeight: 600 } : undefined}>{js(config[k])}</span>
                  <span className="muted">{diff ? `${referenceName}: ${js(reference[k])}` : ""}</span>
                </Fragment>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// The JSON port. Not a second editor bound to the form — a paste-in and a
// copy-out. Two live-linked views of one config is a sync bug waiting to happen
// (type half a line of invalid JSON and watch the form stomp it), and the form is
// the source of truth. So this shows what the form currently holds, and "Apply to
// form" is the only way anything flows back.
function JsonPort({ config, onApply }: { config: DraftConfig; onApply: (c: DraftConfig) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const current = JSON.stringify(config, null, 2);
  const dirty = text != null && text !== current;

  const apply = () => {
    try {
      const v = JSON.parse(text ?? current);
      if (typeof v !== "object" || v === null || Array.isArray(v))
        throw new Error("must be an object");
      onApply(v as DraftConfig);
      setText(null);
      setError(null);
    } catch (e) {
      setError(`Invalid JSON: ${(e as Error).message}`);
    }
  };

  if (!open)
    return (
      <button type="button" className="btn-xs" onClick={() => setOpen(true)}>
        Edit as JSON
      </button>
    );

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <button type="button" className="btn-xs" onClick={() => { setOpen(false); setText(null); setError(null); }}>
          Hide JSON
        </button>
        <button type="button" className="btn-xs" onClick={() => void navigator.clipboard.writeText(current)}>
          Copy
        </button>
        {dirty && (
          <button type="button" className="btn-xs btn-accent" onClick={apply}>
            Apply to form
          </button>
        )}
        {dirty && <span className="muted" style={{ fontSize: 12 }}>edited — not applied</span>}
      </div>
      <textarea
        className="field"
        style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
        rows={14}
        value={text ?? current}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
      />
      {error && <div className="notice" style={{ marginTop: 6 }}>{error}</div>}
    </div>
  );
}

// The tweak loop's entry point: baseline config in a form, change a knob,
// preflight (spend guard), run. Runs land in the list above as they progress.
//
// The form is rendered from the server's schema, so it always covers exactly the
// knobs the engine reads. What it adds over the JSON editor it replaced is the
// diff: every field that differs from the config it was prefilled with is marked,
// the count rides on the Run button, and the same diff is what names the run.
function NewRunPanel({
  slug,
  session,
  schema,
  prefill,
  prefillName,
  prefillKey,
  defaults,
  onStarted,
}: {
  slug: string;
  session: "rth" | "globex";
  schema: ConfigSchema;
  prefill: SimConfig | null;
  prefillName: string;
  prefillKey: string;
  defaults: SimConfig;
  onStarted: (runId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DraftConfig | null>(null);
  const [label, setLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pf, setPf] = useState<Preflight | null>(null);
  // Every run buys the overnight (18:00 → 09:30 ET) on top of RTH, so its charts
  // can draw the night and the Globex-anchored VWAP — even on an RTH strategy,
  // which still *trades* RTH ticks alone. Tick this to buy the NY session only.
  // A globex strategy can't honour it: the night is its input, not its garnish.
  const [rthOnly, setRthOnly] = useState(false);
  const preflight = usePreflight(slug);
  const createRun = useCreateRun(slug);

  const reference = useMemo(() => draftFrom(schema, prefill), [schema, prefill]);
  const config = draft ?? reference;

  // Selecting a different run refills the form: the draft's edits were made
  // against the previous reference, and a diff against the new one would say
  // something the user never meant. Keyed on the selection's identity, not the
  // config object — the detail query polls while runs are in flight, and a
  // refetch must not stomp half-typed edits.
  useEffect(() => {
    setDraft(null);
    setLabel(null);
    setError(null);
    setPf(null);
  }, [prefillKey]);

  const errors = useMemo(() => validate(schema, config), [schema, config]);
  const diffs = useMemo(() => diffConfig(schema, config, reference), [schema, config, reference]);
  const changedKeys = useMemo(() => new Set(diffs.map((d) => d.key)), [diffs]);
  const invalid = Object.keys(errors).length > 0;

  // Suggested, not imposed: a diff names the run until you type over it. But a
  // different diff than the one above — the label is the run's *identity*, so it
  // reads against the immutable strategy defaults, not against whatever happened
  // to be prefilled. A delta from a mutable reference ("entry variant B") stops
  // describing the run the moment the baseline moves; a delta from the defaults
  // never does. The window is left out: it is scope, not the experiment, and the
  // runs table already has a column for it.
  const defaultsDraft = useMemo(() => draftFrom(schema, defaults), [schema, defaults]);
  const labelDiffs = useMemo(
    () => diffConfig(schema, config, defaultsDraft).filter((d) => d.field.group !== "window"),
    [schema, config, defaultsDraft],
  );
  const labelValue = label ?? suggestLabel(labelDiffs);

  const reset = () => {
    setDraft(null);
    setLabel(null);
    setError(null);
    setPf(null);
  };

  const start = async () => {
    setPf(null);
    try {
      const res = await createRun.mutateAsync({
        config: config as unknown as SimConfig,
        label: labelValue || undefined,
        rthOnly,
      });
      onStarted(res.run_id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onRun = async () => {
    if (invalid) return;
    setError(null);
    try {
      const res = await preflight.mutateAsync({
        config: config as unknown as SimConfig,
        rthOnly,
      });
      if (res.exists) {
        // Identical config + code = the same immutable run; just open it.
        onStarted(res.run_id);
        setPf(null);
        return;
      }
      if (res.uncached_sessions > 0) {
        setPf(res); // paid downloads — make the user say yes
        return;
      }
      await start();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!open)
    return (
      <button type="button" className="btn-accent" style={{ marginBottom: 16 }} onClick={() => setOpen(true)}>
        + New run
      </button>
    );

  const pending = preflight.isPending || createRun.isPending;

  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 style={{ marginTop: 0 }}>New run</h3>
        <button type="button" onClick={() => setOpen(false)}>Close</button>
      </div>
      <div className="section-cap" style={{ marginBottom: 12 }}>
        Prefilled from the {prefillName} config — change a knob, run, compare. Identical config on
        identical code resolves to the existing run instead of re-simulating.
      </div>

      <ConfigForm
        schema={schema}
        config={config}
        onChange={setDraft}
        errors={errors}
        changed={changedKeys}
      />

      <div className="field" style={{ marginTop: 12 }}>
        <label>Label</label>
        <input
          type="text"
          value={labelValue}
          placeholder="identical to the strategy defaults"
          onChange={(e) => setLabel(e.target.value)}
        />
      </div>

      <div className="field" style={{ marginTop: 12 }}>
        <label className="cfg-check">
          <input
            type="checkbox"
            checked={session === "globex" ? false : rthOnly}
            disabled={session === "globex"}
            onChange={(e) => {
              setRthOnly(e.target.checked);
              setPf(null); // the estimate below was priced for the other scope
            }}
          />
          <span>Fetch the NY session only (09:30 – 16:00 ET)</span>
        </label>
        <div className="section-cap" style={{ marginTop: 4 }}>
          {session === "globex"
            ? "This strategy reads the overnight — its VWAP is anchored at the Globex open — so the night is always fetched."
            : rthOnly
              ? "Cheaper, but this run's charts can't draw the overnight candles or the Globex-anchored VWAP: charts only ever read the tick cache, they never buy."
              : "Runs also buy the overnight (18:00 → 09:30 ET) so their charts can draw the night and the Globex-anchored VWAP. It changes nothing the engine trades — this strategy still simulates on RTH ticks alone."}
        </div>
      </div>

      <div className="cfg-diff">
        {diffs.length === 0 ? (
          <span className="muted">Identical to the {prefillName} config.</span>
        ) : (
          <span style={{ fontFamily: "monospace", fontSize: 12 }}>{describeDiff(diffs)}</span>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="btn-accent" onClick={onRun} disabled={pending || invalid}>
          {pending
            ? "Starting…"
            : diffs.length
              ? `Run — ${diffs.length} change${diffs.length > 1 ? "s" : ""} from ${prefillName}`
              : "Run"}
        </button>
        <button type="button" onClick={reset} disabled={!draft && label == null}>
          Reset to {prefillName} config
        </button>
        <JsonPort config={config} onApply={setDraft} />
      </div>

      {invalid && (
        <div className="notice" style={{ marginTop: 8 }}>
          {Object.entries(errors).map(([k, v]) => `${k}: ${v}`).join(" · ")}
        </div>
      )}
      {error && <div className="notice" style={{ marginTop: 8 }}>{error}</div>}
      {pf && (
        <div className="notice" style={{ marginTop: 8 }}>
          This window spans <strong>{pf.sessions_total}</strong> sessions;{" "}
          <strong>{pf.uncached_sessions}</strong> are not cached and will be downloaded from
          Databento{pf.est_cost_usd != null && (
            <> (estimated <strong>${pf.est_cost_usd.toFixed(2)}</strong>)</>
          )}
          .
          {session !== "globex" && rthOnly ? null : (
            <>
              {" "}
              Each of those sessions pulls the overnight segment (18:00 → 09:30 ET) on top of RTH
              — roughly two and a half times the tick data of an RTH-only run.{" "}
              {session === "globex"
                ? "This strategy anchors its VWAP at the Globex open, so it cannot run without the night."
                : "Tick “Fetch the NY session only” above to skip it."}
            </>
          )}
          <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
            <button type="button" className="btn-accent" onClick={() => void start()}>
              Download & run
            </button>
            <button type="button" onClick={() => setPf(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

// KPIs + by-trade / by-day views for the selected run — the part of the page
// that answers "where exactly did it fire, and would I have taken that?".
function RunView({
  slug,
  run,
  detail,
  reference,
  referenceName,
}: {
  slug: string;
  run: StrategyRun;
  detail: RunDetail | undefined;
  reference: Record<string, unknown>;
  referenceName: "baseline" | "defaults";
}) {
  const [params, setParams] = useSearchParams();
  const openTrade = params.get("trade") ? Number(params.get("trade")) : null;
  const setParam = useCallback(
    (updates: Record<string, string | null>) => {
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          for (const [k, v] of Object.entries(updates)) {
            if (v == null) next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  const setOpenTrade = useCallback(
    (key: string | number | null) => setParam({ trade: key == null ? null : String(key) }),
    [setParam],
  );

  const viewParam = params.get("view");
  const view = viewParam === "day" ? "day" : viewParam === "calendar" ? "calendar" : "trade";
  const days = detail?.session_days ?? [];
  const dayParam = params.get("day");
  const activeDay = dayParam && days.includes(dayParam) ? dayParam : (days[0] ?? null);

  // Per-day net and win rate for the day tabs. A win is net_pnl > 0, matching
  // the backend's win_rate; days the config covered but that traded nothing are
  // absent from the map and render as "—".
  const dayStats = useMemo(() => {
    const by = new Map<string, { net: number; wins: number; n: number }>();
    for (const t of detail?.trades ?? []) {
      const d = t.session.slice(0, 10);
      const s = by.get(d) ?? { net: 0, wins: 0, n: 0 };
      s.net += t.net_pnl;
      s.n += 1;
      if (t.net_pnl > 0) s.wins += 1;
      by.set(d, s);
    }
    return by;
  }, [detail]);

  // Clicking a trade's rectangle in the day view opens that trade in the
  // by-trade view; ?day= is kept so toggling back lands on the same session.
  const openTradeFromRect = useCallback(
    (r: TradeRect) => {
      if (r.stats) setParam({ view: null, trade: String(r.stats.trade_no) });
    },
    [setParam],
  );

  const tickSize = 0.25; // NQ
  const columns = useMemo<ColumnDef<SimTrade, any>[]>(
    () => [
      { accessorKey: "trade_no", header: "#" },
      { accessorKey: "session", header: "Session", cell: (c) => String(c.getValue()).slice(0, 10) },
      { accessorKey: "entry_ts_local", header: "Entry", cell: (c) => hhmmss(String(c.getValue())) },
      { accessorKey: "exit_ts_local", header: "Exit", cell: (c) => hhmmss(String(c.getValue())) },
      { accessorKey: "avg_entry", header: "Entry px", cell: (c) => (c.getValue() as number).toFixed(2) },
      { accessorKey: "avg_exit", header: "Exit px", cell: (c) => (c.getValue() as number).toFixed(2) },
      { accessorKey: "exit_reason", header: "Why out" },
      {
        accessorKey: "band_width_ticks",
        header: "dev2−dev1",
        cell: (c) => {
          const w = c.getValue() as number;
          const { avg_entry, stop_price } = c.row.original;
          // Flag entries where the target (1σ) was closer than the stop — a
          // sub-1:1 trade. Derive the stop distance from the row so this stays
          // right if stop_ticks is ever changed in the config.
          const tight = w < (avg_entry - stop_price) / tickSize;
          return (
            <span
              className={tight ? "neg" : undefined}
              title={tight ? "target was narrower than the stop — risked more than it stood to make" : undefined}
            >
              {w.toFixed(0)}t
            </span>
          );
        },
      },
      {
        accessorKey: "r_multiple",
        header: "R",
        cell: (c) => {
          const r = c.getValue() as number;
          return <span className={tone(r)}>{r.toFixed(2)}</span>;
        },
      },
      {
        accessorKey: "net_pnl",
        header: "Net",
        cell: (c) => {
          const v = c.getValue() as number;
          return <span className={tone(v)}>{fmt(v)}</span>;
        },
      },
    ],
    [],
  );

  const vetoedColumns = useMemo<ColumnDef<VetoedTrade, any>[]>(
    () => [
      { accessorKey: "session", header: "Session", cell: (c) => String(c.getValue()).slice(0, 10) },
      { accessorKey: "entry_ts_local", header: "Entry", cell: (c) => hhmmss(String(c.getValue())) },
      { accessorKey: "gate", header: "Vetoed by" },
      { accessorKey: "exit_reason", header: "Would have exited" },
      {
        accessorKey: "net_pnl",
        header: "Would-be net",
        cell: (c) => {
          const v = c.getValue() as number;
          return <span className={tone(v)}>{fmt(v)}</span>;
        },
      },
    ],
    [],
  );

  const m = run.metrics;
  const cfg = run.config;

  if (run.state.status === "running")
    return (
      <div className="notice">
        Running — session {run.state.sessions_done}/{run.state.sessions_total}…
      </div>
    );
  if (run.state.status === "error")
    return (
      <>
        <div className="notice" style={{ marginBottom: 12 }}>Run failed: {run.state.error}</div>
        <ConfigSection
          config={cfg as unknown as Record<string, unknown>}
          reference={reference}
          referenceName={referenceName}
        />
      </>
    );

  return (
    <>
      <MetaEditor slug={slug} runId={run.run_id} label={run.meta.label} notes={run.meta.notes} />

      <ConfigSection
        config={cfg as unknown as Record<string, unknown>}
        reference={reference}
        referenceName={referenceName}
      />

      <KpiGrid cards={kpiCards(m)} template="repeat(4, 1fr)" />

      {m.trades > 0 && m.trades < THIN_SAMPLE && (
        <div className="notice" style={{ marginTop: 12 }}>
          n = {m.trades} — far too thin a sample to conclude anything about edge. Read the
          charts, not the stats.
        </div>
      )}

      {m.band_width_min_ticks != null && m.band_width_min_ticks < cfg.stop_ticks && (
        <div className="notice" style={{ marginTop: 12 }}>
          The narrowest entry here had dev2 only{" "}
          <strong>{m.band_width_min_ticks.toFixed(0)} ticks</strong> above dev1 against a fixed{" "}
          {cfg.stop_ticks}-tick stop — it risked more than the target was worth. σ is small right
          after the open and widens through the session, so early entries are structurally poor
          R:R. The <code>min_band_width_ticks</code> knob exists to test filtering them out.
        </div>
      )}

      {m.vetoed && (
        <div className="notice" style={{ marginTop: 12 }}>
          Confluences vetoed <strong>{m.vetoed.count}</strong> entries worth{" "}
          <span className={tone(m.vetoed.net_pnl)}>{fmt(m.vetoed.net_pnl)}</span> net (
          {Object.entries(m.vetoed.by_gate).map(([g, n]) => `${g}: ${n}`).join(", ")}). Positive
          means the gate filtered winners; negative means it saved you money.
        </div>
      )}

      {/* Scores nothing itself: the study is computed server-side and read back
          from the run's own snapshot, so the panel only needs to say which run. */}
      <RegimePnlPanel slug={slug} runId={run.run_id} />

      <RunEdgesPanel slug={slug} runId={run.run_id} />

      <div className="tabs" style={{ marginTop: 20 }}>
        <button
          type="button"
          className={view === "trade" ? "active" : undefined}
          onClick={() => setParam({ view: null })}
        >
          By trade
        </button>
        <button
          type="button"
          className={view === "day" ? "active" : undefined}
          onClick={() => setParam({ view: "day" })}
        >
          By day
        </button>
        <button
          type="button"
          className={view === "calendar" ? "active" : undefined}
          onClick={() => setParam({ view: "calendar" })}
        >
          Calendar
        </button>
      </div>

      {view === "calendar" ? (
        <RunRegimeCalendar
          symbol={cfg.contract}
          start={cfg.start_date}
          end={cfg.end_date}
          dayStats={dayStats}
          activeDay={activeDay}
          onPick={(d) => setParam({ view: "day", day: d })}
        />
      ) : view === "day" ? (
        <>
          <div className="day-strip" style={{ marginTop: 12, marginBottom: 12 }}>
            {days.map((d) => {
              const s = dayStats.get(d);
              return (
                <button
                  key={d}
                  type="button"
                  className={d === activeDay ? "active" : undefined}
                  onClick={() => setParam({ day: d })}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    gap: 2,
                    lineHeight: 1.3,
                  }}
                >
                  <span>{dayLabel(d)}</span>
                  <span
                    className={s ? tone(s.net) : "muted"}
                    style={{ fontSize: 11, fontWeight: 500 }}
                  >
                    {s ? `${fmt(s.net)} · ${fmtPct((s.wins / s.n) * 100, 0)}` : "—"}
                  </span>
                </button>
              );
            })}
          </div>
          {activeDay && (
            <RunDayChart
              slug={slug}
              runId={run.run_id}
              symbol={cfg.contract}
              day={activeDay}
              onTradeClick={openTradeFromRect}
            />
          )}
        </>
      ) : (
        <>
          <h3 style={{ marginTop: 12 }}>Trades — click one to see it on the chart</h3>
          {/* A long window runs to hundreds of trades, and the table is the last
              thing on the page — without a cap the run's chart and metrics scroll
              away and every other run is a page-length flick apart. maxHeight, not
              height: a 6-trade run should not sit in 600px of empty box. The
              expanded chart lives inside the scroller, and DataTable's
              scrollOnExpand pulls the clicked row to the top of it. */}
          <div className="table-scroll" style={{ maxHeight: 600, overflow: "auto" }}>
            <DataTable
              data={detail?.trades ?? []}
              columns={columns}
              rowKey={(r) => r.trade_no}
              expandedKey={openTrade}
              onExpandedChange={setOpenTrade}
              renderExpanded={(r) => (
                <RunTradeChart slug={slug} runId={run.run_id} tradeNo={r.trade_no} />
              )}
            />
          </div>
          {(detail?.vetoed_trades?.length ?? 0) > 0 && (
            <>
              <h3 style={{ marginTop: 20 }}>Vetoed by confluences</h3>
              <div className="table-scroll" style={{ maxHeight: 400, overflow: "auto" }}>
                <DataTable
                  data={detail!.vetoed_trades}
                  columns={vetoedColumns}
                  rowKey={(r) => `${r.session}-${r.entry_ts_local}`}
                />
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}

export function StrategyDetail() {
  const { slug = "" } = useParams();
  const { data: strat, isLoading } = useStrategyDetail(slug);
  const [params, setParams] = useSearchParams();

  const runs = strat?.runs ?? [];
  const baseline = runs.find((r) => r.run_id === strat?.baseline_run_id) ?? null;
  const runParam = params.get("run");
  const selected =
    runs.find((r) => r.run_id === runParam) ??
    baseline ??
    runs.find((r) => r.state.status === "done") ??
    runs[0] ??
    null;

  const selectRun = useCallback(
    (runId: string | null) => {
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          if (runId == null) next.delete("run");
          else next.set("run", runId);
          // Trade numbers aren't comparable across runs.
          next.delete("trade");
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const { data: runDetail } = useRunDetail(
    slug,
    selected && selected.state.status === "done" ? selected.run_id : null,
  );

  const pin = usePinBaseline(slug);
  const del = useDeleteRun(slug);
  const rerun = useRerunBaseline(slug);

  const runColumns = useMemo<ColumnDef<StrategyRun, any>[]>(() => {
    const deltaCell = (run: StrategyRun, key: "net_pnl" | "r_mean") => {
      if (!baseline || run.run_id === baseline.run_id) return null;
      if (!comparableToBaseline(run, baseline)) return null;
      const d = (run.metrics[key] ?? 0) - (baseline.metrics[key] ?? 0);
      return (
        <span className={tone(d)} style={{ marginLeft: 6, fontSize: 11 }}>
          {d >= 0 ? "+" : ""}
          {key === "net_pnl" ? fmt(d) : d.toFixed(2)}
        </span>
      );
    };
    return [
      {
        id: "label",
        header: "Run",
        accessorFn: (r) => r.meta.label || r.run_id,
        cell: (c) => {
          const r = c.row.original;
          const label = r.meta.label || r.run_id;
          return (
            <span style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
              {r.run_id === baseline?.run_id && (
                <span className="badge" title="baseline — deltas are measured against this run" style={{ marginRight: 6, flexShrink: 0 }}>
                  ★ baseline
                </span>
              )}
              <span
                title={label}
                className={r.meta.label ? undefined : "muted"}
                style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 280 }}
              >
                {label}
              </span>
            </span>
          );
        },
      },
      {
        // The debugging handle: the config-hash tail of the run_id, which is also
        // the artifact folder under data/sims/<slug>/. Window and engine version
        // (the id's other parts) already have their own columns.
        id: "id",
        header: "Id",
        accessorFn: (r) => r.run_id,
        cell: (c) => {
          const rid = String(c.getValue());
          return (
            <span
              className="muted"
              style={{ fontFamily: "monospace", fontSize: 11, cursor: "copy" }}
              title={`${rid} — click to copy`}
              onClick={(e) => {
                e.stopPropagation();
                void navigator.clipboard.writeText(rid);
              }}
            >
              {rid.slice(rid.lastIndexOf("-") + 1)}
            </span>
          );
        },
      },
      {
        id: "window",
        header: "Window",
        accessorFn: (r) => r.config.start_date,
        cell: (c) => {
          const r = c.row.original;
          const differs = baseline &&
            r.run_id !== baseline.run_id &&
            (r.config.start_date !== baseline.config.start_date ||
              r.config.end_date !== baseline.config.end_date);
          return (
            <span title={differs ? "different window than the baseline — no delta shown" : undefined}>
              {r.config.start_date} → {r.config.end_date}
              {differs && <span className="badge badge-sm" style={{ marginLeft: 6 }}>≠ window</span>}
            </span>
          );
        },
      },
      {
        id: "version",
        header: "Engine",
        accessorFn: (r) => r.state.engine_version,
        cell: (c) => {
          const r = c.row.original;
          const stale = strat && r.state.engine_version !== strat.version;
          return (
            <span title={stale ? `run was produced by engine v${r.state.engine_version}; current is v${strat!.version} — no delta shown` : undefined}>
              v{r.state.engine_version}
              {stale && <span className="badge badge-sm" style={{ marginLeft: 6 }}>stale</span>}
            </span>
          );
        },
      },
      {
        accessorKey: "trades",
        header: "n",
        cell: (c) => {
          const n = c.getValue() as number;
          return (
            <span>
              {fmtInt(n)}
              {n > 0 && n < THIN_SAMPLE && (
                <span className="badge badge-sm" title="thin sample — read the charts, not the stats" style={{ marginLeft: 4 }}>
                  thin
                </span>
              )}
            </span>
          );
        },
      },
      {
        id: "net",
        header: "Net",
        accessorFn: (r) => r.metrics.net_pnl ?? 0,
        cell: (c) => {
          const r = c.row.original;
          if (r.state.status !== "done") return null;
          return (
            <span>
              <span className={tone(r.metrics.net_pnl ?? 0)}>{fmt(r.metrics.net_pnl)}</span>
              {deltaCell(r, "net_pnl")}
            </span>
          );
        },
      },
      {
        id: "win",
        header: "Win %",
        accessorFn: (r) => r.metrics.win_rate ?? 0,
        cell: (c) => (c.row.original.state.status === "done" ? fmtPct(c.row.original.metrics.win_rate, 0) : null),
      },
      {
        id: "pf",
        header: "PF",
        accessorFn: (r) => r.metrics.profit_factor ?? 0,
        cell: (c) => (c.row.original.state.status === "done" ? fmt(c.row.original.metrics.profit_factor, false) : null),
      },
      {
        id: "rmean",
        header: "R mean",
        accessorFn: (r) => r.metrics.r_mean ?? 0,
        cell: (c) => {
          const r = c.row.original;
          if (r.state.status !== "done") return null;
          return (
            <span>
              <span className={tone(r.metrics.r_mean ?? 0)}>{fmt(r.metrics.r_mean, false)}</span>
              {deltaCell(r, "r_mean")}
            </span>
          );
        },
      },
      {
        id: "sharpe",
        header: "Sharpe",
        // Daily, annualized. Unlike net P&L it already accounts for how often a
        // config trades and how lumpy its days were, which is what makes it the
        // right column to rank a sweep by.
        accessorFn: (r) => r.metrics.sharpe ?? 0,
        cell: (c) => {
          const r = c.row.original;
          if (r.state.status !== "done") return null;
          return <span className={tone(r.metrics.sharpe ?? 0)}>{fmt(r.metrics.sharpe, false)}</span>;
        },
      },
      {
        id: "created",
        header: "Created",
        accessorFn: (r) => r.state.created_at,
        cell: (c) => shortDate(String(c.getValue())),
      },
      {
        id: "status",
        header: "Status",
        accessorFn: (r) => r.state.status,
        cell: (c) => {
          const s = c.row.original.state;
          if (s.status === "running")
            return <span>running {s.sessions_done}/{s.sessions_total}…</span>;
          if (s.status === "error")
            return <span className="neg" title={s.error ?? undefined}>error</span>;
          return <span className="muted">done</span>;
        },
      },
      {
        id: "actions",
        header: "",
        enableSorting: false,
        cell: (c) => {
          const r = c.row.original;
          return (
            <span style={{ display: "flex", gap: 4 }}>
              {r.state.status === "done" && r.run_id !== baseline?.run_id && (
                <button
                  type="button"
                  className="btn-xs"
                  title="pin as baseline"
                  onClick={(e) => {
                    e.stopPropagation();
                    pin.mutate(r.run_id);
                  }}
                >
                  ★
                </button>
              )}
              {r.state.status !== "running" && (
                <button
                  type="button"
                  className="btn-xs btn-danger"
                  title="delete this run"
                  onClick={(e) => {
                    e.stopPropagation();
                    const name = r.meta.label || r.run_id;
                    if (window.confirm(`Delete run “${name}”? The artifact is removed from disk.`))
                      del.mutate(r.run_id);
                  }}
                >
                  ✕
                </button>
              )}
            </span>
          );
        },
      },
    ];
  }, [baseline, strat, pin, del]);

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!strat) return <div className="notice">Unknown strategy.</div>;

  const staleBaseline = baseline && baseline.state.engine_version !== strat.version;
  // The form prefills from whichever run is selected, so "click a run, tweak a
  // knob, run" is one motion; with nothing selected it falls back to baseline,
  // then defaults. The diff it marks is measured against whichever config it
  // actually started from — so the change count never quietly means something
  // other than it says.
  const prefill = selected?.config ?? baseline?.config ?? strat.default_config;
  const prefillName = !selected
    ? baseline
      ? "baseline"
      : "defaults"
    : selected.run_id === baseline?.run_id
      ? "baseline"
      : `“${selected.meta.label || selected.run_id}”`;

  return (
    <div className="page">
      <div style={{ marginBottom: 4 }}>
        <Link to="/strategies" className="muted">← Strategies</Link>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 className="section-title" style={{ marginBottom: 4 }}>{strat.name}</h2>
        <span className="muted">engine v{strat.version}</span>
      </div>
      <div className="section-cap" style={{ marginBottom: 16 }}>{strat.description}</div>

      {staleBaseline && (
        <div className="notice" style={{ marginBottom: 12 }}>
          The baseline was produced by engine v{baseline.state.engine_version}, but the strategy
          code is now v{strat.version} — new runs can't be compared against it.{" "}
          <button type="button" className="btn-xs" onClick={() => rerun.mutate()} disabled={rerun.isPending}>
            {rerun.isPending ? "Starting…" : `Re-run baseline config on v${strat.version}`}
          </button>
        </div>
      )}

      <NewRunPanel
        slug={slug}
        session={strat.session}
        schema={strat.config_schema}
        prefill={prefill}
        prefillName={prefillName}
        prefillKey={selected?.run_id ?? prefillName}
        defaults={strat.default_config}
        onStarted={selectRun}
      />

      {runs.length === 0 ? (
        <div className="notice">
          No runs yet. Start one above, or seed from the CLI:{" "}
          <code>PYTHONPATH=src .venv/bin/python -m journal.sim.run --both</code>
        </div>
      ) : (
        // The run list only grows — every experiment adds a row, and it sits above
        // the run view, so without a cap picking a run means scrolling past all the
        // others to see the one you picked.
        <div className="table-scroll" style={{ maxHeight: 420, overflow: "auto" }}>
          <DataTable
            data={runs}
            columns={runColumns}
            rowKey={(r) => r.run_id}
            selectedKey={selected?.run_id ?? null}
            onRowClick={(r) => selectRun(r.run_id)}
          />
        </div>
      )}

      {selected && (
        <div style={{ marginTop: 20 }}>
          <RunView
            slug={slug}
            run={selected}
            detail={runDetail}
            reference={
              (baseline && selected.run_id !== baseline.run_id
                ? baseline.config
                : strat.default_config) as unknown as Record<string, unknown>
            }
            referenceName={baseline && selected.run_id !== baseline.run_id ? "baseline" : "defaults"}
          />
        </div>
      )}
    </div>
  );
}
