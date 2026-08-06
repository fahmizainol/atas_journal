"""Render annotated example charts for the VAH-snap study (docs/research HTML).

For a curated set of events from vah_snap_events.parquet, extract a window of
1-minute candles + the Globex dev1/dev2 bands + the developing VAH line (the
same causal readings the study used), mark the snap minute, and draw the
forward path so the outcome is visible. Grouped into:

  RULE  — snap gets broken, price continues up (what the stats say happens)
  LEAN  — afternoon violent NY-VAH snaps that did roll into a downtrend
  RESIST— textbook retest-reject (VAH literally held) — the rare 2-5%

Usage: .venv/bin/python data/research/market-structure/render_vah_snap_examples.py
Writes docs/research/vah-snap-examples.html
"""
import sys, json
from datetime import timedelta
sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import profile as profmod
from journal.sim import vwap as vwapmod

TICK = 0.25
TPB = 500
OUT = 'docs/research/vah-snap-examples.html'

# (session, src, snap_hm, group, caption)
EXAMPLES = [
    ('2025-04-07', 'gx', '09:54', 'rule',
     'Globex VAH leaps 180t up past price at 09:54; price is +13t above the new '
     'VAH within minutes and never looks back — VAH acted as a floor, not a ceiling.'),
    ('2025-08-22', 'ny', '09:48', 'rule',
     'NY VAH snaps 139t above; a brief hold, then straight through it. +1,273t at 60m.'),
    ('2025-05-13', 'ny', '09:33', 'rule',
     'Opening snap: NY VAH jumps above the just-broken-out price, price accepts and '
     'trends up all session (+797t/60m).'),
    ('2025-05-21', 'ny', '13:01', 'lean',
     'The hypothesis, realized: violent afternoon NY VAH snap (180t) at the highs, '
     'price rejects and rolls over hard (-1,105t/60m). This is the cohort that leans bearish.'),
    ('2026-04-01', 'ny', '13:35', 'lean',
     'Afternoon 284t NY VAH snap; price fails to reclaim and drifts down -655t/60m.'),
    ('2026-02-13', 'ny', '14:37', 'lean',
     'Late-day 247t snap lands 204t above price; slow bleed lower into the close.'),
    ('2026-01-02', 'gx', '10:04', 'resist',
     'Textbook retest-reject: price tags the snapped-up VAH, gets refused, and sells '
     'off -1,616t. This is the shape the eye remembers — but it is only 2-5% of events.'),
    ('2025-10-15', 'gx', '10:48', 'resist',
     'VAH snaps 187t above, price fails the retest and drops -613t/60m. Genuine '
     'resistance — just rare.'),
]

ev = pd.read_parquet('data/research/market-structure/vah_snap_events.parquet')


