"""One wide panel for 2025-02-04: full RTH session, both developing VAH lines,
the dev1/dev2 band, and all 5 snap events marked and colored by outcome.
Appended into docs/research/vah-snap-examples.html so it renders in-app.
"""
import sys
sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import profile as profmod
from journal.sim import vwap as vwapmod

TICK, TPB = 0.25, 500
SESSION = '2025-02-04'
ev = pd.read_parquet('data/research/market-structure/vah_snap_events.parquet')
evd = ev[ev.session == SESSION].copy()

day = pd.Timestamp(SESSION).date()
sym = tickmod.contract_for_cached('NQ', day)
t = tickmod.get_day_ticks(sym, day, include_overnight=True)
n = len(t)
ts = t['ts_utc'].values.astype('datetime64[ns]')
px = t['price'].to_numpy(dtype='float64')
b = barmod.tick_bars(t, TPB)
bands = vwapmod.vwap_bands(t)
up1 = bands['upper1'].to_numpy(); up2 = bands['upper2'].to_numpy()
rth0_ts, rth1_ts = tickmod.session_bounds_utc(day)
rth_i0 = int(t['ts_utc'].searchsorted(rth0_ts, side='left'))

prof_gx = profmod.developing_profile(t, b, TICK)
vah_gx = profmod.levels_in_force(prof_gx, b, n, edge='vah')
vah_ny = np.full(n, np.nan)
t_r = t.iloc[rth_i0:].reset_index(drop=True)
b_r = barmod.tick_bars(t_r, TPB)
prof_ny = profmod.developing_profile(t_r, b_r, TICK)
vah_ny[rth_i0:] = profmod.levels_in_force(prof_ny, b_r, n - rth_i0, edge='vah')

# window 09:25 -> 16:00 ET
lo = pd.Timestamp(f'{SESSION} 09:25', tz='America/New_York').tz_convert('UTC').tz_localize(None).to_datetime64()
hi = pd.Timestamp(f'{SESSION} 16:00', tz='America/New_York').tz_convert('UTC').tz_localize(None).to_datetime64()
grid = pd.date_range(pd.Timestamp(lo).ceil('1min'), pd.Timestamp(hi).floor('1min'),
                     freq='1min').values.astype('datetime64[ns]')
prev = np.searchsorted(ts, grid, side='left')
bars = []
for gi in range(1, len(grid)):
    a, z = int(prev[gi - 1]), int(prev[gi])
    if z <= a:
        continue
    seg = px[a:z]; il = z - 1
    et = pd.Timestamp(grid[gi]).tz_localize('UTC').tz_convert('America/New_York')
    bars.append(dict(t=et.strftime('%H:%M'), o=float(seg[0]), h=float(seg.max()),
                     l=float(seg.min()), c=float(seg[-1]),
                     u1=float(up1[il]) if np.isfinite(up1[il]) else None,
                     u2=float(up2[il]) if np.isfinite(up2[il]) else None,
                     gx=float(vah_gx[il]) if np.isfinite(vah_gx[il]) else None,
                     ny=float(vah_ny[il]) if np.isfinite(vah_ny[il]) else None))
tmap = {bb['t']: i for i, bb in enumerate(bars)}

W, H = 1020, 380
pad_l, pad_r, pad_t, pad_b = 4, 48, 10, 20
iw, ih = W - pad_l - pad_r, H - pad_t - pad_b
lows = [x['l'] for x in bars] + [x['u1'] for x in bars if x['u1']]
highs = [x['h'] for x in bars] + [x['u2'] for x in bars if x['u2']]
ymin, ymax = min(lows), max(highs)
sp = (ymax - ymin) or 1; ymin -= sp * .04; ymax += sp * .04
Y = lambda v: pad_t + ih * (ymax - v) / (ymax - ymin)
m = len(bars); bw = iw / m
X = lambda i: pad_l + bw * (i + .5)
el = []
top = [f'{X(i):.1f},{Y(x["u2"]):.1f}' for i, x in enumerate(bars) if x['u2']]
bot = [f'{X(i):.1f},{Y(x["u1"]):.1f}' for i, x in enumerate(bars) if x['u1']][::-1]
el.append(f'<polygon class="band" points="{" ".join(top+bot)}"/>')
for k, cls in (('u1', 'bl'), ('u2', 'bl')):
    pts = [f'{X(i):.1f},{Y(x[k]):.1f}' for i, x in enumerate(bars) if x[k]]
    el.append(f'<polyline class="{cls}" points="{" ".join(pts)}"/>')
