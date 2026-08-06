"""Render worked-example charts for the confirmation test (confirm_test.py).

Featured rule: two_close (two consecutive 1-min closes back on the mid's side
of the touched ±1σ — the best-median, split-half-stable variant). Sections:

  WON     — confirmed, entry-price race won (earliest + latest, deterministic)
  LOST    — confirmed, race lost anyway (waiting didn't save it)
  ABORT   — 0.30σ break-away before confirmation (the dodged setups — note the
            abort IS the fade's stop-out, just taken before entry)
  COST    — the two confirmed events with the largest immediate-minus-confirmed
            edge deficit (explicitly selected to illustrate the give-up)

Each panel: candles [touch−45m, entry+75m], weekly bands (orange), purple
dashed = touch minute, green solid = confirmation entry, dotted gray = the
±0.30σ race thresholds AROUND THE ENTRY PRICE, red dashed = the abort level.

Usage: .venv/bin/python data/research/weekly-vwap-context/render_confirm_examples.py
Writes docs/research/weekly-vwap-confirm-examples.html
"""
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, 'data/research/weekly-vwap-context')
import numpy as np
import pandas as pd

from render_touch_examples import day_frame   # (bars, sampled weekly bands)

D = 'data/research/weekly-vwap-context'
OUT = 'docs/research/weekly-vwap-confirm-examples.html'
RULE = 'two_close'
RACE_SIG = 0.30


def load():
    c = pd.read_parquet(f'{D}/confirm_test.parquet')
    c = c[c.rule == RULE].copy()
    imm = (pd.read_parquet(f'{D}/confirm_test.parquet')
           .query('rule == "immediate"')
           .set_index(['day', 'bar', 'level'])[['edge']]
           .rename(columns={'edge': 'edge_imm'}))
    t = pd.read_parquet(f'{D}/touches.parquet')
    up = t.level == 'upper1'
    t['res_beyond_min'] = np.where(up, t.res_beyond_u1_min, t.res_beyond_l1_min)
    t['origin_deep'] = np.where(up, t.min_sig_before <= 0, t.max_sig_before >= 0)
    t = t.set_index(['day', 'bar', 'level'])[['ts_et', 'level_px', 'std',
                                              'approach', 'race_thr_pts',
                                              'res_beyond_min', 'origin_deep']]
    c = c.set_index(['day', 'bar', 'level']).join(t).join(imm).reset_index()
    return c


def obs_tag(ev):
    """Map the event back to the user's original hand observations (1-4).
    Fades split into obs 1 / 3a / 3b by context; bounces are obs 2a / 2b by
    acceptance (obs 4 is session-level, no touch panels)."""
    if ev.setup == 'bounce':
        return ('your obs 2a — pullback after acceptance' if ev.accepted
                else 'your obs 2b — pullback without acceptance')
    if ev.res_beyond_min >= 5:
        return 'your obs 1 — retest after failing out of the band'
    if ev.origin_deep:
        return 'your obs 3 — deep traverse from the mid/other band'
    return 'your obs 3 (loose) — fresh approach, shallow origin'


def trade_sgn(ev):
    """+1 = short at the level, −1 = long (fade rejects toward the mid,
    bounce joins the band holding — mirrored)."""
    fade_sgn = 1 if ev.level == 'upper1' else -1
    return fade_sgn if ev.setup == 'fade' else -fade_sgn


