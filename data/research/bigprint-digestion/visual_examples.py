"""Visual examples for the big-sweep x upper-band pairing cut.

Five real trades from the current baseline (v13 a348d176), each drawn on
1-minute RTH candles with session VWAP / +1sigma, every >=100-lot sweep as a
sized bubble, the trade's entry/exit/stop, and the 15-minute lookback window
shaded. Cache-only. Writes docs/research/bigprint-sweep-examples.html (Lab ->
Research lists it).

    uv run python data/research/bigprint-digestion/visual_examples.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

from extract import sweeps  # noqa: E402  (also puts src on path)
from journal.config import ET_TZ  # noqa: E402

RUN = ROOT / "data/sims/vwap-upper-band-bounce/20250201-20260630-v13-a348d176"
OUT = ROOT / "docs" / "research" / "bigprint-sweep-examples.html"
LWC = ROOT / "frontend" / "node_modules" / "lightweight-charts" / "dist" / (
    "lightweight-charts.standalone.production.js")
BIG = 100
LOOKBACK_MIN = 15


def day_file(day: str) -> Path:
    hits = sorted(ROOT.glob(f"data/cache/ticks/*_{day}_day.parquet"),
                  key=lambda p: p.stat().st_size)
    return hits[-1]


def bucket_trades() -> pd.DataFrame:
    trades = pd.read_parquet(RUN / "trades.parquet")
    m = pd.read_parquet(HERE / "minutes.parquet")
    sw = m[(m["seg"] == "rth") & (m["s_size"].fillna(0) >= BIG)][
        ["s_ts", "s_side", "s_size"]].dropna().sort_values("s_ts")
    ts = sw["s_ts"].to_numpy("datetime64[ns]")
    side = sw["s_side"].to_numpy()
    size = sw["s_size"].to_numpy()

    entry = trades["entry_ts_utc"].dt.tz_convert("UTC").dt.tz_localize(None) \
        .to_numpy("datetime64[ns]")
    lo = np.searchsorted(ts, entry - np.timedelta64(LOOKBACK_MIN * 60, "s"), "left")
    hi = np.searchsorted(ts, entry, "left")
    last = np.maximum(hi - 1, 0)
    trades["bucket"] = np.select(
        [hi == lo, side[last] == "B", side[last] == "A"],
        ["none", "fresh-buy", "fresh-sell"], default="none")
    trades["sw_size"] = np.where(hi > lo, size[last], 0)
    trades["sw_age_min"] = np.where(
        hi > lo, (entry - ts[last]).astype("timedelta64[s]").astype(float) / 60, np.nan)
    return trades


def session_frame(day: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_parquet(day_file(day))
    df = df[df["seg"] == "rth"].sort_values("ts_utc").reset_index(drop=True)
    df["size"] = df["size"].astype("int64")
    p, v = df["price"].astype(float), df["size"].astype(float)
    cum_v = v.cumsum().where(lambda c: c != 0)
    df["_vwap"] = (p * v).cumsum() / cum_v
    var = (p * p * v).cumsum() / cum_v - df["_vwap"] ** 2
    df["_std"] = var.clip(lower=0) ** 0.5
    df["_min"] = df["ts_utc"].dt.tz_convert(ET_TZ).dt.floor("min").dt.tz_localize(None)
    bars = df.groupby("_min", sort=True).agg(
        open=("price", "first"), high=("price", "max"),
        low=("price", "min"), close=("price", "last"),
        vwap=("_vwap", "last"), std=("_std", "last")).reset_index()
    sw = sweeps(df)
    sw = sw[sw["size"] >= BIG].copy()
    sw["_min"] = sw["ts_utc"].dt.tz_convert(ET_TZ).dt.floor("min").dt.tz_localize(None)
    return bars, sw


def epoch(s: pd.Series) -> np.ndarray:
    return s.astype("datetime64[s]").astype("int64").to_numpy()


def build_example(tr: pd.Series, title: str, note: str) -> dict:
    day = tr["session"]
    bars, sw = session_frame(day)
    e_et = tr["entry_ts_utc"].tz_convert(ET_TZ).tz_localize(None)
    x_et = tr["exit_ts_utc"].tz_convert(ET_TZ).tz_localize(None)
    w0, w1 = e_et.floor("min") - pd.Timedelta(minutes=75), \
        x_et.floor("min") + pd.Timedelta(minutes=45)
    view = bars[(bars["_min"] >= w0) & (bars["_min"] <= w1)]
    t = epoch(view["_min"])

    bars_json = [{"time": int(a), "open": o, "high": h, "low": lo, "close": c}
                 for a, o, h, lo, c in zip(t, view["open"], view["high"],
                                           view["low"], view["close"])]
    vwap_json = [{"time": int(a), "value": round(w, 2)} for a, w in zip(t, view["vwap"])]
    band_json = [{"time": int(a), "value": round(w + s, 2)}
                 for a, w, s in zip(t, view["vwap"], view["std"])]

    swv = sw[(sw["_min"] >= w0) & (sw["_min"] <= w1)]
    lb0 = e_et - pd.Timedelta(minutes=LOOKBACK_MIN)
    markers, rows = [], []
    lead_lots, lead_age = int(tr["sw_size"]), tr["sw_age_min"]
    for _, r in swv.iterrows():
        et = r["ts_utc"].tz_convert(ET_TZ).tz_localize(None)
        in_lb = lb0 <= et < e_et
        if in_lb:  # caption quotes the freshest sweep as drawn on this page
            lead_lots = int(r["size"])
            lead_age = (e_et - et).total_seconds() / 60
        buy = r["side"] == "B"
        markers.append({
            "time": int(pd.Series([r["_min"]]).astype("datetime64[s]").astype("int64")[0]),
            "position": "belowBar" if buy else "aboveBar",
            "shape": "circle", "size": 2,
            "color": "#26a69a" if buy else "#ef5350",
            "text": f"{int(r['size'])}"})
        rows.append({"et": et.strftime("%H:%M:%S"), "side": "BUY" if buy else "SELL",
                     "lots": int(r["size"]), "px": round(float(r["price"]), 2),
                     "in_window": bool(in_lb)})

    ent_t = int(pd.Series([e_et.floor("min")]).astype("datetime64[s]").astype("int64")[0])
    ext_t = int(pd.Series([x_et.floor("min")]).astype("datetime64[s]").astype("int64")[0])
    markers.append({"time": ent_t, "position": "belowBar", "shape": "arrowUp",
                    "size": 2, "color": "#ffd54f", "text": "entry"})
    markers.append({"time": ext_t, "position": "aboveBar", "shape": "arrowDown",
                    "size": 2, "color": "#ffffff",
                    "text": f"exit {tr['r_multiple']:+.2f}R"})
    markers.sort(key=lambda m: m["time"])

    return {
        "title": title,
        "sub": (f"{day} · {tr['direction']} · entry {e_et:%H:%M:%S} ET · "
                f"exit {x_et:%H:%M:%S} · {tr['r_multiple']:+.2f}R · "
                f"net ${tr['net_pnl']:,.0f}"),
        "note": (note.replace("{LOTS}", str(lead_lots))
                 .replace("{AGE}", f"{lead_age:.1f}" if np.isfinite(lead_age) else "?")
                 .replace("{AGE_S}", f"{lead_age * 60:.0f}"
                          if np.isfinite(lead_age) else "?")),
        "bars": bars_json, "vwap": vwap_json, "band": band_json,
        "markers": markers, "sweeps": rows,
        "entry_px": round(float(tr["avg_entry"]), 2),
        "stop_px": round(float(tr["stop_price"]), 2),
        "lb": [int(pd.Series([lb0.floor('min')]).astype("datetime64[s]").astype("int64")[0]),
               ent_t],
    }


def main() -> None:
    t = bucket_trades()
    # a sweep milliseconds before entry is the fill itself ("winners fill into
    # selling"), not prior context — examples 1-3 require a truly prior sweep
    fs_all = t[t["bucket"] == "fresh-sell"]
    fs = fs_all[fs_all["sw_age_min"] >= 0.75]
    fb = t[t["bucket"] == "fresh-buy"]
    none = t[t["bucket"] == "none"]

    modal = fs[(fs["r_multiple"] > 0) & (fs["r_multiple"] < 0.6)] \
        .sort_values("sw_size").iloc[-1]
    runner = fs.sort_values("r_multiple").iloc[-1]
    loser = fs.sort_values("r_multiple").iloc[0]
    clean = none.sort_values("r_multiple").iloc[-1]
    tail = fb[fb["sw_age_min"] >= 0.75].sort_values("sw_size").iloc[-1]
    filled = fs_all[fs_all["sw_age_min"] < 0.5].sort_values("sw_size").iloc[-1]

    examples = [
        build_example(modal, "1 · Fresh sell sweep → win, but small (the modal case)",
                      "A {LOTS}-lot SELL sweep printed {AGE} min before this long "
                      "entry — inside the 15-min digestion window (shaded). The trade "
                      f"still wins ({modal['r_multiple']:+.2f}R) but never becomes a "
                      "runner: this is the bucket's texture — 79% win rate, +0.11R "
                      "average. The seller is still being digested while the trade "
                      "is on."),
        build_example(runner, "2 · Fresh sell sweep → runner anyway",
                      "Same setup — a {LOTS}-lot SELL sweep {AGE} min before entry — "
                      f"and the trade runs {runner['r_multiple']:+.2f}R regardless. "
                      "Trades like this are why the fresh-sell cohort stays "
                      "net-positive (+$6k) and a veto can't pay: you'd delete this "
                      "trade too."),
        build_example(loser, "3 · Fresh sell sweep → the loser",
                      "The headwind case the hypothesis predicts: {LOTS}-lot SELL "
                      "sweep {AGE} min before entry, trade goes "
                      f"{loser['r_multiple']:+.2f}R. Real, but too rare in this "
                      "cohort (79% still win) to fund a filter."),
        build_example(clean, "4 · No big sweep anywhere near entry → clean runner",
                      "The no-sweep bucket's texture: nothing ≥100 lots in the prior "
                      f"15 min, and the trade runs {clean['r_multiple']:+.2f}R. "
                      "No-sweep entries average +0.31R vs +0.11R against a fresh "
                      "seller — the dampening is visible, its sign is stable, and it "
                      "still isn't actionable."),
        build_example(tail, "5 · Fresh BUY sweep → no tailwind",
                      "The half of the hypothesis that failed: a {LOTS}-lot BUY "
                      "sweep {AGE} min before entry should be a tailwind for a "
                      "long, but the fresh-buy bucket averages +0.25R — no better "
                      "than no-sweep entries. Support from the tape adds nothing "
                      "here."),
        build_example(filled, "6 · The whale IS the fill",
                      "A {LOTS}-lot SELL sweep lands {AGE_S} seconds before the "
                      "entry timestamp — this isn't prior context, it's the sweep that "
                      "filled the resting long. All 3 such trades in the cohort won "
                      "(avg +0.53R): the loss-study finding that winners fill into "
                      "selling, drawn on the tape. Excluding these from the bucket "
                      "makes the truly-prior dampening stronger (+0.06R vs +0.30) "
                      "but the cohort stays net-positive — verdict unchanged."),
    ]

    html = TEMPLATE.replace("__LWC__", LWC.read_text()) \
        .replace("__DATA__", json.dumps(examples))
    OUT.write_text(html)
    print(f"wrote {OUT}  ({len(examples)} examples)")
    for ex in examples:
        print(f"  {ex['title']}  [{ex['sub']}]")


TEMPLATE = r"""<style>
  .bpx { background:#0f1117; color:#d5d9e0; font:14px/1.5 -apple-system,'Segoe UI',sans-serif;
         padding:16px; border-radius:8px; }
  .bpx h1 { font-size:18px; margin:0 0 4px; color:#fff; }
  .bpx .lead { color:#8b93a3; margin:0 0 14px; max-width:70em; }
  .bpx .tabs { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; }
  .bpx .tabs button { background:#1b1f2a; color:#aab2c0; border:1px solid #2a3040;
         border-radius:6px; padding:6px 10px; cursor:pointer; font:inherit; font-size:13px; }
  .bpx .tabs button.on { background:#2c3550; color:#fff; border-color:#4a5aa8; }
  .bpx .card { background:#171a21; border:1px solid #232837; border-radius:8px; padding:14px; }
  .bpx .card h2 { font-size:15px; margin:0 0 2px; color:#fff; }
  .bpx .sub { color:#8b93a3; font-size:12.5px; margin:0 0 8px; }
  .bpx .note { color:#c3c9d5; margin:0 0 12px; max-width:75em; }
  .bpx #chart { height:520px; }
  .bpx table { border-collapse:collapse; margin-top:10px; font-size:12.5px; }
  .bpx td, .bpx th { padding:3px 12px 3px 0; text-align:right; color:#aab2c0; }
  .bpx th { color:#6b7280; font-weight:600; }
  .bpx td:first-child, .bpx th:first-child { text-align:left; }
  .bpx .buy { color:#26a69a; } .bpx .sell { color:#ef5350; }
  .bpx .inw { color:#ffd54f; }
  .bpx .legend { color:#6b7280; font-size:12px; margin-top:6px; }
</style>
<div class="bpx">
  <h1>Big-sweep × upper-band entries — six real trades</h1>
  <p class="lead">Each chart: 1-min RTH candles, session VWAP (blue) and +1&sigma; band
  (dashed), every &ge;100-lot sweep as a bubble (green = buy aggressor, red = sell,
  number = lots), the trade's entry/exit, entry & stop price lines, and the 15-minute
  pre-entry lookback shaded amber. From run v13-a348d176.</p>
  <div class="tabs" id="tabs"></div>
  <div class="card">
    <h2 id="title"></h2><p class="sub" id="sub"></p><p class="note" id="note"></p>
    <div id="chart"></div>
    <div class="legend">Shaded region = the 15-min digestion window before entry.
    Times are ET.</div>
    <table id="swt"></table>
  </div>
</div>
<script>__LWC__</script>
<script>
const DATA = __DATA__;
const el = id => document.getElementById(id);
const fmtET = t => { const d = new Date(t * 1000);
  return String(d.getUTCHours()).padStart(2,'0') + ':' + String(d.getUTCMinutes()).padStart(2,'0'); };

let chart, candles, vwapS, bandS, markerPrim, lbRange = null, lines = [];

function shadePrimitive() {
  return { paneViews: () => [{ renderer: () => ({ draw(target) {
    if (!lbRange) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const ts = chart.timeScale();
      const x0 = ts.timeToCoordinate(lbRange[0]), x1 = ts.timeToCoordinate(lbRange[1]);
      if (x0 === null || x1 === null) return;
      context.fillStyle = 'rgba(255,213,79,0.08)';
      context.fillRect(x0, 0, x1 - x0, mediaSize.height);
      context.strokeStyle = 'rgba(255,213,79,0.35)';
      context.setLineDash([4,4]);
      [x0, x1].forEach(x => { context.beginPath(); context.moveTo(x, 0);
        context.lineTo(x, mediaSize.height); context.stroke(); });
    });
  } }) }] };
}

function initChart() {
  chart = LightweightCharts.createChart(el('chart'), {
    autoSize: true,
    layout: { background: { color: '#171a21' }, textColor: '#8b93a3',
              attributionLogo: false },
    grid: { vertLines: { color: '#1f2430' }, horzLines: { color: '#1f2430' } },
    timeScale: { timeVisible: true, secondsVisible: false,
                 tickMarkFormatter: t => fmtET(t) },
    localization: { timeFormatter: t => fmtET(t) },
    crosshair: { mode: 0 },
  });
  candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
    wickUpColor: '#26a69a', wickDownColor: '#ef5350' });
  vwapS = chart.addSeries(LightweightCharts.LineSeries,
    { color: '#5b8ff9', lineWidth: 2, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false });
  bandS = chart.addSeries(LightweightCharts.LineSeries,
    { color: '#8b93a3', lineWidth: 1, lineStyle: 1, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false });
  markerPrim = LightweightCharts.createSeriesMarkers(candles, []);
  chart.panes()[0].attachPrimitive(shadePrimitive());
}

function show(i) {
  const ex = DATA[i];
  document.querySelectorAll('.tabs button').forEach((b, j) =>
    b.classList.toggle('on', j === i));
  el('title').textContent = ex.title;
  el('sub').textContent = ex.sub;
  el('note').textContent = ex.note;
  lbRange = ex.lb;
  candles.setData(ex.bars);
  vwapS.setData(ex.vwap);
  bandS.setData(ex.band);
  markerPrim.setMarkers(ex.markers);
  lines.forEach(l => candles.removePriceLine(l)); lines = [];
  lines.push(candles.createPriceLine({ price: ex.entry_px, color: '#ffd54f',
    lineWidth: 1, lineStyle: 2, title: 'entry' }));
  lines.push(candles.createPriceLine({ price: ex.stop_px, color: '#ef5350',
    lineWidth: 1, lineStyle: 2, title: 'stop' }));
  chart.timeScale().fitContent();
  el('swt').innerHTML = '<tr><th>sweep (ET)</th><th>side</th><th>lots</th>' +
    '<th>price</th><th>in 15-min window</th></tr>' + ex.sweeps.map(s =>
    `<tr><td>${s.et}</td><td class="${s.side === 'BUY' ? 'buy' : 'sell'}">${s.side}</td>` +
    `<td>${s.lots}</td><td>${s.px}</td>` +
    `<td class="${s.in_window ? 'inw' : ''}">${s.in_window ? '● yes' : '—'}</td></tr>`
    ).join('');
}

DATA.forEach((ex, i) => {
  const b = document.createElement('button');
  b.textContent = ex.title.split('·')[0].trim() + ' · ' + ex.title.split('·')[1].trim();
  b.onclick = () => show(i);
  el('tabs').appendChild(b);
});
initChart();
show(0);
</script>
"""


if __name__ == "__main__":
    main()
