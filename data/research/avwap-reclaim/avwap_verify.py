"""Anchored-VWAP reclaim — VISUAL VERIFICATION of the outcomes study.

Unlike avwap_example.py (a looser eyeball MVP), this renders EXACTLY what
avwap_outcomes.py measured, so a chart can be checked by eye against the verdict:

  * same anchors  — 'pdl' (prior RTH low, carried over the overnight) and
                    'swing' (first confirmed zigzag swing low);
  * same detector — build_bands / detect / forward are COPIED VERBATIM from
                    avwap_outcomes.py (warmup, dead-band, MIN_HOLD all identical),
                    so the reclaims drawn here are the reclaims the study counted;
  * same outcome  — each reclaim draws its entry, its structural stop, and its 2R
                    target, coloured by how the study's 2R:1R stop-first bracket
                    actually resolved (target = green, stop = red, EOD = grey).

The table under each chart prints the study's own per-reclaim R / hit2R, plus the
day's cross-null (raw pokes) and rand-null (random longs) counts, so the "reclaim
carries no information beyond drift" claim is legible on a single session.

Usage:
  .venv/bin/python data/research/avwap-reclaim/avwap_verify.py            # 3-panel page
  .venv/bin/python data/research/avwap-reclaim/avwap_verify.py DAY ANCHOR # single ad-hoc
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import vwap as vwapmod

# --- constants: identical to avwap_outcomes.py ------------------------------
TICK = 0.25
TPB = 500
BUFFER_T = 2
MIN_HOLD = 3
STOP_BUF_T = 2
MIN_RISK_PTS = 10.0
TARGET_MULT = 2.0
FWD_BARS = 15
NDRAW = 3
CONFIRM_PTS = 22.0
SEED = 20260720
WARMUP = 3

_rth_cache = {}


def rth_of(sym, day):
    k = (sym, day)
    if k not in _rth_cache:
        _rth_cache[k] = tickmod.cached_rth(sym, day)
    return _rth_cache[k]


def prior_session(sym, day, back=7):
    d = day - timedelta(days=1)
    for _ in range(back):
        if d.weekday() < 5 and tickmod.contract_for_cached('NQ', d) == sym:
            r = rth_of(sym, d)
            if r is not None and not r.empty:
                return d, r
        d -= timedelta(days=1)
    return None, None


def zigzag(hi, lo, confirm):
    n = len(hi); piv = []
    mx_i, mx, mn_i, mn, d = 0, hi[0], 0, lo[0], 0
    for i in range(1, n):
        if hi[i] > mx: mx, mx_i = hi[i], i
        if lo[i] < mn: mn, mn_i = lo[i], i
        if d >= 0 and mx - lo[i] >= confirm:
            piv.append(('H', mx_i, mx, i)); d, mn, mn_i = -1, lo[i], i
        elif d <= 0 and hi[i] - mn >= confirm:
            piv.append(('L', mn_i, mn, i)); d, mx, mx_i = 1, hi[i], i
    return piv


def build_bands(day, anchor):
    """VERBATIM logic from avwap_outcomes.build_bands, but `anchor` is a param and
    the return carries the extras the renderer needs (candles, +-1sigma, anchor mark)."""
    sym = tickmod.contract_for_cached('NQ', day)
    if not sym:
        return 'no contract mapped'
    rth = rth_of(sym, day)
    if rth is None or rth.empty:
        return 'no RTH ticks'
    bars = barmod.tick_bars(rth, TPB)
    if len(bars) < 20:
        return 'too few bars'
    bo = bars['open'].to_numpy()
    bh = bars['high'].to_numpy(); bl = bars['low'].to_numpy(); bc = bars['close'].to_numpy()
    bsi = bars['start_idx'].to_numpy(); bei = bars['end_idx'].to_numpy()
    et = pd.to_datetime(bars['ts_utc'], utc=True).dt.tz_convert('America/New_York')
    tod = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    hm = [t.strftime('%H:%M') for t in et]
    n = len(bars)
    mid = np.full(n, np.nan); up1 = np.full(n, np.nan); lo1 = np.full(n, np.nan)
    anchor_lbl = ''; anchor_px = None; anchor_bar = None; confirm_bar = None; ctx = None

    if anchor == 'pdl':
        pday, prth = prior_session(sym, day)
        if prth is None:
            return 'roll boundary (study skips this day)'
        plow_pos = int(prth['price'].to_numpy().argmin())
        anchor_px = float(prth['price'].iloc[plow_pos])
        ov = tickmod.cached_overnight(sym, day)
        ov = ov if (ov is not None and not ov.empty) else rth.iloc[:0]
        ptail = prth.iloc[plow_pos:]
        # the recovered 16:00-17:00 hour of the prior day — the piece that used to
        # be dropped between the prior RTH close and the 18:00 overnight open.
        post = tickmod.cached_post(sym, pday)
        post = post if (post is not None and not post.empty) else rth.iloc[:0]
        frame = pd.concat([ptail, post, ov, rth], ignore_index=True)
        b = vwapmod.vwap_bands(frame)
        fm = b['mid'].to_numpy(); fu = b['upper1'].to_numpy(); fl = b['lower1'].to_numpy()
        off = len(ptail) + len(post) + len(ov)
        mid = fm[off + bei]; up1 = fu[off + bei]; lo1 = fl[off + bei]
        valid_from = WARMUP
        gap = ' +post' if len(post) else ''
        anchor_lbl = f'prior-day low {anchor_px:.2f} @ {pday}{gap}'
        # context: prior RTH (from its low) + recovered post hour + overnight,
        # coarser bars so the ~24h carry fits; aVWAP sampled from the SAME per-tick
        # band, so it's exact. region tags the three zones for the chart backdrop.
        len_ptail = len(ptail); len_post = len(ptail) + len(post)
        ctx_ticks = pd.concat([ptail, post, ov], ignore_index=True)  # == frame[:off]
        ctx_tpb = max(TPB, int(np.ceil(len(ctx_ticks) / 110)))
        cb = barmod.tick_bars(ctx_ticks, ctx_tpb)
        ce = cb['end_idx'].to_numpy()
        cet = pd.to_datetime(cb['ts_utc'], utc=True).dt.tz_convert('America/New_York')
        ctx = dict(n=len(cb), bo=cb['open'].to_numpy(), bh=cb['high'].to_numpy(),
                   bl=cb['low'].to_numpy(), bc=cb['close'].to_numpy(),
                   mid=fm[ce], up1=fu[ce], lo1=fl[ce],
                   hm=[t.strftime('%m/%d %H:%M') for t in cet],
                   region=np.array(['prior' if e < len_ptail else
                                    ('post' if e < len_post else 'on') for e in ce]))
    else:  # swing
        piv = zigzag(bh, bl, CONFIRM_PTS)
        lows = [p for p in piv if p[0] == 'L']
        if not lows:
            return 'no confirmed swing low'
        _, pbar, plo, known = lows[0]
        atick = int(bsi[pbar])
        b = vwapmod.vwap_bands(rth.iloc[atick:])
        bm = b['mid'].to_numpy(); bu = b['upper1'].to_numpy(); bd = b['lower1'].to_numpy()
        m = len(bm)
        for j in range(n):
            pos = int(bei[j]) - atick
            if 0 <= pos < m:
                mid[j] = bm[pos]; up1[j] = bu[pos]; lo1[j] = bd[pos]
        valid_from = max(known + 1, WARMUP)
        anchor_px = float(plo); anchor_bar = int(pbar); confirm_bar = int(known)
        anchor_lbl = f'first swing low {plo:.2f} @ bar {pbar} (confirmed bar {known})'

    return dict(sym=sym, bo=bo, bh=bh, bl=bl, bc=bc, tod=tod, hm=hm,
                mid=mid, up1=up1, lo1=lo1, n=n, valid_from=valid_from,
                anchor=anchor, anchor_lbl=anchor_lbl, anchor_px=anchor_px,
                anchor_bar=anchor_bar, confirm_bar=confirm_bar, ctx=ctx)


def detect(mid, bc, valid_from):
    """VERBATIM from avwap_outcomes.detect."""
    buf = BUFFER_T * TICK
    reclaims, crosses = [], []
    below = above = 0
    for j in range(valid_from, len(bc)):
        if not np.isfinite(mid[j]):
            continue
        if j > 0 and np.isfinite(mid[j - 1]) and bc[j] > mid[j] and bc[j - 1] <= mid[j - 1]:
            crosses.append(j)
        if bc[j] > mid[j] + buf:
            if below >= MIN_HOLD:
                reclaims.append(j)
            above += 1; below = 0
        elif bc[j] < mid[j] - buf:
            below += 1; above = 0
    rec_set = set(reclaims)
    crosses = [c for c in crosses if c not in rec_set and (c - 1) not in rec_set
               and (c + 1) not in rec_set]
    return reclaims, crosses


def forward(bh, bl, bc, j):
    """VERBATIM from avwap_outcomes.forward (returns the study's R / hit2R)."""
    entry = bc[j]
    stop = bl[j] - STOP_BUF_T * TICK
    risk = max(entry - stop, MIN_RISK_PTS)
    stop = entry - risk
    tgt = entry + TARGET_MULT * risk
    seg_hi, seg_lo = bh[j:], bl[j:]
    mfe = (seg_hi.max() - entry) / TICK
    mae = (entry - seg_lo.min()) / TICK
    R, hit = None, 0
    for k in range(j, len(bh)):
        if bl[k] <= stop:
            R = -1.0; break
        if bh[k] >= tgt:
            R = TARGET_MULT; hit = 1; break
    if R is None:
        R = (bc[-1] - entry) / risk
    close_t = (bc[-1] - entry) / TICK
    kf = min(j + FWD_BARS, len(bc) - 1)
    fwd_t = (bc[kf] - entry) / TICK
    return mfe, mae, R, hit, close_t, fwd_t


def bracket(bh, bl, bc, j):
    """Same entry/stop/tgt as forward(); also returns the resolution bar + label
    so the chart can draw the bracket to where it actually resolved."""
    entry = bc[j]
    stop0 = bl[j] - STOP_BUF_T * TICK
    risk = max(entry - stop0, MIN_RISK_PTS)
    stop = entry - risk
    tgt = entry + TARGET_MULT * risk
    for k in range(j, len(bh)):
        if bl[k] <= stop:
            return entry, stop, tgt, k, 'stop'
        if bh[k] >= tgt:
            return entry, stop, tgt, k, 'target'
    return entry, stop, tgt, len(bh) - 1, 'eod'


# --- render -----------------------------------------------------------------

def build_svg(r, W=940, H=400):
    ctx = r.get('ctx')
    # today arrays (detection + brackets always run on these, exactly as the study)
    to, tc, th, tl = r['bo'], r['bc'], r['bh'], r['bl']
    reclaims, crosses = detect(r['mid'], tc, r['valid_from'])
    brs = [(j,) + bracket(th, tl, tc, j) for j in reclaims]

    if ctx:                                    # prepend prior-day context for pdl
        C = ctx['n']; W, H = 1180, 448
        o = np.concatenate([ctx['bo'], to]); c = np.concatenate([ctx['bc'], tc])
        h = np.concatenate([ctx['bh'], th]); l = np.concatenate([ctx['bl'], tl])
        mid = np.concatenate([ctx['mid'], r['mid']])
        up1 = np.concatenate([ctx['up1'], r['up1']]); lo1 = np.concatenate([ctx['lo1'], r['lo1']])
        reg = ctx['region']
        first_post = int(np.argmax(reg == 'post')) if (reg == 'post').any() else -1
        first_on = int(np.argmax(reg == 'on')) if (reg == 'on').any() else C
    else:
        C = 0
        o, c, h, l = to, tc, th, tl
        mid, up1, lo1 = r['mid'], r['up1'], r['lo1']
        first_on = 0
    N = len(c)
    def gi(j): return C + j                     # today bar -> global bar index

    pad_l, pad_r, pad_t, pad_b = 6, 54, 12, 22
    iw, ih = W - pad_l - pad_r, H - pad_t - pad_b
    stacks = [h, l, up1, lo1, mid]
    if r['anchor_px'] is not None:
        stacks.append(np.array([r['anchor_px']]))
    for _, e, s, t, _, _ in brs:
        stacks.append(np.array([s, t]))
    ys = np.concatenate([np.asarray(a, float) for a in stacks])
    ymin, ymax = float(np.nanmin(ys)), float(np.nanmax(ys))
    span = (ymax - ymin) or 1
    ymin -= span * 0.04; ymax += span * 0.04

    def Y(v): return pad_t + ih * (ymax - v) / (ymax - ymin)
    bw = iw / N
    def X(i): return pad_l + bw * (i + 0.5)

    el = []
    # region backgrounds + 09:30 divider (context view only)
    if ctx:
        yb0, yb1 = pad_t, pad_t + ih
        p0 = first_post if first_post >= 0 else first_on   # prior|post boundary
        segs = [('reg-prior', 0, p0), ('reg-post', p0, first_on),
                ('reg-on', first_on, C), ('reg-day', C, N)]
        for cls, a, b in segs:
            if b > a:
                x0 = pad_l + bw * a; w = bw * (b - a)
                el.append(f'<rect class="{cls}" x="{x0:.1f}" y="{yb0}" width="{w:.1f}" height="{ih:.1f}"/>')
        for lbl, a, b in (('prior RTH (from low)', 0, p0),
                          ('recovered 16-17h', p0, first_on),
                          ('overnight', first_on, C), ('today RTH', C, N)):
            if b > a:
                el.append(f'<text class="reglab" x="{X((a+b)//2):.1f}" y="{pad_t+11:.0f}" '
                          f'text-anchor="middle">{lbl}</text>')
        xd = pad_l + bw * C
        el.append(f'<line class="divi" x1="{xd:.1f}" y1="{yb0}" x2="{xd:.1f}" y2="{yb1:.1f}"/>')
        el.append(f'<text class="ax divit" x="{xd+3:.1f}" y="{yb1-4:.0f}">09:30 open</text>')
    # +-1sigma band fill
    top = [f'{X(i):.1f},{Y(up1[i]):.1f}' for i in range(N) if np.isfinite(up1[i])]
    bot = [f'{X(i):.1f},{Y(lo1[i]):.1f}' for i in range(N) if np.isfinite(lo1[i])][::-1]
    if top and bot:
        el.append(f'<polygon class="band" points="{" ".join(top + bot)}"/>')
    for arr, cls in ((up1, 'bl'), (lo1, 'bl'), (mid, 'av')):
        pts = [f'{X(i):.1f},{Y(arr[i]):.1f}' for i in range(N) if np.isfinite(arr[i])]
        if pts:
            el.append(f'<polyline class="{cls}" points="{" ".join(pts)}"/>')
    # anchor price line
    if r['anchor_px'] is not None:
        ya = Y(r['anchor_px'])
        el.append(f'<line class="anc" x1="{pad_l}" y1="{ya:.1f}" x2="{pad_l+iw:.1f}" y2="{ya:.1f}"/>')
    # anchor marker: prior-day low at the left edge (pdl), or swing-low star on-chart
    if ctx:
        xa, ya = X(0), Y(r['anchor_px'])
        el.append(f'<path class="astar" d="M{xa:.1f},{ya-6:.1f} L{xa+6:.1f},{ya:.1f} '
                  f'L{xa:.1f},{ya+6:.1f} L{xa-6:.1f},{ya:.1f} Z"/>')
        el.append(f'<text class="astarl" x="{xa+9:.1f}" y="{ya+3:.1f}">anchor: prior-day low</text>')
    if r['anchor_bar'] is not None:
        xa, ya = X(gi(r['anchor_bar'])), Y(r['anchor_px'])
        el.append(f'<path class="astar" d="M{xa:.1f},{ya-6:.1f} L{xa+6:.1f},{ya:.1f} '
                  f'L{xa:.1f},{ya+6:.1f} L{xa-6:.1f},{ya:.1f} Z"/>')
        el.append(f'<text class="astarl" x="{xa+9:.1f}" y="{ya+11:.1f}">anchor: swing low</text>')
    if r['confirm_bar'] is not None:
        xc = X(gi(r['confirm_bar']))
        el.append(f'<line class="cfl" x1="{xc:.1f}" y1="{pad_t}" x2="{xc:.1f}" y2="{pad_t+ih:.1f}"/>')
        el.append(f'<text class="ax cflt" x="{xc+3:.1f}" y="{pad_t+22:.1f}">anchor known</text>')
    # candles
    cw = max(bw * 0.62, 1.0)
    for i in range(N):
        cls = 'cu' if c[i] >= o[i] else 'cd'
        x = X(i)
        el.append(f'<line class="{cls}" x1="{x:.1f}" y1="{Y(h[i]):.1f}" x2="{x:.1f}" y2="{Y(l[i]):.1f}"/>')
        yo, yc = Y(o[i]), Y(c[i])
        el.append(f'<rect class="{cls}" x="{x-cw/2:.1f}" y="{min(yo,yc):.1f}" '
                  f'width="{cw:.1f}" height="{max(abs(yc-yo),1):.1f}"/>')
    # raw crosses (cross-null): faint grey ticks along the bottom
    yb = pad_t + ih - 3
    for j in crosses:
        el.append(f'<line class="xn" x1="{X(gi(j)):.1f}" y1="{yb:.1f}" x2="{X(gi(j)):.1f}" y2="{yb-6:.1f}"/>')
    # reclaim brackets: entry triangle + stop/target guides to resolution, by outcome
    ocls = {'target': 'ot', 'stop': 'os', 'eod': 'oe'}
    for j, entry, stop, tgt, res, out in brs:
        cl = ocls[out]
        x0, x1 = X(gi(j)), X(gi(res))
        el.append(f'<line class="tgt" x1="{x0:.1f}" y1="{Y(tgt):.1f}" x2="{x1:.1f}" y2="{Y(tgt):.1f}"/>')
        el.append(f'<line class="stp" x1="{x0:.1f}" y1="{Y(stop):.1f}" x2="{x1:.1f}" y2="{Y(stop):.1f}"/>')
        el.append(f'<line class="reso {cl}" x1="{x0:.1f}" y1="{Y(entry):.1f}" x2="{x1:.1f}" y2="{Y(entry):.1f}"/>')
        ry = Y(tl[j]) + 11
        el.append(f'<path class="{cl}" d="M{x0-5:.1f},{ry:.1f} L{x0+5:.1f},{ry:.1f} L{x0:.1f},{ry-8:.1f} Z"/>')
        rvy = Y(tgt) if out == 'target' else (Y(stop) if out == 'stop' else Y(tc[res]))
        el.append(f'<circle class="{cl}d" cx="{x1:.1f}" cy="{rvy:.1f}" r="2.6"/>')
    # price axis
    for f in range(5):
        v = ymin + (ymax - ymin) * f / 4; y = Y(v)
        el.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+iw:.1f}" y2="{y:.1f}"/>')
        el.append(f'<text class="ax" x="{pad_l+iw+4:.1f}" y="{y+3:.1f}">{v:.0f}</text>')
    # time axis (today portion)
    for hh in ['09:30', '11:00', '12:30', '14:00', '15:30']:
        cand = [i for i in range(r['n']) if r['hm'][i] >= hh]
        if cand:
            el.append(f'<text class="ax" x="{X(gi(cand[0])):.1f}" y="{H-6}" text-anchor="middle">{hh}</text>')
    el.append(f'<text class="avl" x="{pad_l+iw+4:.1f}" y="{Y(mid[np.isfinite(mid)][-1])+3:.1f}">aVWAP</text>')
    return f'<svg viewBox="0 0 {W} {H}">{"".join(el)}</svg>'


