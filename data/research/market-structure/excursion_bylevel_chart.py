"""Aggregate per-level excursion chart: after a touch, how far & which way,
across all sessions, broken down by developing VP level.

Two panels + a table, from stable_level_excursion.parquet (RTH touches):

  A. Diverging envelope bars per level: median max-up excursion above the
     baseline, median max-down below, with the median NET @60m as a dot.
  B. Small-multiple net@60m distributions per level (25t bins, clipped at
     +/-400t) -- the "direction is a coin flip everywhere" picture.

Writes docs/research/stable-level-excursion.html (renders in the Lab).

Usage: .venv/bin/python data/research/market-structure/excursion_bylevel_chart.py
"""
import numpy as np
import pandas as pd

D = 'data/research/market-structure'
OUT = 'docs/research/stable-level-excursion.html'
LEVELS = ['gx_poc', 'ny_poc', 'gx_vah', 'ny_vah', 'gx_val', 'ny_val']

df = pd.read_parquet(f'{D}/stable_level_excursion.parquet')
r = df[df.is_rth]

stats = {}
for lv in LEVELS:
    s = r[r.level == lv]
    stats[lv] = dict(
        n=len(s), up_share=float((s.net_t > 0).mean()),
        net_med=float(s.net_t.median()), net_mean=float(s.net_t.mean()),
        env_up=float(s.up_t.median()), env_dn=float(s.dn_t.median()),
        thru=float(s.thru_t.median()), back=float(s.back_t.median()))

# net_t histograms, 25t bins clipped to +/-400t
BINS = np.arange(-400, 425, 25)
hists = {lv: np.histogram(r[r.level == lv].net_t.clip(-400, 400), bins=BINS)[0]
         for lv in LEVELS}

# --- panel A: diverging envelope bars ---------------------------------------
AW, AH = 1040, 360
a_t, a_b, a_l, a_r = 30, 46, 56, 16
plotw, ploth = AW - a_l - a_r, AH - a_t - a_b
maxenv = max(max(s['env_up'], s['env_dn']) for s in stats.values()) * 1.18
y0 = a_t + ploth * maxenv / (2 * maxenv)          # zero baseline (centered)


def ay(v):                                        # value (ticks, +up) -> y
    return y0 - v / maxenv * (ploth / 2)


band = plotw / len(LEVELS)
barw = min(56, band * 0.42)
elA = []
for gv in (100, 200):                             # recessive grid
    for sgn in (1, -1):
        elA.append(f'<line class="grid" x1="{a_l}" y1="{ay(sgn*gv):.1f}" '
                   f'x2="{AW-a_r}" y2="{ay(sgn*gv):.1f}"/>')
        elA.append(f'<text class="ax" x="{a_l-6}" y="{ay(sgn*gv)+3:.1f}" '
                   f'text-anchor="end">{"+" if sgn>0 else "−"}{gv}t</text>')
elA.append(f'<line class="zero" x1="{a_l}" y1="{y0:.1f}" x2="{AW-a_r}" y2="{y0:.1f}"/>')
elA.append(f'<text class="ax" x="{a_l-6}" y="{y0+3:.1f}" text-anchor="end">0</text>')

for i, lv in enumerate(LEVELS):
    s = stats[lv]
    cx = a_l + band * (i + 0.5)
    x = cx - barw / 2
    hu = y0 - ay(s['env_up'])
    hd = ay(-s['env_dn']) - y0
    tip = (f"{lv} — n={s['n']:,} | median envelope +{s['env_up']:.0f}t / "
           f"−{s['env_dn']:.0f}t | net@60m {s['net_med']:+.0f}t | "
           f"{s['up_share']:.0%} finish up")
    elA.append(f'<g class="hov" data-tip="{tip}">'
               f'<rect class="up-arm" x="{x:.1f}" y="{ay(s["env_up"]):.1f}" '
               f'width="{barw:.1f}" height="{max(0,hu-1):.1f}" rx="4"/>'
               f'<rect class="dn-arm" x="{x:.1f}" y="{y0+1:.1f}" '
               f'width="{barw:.1f}" height="{max(0,hd-1):.1f}" rx="4"/>'
               f'<circle class="net" cx="{cx:.1f}" cy="{ay(s["net_med"]):.1f}" r="5"/>'
               f'<rect class="hit" x="{cx-band/2:.1f}" y="{a_t}" width="{band:.1f}" height="{ploth}"/></g>')
    elA.append(f'<text class="dlab up-t" x="{cx:.1f}" y="{ay(s["env_up"])-6:.1f}" '
               f'text-anchor="middle">+{s["env_up"]:.0f}t</text>')
    elA.append(f'<text class="dlab dn-t" x="{cx:.1f}" y="{ay(-s["env_dn"])+14:.1f}" '
               f'text-anchor="middle">−{s["env_dn"]:.0f}t</text>')
    elA.append(f'<text class="cat" x="{cx:.1f}" y="{AH-a_b+18:.1f}" '
               f'text-anchor="middle">{lv}</text>')
    elA.append(f'<text class="catn" x="{cx:.1f}" y="{AH-a_b+32:.1f}" '
               f'text-anchor="middle">n={s["n"]:,}</text>')

