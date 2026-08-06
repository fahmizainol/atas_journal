"""Render ONE session with the stable-level S/R study drawn on it.

The stable_level_study parquet keeps only the analysis columns (age, outcome,
level *name*) — not the level price track or timestamps — so it can't be
charted directly. This re-runs the extractor's exact touch/level logic for a
single day and lays the mechanics on a self-contained SVG:

  * price (minute-grid) line
  * the six DEVELOPING VP level tracks -- {Globex, NY} x {POC, VAH, VAL} --
    as stepped lines, so you can watch a level SIT STILL or RELOCATE
  * every touch marked: green O = held (level rejected price), red X = broke
    through, hollow grey = chop; a ring means the level had been stable >=60 min
    at the touch (the study's independent variable)

The whole point of the null: the rings (stable levels) are no greener than the
un-ringed (fresh) touches -- stability doesn't make a level hold.

Usage: .venv/bin/python data/research/market-structure/stable_level_chart.py [YYYY-MM-DD]
Writes stable_level_chart_<DAY>.html next to this file.
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
from journal.sim import vwap as vwapmod

# --- same constants as stable_level_study.py --------------------------------
TICK = 0.25
TPB = 500
TOUCH_TOL = 6
BREAK_B = 30
REJECT_R = 30
AGE_TOL = 2
WINDOW_S = 3600
DEDUP_M = 30
STABLE_MIN = 60          # ring a touch whose level had sat >= this many minutes

DAY = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2025, 11, 27)
OUT = f'data/research/market-structure/stable_level_chart_{DAY}.html'


def minute_grid(ts):
    lo = pd.Timestamp(ts[0]).ceil('1min')
    hi = pd.Timestamp(ts[-1]).floor('1min')
    if hi <= lo:
        return np.array([], 'int64'), np.array([], 'datetime64[ns]')
    grid = pd.date_range(lo, hi, freq='1min').values.astype('datetime64[ns]')
    idx = np.searchsorted(ts, grid, side='right') - 1
    keep = idx >= 0
    return idx[keep], grid[keep]


def first_to_hit(win, up_lvl, dn_lvl):
    up = np.nonzero(win >= up_lvl)[0]
    dn = np.nonzero(win <= dn_lvl)[0]
    iu = up[0] if len(up) else np.inf
    idn = dn[0] if len(dn) else np.inf
    if iu == np.inf and idn == np.inf:
        return None, -1
    return ('up', int(iu)) if iu < idn else ('dn', int(idn))


# --- load + rebuild the six developing levels (extractor logic) -------------
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
rth1_i = int(np.searchsorted(ts, rth1_ts.tz_localize(None).to_datetime64(),
                             side='right')) - 1

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
if len(gi) < 15:
    sys.exit(f'too few minutes for {DAY}')
pg = px[gi]

# ET wall-clock minutes for the x-axis
gts_et = pd.DatetimeIndex(gts).tz_localize('UTC').tz_convert('America/New_York')
mins = (gts - gts[0]) / np.timedelta64(60, 's')

# --- detect touches (extractor logic), keep the drawable pieces -------------
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
        adir = 1.0 if dist[k - 1] > 0 else -1.0

        j = k - 1
        lim = max(0, k - 180)
        while j >= lim and abs(Lg[j] - lvl) <= AGE_TOL * TICK:
            j -= 1
        age_min = float((gts[k] - gts[j + 1]) / np.timedelta64(60, 's'))

        ej = int(np.searchsorted(ts, ts[i] + np.timedelta64(WINDOW_S, 's'),
                                 side='right')) - 1
        ej = min(ej, n - 1)
        outcome = 'none'
        if ej > i:
            win = px[i + 1:ej + 1]
            if adir < 0:
                side, _ = first_to_hit(win, lvl + BREAK_B * TICK, lvl - REJECT_R * TICK)
                outcome = 'break' if side == 'up' else ('reject' if side == 'dn' else 'none')
            else:
                side, _ = first_to_hit(win, lvl + REJECT_R * TICK, lvl - BREAK_B * TICK)
                outcome = 'break' if side == 'dn' else ('reject' if side == 'up' else 'none')

        touches.append(dict(
            lname=lname, k=k, mins=float(mins[k]), lvl=lvl,
            hm=gts_et[k].strftime('%H:%M'), age_min=age_min,
            is_rth=bool(gts[k] >= rth0 and i <= rth1_i),
            test=('support' if adir > 0 else 'resistance'),
            approach=('above' if adir > 0 else 'below'),
            outcome=outcome))

held = sum(x['outcome'] == 'reject' for x in touches)
broke = sum(x['outcome'] == 'break' for x in touches)
chop = sum(x['outcome'] == 'none' for x in touches)
stable_n = sum(x['age_min'] >= STABLE_MIN for x in touches)
stable_held = sum(x['age_min'] >= STABLE_MIN and x['outcome'] == 'reject' for x in touches)
fresh_n = sum(x['age_min'] < STABLE_MIN for x in touches)
fresh_held = sum(x['age_min'] < STABLE_MIN and x['outcome'] == 'reject' for x in touches)

print(f'=== stable-level chart {DAY} ({sym}) ===')
print(f'{len(touches)} touches: {held} held / {broke} broke / {chop} chop')
print(f'stable(>={STABLE_MIN}m): {stable_held}/{stable_n} held   '
      f'fresh: {fresh_held}/{fresh_n} held')

# --- render -----------------------------------------------------------------
W, H = 1120, 620
pad_t, pad_b, pad_l, pad_r = 26, 34, 54, 132
plot_w = W - pad_l - pad_r

fin = pg[np.isfinite(pg)]
allp = [fin.min(), fin.max()]
for Lg in level_tracks.values():
    f = Lg[np.isfinite(Lg)]
    if f.size:
        allp += [f.min(), f.max()]
p_hi, p_lo = max(allp), min(allp)
pad = (p_hi - p_lo) * 0.04
p_hi += pad
p_lo -= pad


def Y(p):
    return pad_t + (p_hi - p) / (p_hi - p_lo) * (H - pad_t - pad_b)


def Xm(m):
    span = mins[-1] if mins[-1] else 1
    return pad_l + m / span * plot_w


# one colour per edge, solid=Globex dashed=NY
EDGE_COL = {'poc': '#e11d48', 'vah': '#0ea5e9', 'val': '#22a06b'}
el = []

# RTH open shading boundary
x_rth = Xm(float((np.datetime64(rth0) - gts[0]) / np.timedelta64(60, 's')))
if pad_l < x_rth < W - pad_r:
    el.append(f'<rect class="onbg" x="{pad_l}" y="{pad_t}" width="{x_rth-pad_l:.1f}" '
              f'height="{H-pad_t-pad_b}"/>')
    el.append(f'<line class="rthx" x1="{x_rth:.1f}" y1="{pad_t}" x2="{x_rth:.1f}" y2="{H-pad_b}"/>')
    el.append(f'<text class="ax" x="{x_rth+4:.1f}" y="{pad_t+11:.1f}">RTH open</text>')

# price line
pl = " ".join(f'{Xm(m):.1f},{Y(p):.1f}'
              for m, p in zip(mins, pg) if np.isfinite(p))
el.append(f'<polyline class="price" points="{pl}"/>')

# the six developing level tracks
for lname, Lg in level_tracks.items():
    src, edge = lname.split('_')
    col = EDGE_COL[edge]
    dash = '' if src == 'gx' else ' stroke-dasharray="4 3"'
    pts = " ".join(f'{Xm(m):.1f},{Y(p):.1f}'
                   for m, p in zip(mins, Lg) if np.isfinite(p))
    if pts:
        el.append(f'<polyline class="lvl" points="{pts}" stroke="{col}"{dash}/>')

# touch markers
for tc in touches:
    x, y = Xm(tc['mins']), Y(tc['lvl'])
    if tc['age_min'] >= STABLE_MIN:                     # stability ring
        el.append(f'<circle class="ring" cx="{x:.1f}" cy="{y:.1f}" r="7"/>')
    if tc['outcome'] == 'reject':
        el.append(f'<circle class="held" cx="{x:.1f}" cy="{y:.1f}" r="3.4"/>')
    elif tc['outcome'] == 'break':
        el.append(f'<path class="broke" d="M{x-3.2:.1f},{y-3.2:.1f} l6.4,6.4 '
                  f'M{x+3.2:.1f},{y-3.2:.1f} l-6.4,6.4"/>')
    else:
        el.append(f'<circle class="chop" cx="{x:.1f}" cy="{y:.1f}" r="3"/>')

# price grid + axis
for frac in range(6):
    p = p_lo + (p_hi - p_lo) * frac / 5
    el.append(f'<line class="grid" x1="{pad_l}" y1="{Y(p):.1f}" x2="{W-pad_r}" y2="{Y(p):.1f}"/>')
    el.append(f'<text class="ax" x="{pad_l-4}" y="{Y(p)+3:.1f}" text-anchor="end" opacity="0.55">{p:.0f}</text>')

# time axis: label each round ET hour
seen_h = set()
for m, et in zip(mins, gts_et):
    key = (et.hour, et.minute)
    if et.minute == 0 and et.hour not in seen_h:
        seen_h.add(et.hour)
        x = Xm(float(m))
        if pad_l <= x <= W - pad_r:
            el.append(f'<line class="grid" x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{H-pad_b}" opacity="0.35"/>')
            el.append(f'<text class="ax" x="{x:.1f}" y="{H-pad_b+18:.1f}" text-anchor="middle" opacity="0.55">{et.hour:02d}:00</text>')

# right-edge level legend, placed at each track's last value (de-collided)
edge_labels = []
for lname, Lg in level_tracks.items():
    f = Lg[np.isfinite(Lg)]
    if not f.size:
        continue
    edge_labels.append([Y(float(f[-1])), EDGE_COL[lname.split('_')[1]], lname])
edge_labels.sort()
for a in range(1, len(edge_labels)):        # push apart so none overlap
    if edge_labels[a][0] - edge_labels[a - 1][0] < 11:
        edge_labels[a][0] = edge_labels[a - 1][0] + 11
for yy, col, lname in edge_labels:
    el.append(f'<text class="llab" x="{W-pad_r+6:.1f}" y="{yy+3:.1f}" fill="{col}">{lname}</text>')

svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'

trows = "".join(
    f'<tr class="{tc["outcome"]}"><td>{tc["hm"]}</td><td>{tc["lname"]}</td>'
    f'<td>{tc["test"]}</td><td>{tc["lvl"]:.2f}</td><td>{tc["age_min"]:.0f}m'
    f'{" ●" if tc["age_min"]>=STABLE_MIN else ""}</td>'
    f'<td>{"HELD" if tc["outcome"]=="reject" else ("broke" if tc["outcome"]=="break" else "chop")}</td></tr>'
    for tc in sorted(touches, key=lambda z: z['mins']))

stable_pct = f'{100*stable_held/stable_n:.0f}%' if stable_n else '—'
fresh_pct = f'{100*fresh_held/fresh_n:.0f}%' if fresh_n else '—'

HTML = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Developing VP levels as S/R — {DAY}</title><style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd}}
@media(prefers-color-scheme:dark){{:root{{--bg:#14161a;--fg:#e6e6e6;--mut:#9aa;--line:#333}}}}
body{{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:24px;max-width:1180px}}
h1{{font-size:19px;margin:0 0 4px}} .sub{{color:var(--mut);font-size:13px;margin:0 0 14px;line-height:1.5}}
svg{{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:8px}}
svg text{{font-size:11px;font-variant-numeric:tabular-nums;fill:var(--fg)}}
.grid{{stroke:var(--line);stroke-width:.5}}
.onbg{{fill:rgba(120,130,150,.07)}} .rthx{{stroke:var(--mut);stroke-width:.8;stroke-dasharray:3 3;opacity:.6}}
.price{{fill:none;stroke:#111;stroke-width:1;opacity:.5}}
@media(prefers-color-scheme:dark){{.price{{stroke:#eee;opacity:.45}}}}
.lvl{{fill:none;stroke-width:1.4;opacity:.72}}
.llab{{font-size:9.5px;font-weight:600}}
.ring{{fill:none;stroke:var(--fg);stroke-width:1.1;opacity:.75}}
.held{{fill:#22c55e;stroke:var(--bg);stroke-width:.8}}
.broke{{stroke:#ef4444;stroke-width:1.8;fill:none}}
.chop{{fill:none;stroke:var(--mut);stroke-width:1;opacity:.7}}
.leg{{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--mut);margin:10px 0 4px;align-items:center}}
.leg b{{color:var(--fg)}}
table{{border-collapse:collapse;font-size:12.5px;margin-top:14px;width:100%}}
td,th{{padding:3px 12px 3px 0;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}
tr.reject td{{color:#22c55e}} tr.break td{{color:#ef4444}} tr.none td{{color:var(--mut)}}
.note{{background:rgba(120,130,150,.1);border-left:3px solid var(--mut);padding:10px 14px;border-radius:4px;font-size:13px;margin:14px 0;line-height:1.55}}
.cols{{display:flex;gap:28px;flex-wrap:wrap}} .cols>div{{flex:1;min-width:340px}}
</style></head><body>
<h1>Developing VP levels as support/resistance — {DAY} ({sym})</h1>
<p class="sub">The six developing volume-profile levels the study tests, drawn live:
<b>solid</b> = Globex-anchored, <b>dashed</b> = NY (RTH) session. Each touch (price
arriving within {TOUCH_TOL}t) is marked by first-to-hit outcome; a <b>ring</b> means the level
had been stable &ge;{STABLE_MIN} min — the study's independent variable.</p>
<div class="leg">
  <span><b style="color:#e11d48">━</b> POC</span>
  <span><b style="color:#0ea5e9">━</b> VAH</span>
  <span><b style="color:#22a06b">━</b> VAL</span>
  <span>&nbsp;</span>
  <span><span style="color:#22c55e">●</span> <b>held</b> (rejected {REJECT_R}t)</span>
  <span><span style="color:#ef4444">✕</span> <b>broke</b> (through {BREAK_B}t)</span>
  <span><span style="color:var(--mut)">○</span> chop</span>
  <span>◯ = stable &ge;{STABLE_MIN}m</span>
</div>
{svg}
<div class="note"><b>What to look for:</b> the ringed (stable) touches are no greener than
the un-ringed (fresh) ones — that's the null. This session: stable levels held
<b>{stable_pct}</b> ({stable_held}/{stable_n}), fresh levels held <b>{fresh_pct}</b> ({fresh_held}/{fresh_n}).
Overall {held} held / {broke} broke / {chop} chop. A developing level is a magnet price
trades through about as often as it bounces — sitting still doesn't turn it into a wall.
Shaded band = overnight (Globex) before the RTH open.</div>
<div class="cols"><div>
<table><tr><th>time</th><th>level</th><th>test</th><th>px</th><th>age</th><th>outcome</th></tr>
{trows}</table>
</div></div>
</body></html>'''

os.makedirs('data/research/market-structure', exist_ok=True)
with open(OUT, 'w') as f:
    f.write(HTML)
print(f'wrote {OUT}')