STYLE = '''
:root{color-scheme:light dark;--bg:#fff;--surface:#fcfcfb;--fg:#1a1a1a;--mut:#666;--line:#e3e3df;
--up:#2a78d6;--dn:#e34948;--band:rgba(42,120,214,.09);--bl:#2a78d6;--av:#d98600;
--tg:#1f9d55;--st:#e34948;--eo:#8a8a8a;--note:rgba(217,134,0,.10);--good:rgba(31,157,85,.10);--bad:rgba(227,73,72,.10)}
@media(prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surface:#1a1a19;--fg:#e6e6e6;--mut:#9aa;--line:#2a2d33;
--up:#3987e5;--dn:#e66767;--band:rgba(57,135,229,.12);--bl:#4d97ea;--av:#e0a020;
--tg:#3fbf77;--st:#e66767;--eo:#9a9a9a;--note:rgba(224,160,32,.12);--good:rgba(63,191,119,.12);--bad:rgba(230,103,103,.12)}}
:root[data-theme="dark"]{--bg:#0d0d0d;--surface:#1a1a19;--fg:#e6e6e6;--mut:#9aa;--line:#2a2d33;
--up:#3987e5;--dn:#e66767;--band:rgba(57,135,229,.12);--bl:#4d97ea;--av:#e0a020;
--tg:#3fbf77;--st:#e66767;--eo:#9a9a9a;--note:rgba(224,160,32,.12);--good:rgba(63,191,119,.12);--bad:rgba(230,103,103,.12)}
:root[data-theme="light"]{--bg:#fff;--surface:#fcfcfb;--fg:#1a1a1a;--mut:#666;--line:#e3e3df;
--up:#2a78d6;--dn:#e34948;--band:rgba(42,120,214,.09);--bl:#2a78d6;--av:#d98600;
--tg:#1f9d55;--st:#e34948;--eo:#8a8a8a;--note:rgba(217,134,0,.10);--good:rgba(31,157,85,.10);--bad:rgba(227,73,72,.10)}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
margin:0;padding:28px 22px 60px;line-height:1.45}
.wrap{max-width:1020px;margin:0 auto}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:13.5px;max-width:82ch;margin:0 0 8px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--mut);margin:12px 0 4px}
.legend .k{display:inline-flex;align-items:center;gap:6px}
.sw{width:14px;height:0;border-top:2px solid;display:inline-block}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:14px 15px 12px;margin:18px 0}
.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
padding:2px 8px;border-radius:4px;margin-bottom:8px}
.tag.good{background:var(--good);color:var(--tg)} .tag.bad{background:var(--bad);color:var(--st)}
.tag.neu{background:rgba(217,134,0,.12);color:var(--av)}
.ptitle{font-size:14.5px;font-weight:600;margin:0 0 2px}
.ptitle .sess{color:var(--mut);font-weight:400}
.anchor{color:var(--av);font-size:12px;font-weight:600;margin:0 0 10px}
.pstats{font-size:12px;color:var(--mut);font-variant-numeric:tabular-nums;margin:0 0 8px}
svg{display:block;width:100%;height:auto;background:transparent}
svg text{font-family:system-ui,sans-serif;font-size:11px;font-variant-numeric:tabular-nums;fill:var(--fg)}
.grid{stroke:var(--line);stroke-width:.6} .ax{fill:var(--mut);font-size:10px} .cflt{fill:var(--mut);font-size:9px}
.cu{stroke:var(--up);fill:var(--up)} .cd{stroke:var(--dn);fill:var(--dn)}
.band{fill:var(--band);stroke:none}
.bl{fill:none;stroke:var(--bl);stroke-width:1;opacity:.5;stroke-dasharray:3 3}
.av{fill:none;stroke:var(--av);stroke-width:1.9} .avl{fill:var(--av);font-weight:700;font-size:10px}
.anc{stroke:var(--av);stroke-width:1;opacity:.45;stroke-dasharray:1 3}
.astar{fill:var(--av);stroke:var(--bg);stroke-width:1} .astarl{fill:var(--av);font-size:9.5px;font-weight:700}
.reg-prior{fill:var(--mut);opacity:.05} .reg-post{fill:var(--tg);opacity:.13} .reg-on{fill:var(--mut);opacity:.11} .reg-day{fill:none}
.reglab{fill:var(--mut);font-size:9.5px;letter-spacing:.03em;opacity:.8}
.divi{stroke:var(--fg);stroke-width:1;opacity:.32} .divit{fill:var(--mut);font-size:9px}
.cfl{stroke:var(--mut);stroke-width:.8;stroke-dasharray:2 3;opacity:.6}
.xn{stroke:var(--eo);stroke-width:1.4;opacity:.5}
.tgt{stroke:var(--tg);stroke-width:.8;stroke-dasharray:2 2;opacity:.55}
.stp{stroke:var(--st);stroke-width:.8;stroke-dasharray:2 2;opacity:.55}
.reso{stroke-width:1.6;opacity:.85} .reso.ot{stroke:var(--tg)} .reso.os{stroke:var(--st)} .reso.oe{stroke:var(--eo)}
.ot{fill:var(--tg)} .os{fill:var(--st)} .oe{fill:var(--eo)}
.otd{fill:var(--tg)} .osd{fill:var(--st)} .oed{fill:var(--eo)}
table{border-collapse:collapse;font-size:12.5px;margin-top:10px;font-variant-numeric:tabular-nums}
td,th{padding:3px 14px 3px 0;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600}
.cap{font-size:12.5px;color:var(--mut);margin-top:8px;line-height:1.5}
.note{background:var(--note);border-left:3px solid var(--av);padding:11px 15px;border-radius:5px;
font-size:13px;margin:22px 0 0;line-height:1.55}
'''

