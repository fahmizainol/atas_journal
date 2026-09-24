"""Look at Kronos before scoring it.

Draws, at several anchors on held-out sessions: the sampled futures Kronos
generates, the band they form, and the `k x ruler` cone next to them — with the
real continuation hidden until asked for. The point is to see whether the
model's futures look like anything before any loss number is computed.

Compute and render are separate: forecasts are cached to ``kronos_paths.json``,
so re-rendering the page costs nothing.

    .venv-kronos/bin/python data/research/cone/render_kronos.py
    .venv-kronos/bin/python data/research/cone/render_kronos.py --recompute

Writes ``docs/research/kronos-cone.html``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / ".kronos"))

DAYS = ROOT / "data" / "research" / "forward-fan" / "days.parquet"
KFILE = HERE / "results.json"
CACHE = HERE / "kronos_paths.json"
OUT = ROOT / "docs" / "research" / "kronos-cone.html"

TICK = 0.25
BELL = 15 * 60 + 30
RTH = 390
HORIZON = 30
LOOKBACK = 200
PATHS = 12
CONTEXT_SHOWN = 120          # minutes of history drawn before the anchor
ANCHORS = [30, 90, 150, 210, 270, 330]        # 10:00 .. 15:00 ET
QUANTILES = (0.5, 0.8, 0.95)
TEST_FROM = "2025-11-26"
PINNED = "2026-03-25"
N_SESSIONS = 3


def compute() -> dict:
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer

    df = pd.read_parquet(DAYS)
    if "src" not in df.columns:
        df["src"] = "databento"
    held = sorted(df[(df["src"] == "databento")
                     & (df["day"].astype(str) >= TEST_FROM)]["day"].astype(str).unique())
    chosen = [PINNED] if PINNED in held else []
    rest = [d for d in held if d not in chosen]
    idx = np.linspace(0, len(rest) - 1, N_SESSIONS - len(chosen)).round().astype(int)
    chosen = sorted(chosen + [rest[i] for i in idx])

    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-2k")
    mdl = Kronos.from_pretrained("NeoQuasar/Kronos-mini")
    pred = KronosPredictor(mdl, tok, device="cpu", max_context=2048)
    print(f"  Kronos-mini on cpu, {torch.get_num_threads()} threads")
    print(f"  {len(chosen)} sessions x {len(ANCHORS)} anchors x {PATHS} paths\n")

    out, t0, done = [], time.time(), 0
    for day in chosen:
        g = df[df["day"].astype(str) == day].sort_values("i").reset_index(drop=True)
        s = g.iloc[BELL:BELL + RTH].reset_index(drop=True)
        hi, lo, cl = (s[c].to_numpy(float) for c in ("high", "low", "close"))
        ruler = pd.Series((hi - lo) / TICK).rolling(30, min_periods=5).median().to_numpy()
        ts0 = pd.Timestamp(f"{day} 18:00", tz="America/New_York") - pd.Timedelta(days=1)
        for a in ANCHORS:
            if a + HORIZON >= RTH or not np.isfinite(ruler[a]):
                continue
            j = BELL + a
            w = g.iloc[j - LOOKBACK + 1:j + 1]
            if w[["open", "high", "low", "close"]].isna().any().any():
                continue
            xdf = w[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
            xt = pd.Series(ts0 + pd.to_timedelta(np.arange(j - LOOKBACK + 1, j + 1), "min"))
            yt = pd.Series(ts0 + pd.to_timedelta(np.arange(j + 1, j + 1 + HORIZON), "min"))
            paths = []
            for _ in range(PATHS):
                r = pred.predict(df=xdf, x_timestamp=xt, y_timestamp=yt, pred_len=HORIZON,
                                 T=1.0, top_p=0.9, sample_count=1, verbose=False)
                paths.append({"c": [round(float(v), 2) for v in r["close"]],
                              "h": [round(float(v), 2) for v in r["high"]],
                              "l": [round(float(v), 2) for v in r["low"]]})
                done += 1
            k0 = max(0, a - CONTEXT_SHOWN)
            out.append({
                "day": day, "anchor": a, "close": round(float(cl[a]), 2),
                "ruler": round(float(ruler[a]), 1),
                "hist": {"c": [round(float(v), 2) for v in cl[k0:a + 1]],
                         "h": [round(float(v), 2) for v in hi[k0:a + 1]],
                         "l": [round(float(v), 2) for v in lo[k0:a + 1]]},
                "hist_from": int(a - k0),
                "future": {"c": [round(float(v), 2) for v in cl[a + 1:a + 1 + HORIZON]],
                           "h": [round(float(v), 2) for v in hi[a + 1:a + 1 + HORIZON]],
                           "l": [round(float(v), 2) for v in lo[a + 1:a + 1 + HORIZON]]},
                "paths": paths,
            })
            el = time.time() - t0
            print(f"    {day} @{a:3}  {done} paths  {el:5.0f}s  "
                  f"(eta {el/done*(len(chosen)*len(ANCHORS)*PATHS-done):4.0f}s)")

    kj = json.loads(KFILE.read_text())
    k = {f"{s}_{h}_{q}": kj[f"{s}_{h}_q{q}"]["k"]
         for h in (5, 15, 30) for s in ("up", "dn") for q in QUANTILES}
    return {"cells": out, "k": k, "tick": TICK, "horizon": HORIZON,
            "paths": PATHS, "lookback": LOOKBACK, "quantiles": list(QUANTILES)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recompute", action="store_true")
    args = ap.parse_args()

    if CACHE.exists() and not args.recompute:
        data = json.loads(CACHE.read_text())
        print(f"  using cached {CACHE.name} ({len(data['cells'])} anchors)")
    else:
        data = compute()
        CACHE.write_text(json.dumps(data, separators=(",", ":")))
        print(f"\n  cached -> {CACHE.relative_to(ROOT)}")

    OUT.write_text(TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    print(f"  wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size/1024:.0f} KB)")


TEMPLATE = r"""<title>Kronos vs the cone</title>
<style>
  :root { --bg:#0e1117; --bg-2:#15171f; --card:#1a1d27; --card-border:#262a36;
          --accent:#6c5ce7; --green:#21c07a; --red:#f5455f; --text:#e6e8ee;
          --muted:#8a8f9c; --grid:#2a2e38; --gold:#e0a52a; --ny:#c4b5fd; }
  *{box-sizing:border-box} [hidden]{display:none!important}
  body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif;font-size:13px}
  .wrap{max-width:1400px;margin:0 auto;padding:16px 16px 32px}
  h1{font-size:1.15rem;margin:0 0 2px}
  .meta{color:var(--muted);font-size:.78rem;margin-bottom:10px}
  .meta b{color:var(--text)}
  .panel{background:var(--card);border:1px solid var(--card-border);border-radius:12px;padding:12px 14px}
  .controls{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;margin-bottom:10px}
  .controls label{color:var(--muted);font-size:.78rem;display:flex;gap:6px;align-items:center}
  button,select{background:var(--bg-2);color:var(--text);border:1px solid var(--card-border);
                border-radius:7px;padding:4px 10px;font:inherit;cursor:pointer}
  button.on{border-color:var(--accent);color:#fff;background:rgba(108,92,231,.25)}
  canvas{width:100%;display:block;border-radius:8px;background:var(--bg-2)}
  .grid{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:12px}
  @media(max-width:1000px){.grid{grid-template-columns:1fr}}
  .readout{font-size:.78rem;font-variant-numeric:tabular-nums;display:grid;
           grid-template-columns:auto 1fr 1fr;gap:2px 10px}
  .readout .h{color:var(--muted);font-size:.7rem;text-transform:uppercase;letter-spacing:.06em}
  .readout .k{color:var(--ny)} .readout .r{color:var(--muted)}
  .legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.74rem;color:var(--muted);margin-top:8px}
  .legend i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
  .note{color:var(--muted);font-size:.76rem;margin-top:10px;line-height:1.55}
  .note b{color:var(--text)}