def window(session, src, snap_hm):
    day = pd.Timestamp(session).date()
    sym = tickmod.contract_for_cached('NQ', day)
    t = tickmod.get_day_ticks(sym, day, include_overnight=True)
    n = len(t)
    ts = t['ts_utc'].values.astype('datetime64[ns]')
    px = t['price'].to_numpy(dtype='float64')
    b = barmod.tick_bars(t, TPB)
    bands = vwapmod.vwap_bands(t)
    up1 = bands['upper1'].to_numpy()
    up2 = bands['upper2'].to_numpy()
    rth0_ts, rth1_ts = tickmod.session_bounds_utc(day)
    rth_i0 = int(t['ts_utc'].searchsorted(rth0_ts, side='left'))

    if src == 'gx':
        prof = profmod.developing_profile(t, b, TICK)
        vah = profmod.levels_in_force(prof, b, n, edge='vah')
    else:
        vah = np.full(n, np.nan)
        t_r = t.iloc[rth_i0:].reset_index(drop=True)
        b_r = barmod.tick_bars(t_r, TPB)
        prof = profmod.developing_profile(t_r, b_r, TICK)
        vah[rth_i0:] = profmod.levels_in_force(prof, b_r, n - rth_i0, edge='vah')

    # ET minute grid over the window [snap-45m, snap+90m]
    snap_utc = (pd.Timestamp(f'{session} {snap_hm}', tz='America/New_York')
                .tz_convert('UTC').tz_localize(None).to_datetime64())
    lo = snap_utc - np.timedelta64(45, 'm')
    hi = snap_utc + np.timedelta64(90, 'm')
    grid = pd.date_range(pd.Timestamp(lo).ceil('1min'),
                         pd.Timestamp(hi).floor('1min'), freq='1min').values.astype('datetime64[ns]')
    prev = np.searchsorted(ts, grid, side='left')
    bars = []
    for gi in range(1, len(grid)):
        a = int(prev[gi - 1]); z = int(prev[gi])
        if z <= a:
            continue
        seg = px[a:z]
        i_last = z - 1
        et = pd.Timestamp(grid[gi]).tz_localize('UTC').tz_convert('America/New_York')
        bars.append(dict(
            t=et.strftime('%H:%M'),
            o=float(seg[0]), h=float(seg.max()), l=float(seg.min()), c=float(seg[-1]),
            u1=float(up1[i_last]) if np.isfinite(up1[i_last]) else None,
            u2=float(up2[i_last]) if np.isfinite(up2[i_last]) else None,
            vah=float(vah[i_last]) if np.isfinite(vah[i_last]) else None,
            snap=bool(grid[gi - 1] <= snap_utc < grid[gi]),
        ))
    return bars


def svg(bars, W=470, H=250):
    pad_l, pad_r, pad_t, pad_b = 4, 46, 8, 18
    iw, ih = W - pad_l - pad_r, H - pad_t - pad_b
    lows = [b['l'] for b in bars] + [b['u1'] for b in bars if b['u1']] + \
           [b['vah'] for b in bars if b['vah']]
    highs = [b['h'] for b in bars] + [b['u2'] for b in bars if b['u2']] + \
            [b['vah'] for b in bars if b['vah']]
    ymin, ymax = min(lows), max(highs)
    pspan = (ymax - ymin) or 1
    ymin -= pspan * 0.04; ymax += pspan * 0.04
    def Y(v): return pad_t + ih * (ymax - v) / (ymax - ymin)
    m = len(bars)
    bw = iw / m
    def X(i): return pad_l + bw * (i + 0.5)

    el = []
    # band fill (dev1..dev2) as a polygon
    top = [f'{X(i):.1f},{Y(b["u2"]):.1f}' for i, b in enumerate(bars) if b['u2']]
    bot = [f'{X(i):.1f},{Y(b["u1"]):.1f}' for i, b in enumerate(bars) if b['u1']][::-1]
    if top and bot:
        el.append(f'<polygon class="band" points="{" ".join(top + bot)}"/>')
    for key, cls in (('u1', 'bl'), ('u2', 'bl'), ('vah', 'vah')):
        pts = [f'{X(i):.1f},{Y(b[key]):.1f}' for i, b in enumerate(bars) if b[key]]
        if pts:
            el.append(f'<polyline class="{cls}" points="{" ".join(pts)}"/>')
    # snap marker
    for i, b in enumerate(bars):
        if b['snap']:
            el.append(f'<line class="snap" x1="{X(i):.1f}" y1="{pad_t}" '
                      f'x2="{X(i):.1f}" y2="{pad_t+ih:.1f}"/>')
            el.append(f'<text class="snaptxt" x="{X(i)+3:.1f}" y="{pad_t+9}">snap</text>')
    # candles
    cw = max(bw * 0.6, 1.2)
    for i, b in enumerate(bars):
        up = b['c'] >= b['o']
        cls = 'cu' if up else 'cd'
        x = X(i)
        el.append(f'<line class="{cls}" x1="{x:.1f}" y1="{Y(b["h"]):.1f}" '
                  f'x2="{x:.1f}" y2="{Y(b["l"]):.1f}"/>')
        yo, yc = Y(b['o']), Y(b['c'])
        el.append(f'<rect class="{cls}" x="{x-cw/2:.1f}" y="{min(yo,yc):.1f}" '
                  f'width="{cw:.1f}" height="{max(abs(yc-yo),1):.1f}"/>')
    # price axis (5 ticks)
    for f in range(5):
        v = ymin + (ymax - ymin) * f / 4
        y = Y(v)
        el.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+iw:.1f}" y2="{y:.1f}"/>')
        el.append(f'<text class="ax" x="{pad_l+iw+3:.1f}" y="{y+3:.1f}">{v:.0f}</text>')
    # time axis: first, snap, last
    for i in (0, m - 1):
        el.append(f'<text class="ax" x="{X(i):.1f}" y="{H-5}" text-anchor="middle">{bars[i]["t"]}</text>')
    return f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'


