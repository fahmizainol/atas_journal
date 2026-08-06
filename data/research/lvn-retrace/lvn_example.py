"""LVN-detector visual — one session, price path + volume profile with LVNs marked.

FIRST-PASS PROXY (see docs/research/lvn-retrace-continuation.md): the canonical
setup marks LVNs on the *impulse-leg* profile. This demo uses the *whole-session*
RTH profile instead — the cheapest possible check that a thin-bin detector finds
the gaps the eye sees. It is NOT causal-at-decision-time and NOT leg-anchored;
those come after we confirm the detector is sane. Globex (overnight) POC is drawn
as the prior-balance-POC *target* proxy.

Output: a self-contained HTML (hand-rolled SVG, matching the market-structure
render scripts) + a text summary to stdout.

Usage: .venv/bin/python data/research/lvn-retrace/lvn_example.py [YYYY-MM-DD]
"""
import sys
from datetime import date, datetime

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import profile as profmod

TICK = 0.25
DAY = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2025, 2, 12)
OUT = f'data/research/lvn-retrace/lvn_example_{DAY}.html'

# --- LVN detector (simple, documented) -------------------------------------
# Smooth the tick-grid histogram; call a level an HVN peak if it's a local max
# (over +-MIN_SEP ticks) carrying >= PEAK_FRAC of POC volume. Between two
# consecutive HVNs, the trough is an LVN if it drops to <= LVN_FRAC of the
# smaller flanking peak — a genuine gap between two distributions, not a tail.
SMOOTH_T = 5      # 1.25 pt moving average
MIN_SEP_T = 16    # 4 pt min peak separation
PEAK_FRAC = 0.30  # HVN must be >= 30% of POC
LVN_FRAC = 0.30   # trough must fall to <= 30% of the smaller flank


def detect_lvn(hist, poc_i):
    k = np.ones(SMOOTH_T) / SMOOTH_T
    sm = np.convolve(hist, k, mode='same')
    poc_v = sm[poc_i] if sm[poc_i] > 0 else sm.max()
    n = len(sm)
    thr = PEAK_FRAC * poc_v
    peaks, i = [], 0
    while i < n:
        lo, hi = max(0, i - MIN_SEP_T), min(n, i + MIN_SEP_T + 1)
        if sm[i] >= thr and sm[i] == sm[lo:hi].max():
            peaks.append(i)
            i += MIN_SEP_T   # skip the plateau to avoid duplicate peaks
        else:
            i += 1
    lvns = []
    for a, b in zip(peaks, peaks[1:]):
        t = a + int(sm[a:b + 1].argmin())
        flank = min(sm[a], sm[b])
        if flank > 0 and sm[t] <= LVN_FRAC * flank:
            lo, hi = t, t
            while lo > a and sm[lo - 1] <= LVN_FRAC * flank:
                lo -= 1
            while hi < b and sm[hi + 1] <= LVN_FRAC * flank:
                hi += 1
            lvns.append((lo, hi, t, float(sm[t] / poc_v)))
    return peaks, lvns, sm


# --- load + build profiles --------------------------------------------------
sym = tickmod.contract_for_cached('NQ', DAY)
rth = tickmod.cached_rth(sym, DAY)
ov = tickmod.cached_overnight(sym, DAY)
if rth is None or rth.empty:
    sys.exit(f'no RTH ticks for {DAY}')

price = rth['price'].to_numpy(dtype='float64')
size = rth['size'].to_numpy(dtype='float64')
lv = np.rint(price / TICK).astype('int64')
base = int(lv.min())
idx = lv - base
n_levels = int(lv.max()) - base + 1
hist = np.bincount(idx, weights=size, minlength=n_levels)
total = float(size.sum())
poc_i = int(hist.argmax())
lo_va, hi_va = profmod._value_area(hist, poc_i, total, 0.70)
poc = (base + poc_i) * TICK
vah = (base + hi_va) * TICK
val = (base + lo_va) * TICK

gx_poc = None
if ov is not None and not ov.empty:
    olv = np.rint(ov['price'].to_numpy() / TICK).astype('int64')
    ob = int(olv.min())
    oh = np.bincount(olv - ob, weights=ov['size'].to_numpy(dtype='float64'))
    gx_poc = (ob + int(oh.argmax())) * TICK

peaks, lvns, sm = detect_lvn(hist, poc_i)


def px(i):  # bin index -> price
    return (base + i) * TICK


