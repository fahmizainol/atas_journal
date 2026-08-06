"""ONE-DAY demo: after price touches a developing VP level, how far does it go
and which way?

For every touch on a single session, this measures the 60-minute path from the
touch and draws it:

  * NET stem+arrow  from the level to where price sat 60 min later (green up /
    red down) -- the "which direction, how far net" answer
  * a faint whisker  spanning the full up/down excursion envelope over the 60
    min -- the biggest move each way regardless of where it closed

Same touch detection as the stable-level study (within 6t of the level, 30-min
dedup). This is descriptive, not a strategy: it just shows the shape of what
happens after a touch.

Usage: .venv/bin/python data/research/market-structure/stable_level_excursion_demo.py [YYYY-MM-DD]
Writes stable_level_excursion_<DAY>.html next to this file.
"""
import os
import sys
from datetime import date

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import profile as profmod

TICK = 0.25
TPB = 500
TOUCH_TOL = 6
AGE_TOL = 2
WINDOW_S = 3600          # 60-min excursion window
DEDUP_M = 30

DAY = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2025, 9, 10)
OUT = f'data/research/market-structure/stable_level_excursion_{DAY}.html'


def minute_grid(ts):
    lo = pd.Timestamp(ts[0]).ceil('1min')
    hi = pd.Timestamp(ts[-1]).floor('1min')
    grid = pd.date_range(lo, hi, freq='1min').values.astype('datetime64[ns]')
    idx = np.searchsorted(ts, grid, side='right') - 1
    keep = idx >= 0
    return idx[keep], grid[keep]


# --- load + developing levels (study logic) ---------------------------------
sym = tickmod.contract_for_cached('NQ', DAY)
if sym is None:
    sys.exit(f'no cached contract for {DAY}')
t = tickmod.get_day_ticks(sym, DAY, include_overnight=True)
if t is None or t.empty:
    sys.exit(f'no ticks for {DAY}')
n = len(t)
ts = t['ts_utc'].values.astype('datetime64[ns]')
px = t['price'].to_numpy(dtype='float64')
b = barmod.tick_bars(t, TPB)
rth0_ts, rth1_ts = tickmod.session_bounds_utc(DAY)
rth_i0 = int(t['ts_utc'].searchsorted(rth0_ts, side='left'))
rth0 = rth0_ts.tz_localize(None).to_datetime64()

prof_gx = profmod.developing_profile(t, b, TICK)
levels = {}
for edge in ('poc', 'vah', 'val'):
    levels[f'gx_{edge}'] = profmod.levels_in_force(prof_gx, b, n, edge=edge)
if rth_i0 < n - 10:
    t_r = t.iloc[rth_i0:].reset_index(drop=True)
    b_r = barmod.tick_bars(t_r, TPB)
    prof_ny = profmod.developing_profile(t_r, b_r, TICK)
    for edge in ('poc', 'vah', 'val'):
        arr = np.full(n, np.nan)
        arr[rth_i0:] = profmod.levels_in_force(prof_ny, b_r, n - rth_i0, edge=edge)
        levels[f'ny_{edge}'] = arr
else:
    for edge in ('poc', 'vah', 'val'):
        levels[f'ny_{edge}'] = np.full(n, np.nan)

gi, gts = minute_grid(ts)
pg = px[gi]
gts_et = pd.DatetimeIndex(gts).tz_localize('UTC').tz_convert('America/New_York')
mins = (gts - gts[0]) / np.timedelta64(60, 's')