svgA = f'<svg viewBox="0 0 {AW} {AH}">{"".join(elA)}</svg>'

# --- panel B: net@60m small-multiple histograms ------------------------------
FW, FH = 340, 150
f_t, f_b, f_l, f_r = 22, 24, 10, 10
maxc = max(h.max() for h in hists.values())
facets = []
for lv in LEVELS:
    h = hists[lv]
    s = stats[lv]
    fw = (FW - f_l - f_r) / len(h)
    el = [f'<line class="zero" x1="{f_l+(FW-f_l-f_r)/2:.1f}" y1="{f_t-4}" '
          f'x2="{f_l+(FW-f_l-f_r)/2:.1f}" y2="{FH-f_b}"/>']
    for j, c in enumerate(h):
        if c == 0:
            continue
        bx = f_l + j * fw
        bh_ = c / maxc * (FH - f_t - f_b)
        mid = (BINS[j] + BINS[j + 1]) / 2
        cls = 'up-arm' if mid > 0 else ('dn-arm' if mid < 0 else 'zero-bin')
        tip = f"{lv} net {BINS[j]:+d}…{BINS[j+1]:+d}t: {c} touches"
        el.append(f'<rect class="{cls} hov" data-tip="{tip}" x="{bx+0.6:.1f}" '
                  f'y="{FH-f_b-bh_:.1f}" width="{fw-1.2:.1f}" height="{bh_:.1f}" rx="2"/>')
    el.append(f'<text class="cat" x="{f_l}" y="{f_t-8}">{lv}</text>')
    el.append(f'<text class="catn" x="{FW-f_r}" y="{f_t-8}" text-anchor="end">'
              f'{s["up_share"]:.0%} up · med {s["net_med"]:+.0f}t</text>')
    el.append(f'<text class="ax" x="{f_l}" y="{FH-8}">−400t</text>')
    el.append(f'<text class="ax" x="{FW-f_r}" y="{FH-8}" text-anchor="end">+400t</text>')
    facets.append(f'<svg viewBox="0 0 {FW} {FH}">{"".join(el)}</svg>')

trows = "".join(
    f'<tr><td>{lv}</td><td>{s["n"]:,}</td><td>{s["up_share"]:.0%} / {1-s["up_share"]:.0%}</td>'
    f'<td>{s["net_med"]:+.0f}t</td><td>{s["net_mean"]:+.1f}t</td>'
    f'<td>+{s["env_up"]:.0f}t</td><td>−{s["env_dn"]:.0f}t</td>'
    f'<td>{s["thru"]:.0f}t / {s["back"]:.0f}t = {s["thru"]/max(s["back"],1e-9):.2f}</td></tr>'
    for lv, s in stats.items())

n_all = len(r)
up_all = (r.net_t > 0).mean()

HTML = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>After a VP-level touch: how far, which way — by level</title><style>
.viz-root{{
  --surface-1:#fcfcfb; --text-primary:#0b0b0b; --text-secondary:#52514e;
  --text-muted:#7a7975; --line:#e4e3df;
  --up:#22c55e; --dn:#ef4444; --net:#0b0b0b;
}}
@media(prefers-color-scheme:dark){{.viz-root{{
  --surface-1:#1a1a19; --text-primary:#ffffff; --text-secondary:#c3c2b7;
  --text-muted:#8b8a84; --line:#343430;
  --up:#1baf7a; --dn:#e66767; --net:#ffffff;
}}}}
:root{{color-scheme:light dark}}
body{{margin:0;padding:24px;background:var(--surface-1);color:var(--text-primary);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:1100px}}
h1{{font-size:19px;margin:0 0 4px}} h2{{font-size:15px;margin:26px 0 6px}}
.sub{{color:var(--text-secondary);font-size:13px;margin:0 0 6px;line-height:1.5}}
svg{{display:block;width:100%;height:auto}}
svg text{{font-size:11px;font-variant-numeric:tabular-nums;fill:var(--text-secondary)}}
.grid{{stroke:var(--line);stroke-width:.6}}
.zero{{stroke:var(--text-muted);stroke-width:1}}
.up-arm{{fill:var(--up)}} .dn-arm{{fill:var(--dn)}} .zero-bin{{fill:var(--text-muted)}}
.net{{fill:var(--net);stroke:var(--surface-1);stroke-width:1.6}}
.hit{{fill:transparent}}
.dlab{{font-size:11px;font-weight:600;fill:var(--text-primary)}}
.cat{{font-size:11.5px;font-weight:600;fill:var(--text-primary)}}
.catn{{font-size:10.5px;fill:var(--text-muted)}}
.leg{{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);
  margin:8px 0 2px;align-items:center}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px 22px}}