# --- 1-min price line for the time panel ------------------------------------
ts = pd.to_datetime(rth['ts_utc'], utc=True).dt.tz_convert('America/New_York')
mdf = pd.DataFrame({'t': ts, 'p': price})
mins = mdf.set_index('t')['p'].resample('1min').last().dropna()
mt = [(x - mins.index[0]).total_seconds() / 60.0 for x in mins.index]  # minutes from open
mp = mins.to_numpy()

# --- text summary -----------------------------------------------------------
print(f'\n=== LVN detector — {DAY} ({sym}) — WHOLE-SESSION RTH profile ===')
print(f'open {price[0]:.2f}  close {price[-1]:.2f}  '
      f'net {(price[-1]-price[0])/TICK:+.0f}t  range {(price.max()-price.min())/TICK:.0f}t')
print(f'POC {poc:.2f}   VAH {vah:.2f}   VAL {val:.2f}   '
      f'globex-POC {gx_poc:.2f} (target proxy)' if gx_poc else f'POC {poc:.2f}')
print(f'HVN peaks: {len(peaks)}   LVNs found: {len(lvns)}')
print(f'{"LVN band (price)":>22}  {"width":>6}  {"depth vs POC":>12}')
for lo, hi, t, depth in lvns:
    print(f'{px(lo):8.2f} – {px(hi):<8.2f}  {(hi-lo)+1:4d}t  {depth*100:9.0f}%')

# --- render self-contained HTML (SVG) --------------------------------------
W, H = 940, 560
pad_t, pad_b = 30, 34
pad_l, pad_r = 56, 14
gap = 20
prof_w = 300                       # right panel width (profile)
xmid = W - pad_r - prof_w          # boundary price-panel | profile
price_w = xmid - pad_l - gap
p_hi = max(price.max(), (gx_poc or price.max()))
p_lo = min(price.min(), (gx_poc or price.min()))
pad_px = (p_hi - p_lo) * 0.03
p_hi += pad_px
p_lo -= pad_px


def Y(p):
    return pad_t + (p_hi - p) / (p_hi - p_lo) * (H - pad_t - pad_b)


def Xt(m):  # minutes -> x in price panel
    span = mt[-1] if mt[-1] else 1
    return pad_l + m / span * price_w


maxv = sm.max()
xaxis0 = W - pad_r


def Xv(v):  # volume -> x (bars grow leftward from right edge)
    return xaxis0 - (v / maxv) * prof_w


el = []
# LVN shaded bands across the whole figure
for lo, hi, t, depth in lvns:
    y0, y1 = Y(px(hi)), Y(px(lo))
    el.append(f'<rect class="lvn" x="{pad_l}" y="{y0:.1f}" '
              f'width="{xaxis0-pad_l:.1f}" height="{max(1.5,y1-y0):.1f}"/>')
# value area shade (profile side)
el.append(f'<rect class="va" x="{xmid:.1f}" y="{Y(vah):.1f}" '
          f'width="{xaxis0-xmid:.1f}" height="{Y(val)-Y(vah):.1f}"/>')
# profile silhouette (filled area, bars leftward)
pts = [f'{xaxis0:.1f},{Y(px(0)):.1f}']
for i in range(n_levels):
    pts.append(f'{Xv(sm[i]):.1f},{Y(px(i)):.1f}')
pts.append(f'{xaxis0:.1f},{Y(px(n_levels-1)):.1f}')
el.append(f'<polygon class="prof" points="{" ".join(pts)}"/>')
# reference lines: POC / VAH / VAL / globex-POC — across both panels
for lab, p, cls in [('POC', poc, 'poc'), ('VAH', vah, 'vah'),
                    ('VAL', val, 'val')] + ([('gxPOC', gx_poc, 'gx')] if gx_poc else []):
    y = Y(p)
    el.append(f'<line class="{cls}" x1="{pad_l}" y1="{y:.1f}" x2="{xaxis0}" y2="{y:.1f}"/>')
    el.append(f'<text class="rl {cls}" x="{pad_l-4}" y="{y+3:.1f}" text-anchor="end">{lab}</text>')
# price line
pl = " ".join(f'{Xt(m):.1f},{Y(p):.1f}' for m, p in zip(mt, mp))
el.append(f'<polyline class="price" points="{pl}"/>')
# LVN labels (right side)
for lo, hi, t, depth in lvns:
    el.append(f'<text class="lvnlab" x="{xaxis0-prof_w-2:.1f}" y="{Y(px(t))+3:.1f}" '
              f'text-anchor="end">LVN {depth*100:.0f}%</text>')