def svg_panel(ev, W=470, H=250):
    bars, bands = day_frame(ev.day)
    i = int(ev.bar)
    e = i + int(ev.wait) if ev.entered and np.isfinite(ev.wait) else None
    a = max(0, i - 45)
    z = min(len(bars) - 1, (e if e is not None else i + 30) + 75)
    sl = slice(a, z + 1)
    op = bars['open'].to_numpy()[sl]; hi = bars['high'].to_numpy()[sl]
    lo = bars['low'].to_numpy()[sl]; cl = bars['close'].to_numpy()[sl]
    ts = (bars['ts_utc'].dt.tz_convert('America/New_York')
          .dt.strftime('%H:%M').to_numpy()[sl])
    B = {k: bands[k][sl] for k in bands}
    ti = i - a
    sgn = trade_sgn(ev)
    lvl, std = float(ev.level_px), float(ev['std'])
    thr = RACE_SIG * std
    abort_lvl = lvl + sgn * thr

    ys = [lo.min(), hi.max(), abort_lvl]
    for k in ('mid', 'upper1', 'lower1', 'upper2', 'lower2'):
        v = B[k][np.isfinite(B[k])]
        if len(v):
            ys += [v.min(), v.max()]
    if e is not None:
        entry = float(bars['close'].to_numpy()[e])
        ys += [entry - thr, entry + thr]
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
        top = [f'{X(j):.1f},{Y(B[hi_k][j]):.1f}' for j in range(m)
               if np.isfinite(B[hi_k][j])]
        bot = [f'{X(j):.1f},{Y(B[lo_k][j]):.1f}' for j in range(m)
               if np.isfinite(B[lo_k][j])][::-1]
        if top and bot:
            el.append(f'<polygon class="band" points="{" ".join(top + bot)}"/>')
    for k, cls in (('mid', 'wkmid'), ('upper1', 'wk1'), ('lower1', 'wk1'),
                   ('upper2', 'wk2'), ('lower2', 'wk2')):
        pts = [f'{X(j):.1f},{Y(B[k][j]):.1f}' for j in range(m)
               if np.isfinite(B[k][j])]
        if pts:
            el.append(f'<polyline class="{cls}" points="{" ".join(pts)}"/>')

    # abort level over the 30-min confirmation window
    x0, x1 = X(ti), X(min(ti + 30, m - 1))
    el.append(f'<line class="abort" x1="{x0:.1f}" y1="{Y(abort_lvl):.1f}" '
              f'x2="{x1:.1f}" y2="{Y(abort_lvl):.1f}"/>')
    el.append(f'<text class="aborttxt" x="{x0+2:.1f}" '
              f'y="{Y(abort_lvl)-3:.1f}">abort</text>')
    el.append(f'<line class="mark" x1="{X(ti):.1f}" y1="{pad_t}" '
              f'x2="{X(ti):.1f}" y2="{pad_t+ih:.1f}"/>')
    el.append(f'<text class="marktxt" x="{X(ti)+3:.1f}" y="{pad_t+9}">touch</text>')
    if e is not None:
        ei = e - a
        el.append(f'<line class="entry" x1="{X(ei):.1f}" y1="{pad_t}" '
                  f'x2="{X(ei):.1f}" y2="{pad_t+ih:.1f}"/>')
        el.append(f'<text class="entrytxt" x="{X(ei)+3:.1f}" '
                  f'y="{pad_t+20}">confirm</text>')
        xr = X(min(ei + 60, m - 1))
        for v in (entry + thr, entry - thr):
            el.append(f'<line class="race" x1="{X(ei):.1f}" y1="{Y(v):.1f}" '
                      f'x2="{xr:.1f}" y2="{Y(v):.1f}"/>')

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
        el.append(f'<text class="ax" x="{pad_l+iw+3:.1f}" '
                  f'y="{Y(v)+3:.1f}">{v:.0f}</text>')
    for j in sorted({0, ti, m - 1}):
        el.append(f'<text class="ax" x="{X(j):.1f}" y="{H-5}" '
                  f'text-anchor="middle">{ts[j]}</text>')
    return f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'


def stats_line(ev):
    side = 'short' if trade_sgn(ev) > 0 else 'long'
    if ev.entered:
        return (f'{ev.level} · {ev.setup} {side} · '
                f'wait {int(ev.wait)}m · give-up {ev.give_up:+.2f}σ · '
                f'race {ev.race} · edge {ev.edge:+.2f}σ '
                f'(immediate {ev.edge_imm:+.2f}σ)')
    thru = 'through the level' if ev.setup == 'fade' else 'back toward the mid'
    return (f'{ev.level} · {ev.setup} setup aborted — 0.30σ break {thru} before '
            f'confirmation (immediate entry: {ev.edge_imm:+.2f}σ)')