</style>
<div class="wrap">
  <h1>Kronos vs the cone</h1>
  <div class="meta" id="meta"></div>
  <div class="panel">
    <div class="controls">
      <label>session <select id="day"></select></label>
      <label>time <select id="anchor"></select></label>
      <label>show
        <button class="t on" data-t="paths">Kronos paths</button>
        <button class="t on" data-t="band">Kronos band</button>
        <button class="t on" data-t="cone">k x ruler cone</button></label>
      <label><input type="checkbox" id="reveal"> reveal what happened</label>
    </div>
    <div class="grid">
      <div>
        <canvas id="main" height="440"></canvas>
        <div class="legend">
          <span><i style="background:#e6e8ee"></i>price so far</span>
          <span><i style="background:rgba(196,181,253,.55)"></i>Kronos sampled futures</span>
          <span><i style="background:rgba(108,92,231,.30)"></i>Kronos band (10-90%)</span>
          <span><i style="background:transparent;border-top:2px dashed #6c5ce7;height:0"></i>k x ruler 95%</span>
          <span><i style="background:var(--gold)"></i>what actually happened</span>
        </div>
      </div>
      <div class="panel" style="padding:10px 12px">
        <div style="font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em">at this anchor</div>
        <div class="readout" id="readout" style="margin-top:6px"></div>
      </div>
    </div>
    <div class="note">
      <b>What you are looking at.</b> At the cursor time, Kronos was given the previous
      <span id="lb"></span> one-minute candles and asked for the next 30 — <span id="np"></span> separate times.
      Each thin line is one of those futures. The shaded band is where the middle 80% of them landed.
      The dashed envelope is your <b>k x ruler</b> cone at the 95th percentile, computed from the same instant.
      Nothing here is scored: the question is only whether the model's futures look like plausible NQ,
      and whether its spread is tighter or looser than the arithmetic's.
      Every session shown is held out — the k values were fitted on sessions ending 2025-11-18.
    </div>
  </div>