groups = {'rule': [], 'lean': [], 'resist': []}
for session, src, hm, grp, cap in EXAMPLES:
    e = ev[(ev.session == session) & (ev.src == src) & (ev.hm == hm)]
    stat = e.iloc[0] if len(e) else None
    bars = window(session, src, hm)
    srcname = 'Globex VAH' if src == 'gx' else 'NY VAH'
    sub = (f'snap {stat.snap1_t:.0f}t · lands {stat.vah_above_t:.0f}t above · '
           f'fwd60m {stat.fwd_60m:+.0f}t · fwd_eod {stat.fwd_eod:+.0f}t · '
           f'broke {"yes" if stat.broke_vah else "no"}') if stat is not None else ''
    groups[grp].append(dict(session=session, src=srcname, hm=hm, cap=cap,
                            sub=sub, svg=svg(bars)))
    print('rendered', session, src, hm)

SEC = [
    ('rule', 'The rule: snap → broken → continuation',
     'The upward VAH relocation is volume being <em>accepted</em> at the highs. In '
     '85% of events price is back above the snapped VAH within the hour, and forward '
     'drift is positive. The VAH becomes a floor.'),
    ('lean', 'The lean: afternoon violent NY-VAH snaps',
     'The one cohort matching the hypothesis — NY VAH, ≥50t in 5 min, after 12:00 ET '
     '(n=81, −68t/60m, p=0.029). Real examples exist, but this is one significant cell '
     'out of ~30 cuts; treat as a low-prior lead, not a signal.'),
    ('resist', 'Textbook resistance — the rare 2–5%',
     'retest-reject: price tags the snapped VAH, is refused, and sells off without '
     'reclaiming. This is the pattern the eye remembers. It happens, but only 2–5% of '
     'the time — the base rate the anecdote forgets.'),
]

panels_html = []
for key, title, note in SEC:
    cards = []
    for p in groups[key]:
        cards.append(
            f'<div class="panel"><div class="phead"><div class="ptitle">{p["src"]} '
            f'<span class="sess">{p["session"]} · snap {p["hm"]} ET</span></div></div>'
            f'<div class="pstats">{p["sub"]}</div>{p["svg"]}'
            f'<div class="cap">{p["cap"]}</div></div>')
    panels_html.append(f'<h2>{title}</h2><p class="secnote">{note}</p>'
                       f'<div class="panels">{"".join(cards)}</div>')