def card(ev, cap):
    when = ev.ts_et[:16].replace('T', ' ')
    return (f'<div class="panel"><div class="phead"><div class="ptitle">'
            f'{ev.level} <span class="sess">{when} ET · touch</span></div></div>'
            f'<div class="pstats">{stats_line(ev)}</div>{svg_panel(ev)}'
            f'<div class="cap">{cap} · <i>{obs_tag(ev)}</i></div></div>')


def per_category(sub, which):
    """One deterministic example per observation category — obs 1 / 3a / 3b for
    fades, obs 2a / 2b for bounces: the earliest event of each for 'first', the
    latest for 'last' — so every category appears in every section without
    outcome-picking inside it."""
    sub = sub.copy()
    sub['cat'] = np.where(sub.setup == 'bounce',
                          np.where(sub.accepted, 'obs2a', 'obs2b'),
                          np.where(sub.res_beyond_min >= 5, 'obs1',
                                   np.where(sub.origin_deep, 'obs3a', 'obs3b')))
    out = []
    for cat in ('obs1', 'obs2a', 'obs2b', 'obs3a', 'obs3b'):
        g = sub[sub.cat == cat].sort_values(['day', 'bar'])
        if len(g):
            out.append(g.iloc[0] if which == 'first' else g.iloc[-1])
    return out


def main():
    c = load()
    fc, bc = c[c.setup == 'fade'], c[c.setup == 'bounce']
    ent, bent = fc[fc.entered], bc[bc.entered]
    sections = [
        ('Confirmed → won',
         'Two closes back through the level, entry at the second close, the '
         '±0.30σ race from the entry price resolves toward the mid. Note the '
         'give-up: the entry is already well off the level. One example per '
         'observation category (obs 1 / 3a deep / 3b shallow).',
         [card(e, 'race won from the confirmation close') for e in
          per_category(ent[ent.race == 'win'], 'first')]),
        ('Confirmed → lost anyway',
         'The confirmation fired and the fade still failed — waiting filters '
         'none of these, it only worsens the entry on the ones that work. '
         'One example per category.',
         [card(e, 'race lost despite confirmation') for e in
          per_category(ent[ent.race == 'lose'], 'last')]),
        ('Aborted — the break-away the rule dodges',
         'Price traded 0.30σ through the level before two closes could print. '
         'These immediate entries average −0.34σ — but dodging them is exactly '
         'what a 0.30σ stop on an immediate entry would have done, without '
         'paying the give-up on the winners. One example per category.',
         [card(e, 'no trade — abort level hit first') for e in
          per_category(fc[~fc.entered], 'first')]),
        ('Bounce (obs 2): confirmed → won',
         'The mirror trade — join the band holding from outside after two '
         'closes back away from the mid. The bounce is a 48.5% coin flip at '
         'every rule; these are what its winners look like. One example per '
         'category (obs 2a accepted / 2b brief).',
         [card(e, 'race won from the confirmation close') for e in
          per_category(bent[bent.race == 'win'], 'first')]),
        ('Bounce (obs 2): confirmed → lost anyway',
         'Confirmation fired, the band still failed as support/resistance.',
         [card(e, 'race lost despite confirmation') for e in
          per_category(bent[bent.race == 'lose'], 'last')]),
        ('Bounce (obs 2): aborted',
         'Price broke 0.30σ back through the level toward the mid before the '
         'bounce could confirm — the immediate bounce entries here average '
         '−0.38σ, but as with the fade, that protection is just a stop taken '
         'before entry.',
         [card(e, 'no trade — abort level hit first') for e in
          per_category(bc[~bc.entered], 'first')]),
        ('The cost of waiting',
         'The two confirmed events with the largest immediate-minus-confirmed '
         'edge deficit (explicitly selected to show the mechanism): by the '
         'time the second close prints, most of the rejection has been paid '
         'for. This give-up (−0.17σ mean, paired) is why confirmation nets '
         'out to nothing.',
         [card(e, f'immediate {e.edge_imm:+.2f}σ vs confirmed {e.edge:+.2f}σ')
          for _, e in (ent.assign(d=ent.edge_imm - ent.edge)
                       .sort_values('d', ascending=False).head(2).iterrows())]),
    ]

    body = []
    for title, note, cards in sections:
        body.append(f'<h2>{title}</h2><p class="secnote">{note}</p>'
                    f'<div class="panels">{"".join(cards)}</div>')
        print(title, '—', len(cards), 'panels')

    html = HTML_TMPL.replace('__BODY__', ''.join(body))
    open(OUT, 'w').write(html)
    print('WROTE', OUT, len(html), 'bytes')


