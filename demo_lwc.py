"""Standalone demo: reconstruct a single trade with streamlit-lightweight-charts-pro.

Mirrors what `reconstruction_fig` does in Plotly — candles, entry/exit markers,
a holding band tinted by outcome, avg entry/exit price lines, and MAE/MFE markers
— but using TradingView's lightweight-charts via the *-pro wrapper.

Run:  uv run streamlit run demo_lwc.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_lightweight_charts_pro import (
    BaselineSeries,
    Chart,
    ChartOptions,
    CandlestickSeries,
    HistogramSeries,
    LegendOptions,
    LineSeries,
    Marker,
    MarkerPosition,
    MarkerShape,
    SignalSeries,
    TradeData,
    TradeVisualizationOptions,
)
from lightweight_charts_pro.charts.options.price_line_options import PriceLineOptions
from lightweight_charts_pro.charts.options.ui_options import (
    RangeConfig,
    RangeSwitcherOptions,
    TimeRange,
)
from lightweight_charts_pro.data.tooltip import TooltipConfig, TooltipField
from lightweight_charts_pro.type_definitions.enums import TooltipPosition, TooltipType

GREEN, RED, ACCENT, GOLD = "#21c07a", "#f5455f", "#6c5ce7", "#e0a52a"

st.set_page_config(page_title="LWC demo", layout="wide")
st.title("Lightweight Charts Pro — trade reconstruction demo")


@st.cache_data
def make_bars(n: int = 180, seed: int = 7) -> pd.DataFrame:
    """Synthetic 1m NQ-like random walk so the demo runs with no data source."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-05-28 13:30", tz="UTC")
    times = pd.date_range(start, periods=n, freq="1min")
    steps = rng.normal(0, 4, n).cumsum()
    close = 18500 + steps
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0, 6, n)
    low = np.minimum(open_, close) - rng.uniform(0, 6, n)
    volume = rng.integers(150, 1200, n).astype(float)
    return pd.DataFrame(
        {"time": times, "open": open_, "high": high, "low": low,
         "close": close, "volume": volume}
    )


bars = make_bars()

# Session VWAP: cumulative typical-price * volume / cumulative volume.
typical = (bars["high"] + bars["low"] + bars["close"]) / 3
bars["vwap"] = (typical * bars["volume"]).cumsum() / bars["volume"].cumsum()

# A made-up long trade: enter ~bar 40, exit ~bar 110.
entry_i, exit_i = 40, 110
entry_t, exit_t = bars["time"].iloc[entry_i], bars["time"].iloc[exit_i]
entry_px = float(bars["close"].iloc[entry_i])
exit_px = float(bars["close"].iloc[exit_i])
profitable = exit_px >= entry_px

# MAE/MFE within the holding window.
held = bars.iloc[entry_i : exit_i + 1]
mfe_row = held.loc[held["high"].idxmax()]
mae_row = held.loc[held["low"].idxmin()]

# --- Build the chart -------------------------------------------------------
series = CandlestickSeries(data=bars, column_mapping={
    "time": "time", "open": "open", "high": "high", "low": "low", "close": "close",
})
series.set_up_color(GREEN).set_down_color(RED)
series.set_wick_up_color(GREEN).set_wick_down_color(RED)

# avg entry / exit as horizontal price lines (your add_hline equivalents)
series.add_price_line(PriceLineOptions(
    price=entry_px, color=ACCENT, line_width=1, title=f"avg entry {entry_px:.2f}",
    axis_label_visible=True,
))
series.add_price_line(PriceLineOptions(
    price=exit_px, color=GOLD, line_width=1, title=f"avg exit {exit_px:.2f}",
    axis_label_visible=True,
))

# MAE/MFE as markers (your star markers equivalent)
series.add_markers([
    Marker(time=mfe_row["time"], price=float(mfe_row["high"]),
           position=MarkerPosition.ABOVE_BAR, shape=MarkerShape.ARROW_DOWN,
           color=GREEN, text="MFE"),
    Marker(time=mae_row["time"], price=float(mae_row["low"]),
           position=MarkerPosition.BELOW_BAR, shape=MarkerShape.ARROW_UP,
           color=RED, text="MAE"),
])

# Session VWAP overlaid on the candle pane (shares the price scale).
vwap_series = LineSeries(
    data=bars, column_mapping={"time": "time", "value": "vwap"}, pane_id=0,
)
vwap_series.set_display_name("VWAP")
vwap_series.line_options.color = GOLD
vwap_series.line_options.line_width = 2