# --- touches + 60-min excursion ---------------------------------------------
touches = []
level_tracks = {}
for lname, Larr in levels.items():
    Lg = Larr[gi]
    level_tracks[lname] = Lg
    dist = (pg - Lg) / TICK
    last_ev = None
    for k in range(6, len(gi)):
        if not (np.isfinite(Lg[k]) and np.isfinite(Lg[k - 1])):
            continue
        if abs(dist[k]) > TOUCH_TOL or abs(dist[k - 1]) <= TOUCH_TOL:
            continue
        if last_ev is not None and (gts[k] - last_ev) < np.timedelta64(DEDUP_M, 'm'):
            continue
        last_ev = gts[k]
        i = int(gi[k])
        lvl = float(Lg[k])

        ej = int(np.searchsorted(ts, ts[i] + np.timedelta64(WINDOW_S, 's'),
                                 side='right')) - 1
        ej = min(ej, n - 1)
        if ej <= i:
            continue
        win = px[i + 1:ej + 1]
        up_t = max(0.0, (win.max() - lvl) / TICK)      # furthest ABOVE the level
        dn_t = max(0.0, (lvl - win.min()) / TICK)      # furthest BELOW the level
        net_t = (px[ej] - lvl) / TICK                  # where it sat at +60m
        # minutes until the dominant extreme was reached
        ext_i = int(win.argmax() if up_t >= dn_t else win.argmin())
        t_ext = float((ts[i + 1 + ext_i] - ts[i]) / np.timedelta64(60, 's'))

        touches.append(dict(
            lname=lname, mins=float(mins[k]), lvl=lvl, net_px=float(px[ej]),
            hm=gts_et[k].strftime('%H:%M'),
            is_rth=bool(gts[k] >= rth0),
            up_t=up_t, dn_t=dn_t, net_t=net_t, t_ext=t_ext,
            direction='up' if net_t > 0 else 'down'))

up_moves = [x for x in touches if x['net_t'] > 0]
dn_moves = [x for x in touches if x['net_t'] < 0]
net_arr = np.array([x['net_t'] for x in touches])
up_arr = np.array([x['up_t'] for x in touches])
dn_arr = np.array([x['dn_t'] for x in touches])

print(f'=== excursion demo {DAY} ({sym}) — {len(touches)} touches ===')
print(f'net@60m direction: {len(up_moves)} up / {len(dn_moves)} down')
print(f'median net {np.median(net_arr):+.1f}t   '
      f'median max-up {np.median(up_arr):.1f}t   median max-down {np.median(dn_arr):.1f}t')

# --- render -----------------------------------------------------------------
W, H = 1120, 620
pad_t, pad_b, pad_l, pad_r = 26, 34, 54, 132
plot_w = W - pad_l - pad_r

allp = [pg[np.isfinite(pg)].min(), pg[np.isfinite(pg)].max()]
for tc in touches:                       # excursion endpoints must fit
    allp += [tc['lvl'] + tc['up_t'] * TICK, tc['lvl'] - tc['dn_t'] * TICK]
p_hi, p_lo = max(allp), min(allp)
pad = (p_hi - p_lo) * 0.04
p_hi += pad
p_lo -= pad


def Y(p):
    return pad_t + (p_hi - p) / (p_hi - p_lo) * (H - pad_t - pad_b)


def Xm(m):
    span = mins[-1] if mins[-1] else 1
    return pad_l + m / span * plot_w


EDGE_COL = {'poc': '#e11d48', 'vah': '#0ea5e9', 'val': '#22a06b'}
UP, DN = '#22c55e', '#ef4444'
el = []

# RTH boundary + overnight shade
x_rth = Xm(float((np.datetime64(rth0) - gts[0]) / np.timedelta64(60, 's')))
if pad_l < x_rth < W - pad_r:
    el.append(f'<rect class="onbg" x="{pad_l}" y="{pad_t}" width="{x_rth-pad_l:.1f}" height="{H-pad_t-pad_b}"/>')
    el.append(f'<line class="rthx" x1="{x_rth:.1f}" y1="{pad_t}" x2="{x_rth:.1f}" y2="{H-pad_b}"/>')
    el.append(f'<text class="ax" x="{x_rth+4:.1f}" y="{pad_t+11:.1f}">RTH open</text>')

# faint price + level tracks for context
pl = " ".join(f'{Xm(m):.1f},{Y(p):.1f}' for m, p in zip(mins, pg) if np.isfinite(p))
el.append(f'<polyline class="price" points="{pl}"/>')
for lname, Lg in level_tracks.items():
    src, edge = lname.split('_')
    dash = '' if src == 'gx' else ' stroke-dasharray="4 3"'
    pts = " ".join(f'{Xm(m):.1f},{Y(p):.1f}' for m, p in zip(mins, Lg) if np.isfinite(p))
    if pts:
        el.append(f'<polyline class="lvl" points="{pts}" stroke="{EDGE_COL[edge]}"{dash}/>')

