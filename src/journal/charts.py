"""Plotly figures: equity, drawdown, distribution, PnL calendar, reconstruction."""

from __future__ import annotations

import calendar
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

GREEN = "#2e9e5b"
RED = "#d6455d"


def equity_curve_fig(eq: pd.DataFrame) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.68, 0.32], subplot_titles=("Equity curve", "Drawdown"),
    )
    fig.add_trace(
        go.Scatter(x=eq["trade_no"], y=eq["equity"], mode="lines",
                   line=dict(color="#3b7dd8", width=2), name="Equity"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=eq["trade_no"], y=eq["drawdown"], mode="lines",
                   fill="tozeroy", line=dict(color=RED, width=1), name="Drawdown"),
        row=2, col=1,
    )
    fig.update_layout(height=460, showlegend=False, margin=dict(t=40, b=30))
    fig.update_xaxes(title_text="Trade #", row=2, col=1)
    return fig


def daily_pnl_fig(daily: pd.DataFrame) -> go.Figure:
    colors = [GREEN if v >= 0 else RED for v in daily["net_pnl"]]
    fig = go.Figure(go.Bar(x=daily["date"], y=daily["net_pnl"], marker_color=colors))
    fig.update_layout(height=320, title="Daily net PnL", margin=dict(t=40, b=30),
                      yaxis_title="USD")
    return fig


def distribution_fig(trades: pd.DataFrame) -> go.Figure:
    pnl = trades["net_pnl"].astype(float)
    fig = go.Figure(go.Histogram(x=pnl, nbinsx=30, marker_color="#3b7dd8"))
    fig.add_vline(x=0, line_color="#888", line_dash="dash")
    fig.update_layout(height=320, title="Trade PnL distribution",
                      xaxis_title="Net PnL (USD)", yaxis_title="Trades",
                      margin=dict(t=40, b=30))
    return fig


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
        colorscale=[[0, RED], [0.5, "#2b2b2b"], [1, GREEN]],
        zmid=0, showscale=False, xgap=3, ygap=3,
        hoverinfo="text",
    ))
    fig.update_yaxes(autorange="reversed", showticklabels=False)
    month_total = sum(v[0] for v in by_day.values())
    fig.update_layout(
        height=120 + 78 * n_rows,
        title=f"{calendar.month_name[month]} {year} — net {month_total:+,.0f}",
        margin=dict(t=50, b=20),
    )
    return fig


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