</div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const TICK = D.tick, H = D.horizon;
let show = {paths:1, band:1, cone:1};
$("#lb").textContent = D.lookback; $("#np").textContent = D.paths;

const days = [...new Set(D.cells.map(c => c.day))];
$("#day").innerHTML = days.map(d => `<option>${d}</option>`).join("");
const hhmm = a => { const m = 9*60+30+a; return String(m/60|0).padStart(2,"0")+":"+String(m%60).padStart(2,"0"); };
function fillAnchors() {
  const as = D.cells.filter(c => c.day === $("#day").value).map(c => c.anchor);
  $("#anchor").innerHTML = as.map(a => `<option value="${a}">${hhmm(a)}</option>`).join("");
}
const cell = () => D.cells.find(c => c.day === $("#day").value && c.anchor == $("#anchor").value);
const pct = (a,p) => { const s=[...a].sort((x,y)=>x-y), i=(s.length-1)*p; const lo=Math.floor(i),hi=Math.ceil(i);
                       return s[lo]+(s[hi]-s[lo])*(i-lo); };

function draw() {
  const c = cell(); if (!c) return;
  const cv = $("#main"), g = cv.getContext("2d");
  const W = cv.clientWidth, Hh = cv.height, dpr = devicePixelRatio || 1;
  cv.width = W*dpr; cv.height = Hh*dpr; g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,W,Hh);
  const padL=6, padR=58, padT=10, padB=20, plotW=W-padL-padR, plotH=Hh-padT-padB;
  const nH = c.hist.c.length, total = nH + H;
  const X = i => padL + i/(total-1)*plotW;

  let lo=1e18, hi=-1e18;
  const push = v => { if (v!=null){ lo=Math.min(lo,v); hi=Math.max(hi,v);} };
  c.hist.h.forEach(push); c.hist.l.forEach(push);
  if (show.paths||show.band) c.paths.forEach(p => { p.h.forEach(push); p.l.forEach(push); });
  if (show.cone) { const r=c.ruler*TICK;
    push(c.close + D.k[`up_30_0.95`]*r); push(c.close - D.k[`dn_30_0.95`]*r); }
  if ($("#reveal").checked) { c.future.h.forEach(push); c.future.l.forEach(push); }
  const pad=(hi-lo)*.06||1; lo-=pad; hi+=pad;
  const Y = p => padT + (hi-p)/(hi-lo)*plotH;

  g.strokeStyle="#232733"; g.fillStyle="#8a8f9c"; g.font="10px ui-monospace,monospace";
  const step = Math.max(5, Math.round((hi-lo)/7/5)*5);
  for (let p=Math.ceil(lo/step)*step; p<hi; p+=step){ const y=Y(p);
    g.beginPath(); g.moveTo(padL,y); g.lineTo(W-padR,y); g.stroke(); g.fillText(p.toFixed(0), W-padR+5, y+3); }

  // --- k x ruler cone, drawn first
  if (show.cone) {
    const r = c.ruler*TICK, anchors=[[0,0],[5,1],[15,2],[30,3]];
    const kAt=(side,h)=>{ const pts=[[0,0],[5,D.k[`${side}_5_0.95`]],[15,D.k[`${side}_15_0.95`]],[30,D.k[`${side}_30_0.95`]]];
      for(let i=1;i<pts.length;i++){ if(h<=pts[i][0]){ const[a,b]=pts[i-1],[x,y]=pts[i];
        return b+(y-b)*(h-a)/(x-a);} } return pts[3][1]; };
    g.strokeStyle="rgba(108,92,231,.9)"; g.lineWidth=1.2; g.setLineDash([4,3]);
    for (const side of ["up","dn"]) { g.beginPath();
      for (let h=0; h<=H; h++){ const d=kAt(side,h)*r, y=Y(side==="up"?c.close+d:c.close-d);
        h?g.lineTo(X(nH-1+h),y):g.moveTo(X(nH-1),y); } g.stroke(); }
    g.setLineDash([]);
  }
  // --- Kronos band: 10-90% of the sampled closes, minute by minute
  if (show.band) {
    g.fillStyle="rgba(108,92,231,.30)"; g.beginPath();
    for (let h=0; h<H; h++){ const v=c.paths.map(p=>p.c[h]); const y=Y(pct(v,.9));
      h?g.lineTo(X(nH+h),y):g.moveTo(X(nH+h),y); }
    for (let h=H-1; h>=0; h--){ const v=c.paths.map(p=>p.c[h]); g.lineTo(X(nH+h),Y(pct(v,.1))); }
    g.closePath(); g.fill();
  }
  // --- the sampled futures themselves
  if (show.paths) {
    g.strokeStyle="rgba(196,181,253,.55)"; g.lineWidth=1;
    for (const p of c.paths){ g.beginPath(); g.moveTo(X(nH-1),Y(c.close));
      for(let h=0;h<H;h++) g.lineTo(X(nH+h),Y(p.c[h])); g.stroke(); }
  }
  // --- history
  g.strokeStyle="#6b7280"; g.lineWidth=1; g.globalAlpha=.7;
  for(let i=0;i<nH;i++){ g.beginPath(); g.moveTo(X(i),Y(c.hist.h[i])); g.lineTo(X(i),Y(c.hist.l[i])); g.stroke(); }
  g.globalAlpha=1; g.strokeStyle="#e6e8ee"; g.lineWidth=1.4; g.beginPath();
  for(let i=0;i<nH;i++) i?g.lineTo(X(i),Y(c.hist.c[i])):g.moveTo(X(i),Y(c.hist.c[i]));
  g.stroke();
  // --- truth
  if ($("#reveal").checked){ g.strokeStyle="#e0a52a"; g.lineWidth=2; g.beginPath(); g.moveTo(X(nH-1),Y(c.close));
    for(let h=0;h<H;h++) g.lineTo(X(nH+h),Y(c.future.c[h])); g.stroke();
    g.globalAlpha=.35; g.lineWidth=1;
    for(let h=0;h<H;h++){ g.beginPath(); g.moveTo(X(nH+h),Y(c.future.h[h])); g.lineTo(X(nH+h),Y(c.future.l[h])); g.stroke(); }
    g.globalAlpha=1; }
  // --- the anchor
  g.strokeStyle="#fff"; g.globalAlpha=.45; g.beginPath();
  g.moveTo(X(nH-1),padT); g.lineTo(X(nH-1),Hh-padB); g.stroke(); g.globalAlpha=1;
}