LEGEND = ('<div class="legend">'
          '<span class="k"><span class="sw" style="border-color:var(--av)"></span> anchored VWAP</span>'
          '<span class="k"><span class="sw" style="border-color:var(--bl);border-top-style:dashed"></span> &plusmn;1&sigma;</span>'
          '<span class="k">&#9670; anchor (prior-day / swing low)</span>'
          '<span class="k"><span class="dot" style="background:var(--tg);opacity:.45;border-radius:2px"></span> recovered 16-17h (was dropped)</span>'
          '<span class="k">&#9650;<span class="dot" style="background:var(--tg)"></span> reclaim &rarr; 2R target hit</span>'
          '<span class="k"><span class="dot" style="background:var(--st)"></span> reclaim &rarr; stopped &minus;1R</span>'
          '<span class="k"><span class="dot" style="background:var(--eo)"></span> reclaim &rarr; open at EOD</span>'
          '<span class="k"><span class="sw" style="border-color:var(--eo);opacity:.5"></span> raw cross (cross-null)</span>'
          '</div>')


def panel_html(r, day, tag=None, caption=''):
    mid, c = r['mid'], r['bc']
    reclaims, crosses = detect(mid, c, r['valid_from'])
    rows = []
    n_t = n_s = n_e = 0
    for j in reclaims:
        mfe, mae, R, hit, close_t, fwd_t = forward(r['bh'], r['bl'], c, j)
        _, _, _, _, out = bracket(r['bh'], r['bl'], c, j)
        n_t += out == 'target'; n_s += out == 'stop'; n_e += out == 'eod'
        word = {'target': 'target +2R', 'stop': 'stop −1R', 'eod': 'open (EOD)'}[out]
        rows.append(f'<tr><td>{r["hm"][j]}</td><td>{c[j]:.2f}</td><td>{R:+.2f}</td>'
                    f'<td>{"yes" if hit else "—"}</td><td>{mfe:+.0f}t</td><td>{mae:.0f}t</td>'
                    f'<td>{fwd_t:+.0f}t</td><td>{word}</td></tr>')
    # per-day rand-null R (matched count, same seed as the study)
    rng = np.random.default_rng(SEED)
    pool = [j for j in range(r['valid_from'], r['n'] - 1) if np.isfinite(mid[j])]
    rand_R = []
    if pool and reclaims:
        picks = rng.choice(pool, size=min(len(pool), NDRAW * len(reclaims)), replace=False)
        rand_R = [forward(r['bh'], r['bl'], c, int(j))[2] for j in picks]
    real_R = [forward(r['bh'], r['bl'], c, j)[2] for j in reclaims]
    net_t = (c[-1] - c[r['valid_from']]) / TICK
    tag_html = (f'<span class="tag {tag[0]}">{tag[1]}</span><br>' if tag else '')
    rrow = f'{np.mean(real_R):+.2f}' if real_R else '—'
    nrow = f'{np.mean(rand_R):+.2f}' if rand_R else '—'
    return f'''<div class="panel">{tag_html}
<div class="ptitle">{day} <span class="sess">({r['sym']} &middot; {r['anchor']} anchor)</span></div>
<div class="anchor">Anchor: {r['anchor_lbl']}</div>
<div class="pstats">net {net_t:+.0f}t &middot; {len(reclaims)} reclaims
({n_t} target / {n_s} stop / {n_e} open) &middot; {len(crosses)} raw crosses &middot;
reclaim R̄ {rrow} &nbsp;vs&nbsp; rand-long R̄ {nrow}</div>
{build_svg(r)}
<table><tr><th>reclaim @</th><th>close</th><th>R</th><th>hit2R</th><th>MFE</th><th>MAE</th>
<th>fwd15</th><th>bracket</th></tr>{"".join(rows)}</table>
{f'<div class="cap">{caption}</div>' if caption else ''}</div>'''


