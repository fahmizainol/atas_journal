"""Render annotated example charts for the weekly-band touch-context study.

Two kinds of panels, all real events from touches.parquet drawn with the same
causal weekly bands the extractor used (docs/research HTML, Lab Research tab):

  CITED   — the hand-observed Feb-2025 episodes that motivated the study,
            located in the event table (validation that the extractor sees them)
  COHORTS — per cohort, a deterministic stratified sample: earliest + latest
            decisive event of EACH outcome (toward-mid and away), so the doc
            shows representative reality, not outcome-selected exemplars.

Each panel: 1-min candles [touch−60m, touch+90m], weekly mid/±1σ/±2σ in the
weekly-orange family, outer bands filled, dashed marker at the touch minute,
dotted horizontal lines at the ±0.30σ race thresholds over the 60-min window.

Usage: .venv/bin/python data/research/weekly-vwap-context/render_touch_examples.py
Writes docs/research/weekly-vwap-context-examples.html
"""
import sys
sys.path.insert(0, 'src')
import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod
from journal.sim import vwap as vwapmod
from journal.sim import weekly as weeklymod
from journal.sim.regime import minute_bars

D = 'data/research/weekly-vwap-context'
OUT = 'docs/research/weekly-vwap-context-examples.html'
PRE, POST = 60, 90

CITED = [
    ('2025-02-07', '10:02', 'obs 1 · "10:06 poke ~75t through, still failed down"'),
    ('2025-02-19', '15:25', 'obs 1 · "15:33, ~10t shy of dev1, rejected"'),
    ('2025-02-10', '19:24', 'obs 2 · "09 Feb 19:24 bounce" (Sunday eve = 02-10 session)'),
    ('2025-02-06', '14:33', 'obs 2 · "14:32, MAE ~75t then bounced hard"'),
    ('2025-02-13', '09:38', 'obs 2 · "13 Feb 09:46 bounce after acceptance"'),
    ('2025-02-11', '10:49', 'obs 3 · "came from −dev1 traverse, kept rejecting"'),
]

COHORT_NOTE = {
    'retest_after_fail':
        'From the mid\'s side after ≥5m earlier residence beyond the band (the '
        '"failed out, retesting" shape). Next-bar race: 53.6% toward — no edge over '
        'a fresh touch.',
    'fresh_deep':
        'First visit, origin crossed the mid inside 120m — the full-envelope '
        'traverse. 54.3% toward next-bar; deep runs do not rip through, nor '
        'reliably reject.',
    'fresh_shallow':
        'First visit from a shallow origin (never left the band\'s half). 53.6% '
        'toward next-bar.',
    'pullback_accepted':
        'Pullback onto the band from outside after acceptance (≥15m beyond, or '
        '±2σ touched). 50.6% hold next-bar — a coin flip; acceptance has no '
        'dose-response.',
    'pullback_brief':
        'Pullback from outside without acceptance. Same coin flip (n is small).',
}


def load_events():
    t = pd.read_parquet(f'{D}/touches.parquet')
    # cited-episode lookup keeps first sessions (their weekly ≡ Globex line —
    # still drawable); the cohort samples stay seasoned-only like the analysis
    b = t[t.level.isin(['upper1', 'lower1'])].copy()
    up = b.level == 'upper1'
    b['from_mid_side'] = np.where(up, b.approach == 'below', b.approach == 'above')
    b['res_beyond_min'] = np.where(up, b.res_beyond_u1_min, b.res_beyond_l1_min)
    b['t2b'] = np.where(up, b.touched_u2_before, b.touched_l2_before)
    b['origin_deep'] = np.where(up, b.min_sig_before <= 0, b.max_sig_before >= 0)
    b['origin_sig'] = np.where(up, b.min_sig_before, b.max_sig_before)
    b['toward_ex'] = np.where(up, b.race60_ex == 'dn', b.race60_ex == 'up')
    b['away_ex'] = np.where(up, b.race60_ex == 'up', b.race60_ex == 'dn')
    b['edge60'] = np.where(up, b.dn_pts_60 - b.up_pts_60, b.up_pts_60 - b.dn_pts_60)
    s = b[~b.first_session]
    inside, outside = s[s.from_mid_side], s[~s.from_mid_side]
    acc = (outside.res_beyond_min >= 15) | outside.t2b
    cohorts = {
        'retest_after_fail': inside[inside.res_beyond_min >= 5],
        'fresh_deep': inside[(inside.res_beyond_min < 5) & inside.origin_deep],
        'fresh_shallow': inside[(inside.res_beyond_min < 5) & ~inside.origin_deep],
        'pullback_accepted': outside[acc],
        'pullback_brief': outside[~acc],
    }
    return b, cohorts


