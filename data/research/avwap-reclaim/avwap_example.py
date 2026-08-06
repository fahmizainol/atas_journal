"""Anchored-VWAP reclaim — worked example (prior-day-low anchor).

MVP for playbook candidate #4 (docs/research/playbook-scouting-tradezella.md §4).
Two mechanics we don't have in the engine yet:
  1. an *event-anchored* VWAP — here anchored at the PRIOR RTH session's LOW
     (fully known at today's open, so no lookahead), accumulated across the
     overnight into today's RTH using the sim's own tick-by-tick vwap_bands;
  2. a *lose-then-reclaim* trigger — price closes below the anchored line, holds
     there, then closes back above it from below. Our entries are acceptance-
     pullback or breakout; we have no reclaim sequence.

This is a *visual sanity check* of the setup, not an outcomes test. It draws the
day's RTH candles, the prior-day-low anchored VWAP + its 1sigma band, and marks
every committed loss (red) and reclaim (green) crossing, with a light forward
readout so you can eyeball whether a reclaim precedes continuation or just chops.
The null-controlled forward-edge test comes next (cf. lvn_outcomes.py).

Deliberately price-only: no volume-spike / VP-value-area confirmation. Those lean
on order-flow / VP-geometry signals already dead on our NQ data; if the bare
geometric reclaim has no edge, they can't rescue it (same logic that killed #3).

Usage:
  .venv/bin/python data/research/avwap-reclaim/avwap_example.py [YYYY-MM-DD]
  .venv/bin/python data/research/avwap-reclaim/avwap_example.py scan [start] [end]
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import vwap as vwapmod

TICK = 0.25
TPB = 500          # ticks per bar, matching the engine default
BUFFER_T = 2       # a close must clear the line by >= this many ticks to count
MIN_HOLD = 3       # bars a side must hold before the opposite cross is "committed"
FWD_BARS = 15      # forward window (bars) for the eyeball MFE after a reclaim


# --- anchor + band construction --------------------------------------------

def prior_session(sym, day, back=7):
    """Most recent earlier cached RTH session on the SAME contract as `day`.

    Same-contract only: anchoring a VWAP across a roll would splice two price
    series. None if there's no same-contract session in the lookback (e.g. the
    first session after a roll) — the caller falls back to the globex open."""
    d = day - timedelta(days=1)
    for _ in range(back):
        if d.weekday() < 5 and tickmod.contract_for_cached('NQ', d) == sym:
            r = tickmod.cached_rth(sym, d)
            if r is not None and not r.empty:
                return d, r
        d -= timedelta(days=1)
    return None, None


def build(day):
    """Return everything the renderer/summary needs, or a str error."""
    sym = tickmod.contract_for_cached('NQ', day)
    if sym is None:
        return f'no contract mapped for {day}'
    rth = tickmod.cached_rth(sym, day)
    if rth is None or rth.empty:
        return f'no RTH ticks for {day}'
    ov = tickmod.cached_overnight(sym, day)
    ov = ov if (ov is not None and not ov.empty) else rth.iloc[:0]

    pday, prth = prior_session(sym, day)
    if prth is not None:
        plow_pos = int(prth['price'].to_numpy().argmin())
        ptail = prth.iloc[plow_pos:]
        anchor_px = float(prth['price'].iloc[plow_pos])
        anchor_ts = pd.Timestamp(prth['ts_utc'].iloc[plow_pos])
        anchor_lbl = f'prior-day low {anchor_px:.2f} @ {pday}'
    else:                      # roll boundary: fall back to globex 18:00 anchor
        ptail = rth.iloc[:0]
        anchor_px = float(ov['price'].iloc[0]) if len(ov) else float(rth['price'].iloc[0])
        anchor_ts = None
        anchor_lbl = 'globex 18:00 (no same-contract prior session)'

    # concat anchor-tail -> overnight -> today RTH, then one running VWAP
    frame = pd.concat([ptail, ov, rth], ignore_index=True)
    bands = vwapmod.vwap_bands(frame)
    off = len(ptail) + len(ov)           # index where today's RTH begins in `frame`

    bars = barmod.tick_bars(rth, TPB)
    if bars.empty:
        return f'too few RTH ticks for {day}'
    end_pos = off + bars['end_idx'].to_numpy()
    mid = bands['mid'].to_numpy()[end_pos]
    up1 = bands['upper1'].to_numpy()[end_pos]
    lo1 = bands['lower1'].to_numpy()[end_pos]
    close = bars['close'].to_numpy()
    high = bars['high'].to_numpy()
    low = bars['low'].to_numpy()

    et = pd.to_datetime(bars['ts_utc'], utc=True).dt.tz_convert('America/New_York')
    hm = [t.strftime('%H:%M') for t in et]

    # committed loss / reclaim detection (price-only, de-noised)
    buf = BUFFER_T * TICK
    events = []                # (bar_i, kind)  kind in {'reclaim','loss'}
    below = above = 0
    for i in range(len(bars)):
        if close[i] > mid[i] + buf:
            if below >= MIN_HOLD:
                events.append((i, 'reclaim'))
            above += 1; below = 0
        elif close[i] < mid[i] - buf:
            if above >= MIN_HOLD:
                events.append((i, 'loss'))
            below += 1; above = 0
        else:
            pass               # inside the buffer: neither side advances

    # forward eyeball readout per reclaim
    for k, (i, kind) in enumerate(events):
        if kind != 'reclaim':
            continue
        j = min(i + FWD_BARS, len(bars) - 1)
        mfe_t = (high[i:j + 1].max() - close[i]) / TICK
        mae_t = (close[i] - low[i:j + 1].min()) / TICK
        eod_t = (close[-1] - close[i]) / TICK
        events[k] = (i, kind, mfe_t, mae_t, eod_t)

    opens_beyond = bool(close[0] > up1[0] or close[0] < lo1[0])

    return dict(day=day, sym=sym, anchor_lbl=anchor_lbl, anchor_ts=anchor_ts,
                bars=bars, hm=hm, mid=mid, up1=up1, lo1=lo1,
                close=close, high=high, low=low, events=events,
                opens_beyond=opens_beyond,
                net_t=(close[-1] - close[0]) / TICK,
                range_t=(high.max() - low.min()) / TICK)


# --- scan mode: one line per day, to find illustrative sessions -------------

def scan(start, end):
    print(f'{"date":>12} {"sym":>5} {"net_t":>7} {"rng_t":>6} {"recl":>4} '
          f'{"loss":>4} {"mfe_med":>7} {"eod_med":>7}  open>band')
    for d in tickmod.session_dates(start, end):
        try:
            r = build(d)
        except Exception as e:               # keep the scan going
            print(f'{str(d):>12}  ERR {e}'); continue
        if isinstance(r, str):
            continue
        recl = [e for e in r['events'] if e[1] == 'reclaim']
        loss = [e for e in r['events'] if e[1] == 'loss']
        mfe = np.median([e[2] for e in recl]) if recl else float('nan')
        eod = np.median([e[4] for e in recl]) if recl else float('nan')
        print(f'{str(d):>12} {r["sym"]:>5} {r["net_t"]:+7.0f} {r["range_t"]:6.0f} '
              f'{len(recl):4d} {len(loss):4d} {mfe:7.0f} {eod:+7.0f}  '
              f'{"yes" if r["opens_beyond"] else ""}')


# --- render ------------------------------------------------------------------

def build_svg(r, W=940, H=380):
    bars, hm = r['bars'], r['hm']
    mid, up1, lo1 = r['mid'], r['up1'], r['lo1']
    o = bars['open'].to_numpy(); c = r['close']; h = r['high']; l = r['low']
    m = len(bars)

    pad_l, pad_r, pad_t, pad_b = 6, 52, 12, 22
    iw, ih = W - pad_l - pad_r, H - pad_t - pad_b
    ys = np.concatenate([h, l, up1, lo1])
    ymin, ymax = float(np.nanmin(ys)), float(np.nanmax(ys))
    span = (ymax - ymin) or 1
    ymin -= span * 0.04; ymax += span * 0.04

    def Y(v): return pad_t + ih * (ymax - v) / (ymax - ymin)
    bw = iw / m
    def X(i): return pad_l + bw * (i + 0.5)

    el = []
    # 1sigma band fill
    top = [f'{X(i):.1f},{Y(up1[i]):.1f}' for i in range(m) if np.isfinite(up1[i])]
    bot = [f'{X(i):.1f},{Y(lo1[i]):.1f}' for i in range(m) if np.isfinite(lo1[i])][::-1]
    if top and bot:
        el.append(f'<polygon class="band" points="{" ".join(top + bot)}"/>')
    # anchored VWAP mid + band edges
    for arr, cls in ((up1, 'bl'), (lo1, 'bl'), (mid, 'av')):
        pts = [f'{X(i):.1f},{Y(arr[i]):.1f}' for i in range(m) if np.isfinite(arr[i])]
        if pts:
            el.append(f'<polyline class="{cls}" points="{" ".join(pts)}"/>')
    # candles
    cw = max(bw * 0.62, 1.0)
    for i in range(m):
        cls = 'cu' if c[i] >= o[i] else 'cd'
        x = X(i)
        el.append(f'<line class="{cls}" x1="{x:.1f}" y1="{Y(h[i]):.1f}" '
                  f'x2="{x:.1f}" y2="{Y(l[i]):.1f}"/>')
        yo, yc = Y(o[i]), Y(c[i])
        el.append(f'<rect class="{cls}" x="{x-cw/2:.1f}" y="{min(yo,yc):.1f}" '
                  f'width="{cw:.1f}" height="{max(abs(yc-yo),1):.1f}"/>')
    # event markers: reclaim (up triangle, green) / loss (down triangle, red)
    for e in r['events']:
        i, kind = e[0], e[1]
        x = X(i)
        if kind == 'reclaim':
            y = Y(l[i]) + 12
            el.append(f'<path class="mr" d="M{x-5:.1f},{y:.1f} L{x+5:.1f},{y:.1f} '
                      f'L{x:.1f},{y-8:.1f} Z"/>')
            el.append(f'<text class="mrl" x="{x:.1f}" y="{y+10:.1f}" '
                      f'text-anchor="middle">R</text>')
        else:
            y = Y(h[i]) - 12
            el.append(f'<path class="ml" d="M{x-5:.1f},{y:.1f} L{x+5:.1f},{y:.1f} '
                      f'L{x:.1f},{y+8:.1f} Z"/>')
    # price axis
    for f in range(5):
        v = ymin + (ymax - ymin) * f / 4
        y = Y(v)
        el.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+iw:.1f}" y2="{y:.1f}"/>')
        el.append(f'<text class="ax" x="{pad_l+iw+4:.1f}" y="{y+3:.1f}">{v:.0f}</text>')
    # time axis
    for hh in ['09:30', '11:00', '12:30', '14:00', '15:30']:
        cand = [i for i in range(m) if hm[i] >= hh]
        if cand:
            i = cand[0]
            el.append(f'<text class="ax" x="{X(i):.1f}" y="{H-6}" '
                      f'text-anchor="middle">{hh}</text>')
    # anchored-VWAP label at right edge
    el.append(f'<text class="avl" x="{pad_l+iw+4:.1f}" y="{Y(mid[-1])+3:.1f}">aVWAP</text>')

    return f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'


# CSS shared by single + combined pages (house style, theme-aware)
STYLE = '''
:root{color-scheme:light dark;--bg:#fff;--surface:#fcfcfb;--fg:#1a1a1a;--mut:#666;--line:#e3e3df;
--up:#2a78d6;--dn:#e34948;--band:rgba(42,120,214,.09);--bl:#2a78d6;--av:#d98600;
--rc:#1f9d55;--ls:#e34948;--note:rgba(217,134,0,.10);--good:rgba(31,157,85,.10);--bad:rgba(227,73,72,.10)}
@media(prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surface:#1a1a19;--fg:#e6e6e6;--mut:#9aa;--line:#2a2d33;
--up:#3987e5;--dn:#e66767;--band:rgba(57,135,229,.12);--bl:#4d97ea;--av:#e0a020;
--rc:#3fbf77;--ls:#e66767;--note:rgba(224,160,32,.12);--good:rgba(63,191,119,.12);--bad:rgba(230,103,103,.12)}}
:root[data-theme="dark"]{--bg:#0d0d0d;--surface:#1a1a19;--fg:#e6e6e6;--mut:#9aa;--line:#2a2d33;
--up:#3987e5;--dn:#e66767;--band:rgba(57,135,229,.12);--bl:#4d97ea;--av:#e0a020;
--rc:#3fbf77;--ls:#e66767;--note:rgba(224,160,32,.12);--good:rgba(63,191,119,.12);--bad:rgba(230,103,103,.12)}
:root[data-theme="light"]{--bg:#fff;--surface:#fcfcfb;--fg:#1a1a1a;--mut:#666;--line:#e3e3df;
--up:#2a78d6;--dn:#e34948;--band:rgba(42,120,214,.09);--bl:#2a78d6;--av:#d98600;
--rc:#1f9d55;--ls:#e34948;--note:rgba(217,134,0,.10);--good:rgba(31,157,85,.10);--bad:rgba(227,73,72,.10)}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
margin:0;padding:28px 22px 60px;line-height:1.45}
.wrap{max-width:1020px;margin:0 auto}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:13.5px;max-width:78ch;margin:0 0 8px}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--mut);margin:12px 0 4px}
.legend .k{display:inline-flex;align-items:center;gap:6px}
.sw{width:14px;height:0;border-top:2px solid;display:inline-block}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:14px 15px 12px;margin:18px 0}
.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
padding:2px 8px;border-radius:4px;margin-bottom:8px}
.tag.good{background:var(--good);color:var(--rc)} .tag.bad{background:var(--bad);color:var(--ls)}
.ptitle{font-size:14.5px;font-weight:600;margin:0 0 2px}
.ptitle .sess{color:var(--mut);font-weight:400}
.anchor{color:var(--av);font-size:12px;font-weight:600;margin:0 0 10px}
.pstats{font-size:12px;color:var(--mut);font-variant-numeric:tabular-nums;margin:0 0 8px}
svg{display:block;width:100%;height:auto;background:transparent}
svg text{font-family:system-ui,sans-serif;font-size:11px;font-variant-numeric:tabular-nums;fill:var(--fg)}
.grid{stroke:var(--line);stroke-width:.6} .ax{fill:var(--mut);font-size:10px}
.cu{stroke:var(--up);fill:var(--up)} .cd{stroke:var(--dn);fill:var(--dn)}
.band{fill:var(--band);stroke:none}
.bl{fill:none;stroke:var(--bl);stroke-width:1;opacity:.55;stroke-dasharray:3 3}
.av{fill:none;stroke:var(--av);stroke-width:1.9} .avl{fill:var(--av);font-weight:700;font-size:10px}
.mr{fill:var(--rc)} .ml{fill:var(--ls)} .mrl{fill:var(--rc);font-size:9px;font-weight:700}
table{border-collapse:collapse;font-size:12.5px;margin-top:10px;font-variant-numeric:tabular-nums}
td,th{padding:3px 15px 3px 0;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600}
.cap{font-size:12.5px;color:var(--mut);margin-top:8px;line-height:1.5}
.note{background:var(--note);border-left:3px solid var(--av);padding:11px 15px;border-radius:5px;
font-size:13px;margin:22px 0 0;line-height:1.55}
'''

LEGEND = ('<div class="legend">'
          '<span class="k"><span class="sw" style="border-color:var(--av)"></span> prior-day-low aVWAP</span>'
          '<span class="k"><span class="sw" style="border-color:var(--bl);border-top-style:dashed"></span> &plusmn;1&sigma; band</span>'
          '<span class="k">&#9650; reclaim (close crosses above from below)</span>'
          '<span class="k">&#9660; loss (close crosses below from above)</span></div>')


def panel_html(r, tag=None, caption=''):
    recl = [e for e in r['events'] if e[1] == 'reclaim']
    loss = [e for e in r['events'] if e[1] == 'loss']
    rows = "".join(
        f'<tr><td>{r["hm"][e[0]]}</td><td>{r["close"][e[0]]:.2f}</td>'
        f'<td>{e[2]:+.0f}t</td><td>{e[3]:.0f}t</td><td>{e[4]:+.0f}t</td></tr>'
        for e in recl)
    tag_html = (f'<span class="tag {tag[0]}">{tag[1]}</span><br>' if tag else '')
    beyond = ('opens beyond 1&sigma; (source would skip)' if r['opens_beyond']
              else 'opens inside 1&sigma; (tradeable)')
    return f'''<div class="panel">{tag_html}
<div class="ptitle">{r['day']} <span class="sess">({r['sym']})</span></div>
<div class="anchor">Anchor: {r['anchor_lbl']}</div>
<div class="pstats">net {r['net_t']:+.0f}t &middot; range {r['range_t']:.0f}t &middot;
{len(recl)} reclaim / {len(loss)} loss &middot; {beyond}</div>
{build_svg(r)}
<table><tr><th>reclaim @</th><th>close</th><th>fwd MFE</th><th>fwd MAE</th><th>to close</th></tr>{rows}</table>
{f'<div class="cap">{caption}</div>' if caption else ''}</div>'''


def page(title, sub, body):
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>{STYLE}</style></head><body><div class="wrap">'
            f'<h1>{title}</h1><p class="sub">{sub}</p>{LEGEND}{body}</div></body></html>')


def fragment(title, sub, body):
    """Body-only variant (no doctype/head/body) for the Artifact host, which wraps
    the file in its own skeleton. Inline <style> is fine in body."""
    return (f'<title>{title}</title><style>{STYLE}</style><div class="wrap">'
            f'<h1>{title}</h1><p class="sub">{sub}</p>{LEGEND}{body}</div>')


def write(out, html):
    import os
    os.makedirs('data/research/avwap-reclaim', exist_ok=True)
    with open(out, 'w') as f:
        f.write(html)
    return out


def print_summary(r):
    recl = [e for e in r['events'] if e[1] == 'reclaim']
    loss = [e for e in r['events'] if e[1] == 'loss']
    print(f'\n=== aVWAP reclaim — {r["day"]} ({r["sym"]}) ===')
    print(f'anchor: {r["anchor_lbl"]}')
    print(f'net {r["net_t"]:+.0f}t   range {r["range_t"]:.0f}t   '
          f'opens_beyond_band={r["opens_beyond"]}')
    print(f'reclaims {len(recl)}   losses {len(loss)}')
    for e in recl:
        print(f'  reclaim @ {r["hm"][e[0]]}  close {r["close"][e[0]]:.2f}  '
              f'fwd MFE {e[2]:+.0f}t  MAE {e[3]:.0f}t  to-close {e[4]:+.0f}t')


# --- main -------------------------------------------------------------------

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'scan':
        s = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2025, 2, 3)
        e = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date(2025, 3, 31)
        scan(s, e)
    elif mode == 'combined':
        # curated worked examples: (date, tag, caption)
        CASES = [
            ('2025-03-31', ('good', 'rule → continuation'),
             'Trend day. Three reclaims of the prior-day-low aVWAP in the 11:00–12:15 '
             'window, each followed by clean continuation (+150–230t MFE, MAE ≤65t) into '
             'a +1,127t close. This is the shape the playbook sells.'),
            ('2025-03-11', ('bad', 'chop → whipsaw'),
             'Range day. The <i>same</i> reclaim trigger fires ten times — and every one '
             'closes red (net −132t). The line is crossed back and forth all session; the '
             'reclaim carries no information here. This is the failure the anecdote forgets.'),
        ]
        panels = []
        for day_s, tag, cap in CASES:
            r = build(date.fromisoformat(day_s))
            if isinstance(r, str):
                print('skip', day_s, r); continue
            print_summary(r)
            panels.append(panel_html(r, tag=tag, caption=cap))
        sub = ('One VWAP anchored at the <b>prior RTH session’s low</b> (known at the '
               'open — no lookahead), accumulated across the overnight into today’s '
               'RTH with the sim’s own tick-by-tick VWAP. A <b>reclaim</b> = a 500-tick '
               'bar closing back above the line from below after holding under it. '
               'Price-only, no volume/VP confirmation. Two real NQ sessions, same rule, '
               'opposite outcomes — the reason this needs a null-controlled test, not an anecdote.')
        note = ('<div class="note"><b>Resolved: NULL (2026-07-20).</b> The reclaim triggers on '
                'the trend day and the chop day identically — and the full-sample test bears '
                'that out. Across 239–360 days and ~2,900 reclaims (both prior-day-low and '
                'first-swing anchors) it <b>never beats a random long on the same day</b> on '
                'a risk-adjusted basis (dR −0.09 / −0.10), and its edge over a fake-reclaim '
                'null <b>flips sign with the anchor</b> (−13t vs +14t). All that survives is '
                'day-drift (up days win, down days lose, ≈ symmetric). A worked example can '
                'always find the winner; the null-controlled test — the one that killed '
                'candidate&nbsp;#3 — says do not build. Full numbers: '
                '<code>docs/research/anchored-vwap-reclaim.md</code>.</div>')
        title = 'Anchored-VWAP reclaim — worked NQ examples'
        body = "".join(panels) + note
        out = write('data/research/avwap-reclaim/avwap_reclaim_examples.html',
                    page(title, sub, body))
        frag = write('data/research/avwap-reclaim/avwap_reclaim_examples.artifact.html',
                     fragment(title, sub, body))
        print(f'\nwrote {out}\nwrote {frag} (artifact fragment)')
    else:
        day = date.fromisoformat(mode) if mode else date(2025, 3, 31)
        r = build(day)
        if isinstance(r, str):
            sys.exit(r)
        print_summary(r)
        sub = (f'RTH 500-tick candles, prior-day-low anchored VWAP + 1&sigma; band. '
               f'Green = reclaim, red = loss. Forward columns are an eyeball readout over '
               f'the next {FWD_BARS} bars, not a backtest.')
        out = write(f'data/research/avwap-reclaim/avwap_example_{day}.html',
                    page(f'Anchored-VWAP reclaim — {day} ({r["sym"]})', sub,
                         panel_html(r)))
        print(f'\nwrote {out}')