# per-touch excursion glyph: faint full envelope whisker + bold net stem/arrow
for tc in touches:
    x = Xm(tc['mins'])
    yl = Y(tc['lvl'])
    y_up = Y(tc['lvl'] + tc['up_t'] * TICK)
    y_dn = Y(tc['lvl'] - tc['dn_t'] * TICK)
    col = UP if tc['direction'] == 'up' else DN
    # full up/down excursion envelope (faint)
    el.append(f'<line class="env" x1="{x:.1f}" y1="{y_up:.1f}" x2="{x:.1f}" y2="{y_dn:.1f}"/>')
    el.append(f'<line class="envcap" x1="{x-2.4:.1f}" y1="{y_up:.1f}" x2="{x+2.4:.1f}" y2="{y_up:.1f}"/>')
    el.append(f'<line class="envcap" x1="{x-2.4:.1f}" y1="{y_dn:.1f}" x2="{x+2.4:.1f}" y2="{y_dn:.1f}"/>')
    # net stem: level -> +60m price, arrowhead at the end
    yn = Y(tc['net_px'])
    el.append(f'<line x1="{x:.1f}" y1="{yl:.1f}" x2="{x:.1f}" y2="{yn:.1f}" stroke="{col}" stroke-width="2"/>')
    ay = yn + (4 if tc['direction'] == 'down' else -4)
    el.append(f'<path d="M{x:.1f},{yn:.1f} l-3.4,{4 if tc["direction"]=="up" else -4} '
              f'l6.8,0 z" fill="{col}"/>')
    # dot at the touch level
    el.append(f'<circle cx="{x:.1f}" cy="{yl:.1f}" r="1.8" fill="var(--fg)"/>')

# price axis
for frac in range(6):
    p = p_lo + (p_hi - p_lo) * frac / 5
    el.append(f'<line class="grid" x1="{pad_l}" y1="{Y(p):.1f}" x2="{W-pad_r}" y2="{Y(p):.1f}"/>')
    el.append(f'<text class="ax" x="{pad_l-4}" y="{Y(p)+3:.1f}" text-anchor="end" opacity="0.55">{p:.0f}</text>')
# time axis
seen_h = set()
for m, et in zip(mins, gts_et):
    if et.minute == 0 and et.hour not in seen_h:
        seen_h.add(et.hour)
        x = Xm(float(m))
        if pad_l <= x <= W - pad_r:
            el.append(f'<line class="grid" x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{H-pad_b}" opacity="0.3"/>')
            el.append(f'<text class="ax" x="{x:.1f}" y="{H-pad_b+18:.1f}" text-anchor="middle" opacity="0.55">{et.hour:02d}:00</text>')
# right-edge level labels (de-collided)
edge_labels = []
for lname, Lg in level_tracks.items():
    f = Lg[np.isfinite(Lg)]
    if f.size:
        edge_labels.append([Y(float(f[-1])), EDGE_COL[lname.split('_')[1]], lname])
edge_labels.sort()
for a in range(1, len(edge_labels)):
    if edge_labels[a][0] - edge_labels[a - 1][0] < 11:
        edge_labels[a][0] = edge_labels[a - 1][0] + 11
for yy, col, lname in edge_labels:
    el.append(f'<text class="llab" x="{W-pad_r+6:.1f}" y="{yy+3:.1f}" fill="{col}">{lname}</text>')

svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'

trows = "".join(
    f'<tr class="{tc["direction"]}"><td>{tc["hm"]}</td><td>{tc["lname"]}</td>'
    f'<td>{tc["lvl"]:.2f}</td><td>+{tc["up_t"]:.0f}t</td><td>-{tc["dn_t"]:.0f}t</td>'
    f'<td>{tc["net_t"]:+.0f}t</td><td>{"▲ up" if tc["direction"]=="up" else "▼ down"}</td>'
    f'<td>{tc["t_ext"]:.0f}m</td></tr>'
    for tc in sorted(touches, key=lambda z: z['mins']))

