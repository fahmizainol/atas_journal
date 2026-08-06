"""Causal, leg-anchored LVN detector — the honest version.

Upgrade of lvn_example.py per the user's chosen rung: instead of the whole-session
profile (only knowable at day's end), this reads structure *causally* and anchors
LVNs to the impulse leg:

  1. Confirmed swing pivots (zigzag on tick-bars, CONFIRM_PTS reversal). A pivot's
     bar is in the past, but it only becomes KNOWN at the later bar where price has
     reversed CONFIRM_PTS from it — that later bar is the pivot's "known-at" time.
  2. Impulse up-leg = a confirmed low->high, >= MIN_LEG_PTS, whose high sets a new
     high vs everything before the leg (out of balance / broke structure).
  3. LVN = volume profile of *just that leg's ticks*, computed at the high's
     known-at time. The thin band(s) the fast move skipped.
  4. Retrace = the first later bar whose low re-enters the LVN band -> the
     continuation-entry candidate. Because the LVN was frozen at (3), known-at
     ALWAYS precedes the retrace: no lookahead. The visual proves it per leg.

For long setups only (day-with, matching our bias). Output: self-contained HTML
(SVG) + text summary.

Usage: .venv/bin/python data/research/lvn-retrace/lvn_causal.py [YYYY-MM-DD]
"""
import os
import sys
from datetime import date

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod

TICK = 0.25
TPB = 500
DAY = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2025, 2, 12)
OUT = f'data/research/lvn-retrace/lvn_causal_{DAY}.html'

CONFIRM_PTS = 22.0   # reversal needed to confirm a swing pivot
MIN_LEG_PTS = 55.0   # a leg must span this to count as an impulse
LVN_FRAC = 0.35      # leg bins below this * leg-POC volume are "skipped" (LVN)
LEG_BUF_T = 8        # ignore this many ticks at each leg extreme (the pivots hold volume)


# --- causal zigzag ----------------------------------------------------------
def zigzag(hi, lo, confirm):
    """Confirmed alternating pivots. Returns list of (kind, bar_i, price, known_i)."""
    n = len(hi)
    piv = []
    mx_i, mx = 0, hi[0]
    mn_i, mn = 0, lo[0]
    d = 0  # 0 undecided, +1 tracking a high (came from low), -1 tracking a low
    for i in range(1, n):
        if hi[i] > mx:
            mx, mx_i = hi[i], i
        if lo[i] < mn:
            mn, mn_i = lo[i], i
        if d >= 0 and mx - lo[i] >= confirm:      # a high formed, price dropped -> confirm high
            piv.append(('H', mx_i, mx, i))
            d, mn, mn_i = -1, lo[i], i
        elif d <= 0 and hi[i] - mn >= confirm:     # a low formed, price rose -> confirm low
            piv.append(('L', mn_i, mn, i))
            d, mx, mx_i = 1, hi[i], i
    return piv


# --- leg profile + LVN ------------------------------------------------------
def leg_lvn(price, size, i0, i1):
    """LVN band(s) of the tick window [i0, i1]: contiguous prices the leg skipped."""
    lv = np.rint(price[i0:i1 + 1] / TICK).astype('int64')
    if lv.size == 0:
        return None, []
    base = int(lv.min())
    hist = np.bincount(lv - base, weights=size[i0:i1 + 1], minlength=int(lv.max()) - base + 1)
    n = len(hist)
    if n <= 2 * LEG_BUF_T + 2:
        return (base, hist), []
    poc_v = hist.max()
    med = np.median(hist[hist > 0]) if (hist > 0).any() else 0
    if poc_v < 2.0 * max(med, 1e-9):
        return (base, hist), []  # leg too uniform — no clear shelf to gap from
    thr = LVN_FRAC * poc_v
    thin = hist <= thr
    thin[:LEG_BUF_T] = False
    thin[n - LEG_BUF_T:] = False
    bands, j = [], LEG_BUF_T
    while j < n - LEG_BUF_T:
        if thin[j]:
            k = j
            while k + 1 < n - LEG_BUF_T and thin[k + 1]:
                k += 1
            width = k - j + 1
            if 2 <= width <= 0.45 * n:
                trough = int(hist[j:k + 1].argmin()) + j
                bands.append((base + j, base + k, base + trough, float(hist[trough] / poc_v)))
            j = k + 1
        else:
            j += 1
    return (base, hist), bands


# --- load -------------------------------------------------------------------
sym = tickmod.contract_for_cached('NQ', DAY)
rth = tickmod.cached_rth(sym, DAY)
ov = tickmod.cached_overnight(sym, DAY)
if rth is None or rth.empty:
    sys.exit(f'no RTH ticks for {DAY}')
