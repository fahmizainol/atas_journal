import {
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineWidth,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { Bar } from "../../lib/chartTypes";
import {
  catalogue,
  findStudy,
  loadCatalogue,
  type StudyEntry,
  type StudyMarker,
  studyColor,
  studyLineStyle,
  studyWidth,
  type StudyPlotConfig,
  type StudyReport,
  type StudySpec,
} from "../../lib/studies";

/** One study as it exists on the canvas. */
interface Live {
  spec: StudySpec;
  entry: StudyEntry | null;
  /** One series per plotted output, in `plotConfig` order — the id is kept so a
   *  redraw can tell whether the study still emits the same set. */
  series: { id: string; ser: ISeriesApi<"Line"> | ISeriesApi<"Histogram"> }[];
  markers: ISeriesMarkersPluginApi<Time> | null;
  report: StudyReport;
}

/** How much of the chart a study's own pane claims, against the price pane's
 *  1000 — the 5:1 split CVD and the vol ruler already use, so a study pane is
 *  the same height as the panes it sits next to. */
const PRICE_STRETCH = 1000;
const STUDY_STRETCH = 200;

/** The two Pine plot styles that want a histogram series. Everything else —
 *  including the step and break-on-gap variants lightweight-charts has no series
 *  for — draws as a line: the values are right, only the interpolation between
 *  them is a straight segment instead of a stair. */
const HISTOGRAM_STYLES = new Set(["histogram", "columns"]);

const lineWidth = (n: number | undefined): LineWidth =>
  (Math.min(4, Math.max(1, Math.round(n ?? 1))) as LineWidth);

const LINE_STYLE: Record<string, LineStyle> = {
  solid: LineStyle.Solid,
  dashed: LineStyle.Dashed,
  dotted: LineStyle.Dotted,
};

/**
 * The community studies, drawn on one chart.
 *
 * Owns everything a picked indicator puts on the canvas — its series, its own
 * pane when it wants one, its reference levels and its marks — and nothing else.
 * ReplayChart hands it bars and specs; it hands back a report of what actually
 * drew, because a study that computed nothing and a study that threw look
 * identical on a chart and must not look identical in the picker.
 *
 * Two cadences, deliberately different:
 *
 *   `setSpecs`  — structural. Series are made and destroyed, panes appear and
 *                 disappear. Runs when the user adds, removes or re-tunes one.
 *   `setBars`   — arithmetic. Every study is recomputed and re-`setData`, no
 *                 series churn. Runs on a snapshot and on a bar close, the same
 *                 cadence as the vol ruler and Modern VWAP: every value here is
 *                 a fact about a *closed* bar, so a per-tick recompute would buy
 *                 nothing and cost a pass over the session per frame.
 *
 * `remount()` is the third case and belongs to the chart: pane indices are
 * positional, so CVD or the vol ruler coming or going renumbers everything above
 * them. The layer's panes are always the last ones, and this puts them back
 * there.
 */
export class StudyLayer {
  private specs: StudySpec[] = [];
  private live: Live[] = [];
  private bars: Bar[] = [];
  private dead = false;
  /** Set while the catalogue is in flight, so a second spec change doesn't queue
   *  a second rebuild behind the same import. */
  private awaiting = false;

  constructor(
    private readonly chart: IChartApi,
    /** The price series — where an overlay's lines and every study's marks go. */
    private readonly price: ISeriesApi<"Candlestick">,
    private readonly onReport?: (r: StudyReport[]) => void,
  ) {}

  /** The studies that should be on the chart. Idempotent: handing over the list
   *  already drawn still rebuilds, which is what a settings change needs. */
  setSpecs(specs: StudySpec[]): void {
    this.specs = specs;
    if (specs.length && !catalogue() && !this.awaiting) {
      // Nothing to draw until the package lands — a chart opened on saved
      // studies is the case this exists for.
      this.awaiting = true;
      loadCatalogue()
        .then(() => {
          this.awaiting = false;
          if (!this.dead) this.build();
        })
        .catch(() => {
          this.awaiting = false;
          // The picker reports the failure; a chart with no studies on it is the
          // honest fallback here.
        });
    }
    this.build();
  }

  /** New bars. Recomputes and redraws without touching the series. */
  setBars(bars: Bar[]): void {
    this.bars = bars;
    this.redraw();
  }

  /** The panes moved under us — rebuild so ours are the last ones again. */
  remount(): void {
    if (this.live.length) this.build();
  }

  /** Everything this layer put on the chart, taken off. Not for use after the
   *  chart itself is destroyed: the series went with it, and removing them
   *  again throws from inside the library. */
  destroy(): void {
    this.dead = true;
    this.teardown();
  }

  // --- internals ------------------------------------------------------------

  private teardown(): void {
    for (const l of this.live) {
      for (const s of l.series) {
        try {
          this.chart.removeSeries(s.ser);
        } catch {
          // Already gone with its pane.
        }
      }
      if (l.markers) {
        try {
          l.markers.detach();
        } catch {
          // Same.
        }
      }
    }
    this.live = [];
  }

  /** Rebuild from scratch. Removing a study takes its pane with it, which
   *  renumbers every pane above — at this scale a full rebuild is cheaper to
   *  write than the bookkeeping that would avoid it, and impossible to get
   *  wrong. */
  private build(): void {
    if (this.dead) return;
    this.teardown();
    const cat = catalogue();
    let mounted = 0;
    for (const spec of this.specs) {
      const entry = findStudy(spec.key);
      const l: Live = {
        spec,
        entry,
        series: [],
        markers: null,
        report: { id: spec.id, error: null, plots: 0, markers: 0 },
      };
      this.live.push(l);
      if (!entry) {
        // Either the catalogue hasn't landed yet (silent — `setSpecs` has a
        // rebuild queued behind the import) or the package no longer exports it.
        l.report.error = cat ? "not in this version of the catalogue" : null;
        continue;
      }
      // A hidden study is not computed at all. The eye on its legend row is the
      // one switch on this chart that is also a performance switch — the fixed
      // layers mostly keep computing while hidden, because their cost is paid by
      // the engine either way.
      if (spec.hidden) {
        l.report.error = null;
        continue;
      }
      const res = this.compute(l);
      if (!res) continue;

      // A pane per non-overlay study, allocated at the end so the layer always
      // sits below CVD and the vol ruler however those two came and went.
      const paneIdx = entry.overlay ? 0 : this.chart.panes().length;
      for (const cfg of entry.mod.plotConfig ?? []) {
        if (cfg.display === "none") continue;
        const data = cleanPlot(res.plots?.[cfg.id]);
        // A plot that computed nothing over these bars gets no series. Most of
        // that bucket is event-driven (a pattern that did not occur), and an
        // empty series still votes in the legend and the data window.
        if (!data.length) continue;
        const hist = HISTOGRAM_STYLES.has(cfg.style ?? "line");
        // The spec's colour if the user picked one, else the package's own. Read
        // through `studyColor` so this and the legend swatch cannot drift.
        const color = studyColor(entry, spec, cfg.id);
        const ser = hist
          ? this.chart.addSeries(HistogramSeries, histOptions(cfg, color), paneIdx)
          : this.chart.addSeries(
              LineSeries,
              lineOptions(cfg, color, studyWidth(entry, spec, cfg.id), studyLineStyle(entry, spec, cfg.id)),
              paneIdx,
            );
        ser.setData(data);
        l.series.push({ id: cfg.id, ser });
      }
      // Reference levels (RSI's 70/30, an oscillator's zero) hang off the
      // study's first series, so they die with it.
      const host = l.series[0]?.ser;
      if (host) {
        for (const h of entry.mod.hlineConfig ?? []) {
          host.createPriceLine({
            price: h.price,
            color: h.color ?? "rgba(148, 163, 184, 0.5)",
            lineWidth: lineWidth(h.linewidth),
            lineStyle: LINE_STYLE[h.linestyle ?? "dashed"] ?? LineStyle.Dashed,
            axisLabelVisible: false,
            title: h.title ?? "",
          });
        }
      }
      this.setMarkers(l, res.markers);
      l.report.plots = l.series.length;
      if (!l.series.length && !l.report.markers) l.report.error = "no plottable output";
      if (l.series.length && !entry.overlay) mounted++;
    }
    if (mounted) {
      const panes = this.chart.panes();
      panes[0]?.setStretchFactor(PRICE_STRETCH);
      // Ours are the last `mounted` panes, by construction above.
      for (let i = panes.length - mounted; i < panes.length; i++)
        panes[i]?.setStretchFactor(STUDY_STRETCH);
    }
    this.report();
  }

  /** Recompute every study against the bars in hand and push the new values into
   *  the series already on the chart. Falls back to a full rebuild when a study
   *  starts or stops emitting a plot — which is what an event-driven one does the
   *  first time its pattern prints. */
  private redraw(): void {
    if (this.dead || !this.live.length) return;
    for (const l of this.live) {
      if (!l.entry || l.spec.hidden) continue;
      const res = this.compute(l);
      if (!res) continue;
      const want = (l.entry.mod.plotConfig ?? [])
        .filter((c) => c.display !== "none")
        .map((c) => ({ cfg: c, data: cleanPlot(res.plots?.[c.id]) }))
        .filter((p) => p.data.length);
      if (want.length !== l.series.length || want.some((p, i) => p.cfg.id !== l.series[i].id)) {
        this.build();
        return;
      }
      want.forEach((p, i) => l.series[i].ser.setData(p.data));
      this.setMarkers(l, res.markers);
      l.report.plots = l.series.length;
      l.report.error = l.series.length || l.report.markers ? null : "no plottable output";
    }
    this.report();
  }

  /** Run one study, recording what went wrong rather than letting it out. A
   *  transpiled indicator throwing on our bars is a fact about that indicator,
   *  not a reason for the chart to stop drawing. */
  private compute(l: Live) {
    if (!l.entry) return null;
    try {
      const res = l.entry.mod.calculate(this.bars, l.spec.inputs);
      l.report.error = null;
      return res;
    } catch (e) {
      l.report.error = String((e as Error)?.message ?? e);
      return null;
    }
  }

  private setMarkers(l: Live, marks: StudyMarker[] | undefined): void {
    // `setData` does not sort, and the packaged pattern studies emit in
    // discovery order — which for the multi-timeframe ones is not bar order.
    const ms = (marks ?? [])
      .filter((m) => m && m.time != null)
      .slice()
      .sort((a, b) => a.time - b.time) as unknown as SeriesMarker<Time>[];
    l.report.markers = ms.length;
    if (!ms.length) {
      l.markers?.setMarkers([]);
      return;
    }
    if (l.markers) l.markers.setMarkers(ms);
    else l.markers = createSeriesMarkers(this.price, ms);
  }

  private report(): void {
    this.onReport?.(this.live.map((l) => ({ ...l.report })));
  }
}

/** A plot as lightweight-charts will take it: no gaps, no NaNs. Every recursive
 *  indicator has a seeding window at the front where it genuinely has no value,
 *  and `undefined` there would be drawn as a whitespace point rather than
 *  skipped. */
function cleanPlot(raw: { time: number; value: number }[] | undefined) {
  if (!raw) return [];
  const out: { time: Time; value: number }[] = [];
  for (const d of raw) {
    if (!d || d.value == null || !Number.isFinite(d.value)) continue;
    out.push({ time: d.time as Time, value: d.value });
  }
  return out;
}

const common = (cfg: StudyPlotConfig, color: string) => ({
  color: color || cfg.color || "#94a3b8",
  priceLineVisible: false,
  lastValueVisible: false,
  title: "",
});

// Width and dash resolve through lib/studies (spec override → house default →
// the package's own), which also owns the rule that Pine's `circles`/`cross`
// point plots draw dotted rather than joined into a level never computed.
const lineOptions = (cfg: StudyPlotConfig, color: string, width: number, style: string) => ({
  ...common(cfg, color),
  lineWidth: lineWidth(width),
  lineStyle: LINE_STYLE[style] ?? LineStyle.Solid,
  crosshairMarkerVisible: false,
});

const histOptions = (cfg: StudyPlotConfig, color: string) => ({
  ...common(cfg, color),
  base: 0,
});