.panel{{border:1px solid var(--line);border-radius:8px;padding:10px 12px}}
table{{border-collapse:collapse;font-size:12.5px;margin-top:10px;width:100%}}
td,th{{padding:4px 12px 4px 0;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--text-secondary);font-weight:600}}
.note{{background:rgba(120,130,150,.09);border-left:3px solid var(--text-muted);
  padding:10px 14px;border-radius:4px;font-size:13px;margin:14px 0;line-height:1.55}}
#tip{{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);
  font-size:12px;padding:5px 9px;border-radius:6px;opacity:0;transition:opacity .08s;
  max-width:340px;z-index:9}}
</style></head><body class="viz-root">
<h1>After a touch of a developing VP level: how far did price go, and which way?</h1>
<p class="sub">All {n_all:,} RTH touches, 360 sessions (2025-02 → 2026-06), 60-minute window.
Companion to the stable-level S/R study — same touch definition (within 6t, 30-min dedup).</p>

<h2>Median excursion envelope, by level</h2>
<p class="sub">Bars: how far price typically travels <b>above</b> and <b>below</b> the level
within the hour (median of each touch's max excursion). Dot: median <b>net</b> position at +60m.</p>
<div class="leg">
  <span><span class="sw" style="background:var(--up)"></span>max-up excursion</span>
  <span><span class="sw" style="background:var(--dn)"></span>max-down excursion</span>
  <span><span class="sw" style="background:var(--net);border-radius:50%"></span>net @ +60m (median)</span>
</div>
<div class="panel">{svgA}</div>

<h2>Net position at +60m — distribution by level</h2>
<p class="sub">25-tick bins, clipped at ±400t. Every level: wide, roughly centered,
mild rightward lean = the sample's bull drift, not the level.</p>
<div class="grid2">{"".join(f'<div class="panel">{f}</div>' for f in facets)}</div>

<div class="note"><b>Read:</b> {up_all:.0%} of touches finish the hour above the level,
median net +20t — but the typical touch first pokes ~150t up <i>and</i> ~150t down.
No level's net mean is distinguishable from zero (all |t|&lt;1.4), and the
continue-through vs bounce-back ratio is 0.90–1.03 everywhere: price travels equally
far in both directions off every level. The only per-level fingerprint is <b>swing size</b>:
VAL touches carry the widest envelopes (~176–190t) — they happen in faster tape —
vs ~130–150t at POC/VAH. Level identity says how <i>violent</i> the next hour is, not
which way it goes. The apparent VAH-as-support / VAL-as-resistance leans are session
drift: on those same sessions, <i>other</i> touches moved further in the same direction
(e.g. ny_vah/support +34t vs +43t for its own sessions' other touches) — the cells
lag their regime, they don't lead it.</div>

<h2>Table</h2>
<table><tr><th>level</th><th>n</th><th>up / down @60m</th><th>net med</th><th>net mean</th>
<th>env up</th><th>env down</th><th>thru / back (ratio)</th></tr>{trows}</table>

<div id="tip"></div>
<script>
const tip = document.getElementById('tip');
document.querySelectorAll('.hov').forEach(el => {{
  el.addEventListener('mousemove', e => {{
    tip.textContent = el.dataset.tip;
    tip.style.opacity = 1;
    tip.style.left = Math.min(e.clientX + 14, innerWidth - 360) + 'px';
    tip.style.top = (e.clientY + 14) + 'px';
  }});
  el.addEventListener('mouseleave', () => tip.style.opacity = 0);
}});
</script>
</body></html>'''

with open(OUT, 'w') as f:
    f.write(HTML)
print('wrote', OUT)
for lv, s in stats.items():
    print(f"{lv:8s} n={s['n']:5d} up={s['up_share']:.0%} net_med={s['net_med']:+5.1f} "
          f"env +{s['env_up']:.0f}/-{s['env_dn']:.0f}")