def page(title, sub, body):
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>{STYLE}</style></head><body><div class="wrap">'
            f'<h1>{title}</h1><p class="sub">{sub}</p>{LEGEND}{body}</div></body></html>')


def fragment(title, sub, body):
    return (f'<title>{title}</title><style>{STYLE}</style><div class="wrap">'
            f'<h1>{title}</h1><p class="sub">{sub}</p>{LEGEND}{body}</div>')


def write(out, html):
    with open(out, 'w') as f:
        f.write(html)
    return out


def print_summary(r, day):
    mid, c = r['mid'], r['bc']
    reclaims, crosses = detect(mid, c, r['valid_from'])
    print(f'\n=== {day} ({r["sym"]}, {r["anchor"]}) — {r["anchor_lbl"]} ===')
    print(f'reclaims {len(reclaims)}  raw-crosses {len(crosses)}  valid_from bar {r["valid_from"]}')
    for j in reclaims:
        mfe, mae, R, hit, close_t, fwd_t = forward(r['bh'], r['bl'], c, j)
        _, _, _, _, out = bracket(r['bh'], r['bl'], c, j)
        print(f'  {r["hm"][j]}  close {c[j]:.2f}  R {R:+.2f}  hit2R {hit}  '
              f'MFE {mfe:+.0f}t MAE {mae:.0f}t  -> {out}')