def pick(sub):
    """Earliest + latest decisive event of each outcome — deterministic, both
    outcomes shown, spread across the date range."""
    out = []
    for flag in ('toward_ex', 'away_ex'):
        d = sub[sub[flag]].sort_values(['day', 'bar'])
        if len(d):
            out.append(d.iloc[0])
            if len(d) > 1:
                out.append(d.iloc[-1])
    return out


_day_cache = {}


def day_frame(day_iso):
    """(bars, sampled weekly band arrays) for one session, extractor-identical."""
    if day_iso in _day_cache:
        return _day_cache[day_iso]
    day = pd.Timestamp(day_iso).date()
    contract = tickmod.contract_for_cached('NQ', day)
    on = tickmod.cached_overnight(contract, day)
    rth = tickmod.cached_rth(contract, day)
    seed = weeklymod.weekly_seed('NQ', day)
    full = pd.concat([on, rth], ignore_index=True)
    w = vwapmod.vwap_bands(full, seed=seed)
    bars = minute_bars(full)
    pos = bars['end_idx'].to_numpy()
    bands = {k: w[k].to_numpy()[pos]
             for k in ('mid', 'std', 'upper1', 'upper2', 'lower1', 'lower2')}
    _day_cache[day_iso] = (bars, bands)
    return bars, bands


def svg_panel(ev, W=470, H=250):
    bars, bands = day_frame(ev.day)
    i = int(ev.bar)
    a, z = max(0, i - PRE), min(len(bars) - 1, i + POST)
    sl = slice(a, z + 1)
    op = bars['open'].to_numpy()[sl]; hi = bars['high'].to_numpy()[sl]
    lo = bars['low'].to_numpy()[sl]; cl = bars['close'].to_numpy()[sl]
    ts = bars['ts_utc'].dt.tz_convert('America/New_York').dt.strftime('%H:%M').to_numpy()[sl]
    B = {k: bands[k][sl] for k in bands}
    ti = i - a                       # touch index inside the window
    lvl = float(ev.level_px); thr = float(ev.race_thr_pts)

    ys = [lo.min(), hi.max(), lvl - thr, lvl + thr]
    for k in ('mid', 'upper1', 'lower1', 'upper2', 'lower2'):
        v = B[k][np.isfinite(B[k])]
        if len(v):
            ys += [v.min(), v.max()]
    ymin, ymax = min(ys), max(ys)
    span = (ymax - ymin) or 1
    ymin -= span * .04; ymax += span * .04
    pad_l, pad_r, pad_t, pad_b = 4, 46, 8, 18
    iw, ih = W - pad_l - pad_r, H - pad_t - pad_b
    m = len(cl); bw = iw / m
    def Y(v): return pad_t + ih * (ymax - v) / (ymax - ymin)
    def X(j): return pad_l + bw * (j + 0.5)

    el = []
    for hi_k, lo_k in (('upper2', 'upper1'), ('lower1', 'lower2')):
        top = [f'{X(j):.1f},{Y(B[hi_k][j]):.1f}' for j in range(m) if np.isfinite(B[hi_k][j])]
        bot = [f'{X(j):.1f},{Y(B[lo_k][j]):.1f}' for j in range(m) if np.isfinite(B[lo_k][j])][::-1]
        if top and bot:
            el.append(f'<polygon class="band" points="{" ".join(top + bot)}"/>')
    for k, cls in (('mid', 'wkmid'), ('upper1', 'wk1'), ('lower1', 'wk1'),
                   ('upper2', 'wk2'), ('lower2', 'wk2')):
        pts = [f'{X(j):.1f},{Y(B[k][j]):.1f}' for j in range(m) if np.isfinite(B[k][j])]
        if pts:
            el.append(f'<polyline class="{cls}" points="{" ".join(pts)}"/>')
    # race thresholds over the 60-min outcome window
    x0, x1 = X(ti), X(min(ti + 60, m - 1))
    for v in (lvl + thr, lvl - thr):
        el.append(f'<line class="race" x1="{x0:.1f}" y1="{Y(v):.1f}" '
                  f'x2="{x1:.1f}" y2="{Y(v):.1f}"/>')
    el.append(f'<line class="mark" x1="{X(ti):.1f}" y1="{pad_t}" '
              f'x2="{X(ti):.1f}" y2="{pad_t+ih:.1f}"/>')
    el.append(f'<text class="marktxt" x="{X(ti)+3:.1f}" y="{pad_t+9}">touch</text>')
    cw = max(bw * 0.6, 1.2)
    for j in range(m):
        cls = 'cu' if cl[j] >= op[j] else 'cd'
        x = X(j)
        el.append(f'<line class="{cls}" x1="{x:.1f}" y1="{Y(hi[j]):.1f}" '
                  f'x2="{x:.1f}" y2="{Y(lo[j]):.1f}"/>')
        yo, yc = Y(op[j]), Y(cl[j])
        el.append(f'<rect class="{cls}" x="{x-cw/2:.1f}" y="{min(yo,yc):.1f}" '
                  f'width="{cw:.1f}" height="{max(abs(yc-yo),1):.1f}"/>')
    for f in range(5):
        v = ymin + (ymax - ymin) * f / 4
        el.append(f'<line class="grid" x1="{pad_l}" y1="{Y(v):.1f}" '
                  f'x2="{pad_l+iw:.1f}" y2="{Y(v):.1f}"/>')
        el.append(f'<text class="ax" x="{pad_l+iw+3:.1f}" y="{Y(v)+3:.1f}">{v:.0f}</text>')
    for j in (0, ti, m - 1):
        el.append(f'<text class="ax" x="{X(j):.1f}" y="{H-5}" '
                  f'text-anchor="middle">{ts[j]}</text>')
    return f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'