# axes: price ticks (y) + time labels (x)
for frac in range(0, 6):
    p = p_lo + (p_hi - p_lo) * frac / 5
    y = Y(p)
    el.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{xmid:.1f}" y2="{y:.1f}"/>')
    el.append(f'<text class="ax" x="{pad_l-4}" y="{y+3:.1f}" text-anchor="end" '
              f'opacity="0.55">{p:.0f}</text>')
for hm in ['09:30', '11:00', '12:30', '14:00', '15:30']:
    h, m = map(int, hm.split(':'))
    mm = (h * 60 + m) - (9 * 60 + 30)
    x = Xt(mm)
    if pad_l <= x <= xmid:
        el.append(f'<text class="ax" x="{x:.1f}" y="{H-pad_b+18:.1f}" '
                  f'text-anchor="middle" opacity="0.55">{hm}</text>')
el.append(f'<text class="ax" x="{pad_l:.1f}" y="{pad_t-12:.1f}">price / time</text>')
el.append(f'<text class="ax" x="{xmid+8:.1f}" y="{pad_t-12:.1f}">volume profile →</text>')

svg = f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'

rows = "".join(
    f'<tr><td>{px(lo):.2f} – {px(hi):.2f}</td><td>{(hi-lo)+1}t</td>'
    f'<td>{depth*100:.0f}%</td></tr>' for lo, hi, t, depth in lvns)

HTML = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LVN detector — {DAY}</title><style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd}}
@media(prefers-color-scheme:dark){{:root{{--bg:#14161a;--fg:#e6e6e6;--mut:#9aa;--line:#333}}}}
body{{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:24px;max-width:1000px}}
h1{{font-size:19px;margin:0 0 4px}} .sub{{color:var(--mut);font-size:13px;margin:0 0 16px}}
svg{{display:block;width:100%;height:auto;background:transparent;border:1px solid var(--line);border-radius:8px}}
svg text{{font-family:system-ui,sans-serif;font-size:11px;font-variant-numeric:tabular-nums;fill:var(--fg)}}
.grid{{stroke:var(--line);stroke-width:.5}}
.prof{{fill:#8892b0;opacity:.5;stroke:#8892b0;stroke-width:.6}}
.va{{fill:#3b82f6;opacity:.07}}
.lvn{{fill:#f59e0b;opacity:.16}}
.lvnlab{{fill:#b45309;font-size:10px;font-weight:600}}
@media(prefers-color-scheme:dark){{.lvnlab{{fill:#fbbf24}}}}
.price{{fill:none;stroke:#111;stroke-width:1.3;opacity:.85}}
@media(prefers-color-scheme:dark){{.price{{stroke:#f0f0f0}}}}
.poc{{stroke:#ef4444;stroke-width:1.3}} .rl.poc{{fill:#ef4444;font-weight:600}}
.vah,.val{{stroke:#3b82f6;stroke-width:1;stroke-dasharray:4 3;opacity:.8}} .rl.vah,.rl.val{{fill:#3b82f6}}
.gx{{stroke:#10b981;stroke-width:1.2;stroke-dasharray:2 2}} .rl.gx{{fill:#10b981;font-weight:600}}
table{{border-collapse:collapse;font-size:13px;margin-top:16px}}
td,th{{padding:4px 14px 4px 0;text-align:left;border-bottom:1px solid var(--line)}}
.note{{background:rgba(245,158,11,.1);border-left:3px solid #f59e0b;padding:10px 14px;border-radius:4px;font-size:13px;margin:16px 0;line-height:1.5}}
</style></head><body>
<h1>LVN detector — {DAY} ({sym})</h1>
<p class="sub">Whole-session RTH volume profile. net {(price[-1]-price[0])/TICK:+.0f}t &middot;
range {(price.max()-price.min())/TICK:.0f}t &middot; POC {poc:.2f} &middot; VAH {vah:.2f} &middot;
VAL {val:.2f}{f" &middot; globex-POC {gx_poc:.2f}" if gx_poc else ""}</p>
{svg}
<div class="note"><b>What to check:</b> do the amber LVN bands land on the visibly-thin
waists of the profile (the gaps between humps), and does the price line <i>cross them
fast</i> (little time spent inside)? Those are the retrace-continuation launchpads.
The green globex-POC is the prior-balance target proxy. <b>Proxy caveat:</b> this is the
whole-session profile (contaminated, non-causal, not leg-anchored) — a sanity check on
the detector, not the tradeable signal.</div>
<table><tr><th>LVN band (price)</th><th>width</th><th>depth vs POC</th></tr>{rows}</table>
</body></html>'''

import os
os.makedirs('data/research/lvn-retrace', exist_ok=True)
with open(OUT, 'w') as f:
    f.write(HTML)
print(f'\nwrote {OUT}')