price = rth['price'].to_numpy(dtype='float64')
size = rth['size'].to_numpy(dtype='float64')
bars = barmod.tick_bars(rth, TPB)
bh = bars['high'].to_numpy(); bl = bars['low'].to_numpy(); bc = bars['close'].to_numpy()
bsi = bars['start_idx'].to_numpy(); bei = bars['end_idx'].to_numpy()
et = pd.to_datetime(bars['ts_utc'], utc=True).dt.tz_convert('America/New_York')
mins = np.array([h * 60 + m + s / 60 - (9 * 60 + 30)
                 for h, m, s in zip(et.dt.hour, et.dt.minute, et.dt.second)])

onh = float(np.rint(ov['price'].max() / TICK) * TICK) if (ov is not None and not ov.empty) else np.nan
gx_poc = None
if ov is not None and not ov.empty:
    olv = np.rint(ov['price'].to_numpy() / TICK).astype('int64'); ob = int(olv.min())
    oh = np.bincount(olv - ob, weights=ov['size'].to_numpy(dtype='float64'))
    gx_poc = (ob + int(oh.argmax())) * TICK

piv = zigzag(bh, bl, CONFIRM_PTS)

# --- qualifying impulse up-legs --------------------------------------------
legs = []
for a in range(len(piv) - 1):
    k0, b0, p0, _ = piv[a]
    k1, b1, p1, known = piv[a + 1]
    if k0 == 'L' and k1 == 'H' and (p1 - p0) >= MIN_LEG_PTS:
        prior_hi = bh[:b0].max() if b0 > 0 else -np.inf   # new high vs everything before the leg?
        if p1 <= prior_hi:
            continue
        (lb, lh), bands = leg_lvn(price, size, bsi[b0], bei[b1])
        # a tradeable launchpad LVN sits well below the extension, so the retrace
        # is a real pullback — not the leg's final thrust hugging the high.
        bands = [z for z in bands if z[1] * TICK <= p1 - CONFIRM_PTS]
        if not bands:
            continue
        # retrace: first bar AFTER known whose low re-enters any LVN band
        entry = None
        band = max(bands, key=lambda z: (z[1] - z[0]))   # widest band as the representative LVN
        blo, bhi = band[0] * TICK, band[1] * TICK
        for j in range(known + 1, len(bars)):
            if bl[j] <= bhi and bh[j] >= blo:
                entry = j
                break
        legs.append(dict(b0=b0, p0=p0, b1=b1, p1=p1, known=known,
                         bands=bands, band=band, blo=blo, bhi=bhi, entry=entry))

# keep only non-overlapping DOMINANT legs (drop nested staircase micro-legs):
# greedily accept largest moves first, skipping any that overlaps one already kept.
_kept = []
for L in sorted(legs, key=lambda z: -(z['p1'] - z['p0'])):
    if all(L['b1'] < K['b0'] or L['b0'] > K['b1'] for K in _kept):
        _kept.append(L)
legs = sorted(_kept, key=lambda z: z['b0'])

# --- text summary -----------------------------------------------------------
print(f'\n=== CAUSAL leg-anchored LVN — {DAY} ({sym}) ===')
print(f'bars {len(bars)}  pivots {len(piv)}  qualifying impulse up-legs {len(legs)}')
print(f'ONH {onh:.2f}   globex-POC {gx_poc:.2f}   CONFIRM {CONFIRM_PTS}  MIN_LEG {MIN_LEG_PTS}\n')
for n, L in enumerate(legs, 1):
    kt = et.iloc[L["known"]].strftime("%H:%M")
    e = L['entry']
    et_s = et.iloc[e].strftime("%H:%M") if e is not None else '—'
    lag = f'{mins[e]-mins[L["known"]]:.0f}m after known' if e is not None else 'no retrace'
    print(f'leg {n}: {L["p0"]:.0f}->{L["p1"]:.0f} ({L["p1"]-L["p0"]:.0f}pt)  '
          f'LVN {L["blo"]:.2f}-{L["bhi"]:.2f}  known@{kt}  retrace@{et_s} ({lag})')

# --- render -----------------------------------------------------------------
W, H = 960, 560
pad_t, pad_b, pad_l, pad_r = 30, 34, 56, 16
gap = 18
prof_w = 210
xmid = W - pad_r - prof_w
price_w = xmid - pad_l - gap
allp = np.concatenate([bh, bl, [gx_poc or bh.max(), onh if onh == onh else bh.max()]])
p_hi, p_lo = allp.max(), allp.min()
pad = (p_hi - p_lo) * 0.03; p_hi += pad; p_lo -= pad