HTML = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>After a touch: how far, which way — {DAY}</title><style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd}}
@media(prefers-color-scheme:dark){{:root{{--bg:#14161a;--fg:#e6e6e6;--mut:#9aa;--line:#333}}}}
body{{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:24px;max-width:1180px}}
h1{{font-size:19px;margin:0 0 4px}} .sub{{color:var(--mut);font-size:13px;margin:0 0 14px;line-height:1.5}}
svg{{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:8px}}
svg text{{font-size:11px;font-variant-numeric:tabular-nums;fill:var(--fg)}}
.grid{{stroke:var(--line);stroke-width:.5}}
.onbg{{fill:rgba(120,130,150,.07)}} .rthx{{stroke:var(--mut);stroke-width:.8;stroke-dasharray:3 3;opacity:.6}}
.price{{fill:none;stroke:#111;stroke-width:1;opacity:.32}}
@media(prefers-color-scheme:dark){{.price{{stroke:#eee;opacity:.28}}}}
.lvl{{fill:none;stroke-width:1.2;opacity:.4}}
.env{{stroke:var(--mut);stroke-width:1;opacity:.28}} .envcap{{stroke:var(--mut);stroke-width:1;opacity:.28}}
.llab{{font-size:9.5px;font-weight:600}}
.leg{{display:flex;gap:20px;flex-wrap:wrap;font-size:12px;color:var(--mut);margin:10px 0 4px}} .leg b{{color:var(--fg)}}
table{{border-collapse:collapse;font-size:12.5px;margin-top:14px;width:100%}}
td,th{{padding:3px 12px 3px 0;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}
tr.up td{{color:#22c55e}} tr.down td{{color:#ef4444}}
.note{{background:rgba(120,130,150,.1);border-left:3px solid var(--mut);padding:10px 14px;border-radius:4px;font-size:13px;margin:14px 0;line-height:1.55}}
</style></head><body>
<h1>After a touch: how far did price go, and which way? — {DAY} ({sym})</h1>
<p class="sub">Every touch of a developing VP level, and its next-60-minute path.
The <b>bold stem+arrow</b> is the NET move (level &rarr; price 60 min later,
<span style="color:#22c55e">green up</span> / <span style="color:#ef4444">red down</span>);
the <b>faint whisker</b> is the full up/down excursion envelope reached inside that hour.</p>
<div class="leg">
  <span><b>▲/▼</b> net @ +60m (direction + distance)</span>
  <span>│ faint = max up / max down reached</span>
  <span><b style="color:#e11d48">━</b>POC <b style="color:#0ea5e9">━</b>VAH <b style="color:#22a06b">━</b>VAL &nbsp;(solid=Globex, dashed=NY)</span>
</div>
{svg}
<div class="note"><b>This session:</b> {len(touches)} touches &mdash;
<b style="color:#22c55e">{len(up_moves)} drifted up</b> / <b style="color:#ef4444">{len(dn_moves)} drifted down</b> by +60m.
Median net {np.median(net_arr):+.0f}t; typical envelope reached +{np.median(up_arr):.0f}t up and
&minus;{np.median(dn_arr):.0f}t down within the hour. The whiskers show the point: a touch is
rarely a clean bounce &mdash; price usually pokes a meaningful distance BOTH ways before it settles,
which is exactly why the level-as-a-wall (hold/break) signal washes out.</div>
<table><tr><th>time</th><th>level</th><th>px</th><th>max&nbsp;up</th><th>max&nbsp;down</th>
<th>net&nbsp;@60m</th><th>dir</th><th>t&rarr;ext</th></tr>
{trows}</table>
</body></html>'''

os.makedirs('data/research/market-structure', exist_ok=True)
with open(OUT, 'w') as f:
    f.write(HTML)
print(f'wrote {OUT}')