def reconstruction_fig(trade: pd.Series, bars: pd.DataFrame, excursion: dict | None,
                       kl_tz, focus_utc: tuple | None = None) -> go.Figure:
    """Candlestick with fills, avg lines, band, MAE/MFE.

    `bars` is the full cached session; `focus_utc` is the (start, end) UTC range
    to open zoomed into. Drag pans and the mouse wheel zooms across the rest of
    the loaded session.
    """
    b = bars.copy()
    b["ts_kl"] = pd.to_datetime(b["ts_utc"], utc=True).dt.tz_convert(kl_tz)

    fig = go.Figure(go.Candlestick(
        x=b["ts_kl"], open=b["open"], high=b["high"], low=b["low"], close=b["close"],
        increasing_line_color=GREEN, decreasing_line_color=RED, name="1m",
    ))

    # per-fill markers from executions
    fills = trade.get("fills")
    if fills:
        fdf = pd.DataFrame(fills)
        fdf["ts_kl"] = pd.to_datetime(fdf["ts_local"], utc=True).dt.tz_convert(kl_tz) \
            if fdf["ts_local"].dt.tz is None else fdf["ts_local"].dt.tz_convert(kl_tz)
        buys = fdf[fdf["direction"] == "Buy"]
        sells = fdf[fdf["direction"] == "Sell"]
        if not buys.empty:
            fig.add_trace(go.Scatter(
                x=buys["ts_kl"], y=buys["price"], mode="markers", name="Buy",
                marker=dict(symbol="triangle-up", size=13, color=GREEN,
                            line=dict(width=1, color="white")),
            ))
        if not sells.empty:
            fig.add_trace(go.Scatter(
                x=sells["ts_kl"], y=sells["price"], mode="markers", name="Sell",
                marker=dict(symbol="triangle-down", size=13, color=RED,
                            line=dict(width=1, color="white")),
            ))

    # avg entry / exit lines
    if pd.notna(trade["avg_entry"]):
        fig.add_hline(y=trade["avg_entry"], line_dash="dash", line_color="#3b7dd8",
                      annotation_text=f"avg entry {trade['avg_entry']:.2f}")
    if pd.notna(trade["avg_exit"]):
        fig.add_hline(y=trade["avg_exit"], line_dash="dash", line_color="#e0a52a",
                      annotation_text=f"avg exit {trade['avg_exit']:.2f}")

    # holding band tinted by outcome
    entry_kl = pd.Timestamp(trade["entry_ts_local"]).tz_convert(kl_tz)
    exit_kl = pd.Timestamp(trade["exit_ts_local"]).tz_convert(kl_tz)
    band = "rgba(46,158,91,0.12)" if trade["net_pnl"] >= 0 else "rgba(214,69,93,0.12)"
    fig.add_vrect(x0=entry_kl, x1=exit_kl, fillcolor=band, line_width=0)

    # MAE / MFE markers
    if excursion:
        mid = entry_kl + (exit_kl - entry_kl) / 2
        fig.add_trace(go.Scatter(
            x=[mid], y=[excursion["mfe_price"]], mode="markers+text",
            text=["MFE"], textposition="top center", name="MFE",
            marker=dict(symbol="star", size=12, color=GREEN),
        ))
        fig.add_trace(go.Scatter(
            x=[mid], y=[excursion["mae_price"]], mode="markers+text",
            text=["MAE"], textposition="bottom center", name="MAE",
            marker=dict(symbol="star", size=12, color=RED),
        ))

    fig.update_layout(
        height=720, xaxis_rangeslider_visible=False, dragmode="pan",
        title=f"{trade['instrument']} {trade['direction']} — "
              f"{trade['max_contracts']:.0f} lot — net {trade['net_pnl']:+,.0f}",
        margin=dict(t=50, b=30), legend=dict(orientation="h", y=1.02),
        xaxis_title="Time (KL)",
    )
    # Both axes freely rescalable: drag the plot to pan, drag an axis to zoom
    # just that axis, mouse-wheel to zoom, double-click to autoscale.
    fig.update_xaxes(fixedrange=False)
    fig.update_yaxes(fixedrange=False)

    # Open zoomed to the trade; the rest of the session is loaded for pan/zoom.
    if focus_utc is not None:
        fs = pd.Timestamp(focus_utc[0])
        fe = pd.Timestamp(focus_utc[1])
        fs = (fs.tz_localize("UTC") if fs.tzinfo is None else fs.tz_convert("UTC")).tz_convert(kl_tz)
        fe = (fe.tz_localize("UTC") if fe.tzinfo is None else fe.tz_convert("UTC")).tz_convert(kl_tz)
        fig.update_xaxes(range=[fs, fe])

        # Fit y to the focus window (Plotly won't auto-fit y on pan), including
        # the avg lines and MAE/MFE so every overlay stays in view.
        visible = b[(b["ts_kl"] >= fs) & (b["ts_kl"] <= fe)]
        if not visible.empty:
            lo = float(visible["low"].min())
            hi = float(visible["high"].max())
            extra = [trade["avg_entry"], trade["avg_exit"]]
            if excursion:
                extra += [excursion["mfe_price"], excursion["mae_price"]]
            extra = [v for v in extra if pd.notna(v)]
            lo = min([lo, *extra])
            hi = max([hi, *extra])
            pad = (hi - lo) * 0.15 or 1.0
            fig.update_yaxes(range=[lo - pad, hi + pad])

    # Collapse weekend gaps (CME is closed) so panning across days stays tight.
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


def adaptive_window(entry_utc, exit_utc) -> tuple:
    """Pad each side by a fixed 2 hours."""
    entry = pd.Timestamp(entry_utc)
    exit_ = pd.Timestamp(exit_utc)
    pad = timedelta(hours=2)
    return entry - pad, exit_ + pad
