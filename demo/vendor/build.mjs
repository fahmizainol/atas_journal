// Bundles lightweight-charts + the four community addons into one IIFE that the
// research page can inline.
//
// Why a bundle at all: docs/research/*.html renders inside `sandbox="allow-scripts"`
// (api/routers/research.py serves it, frontend/src/pages/Research.tsx frames it), so
// the page gets no network. Every other demo here inlines
// lightweight-charts.standalone.production.js for the same reason; the addons ship
// ESM only, so they need a bundler to reach the same shape.
//
// lightweight-charts is bundled *in* rather than left external. The addons import it
// as a module and the standalone build is a global — bridging the two costs a shim
// and buys nothing, and one copy means the version can never skew.
//
//     pnpm install && pnpm build     # -> lwc-addons.iife.js
//
// The output is committed so `uv run python demo/lwc_addons_demo.py` works without a
// node install. Re-run this only when bumping a version in package.json.

import { build } from 'esbuild';
import { writeFileSync } from 'node:fs';

const ENTRY = `
import {
  createChart, CandlestickSeries, HistogramSeries, LineSeries, BaselineSeries,
  createSeriesMarkers, ColorType, CrosshairMode, LineStyle, PriceScaleMode,
} from 'lightweight-charts';

import {
  createFootprintSeries, createDeltaSummarySeries, createVolumeFootprintSeries,
  createSessionVolumeProfilePrimitive, createVolumeProfilePrimitive,
  computeVwapSeriesData, setPriceScaleAutoFit,
  clusterOrderFlowBarsByMintick, buildVisibleRangeProfile, deriveFootprintMetrics,
  ORDER_FLOW_STYLE_PRESETS, ORDER_FLOW_THEME_PRESETS,
  DEFAULT_SESSION_VOLUME_PROFILE_OPTIONS, DEFAULT_DELTA_SUMMARY_SERIES_OPTIONS,
} from 'lightweight-orderflow-charts';

// Named tool imports are unnecessary: every registry entry carries a
// factory(id, anchors, style, options), so the terminal's left rail can build
// any of the 67 tools without knowing their class names. DrawingManager and the
// registry are the whole surface.
import { DrawingManager, getToolRegistry } from 'lightweight-charts-drawing';

// The whole indicator index, not a hand-picked six. Each module carries
// metadata / inputConfig / plotConfig, which is what lets the picker and the
// settings dialogs be generated rather than written — see the Terminal tab.
// Costs ~120 KB over the selective import; the catalogue is the point.
import * as INDICATORS from 'lightweight-charts-indicators';
import { taCore } from 'oakscriptjs';

import { WhiskerBoxSeries } from './box-whisker/plugins/box-whisker-series/box-whisker-series';

export {
  createChart, CandlestickSeries, HistogramSeries, LineSeries, BaselineSeries,
  createSeriesMarkers, ColorType, CrosshairMode, LineStyle, PriceScaleMode,

  createFootprintSeries, createDeltaSummarySeries, createVolumeFootprintSeries,
  createSessionVolumeProfilePrimitive, createVolumeProfilePrimitive,
  computeVwapSeriesData, setPriceScaleAutoFit,
  clusterOrderFlowBarsByMintick, buildVisibleRangeProfile, deriveFootprintMetrics,
  ORDER_FLOW_STYLE_PRESETS, ORDER_FLOW_THEME_PRESETS,
  DEFAULT_SESSION_VOLUME_PROFILE_OPTIONS, DEFAULT_DELTA_SUMMARY_SERIES_OPTIONS,

  DrawingManager, getToolRegistry,
  INDICATORS, taCore,
  WhiskerBoxSeries,
};
`;

writeFileSync(new URL('./entry.generated.js', import.meta.url), ENTRY);

const result = await build({
	entryPoints: ['entry.generated.js'],
	bundle: true,
	format: 'iife',
	globalName: 'LWCAddons',
	minify: true,
	target: 'es2020',
	outfile: 'lwc-addons.iife.js',
	legalComments: 'none',
	metafile: true,
	logLevel: 'info',
});

const bytes = Object.values(result.metafile.outputs)[0].bytes;
console.log(`lwc-addons.iife.js — ${(bytes / 1024).toFixed(0)} KB minified`);
