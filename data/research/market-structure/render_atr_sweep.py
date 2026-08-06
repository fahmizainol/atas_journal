"""ATR-multiplier sweep — the one knob on the volatility-adaptive swing tier.

Same session, same non-repainting detector; only `mult` in thr = mult x median
ATR14 changes. Lower mult -> more, finer swings; higher -> fewer, major-only.
Lets you dial the granularity you want. Companion for §7 of
market-structure-events.md.
"""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, "data/research/market-structure")
import numpy as np
import pandas as pd
from datetime import date
from journal.sim.ticks import cached_rth
from journal.sim.regime import minute_bars
from structure_events import structure_events, causal_zigzag

ET = "America/New_York"
SYM, SESS = "NQM6", "2026-06-11"
MULTS = [2.0, 3.0, 4.0, 6.0]


def median_atr(b, n=14):
    h, l, c = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return float(pd.Series(tr).rolling(n).mean().median())


def panel(full, atr, mult):
    thr = round(mult * atr, 1)
    _, ev = structure_events(full, thr_pts=thr)
    piv = causal_zigzag(full["high"].to_numpy(), full["low"].to_numpy(), thr)
    label_tier = len(piv) <= 42
    o, h, l, c = (full[x].to_numpy() for x in ("open", "high", "low", "close"))
    hm = pd.to_datetime(full["ts_utc"]).dt.tz_convert(ET).dt.strftime("%H:%M").to_numpy()
    m = len(full)

    W, H = 1020, 260
    pl, pr, pt, pb = 6, 54, 14, 20
    iw, ih = W - pl - pr, H - pt - pb
    ymin, ymax = float(l.min()), float(h.max())
    sp = (ymax - ymin) or 1; ymin -= sp * .05; ymax += sp * .05
    Y = lambda v: pt + ih * (ymax - v) / (ymax - ymin)
    bw = iw / m
    X = lambda i: pl + bw * (i + .5)
    el = []
    for f in range(4):
        v = ymin + (ymax - ymin) * f / 3; y = Y(v)
        el.append(f'<line class="gridln" x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}"/>')
        el.append(f'<text class="axistxt" x="{pl+iw+4:.1f}" y="{y+3:.1f}">{v:.0f}</text>')
    for t in [f"{hh:02d}:00" for hh in range(10, 17)]:
        w = np.where(hm == t)[0]
        if len(w):
            el.append(f'<text class="axistxt" x="{X(int(w[0])):.1f}" y="{H-5}" '
                      f'text-anchor="middle">{t}</text>')
    cw = max(bw * .55, .8)
    for i in range(m):
        cls = "cu" if c[i] >= o[i] else "cd"
        xx = X(i)
        el.append(f'<line class="{cls}" x1="{xx:.1f}" y1="{Y(h[i]):.1f}" x2="{xx:.1f}" '
                  f'y2="{Y(l[i]):.1f}" stroke-width=".7"/>')
        yo, yc = Y(o[i]), Y(c[i])
        el.append(f'<rect class="{cls}" x="{xx-cw/2:.1f}" y="{min(yo,yc):.1f}" '
                  f'width="{cw:.1f}" height="{max(abs(yc-yo),.8):.1f}"/>')
    lab_by_bar = {int(r["bar"]): r["label"] for _, r in ev.iterrows()
                  if r["type"].startswith("pivot")}
    pts = [(pi, p, k, lab_by_bar.get(pi, "")) for (pi, p, k, _c) in piv]
    if len(pts) >= 2:
        poly = " ".join(f"{X(i):.1f},{Y(p):.1f}" for i, p, _, _ in pts)
        w = 1.9 if label_tier else 1.1
        op = ".95" if label_tier else ".5"
        el.append(f'<polyline points="{poly}" fill="none" stroke="var(--zz)" '
                  f'stroke-width="{w}" opacity="{op}"/>')
    if label_tier:
        for i, p, k, lab in pts:
            el.append(f'<circle cx="{X(i):.1f}" cy="{Y(p):.1f}" r="2.5" fill="var(--zz)"/>')
            if lab:
                dy = -5 if k == "H" else 11
                el.append(f'<text x="{X(i):.1f}" y="{Y(p)+dy:.1f}" fill="var(--zz)" '
                          f'font-size="9.5" font-weight="700" text-anchor="middle">{lab}</text>')
        for _, e in ev[ev["type"].str.startswith("CHoCH")].iterrows():
            k = int(e["bar"]); up = e["type"].endswith("_up")
            col = "var(--up)" if up else "var(--dn)"
            el.append(f'<line x1="{X(k):.1f}" y1="{pt}" x2="{X(k):.1f}" y2="{pt+ih:.1f}" '
                      f'stroke="{col}" stroke-width="1.1" stroke-dasharray="4 3" opacity=".8"/>')
    svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'
    cad = 390 / max(len(piv), 1)
    tag = ("fine / internal" if mult <= 2 else "intermediate" if mult < 4
           else "major" if mult < 6 else "skeleton")
    return f'''<div class="panel">
  <div class="phead"><div class="ptitle">ATR × {mult:g}
    <span class="sess">thr = {thr:.0f}pt · {len(piv)} swings · ≈1 pivot / {cad:.0f} min · {tag}</span></div>
    <span class="chip">mult {mult:g}</span></div>{svg}</div>'''