CASES = [
    ('2025-03-31', 'pdl', ('good', 'trend → continuation'),
     'Trend day, prior-day-low anchor. Left of the “09:30 open” divider is the context: '
     'yesterday’s RTH <i>from its low</i> (the ◆ anchor) through the overnight, where the '
     'aVWAP is seeded and carried in. Right of it is today’s traded session — the reclaims '
     'in the 11:00–12:15 window ride the line up and several tag the +2R target (green). '
     'This is the shape the playbook advertises, and the one a worked example always finds.'),
    ('2025-03-11', 'pdl', ('bad', 'chop → whipsaw'),
     'Range day, SAME anchor construction (◆ prior-day low → overnight carry → today), SAME '
     'detector. Today the reclaim trigger fires repeatedly and price knifes back through the '
     'line — most brackets resolve on the red stop. The reclaim carried no information here; '
     'the trend-day panel above only looked informative in hindsight.'),
    ('2025-03-31', 'swing', ('neu', 'same day, other anchor'),
     'The SAME trend session, but anchored at the first confirmed swing low instead of the '
     'prior-day low. A different line means different reclaims at different bars with different '
     'outcomes — this is why the study’s cross-null edge <b>flips sign with the anchor</b> '
     '(−13t pdl vs +14t swing): the "signal" is an artifact of anchor choice, not structure.'),
]