HTML_TMPL = '''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly-band fade — confirmation examples</title>
<style>
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e9e8e2;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;
--band:rgba(224,140,32,.10);--wk:#d98600;--mark:#9b59b6;--race:#52514e;
--entry:#2e9e5b;--abort:#e34948;}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;
--ink2:#c3c2b7;--muted:#898781;--grid:#242422;--border:rgba(255,255,255,.10);
--up:#3987e5;--dn:#e66767;--band:rgba(224,160,32,.13);--wk:#e0a020;
--mark:#b07cd6;--race:#c3c2b7;--entry:#43b573;--abort:#e66767;}}
:root[data-theme="dark"]{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#242422;--border:rgba(255,255,255,.10);--up:#3987e5;--dn:#e66767;
--band:rgba(224,160,32,.13);--wk:#e0a020;--mark:#b07cd6;--race:#c3c2b7;
--entry:#43b573;--abort:#e66767;}
:root[data-theme="light"]{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e9e8e2;--border:rgba(11,11,11,.10);--up:#2a78d6;--dn:#e34948;
--band:rgba(224,140,32,.10);--wk:#d98600;--mark:#9b59b6;--race:#52514e;
--entry:#2e9e5b;--abort:#e34948;}
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
.wk2{fill:none;stroke:var(--wk);stroke-width:1;opacity:.45;stroke-dasharray:2 3;}
.race{stroke:var(--race);stroke-width:1;stroke-dasharray:2 3;opacity:.6;}
.mark{stroke:var(--mark);stroke-width:1.3;stroke-dasharray:4 3;}
.marktxt{fill:var(--mark);font-size:9.5px;font-weight:700;}
.entry{stroke:var(--entry);stroke-width:1.4;}
.entrytxt{fill:var(--entry);font-size:9.5px;font-weight:700;}
.abort{stroke:var(--abort);stroke-width:1.2;stroke-dasharray:5 3;}
.aborttxt{fill:var(--abort);font-size:9.5px;font-weight:700;}
</style></head><body><div class="wrap">
<h1>Weekly-band fade — wait-for-confirmation, worked examples</h1>
<p class="sub">Real events from <code>confirm_test.py</code>, featured rule
<b>two_close</b> (two consecutive 1-min closes back on the mid’s side of the
touched weekly &plusmn;1&sigma;; 30-min window; abort if price first trades 0.30&sigma;
through the level). Orange = weekly mid/&plusmn;1&sigma;/&plusmn;2&sigma;; purple dashed = touch;
green = confirmation entry; dotted gray = the &plusmn;0.30&sigma; race around the
<em>entry price</em>; red dashed = the abort level over the wait window.
Won/lost/abort samples are deterministic (earliest + latest); the
cost-of-waiting pair is explicitly value-selected to illustrate the give-up.</p>
<div class="legend">
<span class="k"><span class="swline" style="border-color:var(--wk)"></span> weekly mid / &plusmn;1&sigma;</span>
<span class="k"><span class="swline" style="border-color:var(--mark);border-top-style:dashed"></span> touch</span>
<span class="k"><span class="swline" style="border-color:var(--entry)"></span> confirmation entry</span>
<span class="k"><span class="swline" style="border-color:var(--race);border-top-style:dotted"></span> entry race &plusmn;0.30&sigma;</span>
<span class="k"><span class="swline" style="border-color:var(--abort);border-top-style:dashed"></span> abort level</span>
</div>
__BODY__
<p class="secnote" style="margin-top:30px">Stats &amp; method:
<code>docs/research/weekly-vwap-context.md</code> (confirmation section).
Script: <code>data/research/weekly-vwap-context/render_confirm_examples.py</code>.</p>
</div></body></html>'''


if __name__ == '__main__':
    main()