function readout() {
  const c = cell(); if (!c) return;
  const r = c.ruler*TICK;
  const kUp = D.k["up_30_0.95"]*r, kDn = D.k["dn_30_0.95"]*r;
  const ups = c.paths.map(p => Math.max(...p.h) - c.close);
  const dns = c.paths.map(p => c.close - Math.min(...p.l));
  const aUp = Math.max(...c.future.h) - c.close, aDn = c.close - Math.min(...c.future.l);
  const f = v => v.toFixed(1);
  $("#readout").innerHTML =
    `<div class="h"></div><div class="h k">Kronos</div><div class="h r">k x ruler</div>` +
    `<div class="h">up 95%</div><div class="k">${f(pct(ups,.95))}</div><div class="r">${f(kUp)}</div>` +
    `<div class="h">down 95%</div><div class="k">${f(pct(dns,.95))}</div><div class="r">${f(kDn)}</div>` +
    `<div class="h">up median</div><div class="k">${f(pct(ups,.5))}</div><div class="r">${f(D.k["up_30_0.5"]*r)}</div>` +
    `<div class="h">down median</div><div class="k">${f(pct(dns,.5))}</div><div class="r">${f(D.k["dn_30_0.5"]*r)}</div>` +
    `<div class="h" style="padding-top:6px">ACTUAL up</div><div style="color:var(--gold);padding-top:6px">${f(aUp)}</div><div></div>` +
    `<div class="h">ACTUAL down</div><div style="color:var(--gold)">${f(aDn)}</div><div></div>`;
  $("#meta").innerHTML = `<b>${c.day}</b> · anchor <b>${hhmm(c.anchor)}</b> · ruler <b>${c.ruler}t</b> `
    + `(${(c.ruler*TICK).toFixed(2)} pts) · ${D.paths} sampled futures · Kronos-mini, lookback ${D.lookback} · held-out session`;
}
function render(){ draw(); readout(); }
$("#day").onchange = () => { fillAnchors(); render(); };
$("#anchor").onchange = render;
$("#reveal").onchange = render;
document.querySelectorAll(".t").forEach(b => b.onclick = () => {
  show[b.dataset.t] = !show[b.dataset.t]; b.classList.toggle("on"); render(); });
addEventListener("resize", draw);
fillAnchors(); render();
</script>
"""

if __name__ == "__main__":
    main()