for k, cls in (('gx', 'vahg'), ('ny', 'vahn')):
    pts = [f'{X(i):.1f},{Y(x[k]):.1f}' for i, x in enumerate(bars) if x[k]]
    el.append(f'<polyline class="{cls}" points="{" ".join(pts)}"/>')
# events
for _, e in evd.iterrows():
    if e.hm not in tmap:
        continue
    i = tmap[e.hm]
    broke = bool(e.broke_vah); rej = bool(e.retest_reject)
    col = 'var(--dn)' if (rej or e.fwd_60m < -80) else ('var(--up)' if e.fwd_60m > 80 else 'var(--muted)')
    el.append(f'<line x1="{X(i):.1f}" y1="{pad_t}" x2="{X(i):.1f}" y2="{pad_t+ih:.1f}" '
              f'stroke="{col}" stroke-width="1.3" stroke-dasharray="4 3"/>')
    lab = f'{e.src} {e.hm} {e.fwd_60m:+.0f}'
    el.append(f'<text x="{X(i)+3:.1f}" y="{pad_t+11}" font-size="9.5" font-weight="700" '
              f'fill="{col}">{lab}</text>')
cw = max(bw * .6, 1)
for i, x in enumerate(bars):
    cls = 'cu' if x['c'] >= x['o'] else 'cd'
    xx = X(i)
    el.append(f'<line class="{cls}" x1="{xx:.1f}" y1="{Y(x["h"]):.1f}" x2="{xx:.1f}" y2="{Y(x["l"]):.1f}"/>')
    yo, yc = Y(x['o']), Y(x['c'])
    el.append(f'<rect class="{cls}" x="{xx-cw/2:.1f}" y="{min(yo,yc):.1f}" width="{cw:.1f}" height="{max(abs(yc-yo),1):.1f}"/>')
for f in range(6):
    v = ymin + (ymax - ymin) * f / 5; y = Y(v)
    el.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+iw:.1f}" y2="{y:.1f}"/>')
    el.append(f'<text class="ax" x="{pad_l+iw+3:.1f}" y="{y+3:.1f}">{v:.0f}</text>')
for hm in ('09:30', '10:30', '11:30', '12:30', '13:30', '14:30', '15:30'):
    if hm in tmap:
        el.append(f'<text class="ax" x="{X(tmap[hm]):.1f}" y="{H-5}" text-anchor="middle">{hm}</text>')
svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'

block = f'''<h2 id="feb04">Case study: 2025-02-04 — a day that lands in both buckets</h2>
<p class="secnote">Five snap events in one session. The two morning snaps (NY 09:52,
Globex 10:33 — a violent 396t relocation) get broken and price runs higher: the rule.
Then at 11:41 the NY VAH snaps up, price fails the retest, and the day rolls over into a
midday downtrend — a genuine <b>retest-reject</b>, the user's hypothesis realized. Note it
fires at 11:41, just outside the afternoon window, so it is not in the significant lean
cohort; it is the ~3% textbook-resistance case that happens on any given day. Orange = Globex
VAH, teal = NY VAH; dashed markers colored by 60-min outcome (green up, red down).</p>
<div class="panel" style="grid-column:1/-1">{svg}</div>
<style>.vahg{{fill:none;stroke:var(--vah);stroke-width:1.8;}}
.vahn{{fill:none;stroke:#0e9b8a;stroke-width:1.6;opacity:.9;}}</style>'''

html = open('docs/research/vah-snap-examples.html').read()
# drop any prior injected block, then insert before the closing method note
marker = '<h2 id="feb04">'
if marker in html:
    html = html[:html.index(marker)] + html[html.index('<p class="secnote" style="margin-top:30px">'):]
anchor = '<p class="secnote" style="margin-top:30px">'
html = html.replace(anchor, f'<div class="panels">{block}</div>\n{anchor}', 1)
open('docs/research/vah-snap-examples.html', 'w').write(html)
print('injected Feb 4 panel; events:', len(evd), 'bars:', len(bars))
