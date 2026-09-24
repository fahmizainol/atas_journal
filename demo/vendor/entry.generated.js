
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