full = minute_bars(cached_rth(SYM, date.fromisoformat(SESS)), "1min").reset_index(drop=True)
atr = median_atr(full)
blocks = "\n".join(panel(full, atr, mu) for mu in MULTS)
SHELL = '''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ATR swing multiplier sweep</title>
<style>
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
 --border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;--zz:#4a3aa7;}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
 --muted:#898781;--grid:#2c2c2a;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;--zz:#9085e9;}}
:root[data-theme="dark"]{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;--zz:#9085e9;}
:root[data-theme="light"]{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;--zz:#4a3aa7;}
body{background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
 margin:0;padding:26px 20px 60px;line-height:1.45;}
.wrap{max-width:1060px;margin:0 auto;}
h1{font-size:21px;margin:0 0 4px;} .sub{color:var(--ink2);font-size:13.5px;max-width:82ch;margin:0 0 16px;}
.panels{display:grid;grid-template-columns:1fr;gap:14px;}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px 6px;}
.phead{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:2px;}
.ptitle{font-size:14px;font-weight:600;} .ptitle .sess{color:var(--ink2);font-weight:400;font-size:12px;}
.chip{font-size:11px;font-weight:700;padding:1px 8px;border-radius:99px;border:1px solid var(--border);color:var(--zz);}
svg{display:block;width:100%;height:auto;} svg text{font-family:system-ui,sans-serif;font-variant-numeric:tabular-nums;}
.gridln{stroke:var(--grid);stroke-width:1;} .axistxt{fill:var(--muted);font-size:10px;}
.cu{stroke:var(--up);fill:var(--up);opacity:.5;} .cd{stroke:var(--dn);fill:var(--dn);opacity:.5;}
</style></head><body><div class="wrap">
<h1>ATR swing multiplier — dialling granularity on one session</h1>
<p class="sub">__SESSHDR__. The only thing changing top to bottom is the multiplier in
<code>threshold = mult × median&nbsp;ATR14</code>. Same detector, same day. Pick the mult that matches the
structure you care about: <b>~2–3</b> for intraday internal structure, <b>~4–5</b> for the major swing
skeleton, <b>~6+</b> for only the day's defining legs. Labels/CHoCH marks shown once the tier is sparse
enough to read (≤42 swings).</p>
<div class="panels">
__BLOCKS__
</div></div></body></html>'''
SHELL = SHELL.replace("__SESSHDR__",
    f"{SYM} {SESS} · median ATR14 ≈ {atr:.0f}pt (so ATR×4 ≈ {4*atr:.0f}pt reversals)")
SHELL = SHELL.replace("__BLOCKS__", blocks)
open("docs/research/market-structure-atr-sweep.html", "w").write(SHELL)
print(f"wrote docs/research/market-structure-atr-sweep.html  (ATR14={atr:.1f})")