def stats_line(ev):
    org = f'{ev.origin_sig:+.2f}σ' if np.isfinite(ev.origin_sig) else 'n/a (open)'
    return (f'{ev.level} · from {ev.approach} · res {int(ev.res_beyond_min)}m · '
            f'origin {org} · race60 {ev.race60} / next-bar '
            f'{ev.race60_ex} · edge60 {ev.edge60:+.0f}pt')


def card(ev, cap):
    when = ev.ts_et[:16].replace('T', ' ')
    return (f'<div class="panel"><div class="phead"><div class="ptitle">'
            f'{ev.level} <span class="sess">{when} ET</span></div></div>'
            f'<div class="pstats">{stats_line(ev)}</div>{svg_panel(ev)}'
            f'<div class="cap">{cap}</div></div>')


def main():
    b, cohorts = load_events()
    sections = []

    cards = []
    for day, hm, cap in CITED:
        near = b[(b.day == day) & (b.level == 'upper1')]
        near = near[near.ts_et.str[11:16] == hm]
        if len(near) == 0:
            print('MISS cited', day, hm)
            continue
        cards.append(card(near.iloc[0], cap))
        print('cited', day, hm)
    sections.append(
        ('Your cited episodes, as the extractor saw them',
         'The hand-observed Feb-2025 events located in the event table — the '
         'extractor finds and classifies them as intended. The study\'s point is '
         'that scored across ALL 1,555 touches these shapes occur at ~50-54% '
         'base rates, not the rates the eye remembers.', cards))

    for name, sub in cohorts.items():
        cards = [card(ev,
                      ('resolved <b>toward the mid</b>' if ev.toward_ex else
                       'resolved <b>away from the mid</b>') + ' (next-bar race)')
                 for ev in pick(sub)]
        sections.append((f'{name} — {len(sub)} events',
                         COHORT_NOTE[name], cards))
        print('cohort', name, len(cards), 'panels')

    body = []
    for title, note, cards in sections:
        body.append(f'<h2>{title}</h2><p class="secnote">{note}</p>'
                    f'<div class="panels">{"".join(cards)}</div>')

    html = HTML_TMPL.replace('__BODY__', ''.join(body))
    open(OUT, 'w').write(html)
    print('WROTE', OUT, len(html), 'bytes')


