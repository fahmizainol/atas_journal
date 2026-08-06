"""Major vs minor swings — the noise is a threshold choice, not a limitation.

Renders the SAME sessions at two tiers, LuxAlgo internal-vs-swing style:
  * minor  = fixed 10pt zigzag (a pivot almost every bar) — what looked noisy
  * major  = ATR-scaled zigzag (thr = mult x session median ATR14) — the
    significant swings only, and volatility-adaptive so a 17k contract and a
    28k contract come out equally clean under the SAME rule.

Companion image for market-structure-events.md §"detecting major swings".
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
ATR_MULT = 5.0


def median_atr(b, n=14):
    h, l, c = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return float(pd.Series(tr).rolling(n).mean().median())


def panel(sym, sess, tier, title, note, tone):
    day = date.fromisoformat(sess)
    full = minute_bars(cached_rth(sym, day), "1min").reset_index(drop=True)
    atr = median_atr(full)
    thr = 10.0 if tier == "minor" else round(ATR_MULT * atr, 1)
    _, ev = structure_events(full, thr_pts=thr)
    piv = causal_zigzag(full["high"].to_numpy(), full["low"].to_numpy(), thr)
    label_tier = tier == "major"   # only the major tier is legible enough to label

    o, h, l, c = (full[x].to_numpy() for x in ("open", "high", "low", "close"))
    tset = pd.to_datetime(full["ts_utc"]).dt.tz_convert(ET)
    hm = tset.dt.strftime("%H:%M").to_numpy()
    m = len(full)

    W, H = 1020, 300
    pl, pr, pt, pb = 6, 54, 16, 22
    iw, ih = W - pl - pr, H - pt - pb
    ymin, ymax = float(l.min()), float(h.max())
    sp = (ymax - ymin) or 1; ymin -= sp * .05; ymax += sp * .05
    Y = lambda v: pt + ih * (ymax - v) / (ymax - ymin)
    bw = iw / m
    X = lambda i: pl + bw * (i + .5)
    el = []

    for f in range(5):
        v = ymin + (ymax - ymin) * f / 4; y = Y(v)
        el.append(f'<line class="gridln" x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}"/>')
        el.append(f'<text class="axistxt" x="{pl+iw+4:.1f}" y="{y+3:.1f}">{v:.0f}</text>')
    for t in [f"{hh:02d}:{mm:02d}" for hh in range(9, 17) for mm in (0, 30)]:
        w = np.where(hm == t)[0]
        if len(w):
            el.append(f'<text class="axistxt" x="{X(int(w[0])):.1f}" y="{H-6}" '
                      f'text-anchor="middle">{t}</text>')

    # candles first (faint) so the zigzag reads on top
    cw = max(bw * .55, .8)
    for i in range(m):
        cls = "cu" if c[i] >= o[i] else "cd"
        xx = X(i)
        el.append(f'<line class="{cls}" x1="{xx:.1f}" y1="{Y(h[i]):.1f}" x2="{xx:.1f}" '
                  f'y2="{Y(l[i]):.1f}" stroke-width=".8"/>')
        yo, yc = Y(o[i]), Y(c[i])
        el.append(f'<rect class="{cls}" x="{xx-cw/2:.1f}" y="{min(yo,yc):.1f}" '
                  f'width="{cw:.1f}" height="{max(abs(yc-yo),.8):.1f}"/>')

    # zigzag + labels
    lab_by_bar = {int(r["bar"]): r["label"] for _, r in ev.iterrows()
                  if r["type"].startswith("pivot")}
    pts = [(i, p, k, lab_by_bar.get(pi, "")) for (pi, p, k, _c) in piv
           for i in [pi]]
    if len(pts) >= 2:
        poly = " ".join(f"{X(i):.1f},{Y(p):.1f}" for i, p, _, _ in pts)
        w = 1.9 if label_tier else 1.0
        op = ".95" if label_tier else ".45"
        el.append(f'<polyline points="{poly}" fill="none" stroke="var(--zz)" '
                  f'stroke-width="{w}" opacity="{op}"/>')
    if label_tier:
        for i, p, k, lab in pts:
            el.append(f'<circle cx="{X(i):.1f}" cy="{Y(p):.1f}" r="2.6" fill="var(--zz)"/>')
            if lab:
                dy = -6 if k == "H" else 12
                el.append(f'<text x="{X(i):.1f}" y="{Y(p)+dy:.1f}" fill="var(--zz)" '
                          f'font-size="10" font-weight="700" text-anchor="middle">{lab}</text>')
        # CHoCH markers on the major tier
        for _, e in ev[ev["type"].str.startswith("CHoCH")].iterrows():
            k = int(e["bar"]); up = e["type"].endswith("_up")
            col = "var(--up)" if up else "var(--dn)"
            el.append(f'<line x1="{X(k):.1f}" y1="{pt}" x2="{X(k):.1f}" y2="{pt+ih:.1f}" '
                      f'stroke="{col}" stroke-width="1.1" stroke-dasharray="4 3" opacity=".8"/>')

    swings = len(piv)
    svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'
    return f'''<div class="panel">
  <div class="phead"><div class="ptitle">{title}
    <span class="sess">{sym} {sess} · ATR14≈{atr:.0f}pt · thr={thr:.0f}pt · {swings} swings</span></div>
    <span class="chip {tone}">{tone.upper()}</span></div>
  <div class="pstats">{note}</div>{svg}</div>'''


PANELS = [
    dict(sym="NQM5", sess="2025-04-09", tier="minor", tone="loss",
         title="Minor tier — fixed 10pt (what looked noisy)",
         note="390 pivots — a label on almost every bar. This is the “internal structure” in LuxAlgo "
              "terms and the tier the earlier panels drew; the HH/HL sequence flips so often it barely "
              "reads as structure at all."),
    dict(sym="NQM5", sess="2025-04-09", tier="major", tone="win",
         title="Major tier — ATR×5 (same session, same detector)",
         note="17 swings now tell the actual day: a choppy morning that carves a higher low, then a "
              "clean HH-over-HL uptrend into the afternoon. Nothing changed but the threshold — the "
              "dashed marks are CHoCH flips on this tier."),
    dict(sym="NQM6", sess="2026-06-11", tier="major", tone="win",
         title="Major tier — ATR×5 on a 28k contract (adapts automatically)",
         note="Identical rule on a contract trading 60% higher with a different ATR. Because the "
              "threshold is ATR-scaled it stays equally clean at ~11 swings — a fixed-point threshold "
              "would swamp this in noise or miss it entirely, as the earlier table showed."),
]

blocks = "\n".join(panel(**p) for p in PANELS)
SHELL = '''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Major vs minor swings</title>
<style>
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
 --border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;--zz:#4a3aa7;--chipwin:#006300;--chiploss:#d03b3b;}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
 --muted:#898781;--grid:#2c2c2a;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;--zz:#9085e9;
 --chipwin:#0ca30c;--chiploss:#e66767;}}
:root[data-theme="dark"]{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;--zz:#9085e9;--chipwin:#0ca30c;--chiploss:#e66767;}
:root[data-theme="light"]{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;--zz:#4a3aa7;--chipwin:#006300;--chiploss:#d03b3b;}
body{background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
 margin:0;padding:26px 20px 60px;line-height:1.45;}
.wrap{max-width:1060px;margin:0 auto;}
h1{font-size:21px;margin:0 0 4px;} .sub{color:var(--ink2);font-size:13.5px;max-width:80ch;margin:0 0 16px;}
.panels{display:grid;grid-template-columns:1fr;gap:16px;}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px 8px;}
.phead{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;}
.ptitle{font-size:14px;font-weight:600;} .ptitle .sess{color:var(--ink2);font-weight:400;font-size:12px;}
.chip{font-size:11px;font-weight:700;padding:1px 8px;border-radius:99px;border:1px solid var(--border);}
.chip.win{color:var(--chipwin);} .chip.loss{color:var(--chiploss);}
.pstats{font-size:12.5px;color:var(--ink2);margin:2px 0 8px;max-width:96ch;}
svg{display:block;width:100%;height:auto;} svg text{font-family:system-ui,sans-serif;font-variant-numeric:tabular-nums;}
.gridln{stroke:var(--grid);stroke-width:1;} .axistxt{fill:var(--muted);font-size:10px;}
.cu{stroke:var(--up);fill:var(--up);opacity:.55;} .cd{stroke:var(--dn);fill:var(--dn);opacity:.55;}
</style></head><body><div class="wrap">
<h1>Detecting major swings — it's a threshold, not a limitation</h1>
<p class="sub">The purple zigzag is the same causal, non-repainting detector throughout; only the reversal
threshold changes. <b>Minor</b> = fixed 10pt (labels every wiggle). <b>Major</b> = ATR×5, the volatility-scaled
“swing structure” tier (LuxAlgo internal-vs-swing; ATR-ZigZag). Candles are faded so the structure reads on top.</p>
<div class="panels">
__BLOCKS__
</div></div></body></html>'''.replace("__BLOCKS__", blocks)
open("docs/research/market-structure-swing-tiers.html", "w").write(SHELL)
print("wrote docs/research/market-structure-swing-tiers.html")
