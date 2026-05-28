"""ATAS Trading Journal — Streamlit entrypoint."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from journal import charts, db, edges, excursion, ingest, metrics, trades, ui  # noqa: E402
from journal import databento_client as dbn  # noqa: E402
from journal.config import IMPORTS_DIR, KL_TZ  # noqa: E402

st.set_page_config(page_title="ATAS Journal", layout="wide")
ui.inject_css()


@st.cache_resource
def get_conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


conn = get_conn()


def fmt(x, money=True):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if x == float("inf"):
        return "∞"
    return f"${x:,.2f}" if money else f"{x:,.2f}"


# --- Load + build trade frames ------------------------------------------
def load_trades():
    ex = db.load_executions(conn)
    jr = db.load_journal(conn)
    logical = trades.build_logical_trades(ex)
    atas = trades.atas_trades(jr)
    return ex, jr, logical, atas


# --- Sidebar (data controls only) ---------------------------------------
st.sidebar.markdown("### Data")

if st.sidebar.button("Import from data/imports/", width="stretch"):
    res = ingest.import_dir(conn)
    total = sum(c["executions"] for c in res.values())
    st.sidebar.success(f"Imported {len(res)} file(s); {total} new fills.")

uploaded = st.sidebar.file_uploader("Upload ATAS .xlsx", type="xlsx", accept_multiple_files=True)
if uploaded:
    for uf in uploaded:
        dest = IMPORTS_DIR / uf.name
        dest.write_bytes(uf.getbuffer())
        c = ingest.import_file(conn, dest)
        st.sidebar.info(f"{uf.name}: +{c['executions']} fills, +{c['journal']} journal rows")

if not dbn.is_available():
    st.sidebar.warning("DATABENTO_API_KEY not set — charts/excursion disabled. "
                       "Add it to .env to enable.")
else:
    st.sidebar.caption("Databento: connected")

ex, jr, logical, atas = load_trades()

ui.app_header("ATAS Journal", "NQ futures performance & trade review")

if ex.empty and jr.empty:
    st.info("No data yet. Click **Import from data/imports/** in the sidebar.")
    st.stop()

# --- Top filter bar ------------------------------------------------------
notes_df = db.all_notes(conn)
all_tags = set()
if not notes_df.empty:
    for tj in notes_df["tags_json"].dropna():
        all_tags.update(json.loads(tj or "[]"))

with st.container(border=True):
    fc = st.columns([1.3, 2, 2.2, 1.8])
    with fc[0]:
        view = st.radio("Trade view", ["Logical", "ATAS rows"], index=0, horizontal=True)
    base = logical if view == "Logical" else atas
    instruments = sorted(base["instrument"].dropna().unique()) if not base.empty else []
    with fc[1]:
        sel_instr = st.multiselect("Instrument", instruments, default=instruments)
    with fc[2]:
        if not base.empty:
            dmin = base["entry_ts_local"].min().date()
            dmax = base["entry_ts_local"].max().date()
            dr = st.date_input("Date range", (dmin, dmax), min_value=dmin, max_value=dmax)
        else:
            dr = None
    with fc[3]:
        sel_tags = st.multiselect("Tags", sorted(all_tags))


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if sel_instr:
        out = out[out["instrument"].isin(sel_instr)]
    if dr and isinstance(dr, tuple) and len(dr) == 2:
        d0, d1 = dr
        d = out["entry_ts_local"].dt.date
        out = out[(d >= d0) & (d <= d1)]
    if sel_tags and not notes_df.empty:
        keymap = {r["trade_key"]: set(json.loads(r["tags_json"] or "[]"))
                  for _, r in notes_df.iterrows()}
        out = out[out["trade_key"].apply(lambda k: bool(keymap.get(k, set()) & set(sel_tags)))]
    return out.reset_index(drop=True)


tf = apply_filters(base)


# --- Inline trade detail (used in the Trades tab) -----------------------
def render_trade_detail(trade: pd.Series) -> None:
    ui.render_cards([
        {"label": "Direction", "value": trade["direction"]},
        {"label": "Contracts", "value": f"{trade['max_contracts']:.0f}"},
        {"label": "Net PnL", "value": fmt(trade["net_pnl"]),
         "tone": ui.tone_of(trade["net_pnl"])},
        {"label": "Avg entry", "value": fmt(trade["avg_entry"], money=False)},
        {"label": "Avg exit", "value": fmt(trade["avg_exit"], money=False)},
    ], "repeat(5, 1fr)")
    ui.render_cards([
        {"label": "Entry", "value": trade["entry_ts_local"].strftime("%Y-%m-%d %H:%M:%S")},
        {"label": "Exit", "value": trade["exit_ts_local"].strftime("%Y-%m-%d %H:%M:%S")},
        {"label": "Hold", "value": f"{trade['duration_s'] / 60:.1f}m"},
    ], "repeat(3, 1fr)")

    if dbn.is_available():
        start_utc, end_utc = charts.adaptive_window(
            trade["entry_ts_utc"], trade["exit_ts_utc"])
        bars = dbn.get_bars(trade["instrument"], start_utc, end_utc,
                            slice_to_window=False)
        if bars is not None and not bars.empty:
            exc = excursion.trade_excursion(trade)
            tf_label = st.radio("Timeframe", ["1m", "5m", "15m"], index=0,
                                horizontal=True, key=f"tf_{trade['trade_key']}")
            rule = {"1m": "1min", "5m": "5min", "15m": "15min"}[tf_label]
            pbars = charts.resample_ohlc(bars, rule)
            focus = (pd.Timestamp(trade["entry_ts_utc"]) - pd.Timedelta(minutes=45),
                     pd.Timestamp(trade["exit_ts_utc"]) + pd.Timedelta(minutes=45))
            st.plotly_chart(
                charts.reconstruction_fig(trade, pbars, exc, KL_TZ, focus_utc=focus),
                width="stretch",
                config={"scrollZoom": True, "displayModeBar": True,
                        "displaylogo": False},
            )
            st.caption(
                "Drag = pan · mouse-wheel = zoom · double-click = autoscale. "
                "To **stretch/compress an axis**, click the **Zoom** tool in the "
                "top-right toolbar, then drag along the price or time axis."
            )
            if exc:
                eff = exc["exit_efficiency"]
                ui.render_cards([
                    {"label": "MFE", "value": fmt(exc["mfe_usd"]), "tone": "pos"},
                    {"label": "MAE", "value": fmt(exc["mae_usd"]), "tone": "neg"},
                    {"label": "Exit efficiency",
                     "value": f"{eff * 100:.0f}%" if eff is not None else "—"},
                ], "repeat(3, 1fr)")
        else:
            st.warning("No market data returned for this window.")
    else:
        st.info("Set DATABENTO_API_KEY in .env to render the candlestick chart "
                "with fills, MAE/MFE and exit efficiency.")

    ui.section_title("Journal")
    existing = db.get_note(conn, trade["trade_key"])
    with st.form(f"note_{trade['trade_key']}"):
        note = st.text_area("Note", value=existing["note"], height=120)
        tags = st.text_input("Tags (comma-separated)",
                             value=", ".join(json.loads(existing["tags_json"])))
        if st.form_submit_button("Save"):
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            db.save_note(conn, trade["trade_key"], note, json.dumps(tag_list))
            st.success("Saved.")


# --- Tabs ----------------------------------------------------------------
tab_over, tab_cal, tab_edges, tab_trades, tab_xcheck = st.tabs(
    ["Overview", "Calendar", "Edges", "Trades", "ATAS Cross-check"]
)

# ---- Overview ----
with tab_over:
    m = metrics.compute_metrics(tf)
    if m.get("trades", 0) == 0:
        st.info("No trades match the current filters.")
    else:
        # Hero: featured Net PnL + headline stats
        ui.render_cards([
            {"label": "Net PnL", "value": fmt(m["net_pnl"]),
             "tone": ui.tone_of(m["net_pnl"]), "hero": True,
             "sub": f"{m['trades']} trades · {view} view"},
            {"label": "Win rate", "value": f"{m['win_rate']:.1f}%",
             "sub": f"{m['wins']}W / {m['losses']}L"},
            {"label": "Profit factor", "value": fmt(m["profit_factor"], money=False)},
            {"label": "Expectancy", "value": fmt(m["expectancy"]),
             "tone": ui.tone_of(m["expectancy"]), "sub": "per trade"},
        ], "1.5fr 1fr 1fr 1fr")

        st.plotly_chart(charts.equity_curve_fig(metrics.equity_curve(tf)),
                        width="stretch")

        # Secondary KPI grid
        ui.render_cards([
            {"label": "Avg win", "value": fmt(m["avg_win"]), "tone": "pos"},
            {"label": "Avg loss", "value": fmt(m["avg_loss"]), "tone": "neg"},
            {"label": "Best trade", "value": fmt(m["best_trade"]), "tone": "pos"},
            {"label": "Worst trade", "value": fmt(m["worst_trade"]), "tone": "neg"},
            {"label": "Max drawdown", "value": fmt(m["max_drawdown"]),
             "tone": ui.tone_of(m["max_drawdown"])},
            {"label": "Sharpe", "value": fmt(m["sharpe"], money=False)},
            {"label": "Sortino", "value": fmt(m["sortino"], money=False)},
            {"label": "Recovery factor", "value": fmt(m["recovery_factor"], money=False)},
            {"label": "Max consec W/L",
             "value": f"{m['max_consecutive_wins']} / {m['max_consecutive_losses']}"},
        ], "repeat(4, 1fr)")

        cc = st.columns(2)
        cc[0].plotly_chart(charts.daily_pnl_fig(metrics.daily_pnl(tf)), width="stretch")
        cc[1].plotly_chart(charts.distribution_fig(tf), width="stretch")

# ---- Calendar ----
with tab_cal:
    ui.section_title("Monthly PnL calendar")
    daily = metrics.daily_pnl(tf)
    if daily.empty:
        st.info("No trades to display.")
    else:
        months = sorted({(d.year, d.month) for d in daily["date"]}, reverse=True)
        labels = [f"{datetime(y, mo, 1):%B %Y}" for y, mo in months]
        pick = st.selectbox("Month", range(len(months)), format_func=lambda i: labels[i])
        y, mo = months[pick]
        st.plotly_chart(charts.calendar_fig(daily, y, mo), width="stretch")

# ---- Edges ----
with tab_edges:
    ui.section_title("Behavioral edges")
    if tf.empty:
        st.info("No trades to display.")
    else:
        cc = st.columns(2)
        with cc[0]:
            st.caption("By weekday")
            st.dataframe(edges.by_weekday(tf), width="stretch", hide_index=True)
            st.caption("By hold time")
            st.dataframe(edges.by_hold_time(tf), width="stretch", hide_index=True)
            st.caption("Long vs Short")
            st.dataframe(edges.by_direction(tf), width="stretch", hide_index=True)
        with cc[1]:
            st.caption("By hour (KL)")
            st.dataframe(edges.by_hour_kl(tf), width="stretch", hide_index=True)
            st.caption("By hour (US Eastern / session)")
            st.dataframe(edges.by_hour_et(tf), width="stretch", hide_index=True)

# ---- Trades (table + inline detail) ----
with tab_trades:
    ui.section_title("Trades", "Select a row to inspect the trade below.")
    if tf.empty:
        st.info("No trades to display.")
    else:
        disp = tf.copy()
        disp["entry"] = disp["entry_ts_local"].dt.strftime("%Y-%m-%d %H:%M:%S")
        disp["exit"] = disp["exit_ts_local"].dt.strftime("%H:%M:%S")
        disp["hold"] = (disp["duration_s"] / 60).round(1).astype(str) + "m"
        show = disp[["trade_no", "instrument", "direction", "max_contracts",
                     "entry", "exit", "hold", "avg_entry", "avg_exit", "net_pnl"]]
        event = st.dataframe(
            show, width="stretch", hide_index=True,
            on_select="rerun", selection_mode="single-row",
        )
        sel = event.selection.rows if event and event.selection else []
        if sel:
            st.session_state["sel_trade_no"] = int(tf.iloc[sel[0]]["trade_no"])

        nums = tf["trade_no"].tolist()
        chosen = st.session_state.get("sel_trade_no", nums[0])
        if chosen not in nums:
            chosen = nums[0]
        trade = tf[tf["trade_no"] == chosen].iloc[0]

        st.divider()
        ui.section_title(
            f"Trade #{chosen} — {trade['entry_ts_local'].strftime('%Y-%m-%d %H:%M:%S')}")
        render_trade_detail(trade)

# ---- ATAS cross-check ----
with tab_xcheck:
    ui.section_title("Our metrics vs ATAS Statistics sheet")
    stats = db.load_statistics(conn)
    if stats.empty:
        st.info("No Statistics sheets imported.")
    else:
        files = sorted(stats["source_file"].unique())
        sf = st.selectbox("Source file", files)
        atas_view = stats[stats["source_file"] == sf]
        pivot = atas_view.pivot_table(index="metric", columns="scope", values="value",
                                      aggfunc="first")
        st.caption("ATAS Statistics (as exported)")
        st.dataframe(pivot, width="stretch")

        jr_sf = jr[jr["source_file"] == sf]
        our = metrics.compute_metrics(trades.atas_trades(jr_sf))
        st.caption("Our recomputed metrics (ATAS-rows view, this file)")
        comp = pd.DataFrame({
            "metric": ["Total trades", "Net PnL", "Win Rate", "Profit factor",
                       "Profitable trades", "Losing trades", "Best Trade", "Worst Trade"],
            "ours": [str(our["trades"]), fmt(our["net_pnl"]), f"{our['win_rate']:.2f}",
                     fmt(our["profit_factor"], money=False), str(our["wins"]),
                     str(our["losses"]), fmt(our["best_trade"]), fmt(our["worst_trade"])],
        })
        st.dataframe(comp, width="stretch", hide_index=True)

        rec = trades.reconcile(logical, jr)
        st.caption("Logical-vs-ATAS PnL reconciliation (all imported data)")
        st.json(rec)