HTML_TMPL = '''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly-band touch context — worked examples</title>
<style>
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e9e8e2;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;
--band:rgba(224,140,32,.10);--wk:#d98600;--wk2:#d98600;--mark:#9b59b6;--race:#52514e;}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;
--ink2:#c3c2b7;--muted:#898781;--grid:#242422;--border:rgba(255,255,255,.10);
--up:#3987e5;--dn:#e66767;--band:rgba(224,160,32,.13);--wk:#e0a020;--wk2:#e0a020;
--mark:#b07cd6;--race:#c3c2b7;}}
:root[data-theme="dark"]{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#242422;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;
--band:rgba(224,160,32,.13);--wk:#e0a020;--wk2:#e0a020;--mark:#b07cd6;--race:#c3c2b7;}
:root[data-theme="light"]{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e9e8e2;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;
--band:rgba(224,140,32,.10);--wk:#d98600;--wk2:#d98600;--mark:#9b59b6;--race:#52514e;}
body{background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
margin:0;padding:28px 20px 60px;line-height:1.45;}
.wrap{max-width:1060px;margin:0 auto;}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em;}
.sub{color:var(--ink2);font-size:13.5px;max-width:74ch;margin:0 0 8px;}
h2{font-size:15px;margin:34px 0 4px;text-transform:uppercase;letter-spacing:.06em;}
.secnote{color:var(--ink2);font-size:13px;max-width:80ch;margin:0 0 14px;}
.legend{display:flex;gap:18px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--ink2);margin:12px 0 6px;}
.legend .k{display:inline-flex;align-items:center;gap:6px;}
.swline{width:16px;height:0;border-top:2px solid;display:inline-block;}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block;}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
@media(max-width:840px){.panels{grid-template-columns:1fr;}}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 12px 10px;}
.phead{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:2px;}
.ptitle{font-size:13px;font-weight:600;}
.ptitle .sess{color:var(--ink2);font-weight:400;}
.pstats{font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums;margin-bottom:4px;}
.cap{font-size:12px;color:var(--ink2);margin-top:6px;}
svg{display:block;width:100%;height:auto;}
svg text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums;}
.grid{stroke:var(--grid);stroke-width:1;}
.ax{fill:var(--muted);font-size:9.5px;}
.cu{stroke:var(--up);fill:var(--up);} .cd{stroke:var(--dn);fill:var(--dn);}
.band{fill:var(--band);stroke:none;}
.wkmid{fill:none;stroke:var(--wk);stroke-width:1.8;}
.wk1{fill:none;stroke:var(--wk);stroke-width:1.2;opacity:.85;}
.wk2{fill:none;stroke:var(--wk2);stroke-width:1;opacity:.45;stroke-dasharray:2 3;}
.race{stroke:var(--race);stroke-width:1;stroke-dasharray:2 3;opacity:.6;}
.mark{stroke:var(--mark);stroke-width:1.3;stroke-dasharray:4 3;}
.marktxt{fill:var(--mark);font-size:9.5px;font-weight:700;}
</style></head><body><div class="wrap">
<h1>Weekly-band touches in context — worked examples</h1>
<p class="sub">Real NQ events from the touch-context study, drawn with the same causal
weekly bands the extractor scored. 1-minute candles; orange = weekly VWAP mid / &plusmn;1&sigma;
(solid) and &plusmn;2&sigma; (dashed), shaded = the outer bands, dashed purple = the touch
minute, dotted gray = the &plusmn;0.30&sigma; race thresholds over the 60-min outcome window.
Cohort samples are deterministic (earliest + latest of each outcome), not outcome-picked.</p>
<div class="legend">
<span class="k"><span class="swline" style="border-color:var(--wk)"></span> weekly mid / &plusmn;1&sigma;</span>
<span class="k"><span class="swline" style="border-color:var(--wk);border-top-style:dashed"></span> weekly &plusmn;2&sigma;</span>
<span class="k"><span class="sw" style="background:var(--band);border:1px solid var(--wk)"></span> outer band</span>
<span class="k"><span class="swline" style="border-color:var(--mark);border-top-style:dashed"></span> touch minute</span>
<span class="k"><span class="swline" style="border-color:var(--race);border-top-style:dotted"></span> race thresholds</span>
</div>
__BODY__
<p class="secnote" style="margin-top:30px">Full method &amp; stats:
<code>docs/research/weekly-vwap-context.md</code>. Data &amp; scripts:
<code>data/research/weekly-vwap-context/</code>.</p>
</div></body></html>'''


if __name__ == '__main__':
    main()