def Y(p):
    return pad_t + (p_hi - p) / (p_hi - p_lo) * (H - pad_t - pad_b)


def Xm(m):
    span = mins[-1] if mins[-1] else 1
    return pad_l + m / span * price_w


el = []
# reference lines
for lab, p, cls in ([('ONH', onh, 'onh')] if onh == onh else []) + \
                   ([('gxPOC', gx_poc, 'gx')] if gx_poc else []):
    el.append(f'<line class="{cls}" x1="{pad_l}" y1="{Y(p):.1f}" x2="{xmid:.1f}" y2="{Y(p):.1f}"/>')
    el.append(f'<text class="rl {cls}" x="{pad_l-4}" y="{Y(p)+3:.1f}" text-anchor="end">{lab}</text>')

# each leg: highlight L->H segment, LVN band (known-from -> retrace), markers
leg_colors = ['#f59e0b', '#d97706', '#b45309']
for n, L in enumerate(legs):
    c = leg_colors[n % len(leg_colors)]
    x0, y0 = Xm(mins[L['b0']]), Y(L['p0'])
    x1, y1 = Xm(mins[L['b1']]), Y(L['p1'])
    el.append(f'<line class="leg" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" stroke="{c}"/>')
    el.append(f'<circle class="piv lo" cx="{x0:.1f}" cy="{y0:.1f}" r="3"/>')
    el.append(f'<circle class="piv hi" cx="{x1:.1f}" cy="{y1:.1f}" r="3"/>')
    xk = Xm(mins[L['known']])
    xend = Xm(mins[L['entry']]) if L['entry'] is not None else xmid
    yb0, yb1 = Y(L['bhi']), Y(L['blo'])
    el.append(f'<rect class="lvn" x="{xk:.1f}" y="{yb0:.1f}" '
              f'width="{max(4,xend-xk):.1f}" height="{max(2,yb1-yb0):.1f}" fill="{c}"/>')
    el.append(f'<line class="known" x1="{xk:.1f}" y1="{yb0-6:.1f}" x2="{xk:.1f}" y2="{yb1+6:.1f}" stroke="{c}"/>')
    el.append(f'<text class="klab" x="{xk:.1f}" y="{yb0-9:.1f}" text-anchor="middle" fill="{c}">LVN known</text>')
    if L['entry'] is not None:
        xe, ye = Xm(mins[L['entry']]), Y(L['bhi'])
        el.append(f'<path class="entry" d="M{xe:.1f},{ye+11:.1f} l-5,9 l10,0 z" fill="{c}"/>')
        el.append(f'<text class="klab" x="{xe:.1f}" y="{ye+30:.1f}" text-anchor="middle" fill="{c}">retrace</text>')

# price line (bar closes)
pl = " ".join(f'{Xm(m):.1f},{Y(p):.1f}' for m, p in zip(mins, bc))
el.append(f'<polyline class="price" points="{pl}"/>')

# right panel: the FIRST leg's own profile, to show "profile the leg, not the session"
if legs:
    L0 = legs[0]
    (lb, lh) = leg_lvn(price, size, bsi[L0['b0']], bei[L0['b1']])[0]
    maxv = lh.max() or 1
    xaxis0 = W - pad_r
    el.append(f'<text class="ax" x="{xmid+6:.1f}" y="{pad_t-12:.1f}">leg #1 profile →</text>')
    pts = [f'{xaxis0:.1f},{Y((lb)*TICK):.1f}']
    for i in range(len(lh)):
        pp = (lb + i) * TICK
        if p_lo <= pp <= p_hi:
            pts.append(f'{xaxis0-(lh[i]/maxv)*prof_w:.1f},{Y(pp):.1f}')
    pts.append(f'{xaxis0:.1f},{Y((lb+len(lh)-1)*TICK):.1f}')
    el.append(f'<polygon class="prof" points="{" ".join(pts)}"/>')
    for (blo_i, bhi_i, tr, depth) in L0['bands']:
        yy0, yy1 = Y(bhi_i * TICK), Y(blo_i * TICK)
        el.append(f'<rect class="lvn" x="{xmid:.1f}" y="{yy0:.1f}" '
                  f'width="{xaxis0-xmid:.1f}" height="{max(2,yy1-yy0):.1f}" fill="#f59e0b"/>')

