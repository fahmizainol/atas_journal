"""Draw the cone on real sessions.

The cone is arithmetic, not a model: take the ruler (median 1-min bar range over
the trailing 30 min) and multiply it by the travel multiples measured in
``fit.py`` over 427 training sessions. This page walks a session minute by
minute, projects the envelope forward from the cursor, and — on demand — reveals
what price actually did, so the containment claim can be checked by eye.

Every day rendered is from the HELD-OUT span (2025-11-26 onward), so the k's on
screen were fitted without ever seeing these sessions.

    .venv/bin/python data/research/cone/render_cone.py

Writes ``docs/research/cone-visual.html``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DAYS = ROOT / "data" / "research" / "forward-fan" / "days.parquet"
RESULTS = HERE / "results.json"
OUT = ROOT / "docs" / "research" / "cone-visual.html"

TICK = 0.25
BELL = 15 * 60 + 30          # 09:30 as minutes from 18:00
CLOSE = BELL + 390           # 16:00
RULER_W, RULER_MIN = 30, 5
HORIZONS = (5, 15, 30)
QUANTILES = (0.5, 0.8, 0.95)
MAX_H = 30

#: the held-out span fit.py tested on — anything here is out-of-sample.
TEST_FROM = "2025-11-26"
#: the session behind the 0.6-ruler hand-exit, plus spread-out held-out days.
PINNED = "2026-03-25"
N_DAYS = 6


def ruler_of(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """Median 1-min bar range in ticks over the trailing window, closed bars only."""
    rng = pd.Series((high - low) / TICK)
    return rng.rolling(RULER_W, min_periods=RULER_MIN).median().to_numpy()


def pick_days(all_days: list[str]) -> list[str]:
    held = [d for d in all_days if d >= TEST_FROM]
    if PINNED in held:
        held.remove(PINNED)
    # Spread the rest across the held-out span rather than clumping at one end.
    idx = np.linspace(0, len(held) - 1, N_DAYS - 1).round().astype(int)
    chosen = sorted({PINNED, *(held[i] for i in idx)})
    return chosen


def one_day(g: pd.DataFrame) -> dict | None:
    s = g.iloc[BELL:CLOSE].reset_index(drop=True)
    if len(s) < 300 or s["high"].isna().mean() > 0.02:
        return None
    close = s["close"].to_numpy(float)
    high, low = s["high"].to_numpy(float), s["low"].to_numpy(float)
    ruler = ruler_of(high, low)

    # Realised forward travel, in ticks, for the containment check. Uses intrabar
    # extremes over t+1..t+h, exactly as the labels in build.py do.
    fwd = {}
    n = len(s)
    for h in HORIZONS:
        up = np.full(n, np.nan)
        dn = np.full(n, np.nan)
        for t in range(n - 1):
            j = min(t + h, n - 1)
            up[t] = (high[t + 1:j + 1].max() - close[t]) / TICK
            dn[t] = (close[t] - low[t + 1:j + 1].min()) / TICK
        fwd[f"up{h}"] = np.round(up, 1)
        fwd[f"dn{h}"] = np.round(dn, 1)

    def col(name: str) -> list:
        return [None if not np.isfinite(v) else round(float(v), 2)
                for v in s[name].ffill().to_numpy(float)]

    def arr(a: np.ndarray, nd: int = 2) -> list:
        return [None if not np.isfinite(v) else round(float(v), nd) for v in a]

    return {
        "day": str(g["day"].iloc[0]),
        "close": arr(close), "high": arr(high), "low": arr(low),
        "ruler": arr(ruler, 2),
        "nyv": col("nyv"), "gxv": col("gxv"),
        **{k: arr(v, 1) for k, v in fwd.items()},
    }


def main() -> None:
    df = pd.read_parquet(DAYS)
    res = json.loads(RESULTS.read_text())
    k = {f"{s}_{h}_{q}": res[f"{s}_{h}_q{q}"]["k"]
         for h in HORIZONS for s in ("up", "dn") for q in QUANTILES}

    all_days = sorted(df["day"].astype(str).unique())
    want = pick_days(all_days)
    out = []
    for d in want:
        g = df[df["day"].astype(str) == d].sort_values("i").reset_index(drop=True)
        r = one_day(g)
        if r is not None:
            out.append(r)
    if not out:
        raise SystemExit("no sessions rendered")

    payload = {"days": out, "k": k, "horizons": list(HORIZONS),
               "quantiles": list(QUANTILES), "tick": TICK, "maxH": MAX_H}
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(out)} sessions: {', '.join(r['day'] for r in out)})")
    print(f"  page size {OUT.stat().st_size / 1024:.0f} KB")


TEMPLATE = r"""<title>The cone — how far, not which way</title>
<style>
  :root {
    --bg: #0e1117; --bg-2: #15171f; --card: #1a1d27; --card-border: #262a36;
    --accent: #6c5ce7; --green: #21c07a; --red: #f5455f; --text: #e6e8ee;
    --muted: #8a8f9c; --grid: #2a2e38; --gold: #e0a52a; --ny: #c4b5fd; --blue: #3b82f6;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
         font-size: 13px; line-height: 1.4; -webkit-font-smoothing: antialiased; }
  .wrap { max-width: 1400px; margin: 0 auto; padding: 16px 16px 32px; }
  h1 { font-size: 1.15rem; margin: 0 0 2px; }
  .meta { color: var(--muted); font-size: .78rem; margin-bottom: 10px; }
  .meta b { color: var(--text); font-weight: 600; }
  .panel { background: var(--card); border: 1px solid var(--card-border); border-radius: 12px; padding: 12px 14px; }
  .controls { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; margin-bottom: 10px; }
  .controls label { color: var(--muted); font-size: .78rem; display: flex; gap: 6px; align-items: center; }
  button, select { background: var(--bg-2); color: var(--text); border: 1px solid var(--card-border);
                   border-radius: 7px; padding: 4px 10px; font: inherit; cursor: pointer; }
  button.on { border-color: var(--accent); color: #fff; background: rgba(108,92,231,.25); }
  input[type=range] { width: 100%; accent-color: var(--accent); }
  .grid { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 12px; }
  @media (max-width: 1000px) { .grid { grid-template-columns: 1fr; } }
  canvas { width: 100%; display: block; border-radius: 8px; background: var(--bg-2); }
  .side { display: flex; flex-direction: column; gap: 12px; }
  .readout { font-size: .78rem; font-variant-numeric: tabular-nums; display: grid;
             grid-template-columns: auto 1fr 1fr 1fr; gap: 2px 10px; }
  .readout .lab { color: var(--muted); }
  .readout .h { color: var(--muted); font-size: .7rem; text-transform: uppercase; letter-spacing: .06em; }
  .big { font-size: 1.5rem; font-variant-numeric: tabular-nums; }
  .sub { color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: .74rem; color: var(--muted); margin-top: 8px; }
  .legend i { display: inline-block; width: 14px; height: 9px; vertical-align: middle; margin-right: 5px; border-radius: 2px; }
  .note { color: var(--muted); font-size: .76rem; margin-top: 10px; line-height: 1.55; }
  .note b { color: var(--text); }
  kbd { background: var(--bg-2); border: 1px solid var(--card-border); border-radius: 4px; padding: 0 5px; font-size: .7rem; }
  table.k { width: 100%; border-collapse: collapse; font-size: .74rem; font-variant-numeric: tabular-nums; }
  table.k th { color: var(--muted); font-weight: 500; text-align: right; padding: 2px 0; font-size: .68rem;
               text-transform: uppercase; letter-spacing: .05em; }
  table.k td { text-align: right; padding: 2px 0; }
  table.k td:first-child, table.k th:first-child { text-align: left; color: var(--muted); }
  [hidden] { display: none !important; }
</style>

<div class="wrap">
  <h1>The cone — how far, not which way</h1>
  <div class="meta" id="meta"></div>

  <div class="panel">
    <div class="controls">
      <label>session <select id="day"></select></label>
      <button id="play">▶ Play</button>
      <label>speed <select id="speed">
        <option value="2">2 min/s</option><option value="5" selected>5 min/s</option>
        <option value="15">15 min/s</option><option value="60">60 min/s</option></select></label>
      <label>bands
        <button class="q on" data-q="0.5">50%</button>
        <button class="q on" data-q="0.8">80%</button>
        <button class="q on" data-q="0.95">95%</button></label>
      <label><input type="checkbox" id="reveal"> reveal what actually happened</label>
      <span style="color:var(--muted);font-size:.74rem"><kbd>←</kbd> <kbd>→</kbd> step · <kbd>space</kbd> play · <kbd>r</kbd> reveal</span>
    </div>
    <input type="range" id="scrub" min="0" max="389" value="60">
    <div class="grid" style="margin-top:10px">
      <div>
        <canvas id="main" height="520"></canvas>
        <div class="legend">
          <span><i style="background:rgba(108,92,231,.45)"></i>50% — ordinary wiggle</span>
          <span><i style="background:rgba(108,92,231,.26)"></i>80%</span>
          <span><i style="background:rgba(108,92,231,.13)"></i>95% — where a stop belongs</span>
          <span><i style="background:var(--ny);height:2px"></i>NY VWAP</span>
          <span><i style="background:#8b7cf6;height:2px"></i>Globex VWAP</span>
          <span><i style="background:var(--gold);height:2px"></i>actual path ahead</span>
        </div>
      </div>
      <div class="side">
        <div class="panel" style="padding:10px 12px">
          <div class="sub">ruler right now</div>
          <div class="big" id="ruler">—</div>
          <div class="sub" style="margin-top:8px">the cone, in points from here</div>
          <div class="readout" id="readout" style="margin-top:4px"></div>
        </div>
        <div class="panel" style="padding:10px 12px">
          <div class="sub">this session's containment</div>
          <div style="font-size:.74rem;color:var(--muted);margin:3px 0 6px">
            share of minutes whose realised travel stayed inside the band
          </div>
          <table class="k" id="cover"></table>
        </div>
        <div class="panel" style="padding:10px 12px">
          <div class="sub">travel multiples (k)</div>
          <div style="font-size:.74rem;color:var(--muted);margin:3px 0 6px">
            fitted on 427 training sessions — never on this one
          </div>
          <table class="k" id="ktab"></table>
        </div>
      </div>
    </div>
    <div class="note">
      <b>How to read it.</b> The cone is drawn forward from the cursor. Its width is the
      <i>ruler</i> — the median 1-minute bar range over the last 30 minutes — multiplied by how many
      rulers price historically travelled over the next 5, 15 and 30 minutes. It says nothing about
      direction: the shape is the same whether you are long or short.
      <b>The down half is wider than the up half</b>, and the gap grows with time — 1.14× at 5 minutes,
      1.25× at 30. Tick <i>reveal</i> to draw the actual high/low path over the next 30 minutes and see
      whether it stayed inside. A stop placed inside the 50% band is inside the market's ordinary
      breathing, and will be taken out by noise alone.
    </div>
  </div>
</div>

<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const C = { accent: "108,92,231", ny: "#c4b5fd", gx: "#8b7cf6", gold: "#e0a52a",
            grid: "#2a2e38", muted: "#8a8f9c", green: "#21c07a", red: "#f5455f" };
const ALPHA = { "0.5": .45, "0.8": .26, "0.95": .13 };

let day = D.days[0], now = 60, playing = false, timer = null;
let shownQ = new Set(["0.5", "0.8", "0.95"]);

// --- the cone's half-width, in ticks, h minutes ahead. k is measured only at
// 5/15/30, so interpolate piecewise-linearly between those anchors (and from 0
// at h=0). Extrapolation past 30 is refused rather than guessed.
function kAt(side, h, q) {
  const pts = [[0, 0], ...D.horizons.map(H => [H, D.k[`${side}_${H}_${q}`]])];
  if (h >= pts[pts.length - 1][0]) return pts[pts.length - 1][1];
  for (let i = 1; i < pts.length; i++) {
    if (h <= pts[i][0]) {
      const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
      return y0 + (y1 - y0) * (h - x0) / (x1 - x0);
    }
  }
  return pts[pts.length - 1][1];
}

function fmtTime(i) {
  const m = 9 * 60 + 30 + i;
  return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
}

// --- containment over the whole session: did realised travel stay inside?
function coverage(d) {
  const out = {};
  for (const q of D.quantiles) {
    for (const h of D.horizons) {
      let inside = 0, total = 0;
      for (let t = 0; t < d.close.length; t++) {
        const r = d.ruler[t], up = d[`up${h}`][t], dn = d[`dn${h}`][t];
        if (r == null || up == null || dn == null) continue;
        total++;
        if (up <= D.k[`up_${h}_${q}`] * r && dn <= D.k[`dn_${h}_${q}`] * r) inside++;
      }
      out[`${h}_${q}`] = total ? inside / total : null;
    }
  }
  return out;
}

function draw() {
  const cv = $("#main"), ctx = cv.getContext("2d");
  const W = cv.clientWidth, H = cv.height;
  if (cv.width !== W * devicePixelRatio) {
    cv.width = W * devicePixelRatio; cv.height = H * devicePixelRatio;
  }
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const n = day.close.length, padL = 8, padR = 62, padT = 10, padB = 22;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  // The x axis always spans the whole session plus the projection, so the cone
  // does not make the chart jump as the cursor moves.
  const xmax = n + D.maxH;
  const X = i => padL + (i / xmax) * plotW;

  const r = day.ruler[now], c = day.close[now];
  // Price window: the session so far, plus whatever the widest drawn cone needs.
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i <= Math.min(now + D.maxH, n - 1); i++) {
    if (day.low[i] != null) lo = Math.min(lo, day.low[i]);
    if (day.high[i] != null) hi = Math.max(hi, day.high[i]);
  }
  if (r != null && c != null) {
    const qs = [...shownQ].map(Number);
    const qmax = qs.length ? Math.max(...qs) : .95;
    lo = Math.min(lo, c - kAt("dn", D.maxH, qmax) * r * D.tick);
    hi = Math.max(hi, c + kAt("up", D.maxH, qmax) * r * D.tick);
  }
  const pad = (hi - lo) * .06 || 1;
  lo -= pad; hi += pad;
  const Y = p => padT + (1 - (p - lo) / (hi - lo)) * plotH;

  // --- grid + price axis
  ctx.strokeStyle = C.grid; ctx.fillStyle = C.muted;
  ctx.font = "10px ui-monospace, monospace"; ctx.lineWidth = 1;
  const step = Math.max(5, Math.round((hi - lo) / 8 / 5) * 5);
  for (let p = Math.ceil(lo / step) * step; p < hi; p += step) {
    const y = Y(p);
    ctx.globalAlpha = .5; ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.globalAlpha = 1; ctx.fillText(p.toFixed(0), W - padR + 5, y + 3);
  }
  ctx.textAlign = "center";
  for (let i = 0; i < n; i += 60) {
    const x = X(i);
    ctx.globalAlpha = .5; ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, H - padB); ctx.stroke();
    ctx.globalAlpha = 1; ctx.fillText(fmtTime(i), x, H - padB + 12);
  }
  ctx.textAlign = "left"; ctx.globalAlpha = 1;

  // --- THE CONE, drawn before price so the candles sit on top of it.
  if (r != null && c != null) {
    for (const q of ["0.95", "0.8", "0.5"]) {
      if (!shownQ.has(q)) continue;
      ctx.fillStyle = `rgba(${C.accent},${ALPHA[q]})`;
      ctx.beginPath();
      ctx.moveTo(X(now), Y(c));
      for (let h = 1; h <= D.maxH; h++) ctx.lineTo(X(now + h), Y(c + kAt("up", h, q) * r * D.tick));
      for (let h = D.maxH; h >= 1; h--) ctx.lineTo(X(now + h), Y(c - kAt("dn", h, q) * r * D.tick));
      ctx.closePath(); ctx.fill();
    }
    // The 30-min edges, labelled — the numbers a stop is actually placed against.
    ctx.strokeStyle = `rgba(${C.accent},.85)`; ctx.lineWidth = 1;
    for (const side of ["up", "dn"]) {
      if (!shownQ.has("0.95")) break;
      const k = kAt(side, D.maxH, "0.95") * r * D.tick;
      const y = Y(side === "up" ? c + k : c - k);
      ctx.setLineDash([3, 3]); ctx.beginPath();
      ctx.moveTo(X(now), y); ctx.lineTo(X(now + D.maxH), y); ctx.stroke(); ctx.setLineDash([]);
    }
  }

  // --- levels
  for (const [key, col] of [["nyv", C.ny], ["gxv", C.gx]]) {
    ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.globalAlpha = .75;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i <= now; i++) {
      const v = day[key][i];
      if (v == null) { started = false; continue; }
      if (!started) { ctx.moveTo(X(i), Y(v)); started = true; } else ctx.lineTo(X(i), Y(v));
    }
    ctx.stroke(); ctx.globalAlpha = 1;
  }

  // --- price so far: hi/lo wick per minute, tinted by the minute's direction.
  const bw = Math.max(1, plotW / xmax * .8);
  for (let i = 0; i <= now; i++) {
    if (day.high[i] == null) continue;
    const up = i === 0 || day.close[i] >= day.close[i - 1];
    ctx.strokeStyle = up ? C.green : C.red;
    ctx.globalAlpha = .85; ctx.lineWidth = bw;
    ctx.beginPath(); ctx.moveTo(X(i), Y(day.high[i])); ctx.lineTo(X(i), Y(day.low[i])); ctx.stroke();
  }
  ctx.globalAlpha = 1; ctx.lineWidth = 1;

  // --- the future, on request
  if ($("#reveal").checked) {
    ctx.strokeStyle = C.gold; ctx.lineWidth = 1.4; ctx.globalAlpha = .95;
    ctx.beginPath();
    let started = false;
    for (let i = now; i <= Math.min(now + D.maxH, n - 1); i++) {
      if (day.close[i] == null) continue;
      if (!started) { ctx.moveTo(X(i), Y(day.close[i])); started = true; } else ctx.lineTo(X(i), Y(day.close[i]));
    }
    ctx.stroke();
    ctx.globalAlpha = .35; ctx.lineWidth = 1;
    for (let i = now + 1; i <= Math.min(now + D.maxH, n - 1); i++) {
      if (day.high[i] == null) continue;
      ctx.beginPath(); ctx.moveTo(X(i), Y(day.high[i])); ctx.lineTo(X(i), Y(day.low[i])); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  // --- cursor
  ctx.strokeStyle = "#fff"; ctx.globalAlpha = .5;
  ctx.beginPath(); ctx.moveTo(X(now), padT); ctx.lineTo(X(now), H - padB); ctx.stroke();
  ctx.globalAlpha = 1;
}

function readout() {
  const r = day.ruler[now], c = day.close[now];
  $("#ruler").textContent = r == null ? "—"
    : `${r.toFixed(1)} ticks  ·  ${(r * D.tick).toFixed(2)} pts`;

  const rows = ['<div class="h"></div><div class="h">50%</div><div class="h">80%</div><div class="h">95%</div>'];
  for (const h of D.horizons) {
    for (const side of ["dn", "up"]) {
      const cells = D.quantiles.map(q => {
        if (r == null) return "<div>—</div>";
        const pts = D.k[`${side}_${h}_${q}`] * r * D.tick;
        const px = side === "up" ? c + pts : c - pts;
        return `<div title="${px.toFixed(2)}">${pts.toFixed(1)}</div>`;
      }).join("");
      const lab = `${side === "dn" ? "↓" : "↑"} ${h}m`;
      rows.push(`<div class="lab">${lab}</div>${cells}`);
    }
  }
  $("#readout").innerHTML = rows.join("");

  const cov = coverage(day);
  let t = "<tr><th>horizon</th><th>50%</th><th>80%</th><th>95%</th></tr>";
  for (const h of D.horizons) {
    t += `<tr><td>${h} min</td>` + D.quantiles.map(q => {
      const v = cov[`${h}_${q}`];
      if (v == null) return "<td>—</td>";
      const off = Math.abs(v - q);
      const col = off <= .04 ? C.green : off <= .09 ? C.gold : C.red;
      return `<td style="color:${col}">${(v * 100).toFixed(0)}%</td>`;
    }).join("") + "</tr>";
  }
  $("#cover").innerHTML = t;

  $("#meta").innerHTML = `<b>${day.day}</b> · NQ · cursor <b>${fmtTime(now)}</b> · `
    + `the k's below were fitted on 427 sessions ending 2025-11-18 — this session is held out`;
}

function render() { draw(); readout(); }

function setDay(d) {
  day = D.days.find(x => x.day === d) || D.days[0];
  $("#scrub").max = day.close.length - 1;
  now = Math.min(now, day.close.length - 1);
  render();
}

// --- static k table
(function () {
  let t = "<tr><th>horizon</th><th>50%</th><th>80%</th><th>95%</th></tr>";
  for (const h of D.horizons) {
    for (const side of ["dn", "up"]) {
      t += `<tr><td>${side === "dn" ? "↓" : "↑"} ${h} min</td>`
        + D.quantiles.map(q => `<td>${D.k[`${side}_${h}_${q}`].toFixed(2)}</td>`).join("") + "</tr>";
    }
  }
  $("#ktab").innerHTML = t;
})();

$("#day").innerHTML = D.days.map(d => `<option value="${d.day}">${d.day}</option>`).join("");
$("#day").onchange = e => setDay(e.target.value);
$("#scrub").oninput = e => { now = +e.target.value; render(); };
$("#reveal").onchange = render;
document.querySelectorAll(".q").forEach(b => b.onclick = () => {
  const q = b.dataset.q;
  if (shownQ.has(q)) shownQ.delete(q); else shownQ.add(q);
  b.classList.toggle("on");
  render();
});

function stop() { playing = false; clearInterval(timer); $("#play").textContent = "▶ Play"; }
$("#play").onclick = () => {
  if (playing) return stop();
  playing = true; $("#play").textContent = "❚❚ Pause";
  timer = setInterval(() => {
    if (now >= day.close.length - 1) return stop();
    now++; $("#scrub").value = now; render();
  }, 1000 / +$("#speed").value);
};
$("#speed").onchange = () => { if (playing) { stop(); $("#play").click(); } };

addEventListener("keydown", e => {
  if (e.key === "ArrowRight") { now = Math.min(now + 1, day.close.length - 1); }
  else if (e.key === "ArrowLeft") { now = Math.max(now - 1, 0); }
  else if (e.key === " ") { e.preventDefault(); $("#play").click(); return; }
  else if (e.key === "r") { $("#reveal").checked = !$("#reveal").checked; render(); return; }
  else return;
  $("#scrub").value = now; render();
});

addEventListener("resize", draw);
setDay(D.days.find(d => d.day === "2026-03-25") ? "2026-03-25" : D.days[0].day);
</script>
"""

if __name__ == "__main__":
    main()
