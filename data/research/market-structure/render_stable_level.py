"""Chart the null: hold rate by level age, dead flat below the 50% coin flip.
Writes docs/research/stable-level-sr.html (renders in the Research tab)."""
import numpy as np
import pandas as pd

df = pd.read_parquet('data/research/market-structure/stable_level_events.parquet')
r = df[df.is_rth]
bins = [(0, 5, '<5m'), (5, 15, '5-15'), (15, 30, '15-30'),
        (30, 60, '30-60'), (60, 120, '60-120'), (120, 1e9, '>120m')]
data = []
for lo, hi, tag in bins:
    d = r[(r.age_min >= lo) & (r.age_min < hi) & (r.decisive == 1)]
    data.append((tag, d.held.mean(), len(d)))

W, H = 640, 300
pl, pr, pt, pb = 44, 16, 22, 40
iw, ih = W - pl - pr, H - pt - pb
ymin, ymax = 0.30, 0.60
Y = lambda v: pt + ih * (ymax - v) / (ymax - ymin)
m = len(data); slot = iw / m
el = []
# gridlines + y labels
for g in (0.30, 0.40, 0.45, 0.50, 0.55, 0.60):
    y = Y(g)
    cls = 'ref' if g == 0.50 else 'grid'
    el.append(f'<line class="{cls}" x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}"/>')
    el.append(f'<text class="ax" x="{pl-6}" y="{y+3:.1f}" text-anchor="end">{g:.0%}</text>')
el.append(f'<text class="reftxt" x="{pl+iw-2}" y="{Y(0.50)-4:.1f}" text-anchor="end">'
          f'50% coin flip — a level neither holds nor breaks preferentially</text>')
bw = slot * 0.56
for i, (tag, hr, n) in enumerate(data):
    cx = pl + slot * (i + 0.5)
    y = Y(hr)
    el.append(f'<rect class="bar" x="{cx-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
              f'height="{pt+ih-y:.1f}"/>')
    el.append(f'<text class="val" x="{cx:.1f}" y="{y-5:.1f}" text-anchor="middle">{hr:.0%}</text>')
    el.append(f'<text class="ax" x="{cx:.1f}" y="{pt+ih+15:.1f}" text-anchor="middle">{tag}</text>')
    el.append(f'<text class="nn" x="{cx:.1f}" y="{pt+ih+27:.1f}" text-anchor="middle">n={n}</text>')
el.append(f'<text class="axt" x="{pl+iw/2:.1f}" y="{H-4}" text-anchor="middle">'
          f'how long the level had been sitting still at the touch</text>')
svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'

HTML = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stable VP level as S/R</title><style>
:root{{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--border:rgba(11,11,11,.10);--bar:#2a78d6;--ref:#e34948;}}
@media(prefers-color-scheme:dark){{:root{{--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;
--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--border:rgba(255,255,255,.10);--bar:#3987e5;--ref:#e66767;}}}}
:root[data-theme="dark"]{{--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--border:rgba(255,255,255,.10);--bar:#3987e5;--ref:#e66767;}}
:root[data-theme="light"]{{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e1e0d9;--border:rgba(11,11,11,.10);--bar:#2a78d6;--ref:#e34948;}}
body{{background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
margin:0;padding:28px 20px 60px;line-height:1.45;}}.wrap{{max-width:760px;margin:0 auto;}}
h1{{font-size:22px;margin:0 0 4px;letter-spacing:-.01em;}}
.sub{{color:var(--ink2);font-size:13.5px;max-width:74ch;margin:0 0 16px;}}
.panel{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;}}
svg{{display:block;width:100%;height:auto;}}svg text{{font-variant-numeric:tabular-nums;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;}}
.grid{{stroke:var(--grid);stroke-width:1;}}.ref{{stroke:var(--ref);stroke-width:1.3;stroke-dasharray:5 4;}}
.reftxt{{fill:var(--ref);font-size:10px;font-weight:600;}}
.bar{{fill:var(--bar);opacity:.85;}}.val{{fill:var(--ink);font-size:12px;font-weight:700;}}
.ax{{fill:var(--muted);font-size:10.5px;}}.nn{{fill:var(--muted);font-size:9px;}}
.axt{{fill:var(--ink2);font-size:11px;}}
.take{{font-size:13.5px;color:var(--ink2);margin:16px 0 0;max-width:74ch;}}
.take b{{color:var(--ink);}}
</style></head><body><div class="wrap">
<h1>Does a stable VP level hold as support/resistance?</h1>
<p class="sub">Hold rate = share of touches where price rejected off a developing
POC/VAH/VAL (fell 15t back) instead of breaking 12t through it. 5,759 RTH touches,
360 sessions. Bars grouped by how long the level had been sitting still (±2t) when
price arrived — the "flatness / stability" axis.</p>
<div class="panel">{svg}</div>
<p class="take"><b>The bars are flat and all sit below 50%.</b> A developing VP level
breaks through more often than it holds (~45% / 55%), and stability doesn't change
that — an entrenched level that hasn't budged in two hours holds no better than one
five minutes old (perm p=1.00, ρ=+0.015). The stable-cohort rate is also 40% in the
first half of the sample and 52% in the second: it straddles the coin flip and flips.
Same lesson as every VP-geometry cut here — a level is a magnet that gets consumed,
not a wall price respects.</p>
<p class="sub" style="margin-top:18px">Full method &amp; stats:
<code>docs/research/stable-level-sr.md</code>.</p>
</div></body></html>'''
open('docs/research/stable-level-sr.html', 'w').write(HTML)
print('WROTE docs/research/stable-level-sr.html')