if __name__ == '__main__':
    if len(sys.argv) > 2:                       # ad-hoc single: DAY ANCHOR
        day_s, anchor = sys.argv[1], sys.argv[2]
        r = build_bands(date.fromisoformat(day_s), anchor)
        if isinstance(r, str):
            sys.exit(r)
        print_summary(r, day_s)
        out = write(f'data/research/avwap-reclaim/avwap_verify_{day_s}_{anchor}.html',
                    page(f'aVWAP reclaim verify — {day_s} ({anchor})',
                         'Study detector + 2R:1R bracket, drawn exactly as avwap_outcomes.py scored it.',
                         panel_html(r, day_s)))
        print(f'\nwrote {out}')
    else:
        panels = []
        for day_s, anchor, tag, cap in CASES:
            r = build_bands(date.fromisoformat(day_s), anchor)
            if isinstance(r, str):
                print('skip', day_s, anchor, r); continue
            print_summary(r, day_s)
            panels.append(panel_html(r, day_s, tag=tag, caption=cap))
        sub = ('Each chart renders the anchored-VWAP study exactly as it was scored: the detector '
               '(<code>build_bands</code>/<code>detect</code>/<code>forward</code>) is copied verbatim '
               'from <code>avwap_outcomes.py</code>, so the &#9650; reclaims are the ones the study '
               'counted, and each is coloured by how its 2R:1R stop-first bracket actually resolved. '
               'For the prior-day-low panels the shaded band left of the “09:30 open” divider shows '
               'where the line is anchored (◆ yesterday’s low), through the <b>recovered 16:00–17:00 '
               'hour</b> (green zone — the live post-RTH hour that used to be dropped, now bought and '
               'spliced in), and across the overnight; only the today-RTH bars are traded. The '
               'R̄-vs-rand-long line under each chart is the whole verdict in miniature.')
        note = ('<div class="note"><b>What to look for.</b> On the trend day the green targets look like '
                'an edge; on the chop day the identical trigger bleeds into red stops; on the swing-anchor '
                'panel the same session throws different reclaims entirely. Across the full sample that '
                'averages out: reclaim R̄ never beats a random long on the same day (dR −0.10 pdl / '
                '−0.10 swing, <b>now on gap-corrected data</b> — the recovered 16:00–17:00 hour is '
                'spliced in) and the cross-null edge flips sign with the anchor. Verdict stands: '
                '<b>NULL, do not build.</b> Full numbers: <code>docs/research/anchored-vwap-reclaim.md</code>.</div>')
        title = 'Anchored-VWAP reclaim — study verification (3 sessions)'
        body = ''.join(panels) + note
        out = write('data/research/avwap-reclaim/avwap_verify.html', page(title, sub, body))
        frag = write('data/research/avwap-reclaim/avwap_verify.artifact.html', fragment(title, sub, body))
        print(f'\nwrote {out}\nwrote {frag}')