# axes
for frac in range(6):
    p = p_lo + (p_hi - p_lo) * frac / 5
    el.append(f'<line class="grid" x1="{pad_l}" y1="{Y(p):.1f}" x2="{xmid:.1f}" y2="{Y(p):.1f}"/>')
    el.append(f'<text class="ax" x="{pad_l-4}" y="{Y(p)+3:.1f}" text-anchor="end" opacity="0.55">{p:.0f}</text>')
for hm in ['09:30', '11:00', '12:30', '14:00', '15:30']:
    h, m = map(int, hm.split(':')); mm = h * 60 + m - (9 * 60 + 30); x = Xm(mm)
    if pad_l <= x <= xmid:
        el.append(f'<text class="ax" x="{x:.1f}" y="{H-pad_b+18:.1f}" text-anchor="middle" opacity="0.55">{hm}</text>')

svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'
lrows = "".join(
    f'<tr><td>{n}</td><td>{L["p0"]:.0f}→{L["p1"]:.0f} ({L["p1"]-L["p0"]:.0f}pt)</td>'
    f'<td>{L["blo"]:.2f}–{L["bhi"]:.2f}</td><td>{et.iloc[L["known"]].strftime("%H:%M")}</td>'
    f'<td>{et.iloc[L["entry"]].strftime("%H:%M") if L["entry"] is not None else "—"}</td>'
    f'<td>{f"+{mins[L["entry"]]-mins[L["known"]]:.0f}m" if L["entry"] is not None else "no retrace"}</td></tr>'
    for n, L in enumerate(legs, 1))

HTML = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Causal LVN — {DAY}</title><style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd}}
@media(prefers-color-scheme:dark){{:root{{--bg:#14161a;--fg:#e6e6e6;--mut:#9aa;--line:#333}}}}
body{{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:24px;max-width:1020px}}
h1{{font-size:19px;margin:0 0 4px}} .sub{{color:var(--mut);font-size:13px;margin:0 0 16px}}
svg{{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:8px}}
svg text{{font-size:11px;font-variant-numeric:tabular-nums;fill:var(--fg)}}
.grid{{stroke:var(--line);stroke-width:.5}}
.prof{{fill:#8892b0;opacity:.45;stroke:#8892b0;stroke-width:.6}}
.lvn{{opacity:.18}} .leg{{stroke-width:2.4;opacity:.9}}
.known{{stroke-width:1;stroke-dasharray:3 2;opacity:.8}} .klab{{font-size:9px;font-weight:600}}
.piv{{stroke:var(--bg);stroke-width:1}} .piv.lo{{fill:#10b981}} .piv.hi{{fill:#ef4444}}
.price{{fill:none;stroke:#111;stroke-width:1.2;opacity:.8}}
@media(prefers-color-scheme:dark){{.price{{stroke:#f0f0f0}}}}
.onh{{stroke:#a855f7;stroke-width:1;stroke-dasharray:5 3;opacity:.7}} .rl.onh{{fill:#a855f7}}
.gx{{stroke:#10b981;stroke-width:1.2;stroke-dasharray:2 2;opacity:.8}} .rl.gx{{fill:#10b981;font-weight:600}}
table{{border-collapse:collapse;font-size:13px;margin-top:16px}} td,th{{padding:4px 14px 4px 0;text-align:left;border-bottom:1px solid var(--line)}}
.note{{background:rgba(245,158,11,.1);border-left:3px solid #f59e0b;padding:10px 14px;border-radius:4px;font-size:13px;margin:16px 0;line-height:1.5}}
</style></head><body>
<h1>Causal, leg-anchored LVN — {DAY} ({sym})</h1>
<p class="sub">Confirmed swing pivots (CONFIRM {CONFIRM_PTS:.0f}pt) &middot; impulse legs &ge; {MIN_LEG_PTS:.0f}pt breaking to new highs &middot;
LVN = profile of the leg's own ticks, frozen when the high confirms. {len(legs)} qualifying long legs.</p>
{svg}
<div class="note"><b>The causal proof to check:</b> for every leg, the dashed <b>“LVN known”</b> marker
(where the amber band starts) sits <i>to the left of</i> the <b>“retrace”</b> arrow. The band exists before
price comes back to it — no lookahead. Green dot = confirmed swing low, red = swing high; the amber band on
the right is leg&nbsp;#1's own volume profile (thin = the LVN). Purple = overnight high (structure broken),
green dashes = globex-POC target proxy.</div>
<table><tr><th>leg</th><th>move</th><th>LVN band</th><th>known@</th><th>retrace@</th><th>lag</th></tr>{lrows}</table>
</body></html>'''

os.makedirs('data/research/lvn-retrace', exist_ok=True)
with open(OUT, 'w') as f:
    f.write(HTML)
print(f'\nwrote {OUT}')
