"""Charts: Plotly for stats/calendar, lightweight-charts for candlestick views."""

from __future__ import annotations

import calendar
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_lightweight_charts_pro import (
    BandSeries,
    CandlestickSeries,
    Chart,
    ChartOptions,
    HistogramSeries,
    Marker,
    MarkerPosition,
    MarkerShape,
    TradeData,
    TradeVisualizationOptions,
)
from lightweight_charts_pro.charts.options.layout_options import (
    GridLineOptions,
    GridOptions,
    LayoutOptions,
    PaneHeightOptions,
)
from lightweight_charts_pro.charts.options.price_line_options import PriceLineOptions
from lightweight_charts_pro.data.tooltip import TooltipConfig, TooltipField
from lightweight_charts_pro.type_definitions.colors import BackgroundSolid
from lightweight_charts_pro.type_definitions.enums import TooltipPosition, TooltipType

GREEN = "#21c07a"
RED = "#f5455f"
ACCENT = "#6c5ce7"
GOLD = "#e0a52a"
TEXT = "#e6e8ee"
MUTED = "#8a8f9c"
GRID = "#2a2e38"


def _apply_theme(fig: go.Figure) -> go.Figure:
    """Transparent dark canvas + faint gridlines so charts blend into cards."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color=TEXT, size=12),
        title_font=dict(color=TEXT, size=15),
        legend=dict(font=dict(color=MUTED)),
        hoverlabel=dict(bgcolor="#1a1d27", bordercolor=GRID,
                        font=dict(color=TEXT, family="Inter, sans-serif")),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID,
                     tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID,
                     tickfont=dict(color=MUTED))
    return fig


def equity_curve_fig(eq: pd.DataFrame) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.68, 0.32], subplot_titles=("Equity curve", "Drawdown"),
    )
    fig.add_trace(
        go.Scatter(x=eq["trade_no"], y=eq["equity"], mode="lines",
                   line=dict(color=ACCENT, width=2.5), name="Equity",
                   fill="tozeroy", fillcolor="rgba(108,92,231,0.16)"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=eq["trade_no"], y=eq["drawdown"], mode="lines",
                   fill="tozeroy", line=dict(color=RED, width=1),
                   fillcolor="rgba(245,69,95,0.18)", name="Drawdown"),
        row=2, col=1,
    )
    fig.update_layout(height=460, showlegend=False, margin=dict(t=40, b=30))
    fig.update_xaxes(title_text="Trade #", row=2, col=1)
    return _apply_theme(fig)


def daily_pnl_fig(daily: pd.DataFrame) -> go.Figure:
    colors = [GREEN if v >= 0 else RED for v in daily["net_pnl"]]
    fig = go.Figure(go.Bar(x=daily["date"], y=daily["net_pnl"], marker_color=colors,
                           marker_line_width=0))
    fig.update_layout(height=320, title="Daily net PnL", margin=dict(t=40, b=30),
                      yaxis_title="USD", bargap=0.25)
    return _apply_theme(fig)


def distribution_fig(trades: pd.DataFrame) -> go.Figure:
    pnl = trades["net_pnl"].astype(float)
    fig = go.Figure(go.Histogram(x=pnl, nbinsx=30, marker_color=ACCENT,
                                 marker_line_width=0))
    fig.add_vline(x=0, line_color=MUTED, line_dash="dash")
    fig.update_layout(height=320, title="Trade PnL distribution",
                      xaxis_title="Net PnL (USD)", yaxis_title="Trades",
                      margin=dict(t=40, b=30), bargap=0.05)
    return _apply_theme(fig)


def calendar_fig(daily: pd.DataFrame, year: int, month: int) -> go.Figure:
    """Month grid (weeks x weekdays); each cell shows day net PnL + trade count."""
    by_day = {}
    if not daily.empty:
        for _, r in daily.iterrows():
            d = pd.Timestamp(r["date"]).date()
            if d.year == year and d.month == month:
                by_day[d.day] = (r["net_pnl"], int(r["trades"]))

    cal = calendar.Calendar(firstweekday=0)  # Monday first
    weeks = cal.monthdayscalendar(year, month)
    n_rows = len(weeks)
    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    z, text = [], []
    max_abs = max((abs(v[0]) for v in by_day.values()), default=1) or 1
    for week in weeks:
        zrow, trow = [], []
        for col, day in enumerate(week):
            if day == 0:
                zrow.append(None)
                trow.append("")
                continue
            if day in by_day:
                pnl, cnt = by_day[day]
                zrow.append(pnl / max_abs)
                trow.append(f"<b>{day}</b><br>{pnl:+,.0f}<br>{cnt} trd")
            else:
                zrow.append(0.0)
                trow.append(f"<b>{day}</b>")
        z.append(zrow)
        text.append(trow)

    fig = go.Figure(go.Heatmap(
        z=z, text=text, texttemplate="%{text}", textfont=dict(size=12),
        x=weekday_labels, y=[f"W{i+1}" for i in range(n_rows)],
        colorscale=[[0, RED], [0.5, "#1a1d27"], [1, GREEN]],
        zmid=0, showscale=False, xgap=4, ygap=4,
        hoverinfo="text",
    ))
    fig.update_yaxes(autorange="reversed", showticklabels=False, showgrid=False)
    fig.update_xaxes(showgrid=False, side="top", tickfont=dict(color=MUTED))
    month_total = sum(v[0] for v in by_day.values())
    fig.update_layout(
        height=120 + 78 * n_rows,
        title=f"{calendar.month_name[month]} {year} — net {month_total:+,.0f}",
        margin=dict(t=50, b=20),
    )
    return _apply_theme(fig)


def day_trades_bar_fig(day_trades: pd.DataFrame) -> go.Figure:
    """One bar per trade for a day, green/red by outcome, ordered by entry time."""
    t = day_trades.sort_values("entry_ts_utc").reset_index(drop=True)
    labels = [f"#{int(n)}" for n in t["trade_no"]] if "trade_no" in t else \
        [f"#{i+1}" for i in range(len(t))]
    pnl = t["net_pnl"].astype(float)
    colors = [GREEN if v >= 0 else RED for v in pnl]
    times = t["entry_ts_local"].dt.strftime("%H:%M:%S")
    fig = go.Figure(go.Bar(
        x=labels, y=pnl, marker_color=colors, marker_line_width=0,
        customdata=times, hovertemplate="%{x} @ %{customdata}<br>%{y:+,.0f}<extra></extra>",
    ))
    fig.update_layout(height=300, title="Per-trade net PnL", margin=dict(t=40, b=30),
                      yaxis_title="USD", xaxis_title="Trade", bargap=0.3)
    return _apply_theme(fig)


# --- Lightweight-charts (TradingView) candlestick views ------------------
# These two return a `Chart`; the caller renders with `.render(key=...)`.
# Weekend/overnight gaps collapse automatically (missing bars aren't drawn), so
# no rangebreaks are needed. There is no absolute set-visible-range in the
# wrapper, so charts open on the full loaded window (no auto-zoom to the trade);
# the trade is located by its rectangle + fill markers.

BG = "#0e1117"          # matches the app background (.streamlit/config.toml)
VWAP_FILL = "rgba(108,92,231,0.06)"
VOL_UP = "rgba(33,192,122,0.5)"
VOL_DOWN = "rgba(245,69,95,0.5)"

_OHLC_TOOLTIP = TooltipConfig(
    type=TooltipType.OHLC, position=TooltipPosition.CURSOR,
    fields=[TooltipField(label="O", value_key="open", precision=2),
            TooltipField(label="H", value_key="high", precision=2),
            TooltipField(label="L", value_key="low", precision=2),
            TooltipField(label="C", value_key="close", precision=2, color=ACCENT)],
)
_TRADE_TOOLTIP = TooltipConfig(type=TooltipType.TRADE, position=TooltipPosition.CURSOR)


def _to_local(s, tz) -> pd.Series:
    """UTC instants -> naive wall-clock in the display tz, so the time axis reads
    in local time (lightweight-charts treats a naive stamp as its epoch)."""
    return pd.to_datetime(s, utc=True).dt.tz_convert(tz).dt.tz_localize(None)


def _local_ts(ts, tz) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.tz_convert(tz).tz_localize(None)


def _chart_options(height: int) -> ChartOptions:
    return ChartOptions(
        height=height,
        layout=LayoutOptions(
            background_options=BackgroundSolid(color=BG), text_color=MUTED,
            pane_heights={0: PaneHeightOptions(factor=3.0),
                          1: PaneHeightOptions(factor=1.0)},
        ),
        grid=GridOptions(vert_lines=GridLineOptions(color=GRID),
                         horz_lines=GridLineOptions(color=GRID)),
        trade_visualization=TradeVisualizationOptions(
            style="rectangles", rectangle_color_profit=GREEN,
            rectangle_color_loss=RED, rectangle_fill_opacity=0.12,
            show_pnl_in_markers=True, show_quantity=False),
    )


def _candle_series(bars: pd.DataFrame, tz) -> CandlestickSeries:
    df = pd.DataFrame({"time": _to_local(bars["ts_utc"], tz),
                       "open": bars["open"], "high": bars["high"],
                       "low": bars["low"], "close": bars["close"]})
    s = CandlestickSeries(data=df, column_mapping={
        "time": "time", "open": "open", "high": "high",
        "low": "low", "close": "close"})
    s.set_up_color(GREEN).set_down_color(RED)
    s.set_wick_up_color(GREEN).set_wick_down_color(RED)
    s.set_tooltip("candles")
    return s


def _vwap_series(bars: pd.DataFrame, tz) -> BandSeries:
    """Session VWAP ±1 volume-weighted standard deviation as a 3-line band."""
    typ = (bars["high"] + bars["low"] + bars["close"]) / 3
    vol = bars["volume"].astype(float)
    cum = vol.cumsum().where(lambda c: c != 0)
    vwap = (typ * vol).cumsum() / cum
    std = ((vol * (typ - vwap) ** 2).cumsum() / cum) ** 0.5
    df = pd.DataFrame({
        "time": _to_local(bars["ts_utc"], tz),
        "upper": vwap + std, "middle": vwap, "lower": vwap - std,
        "middle_line_color": GOLD, "upper_line_color": MUTED,
        "lower_line_color": MUTED, "upper_fill_color": VWAP_FILL,
        "lower_fill_color": VWAP_FILL,
    }).dropna(subset=["middle"])
    return BandSeries(data=df, price_scale_id="right", pane_id=0, column_mapping={
        "time": "time", "upper": "upper", "middle": "middle", "lower": "lower",
        "upper_line_color": "upper_line_color",
        "middle_line_color": "middle_line_color",
        "lower_line_color": "lower_line_color",
        "upper_fill_color": "upper_fill_color",
        "lower_fill_color": "lower_fill_color"})


def _volume_series(bars: pd.DataFrame, tz) -> HistogramSeries:
    up = bars["close"] >= bars["open"]
    df = pd.DataFrame({"time": _to_local(bars["ts_utc"], tz),
                       "value": bars["volume"].astype(float),
                       "color": [VOL_UP if u else VOL_DOWN for u in up]})
    return HistogramSeries(data=df, pane_id=1, column_mapping={
        "time": "time", "value": "value", "color": "color"})


def _fill_marker_tuples(fdf: pd.DataFrame | None, tz) -> list:
    """(time, Marker) per execution: Buy = up arrow below bar, Sell = down arrow."""
    if fdf is None or fdf.empty:
        return []
    t = _to_local(fdf["ts_utc"], tz)
    res = []
    for tk, px, d in zip(t, fdf["price"], fdf["direction"]):
        if d == "Buy":
            mk = Marker(time=tk, price=float(px), position=MarkerPosition.BELOW_BAR,
                        shape=MarkerShape.ARROW_UP, color=GREEN)
        else:
            mk = Marker(time=tk, price=float(px), position=MarkerPosition.ABOVE_BAR,
                        shape=MarkerShape.ARROW_DOWN, color=RED)
        res.append((tk, mk))
    return res


def _trade_data(trade: pd.Series, tz) -> TradeData:
    return TradeData(
        entry_time=_local_ts(trade["entry_ts_utc"], tz),
        entry_price=float(trade["avg_entry"]),
        exit_time=_local_ts(trade["exit_ts_utc"], tz),
        exit_price=float(trade["avg_exit"]),
        is_profitable=bool(trade["net_pnl"] >= 0),
        id=str(trade.get("trade_no", "T")),
    )


def _register_tooltips(chart: Chart) -> None:
    chart.add_tooltip_config("candles", _OHLC_TOOLTIP)
    chart.add_tooltip_config("trade", _TRADE_TOOLTIP)


def day_session_fig(day_trades: pd.DataFrame, bars: pd.DataFrame, disp_tz) -> Chart:
    """Full-day candlestick (+ VWAP band + volume) with every trade's fills and
    an outcome-tinted holding rectangle per trade."""
    candles = _candle_series(bars, disp_tz)

    fills = [pd.DataFrame(tr["fills"]) for _, tr in day_trades.iterrows()
             if tr.get("fills")]
    fdf = pd.concat(fills, ignore_index=True) if fills else None
    tuples = sorted(_fill_marker_tuples(fdf, disp_tz), key=lambda x: x[0])
    if tuples:
        candles.add_markers([mk for _, mk in tuples])

    chart = Chart(series=[candles, _vwap_series(bars, disp_tz),
                          _volume_series(bars, disp_tz)],
                  options=_chart_options(620))
    trade_list = [_trade_data(tr, disp_tz) for _, tr in day_trades.iterrows()
                  if pd.notna(tr["avg_entry"]) and pd.notna(tr["avg_exit"])]
    if trade_list:
        chart.add_trades(trade_list)
    _register_tooltips(chart)
    return chart


def resample_ohlc(bars: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """Aggregate 1m bars to a coarser timeframe (e.g. '5min'). Free / local."""
    if bars is None or bars.empty or rule in (None, "1min"):
        return bars
    df = bars.set_index("ts_utc")
    agg = df.resample(rule, label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    )
    return agg.dropna(subset=["open"]).reset_index()


def reconstruction_fig(trade: pd.Series, bars: pd.DataFrame,
                       excursion: dict | None, disp_tz) -> Chart:
    """Single-trade candlestick (+ VWAP band + volume) with per-fill markers,
    avg entry/exit price lines, an outcome-tinted holding rectangle (with a
    cursor PnL tooltip) and MAE/MFE markers."""
    candles = _candle_series(bars, disp_tz)

    fdf = pd.DataFrame(trade["fills"]) if trade.get("fills") else None
    tuples = _fill_marker_tuples(fdf, disp_tz)
    if excursion:
        entry = pd.Timestamp(trade["entry_ts_utc"])
        exit_ = pd.Timestamp(trade["exit_ts_utc"])
        mid = _local_ts(entry + (exit_ - entry) / 2, disp_tz)
        tuples.append((mid, Marker(
            time=mid, price=float(excursion["mfe_price"]),
            position=MarkerPosition.ABOVE_BAR, shape=MarkerShape.CIRCLE,
            color=GREEN, text="MFE")))
        tuples.append((mid, Marker(
            time=mid, price=float(excursion["mae_price"]),
            position=MarkerPosition.BELOW_BAR, shape=MarkerShape.CIRCLE,
            color=RED, text="MAE")))
    tuples.sort(key=lambda x: x[0])
    if tuples:
        candles.add_markers([mk for _, mk in tuples])

    if pd.notna(trade["avg_entry"]):
        candles.add_price_line(PriceLineOptions(
            price=float(trade["avg_entry"]), color=ACCENT, line_width=1,
            title=f"avg entry {trade['avg_entry']:.2f}", axis_label_visible=True))
    if pd.notna(trade["avg_exit"]):
        candles.add_price_line(PriceLineOptions(
            price=float(trade["avg_exit"]), color=GOLD, line_width=1,
            title=f"avg exit {trade['avg_exit']:.2f}", axis_label_visible=True))

    chart = Chart(series=[candles, _vwap_series(bars, disp_tz),
                          _volume_series(bars, disp_tz)],
                  options=_chart_options(720))
    if pd.notna(trade["avg_entry"]) and pd.notna(trade["avg_exit"]):
        chart.add_trades([_trade_data(trade, disp_tz)])
    _register_tooltips(chart)
    return chart


def adaptive_window(entry_utc, exit_utc) -> tuple:
    """Pad each side by a fixed 2 hours."""
    entry = pd.Timestamp(entry_utc)
    exit_ = pd.Timestamp(exit_utc)
    pad = timedelta(hours=2)
    return entry - pad, exit_ + pad