HTML = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VAH snap examples</title>
<style>
:root{{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e9e8e2;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;
--band:rgba(42,120,214,.10);--bl:#2a78d6;--vah:#d98600;--snap:#9b59b6;}}
@media (prefers-color-scheme:dark){{:root{{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;
--ink2:#c3c2b7;--muted:#898781;--grid:#242422;--border:rgba(255,255,255,.10);
--up:#3987e5;--dn:#e66767;--band:rgba(57,135,229,.13);--bl:#4d97ea;--vah:#e0a020;--snap:#b07cd6;}}}}
:root[data-theme="dark"]{{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#242422;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;
--band:rgba(57,135,229,.13);--bl:#4d97ea;--vah:#e0a020;--snap:#b07cd6;}}
:root[data-theme="light"]{{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e9e8e2;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;
--band:rgba(42,120,214,.10);--bl:#2a78d6;--vah:#d98600;--snap:#9b59b6;}}
body{{background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
margin:0;padding:28px 20px 60px;line-height:1.45;}}
.wrap{{max-width:1060px;margin:0 auto;}}
h1{{font-size:22px;margin:0 0 4px;letter-spacing:-.01em;}}
.sub{{color:var(--ink2);font-size:13.5px;max-width:74ch;margin:0 0 8px;}}
h2{{font-size:15px;margin:34px 0 4px;text-transform:uppercase;letter-spacing:.06em;}}
.secnote{{color:var(--ink2);font-size:13px;max-width:80ch;margin:0 0 14px;}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--ink2);margin:12px 0 6px;}}
.legend .k{{display:inline-flex;align-items:center;gap:6px;}}
.swline{{width:16px;height:0;border-top:2px solid;display:inline-block;}}
.sw{{width:12px;height:12px;border-radius:3px;display:inline-block;}}
.panels{{display:grid;grid-template-columns:1fr 1fr;gap:16px;}}
@media(max-width:840px){{.panels{{grid-template-columns:1fr;}}}}
.panel{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 12px 10px;}}
.phead{{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:2px;}}
.ptitle{{font-size:13px;font-weight:600;}}
.ptitle .sess{{color:var(--ink2);font-weight:400;}}
.pstats{{font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums;margin-bottom:4px;}}
.cap{{font-size:12px;color:var(--ink2);margin-top:6px;}}
svg{{display:block;width:100%;height:auto;}}
svg text{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums;}}
.grid{{stroke:var(--grid);stroke-width:1;}}
.ax{{fill:var(--muted);font-size:9.5px;}}
.cu{{stroke:var(--up);fill:var(--up);}} .cd{{stroke:var(--dn);fill:var(--dn);}}
.band{{fill:var(--band);stroke:none;}}
.bl{{fill:none;stroke:var(--bl);stroke-width:1.1;opacity:.75;}}
.vah{{fill:none;stroke:var(--vah);stroke-width:1.8;}}
.snap{{stroke:var(--snap);stroke-width:1.3;stroke-dasharray:4 3;}}
.snaptxt{{fill:var(--snap);font-size:9.5px;font-weight:700;}}
</style></head><body><div class="wrap">
<h1>VAH snap in the upper band — worked examples</h1>
<p class="sub">Real NQ sessions selected by the study's own criteria: the developing VAH
relocates upward past price while price sits between the Globex dev1 and dev2 bands.
1-minute candles; blue = dev1/dev2 band, orange = the developing VAH, dashed purple =
the snap minute. Each panel's stats are the same causal readings used in the analysis.</p>
<div class="legend">
<span class="k"><span class="sw" style="background:var(--band);border:1px solid var(--bl)"></span> dev1–dev2 band</span>
<span class="k"><span class="swline" style="border-color:var(--vah)"></span> developing VAH</span>
<span class="k"><span class="swline" style="border-color:var(--snap);border-top-style:dashed"></span> snap minute</span>
<span class="k"><span class="sw" style="background:var(--up)"></span> up min</span>
<span class="k"><span class="sw" style="background:var(--dn)"></span> down min</span>
</div>
{"".join(panels_html)}
<p class="secnote" style="margin-top:30px">Full method &amp; stats:
<code>docs/research/vah-snap-resistance.md</code>. Extractors:
<code>data/research/market-structure/vah_snap_study.py</code>,
<code>render_vah_snap_examples.py</code>.</p>
</div></body></html>'''

open(OUT, 'w').write(HTML)
print('WROTE', OUT, len(HTML), 'bytes')