# Volume histogram on its own pane below the price, green/red by candle outcome.
vol_df = bars[["time", "volume"]].copy()
vol_df["color"] = np.where(bars["close"] >= bars["open"],
                           "rgba(33,192,122,0.5)", "rgba(245,69,95,0.5)")
volume_series = HistogramSeries(
    data=vol_df,
    column_mapping={"time": "time", "value": "volume", "color": "color"},
    pane_id=1,
)

# (5) SignalSeries: full-height background band over the first 30 min ("opening
# range"). value != 0 paints the band; the colour comes from signal_color.
sig_df = bars[["time"]].copy()
sig_df["value"] = np.where(np.arange(len(bars)) < 30, 1, 0)
session_signal = SignalSeries(
    data=sig_df, column_mapping={"time": "time", "value": "value"},
    signal_color="rgba(108,92,231,0.10)", pane_id=0,
)

# (2) Crosshair-tracking legend: live OHLC values follow the cursor.
series.set_legend(LegendOptions(
    visible=True, position="top-left", update_on_crosshair=True,
    show_values=True, value_format=".2f",
))

# (6) add_tooltip_config: a custom OHLC+volume tooltip linked to the candles.
candle_tooltip = TooltipConfig(
    show_date=True, show_time=True,
    fields=[
        TooltipField(label="O", value_key="open", precision=2),
        TooltipField(label="H", value_key="high", precision=2),
        TooltipField(label="L", value_key="low", precision=2),
        TooltipField(label="C", value_key="close", precision=2, color=ACCENT),
    ],
)

# Trade tooltip pinned to the cursor (so the PnL popup follows the mouse rather
# than docking at a fixed spot / the bottom pane).
trade_tooltip = TooltipConfig(type=TooltipType.TRADE, position=TooltipPosition.CURSOR)

# Cumulative-delta line on its own pane (baseline at 0: green = net buying above,
# red = net selling below). Synthetic here — real data comes from ATAS bid/ask
# (aggressor) volume.
bar_range = (bars["high"] - bars["low"]).replace(0, 1)
bar_delta = ((bars["close"] - bars["open"]) / bar_range) * bars["volume"]
cum_delta = bar_delta.cumsum()
cvd_df = pd.DataFrame({"time": bars["time"], "value": cum_delta})
cvd_series = BaselineSeries(
    data=cvd_df, column_mapping={"time": "time", "value": "value"}, pane_id=2,
)
cvd_series.set_base_value(0.0)
cvd_series.set_display_name("Cumulative delta")

# Single chart: candles+VWAP+signal (pane 0), volume (pane 1), CVD candles (pane 2).
chart = Chart(
    series=[session_signal, series, vwap_series, volume_series, cvd_series],
    options=ChartOptions(
        height=720,
        range_switcher=RangeSwitcherOptions(
            visible=True, position="top-right",
            ranges=[
                RangeConfig(text="5m", range=TimeRange.FIVE_MINUTES),
                RangeConfig(text="15m", range=TimeRange.FIFTEEN_MINUTES),
                RangeConfig(text="1H", range=TimeRange.ONE_HOUR),
                RangeConfig(text="All", range=TimeRange.ALL),
            ],
        ),
        trade_visualization=TradeVisualizationOptions(
            style="rectangles",
            rectangle_color_profit=GREEN, rectangle_color_loss=RED,
            rectangle_fill_opacity=0.12,
            show_pnl_in_markers=True, show_quantity=False,
        ),
    ),
)
chart.add_tooltip_config("candles", candle_tooltip)
chart.add_tooltip_config("trade", trade_tooltip)
series.set_tooltip("candles")   # link the candle series to the named config
chart.add_trades([TradeData(
    entry_time=entry_t, entry_price=entry_px,
    exit_time=exit_t, exit_price=exit_px,
    is_profitable=profitable, id="T1",
)])

c1, c2, c3, c4 = st.columns(4)
c1.metric("Entry", f"{entry_px:.2f}")
c2.metric("Exit", f"{exit_px:.2f}")
c3.metric("Net (pts)", f"{exit_px - entry_px:+.2f}")
c4.metric("Outcome", "WIN" if profitable else "LOSS")

chart.render(key="reconstruction_demo")

st.caption(
    "One chart, three panes: candles (VWAP, opening-range band, trade rectangle, "
    "MAE/MFE, range buttons), volume, and cumulative delta (baseline at 0 — green "
    "net buying, red net selling; synthetic here, real data comes from ATAS "
    "bid/ask volume). Hover the trade for a cursor-pinned PnL tooltip."
)
