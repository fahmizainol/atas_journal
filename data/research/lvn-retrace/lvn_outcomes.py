"""Do causal leg-LVN retraces have a forward edge? — vs a random-pullback null.

For every cached RTH day, find causal impulse up-legs (lvn_causal.py logic), and
for each leg's frozen LVN band, if price later retraces into it, measure the
forward path from the re-entry:

  entry = LVN top (bhi), a limit filled as price pulls back into the thin zone
  stop  = LVN bottom - 2t (thesis dead if it trades back through the gap),
          floored at MIN_RISK_PTS
  fwd   = MFE / MAE to session close; R under a 2R-target / 1R-stop bracket
          (stop-first within a bar = conservative, applied to real AND null)

NULL (the control the whole read hinges on): the SAME legs, but the band placed
at other heights in the leg (fractions of the leg range, same width, excluding
the real LVN). Answers "is the THIN location special, or is this just 'buy any
pullback in a trending leg'?" No big-lot filter yet — that's the next rung.

Usage: .venv/bin/python data/research/lvn-retrace/lvn_outcomes.py [start] [end]
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
CONFIRM_PTS = 22.0
MIN_LEG_PTS = 55.0
LVN_FRAC = 0.35
LEG_BUF_T = 8
MIN_RISK_PTS = 6.0
NULL_FRACS = [0.15, 0.30, 0.45, 0.60, 0.75]

START = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2025, 2, 3)
END = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 12, 31)
OUT = 'data/research/lvn-retrace/lvn_outcomes.parquet'


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


def leg_lvn_bands(price, size, i0, i1):
    lv = np.rint(price[i0:i1 + 1] / TICK).astype('int64')
    if lv.size == 0: return []
    base = int(lv.min())
    hist = np.bincount(lv - base, weights=size[i0:i1 + 1], minlength=int(lv.max()) - base + 1)
    n = len(hist)
    if n <= 2 * LEG_BUF_T + 2: return []
    poc_v = hist.max()
    med = np.median(hist[hist > 0]) if (hist > 0).any() else 0
    if poc_v < 2.0 * max(med, 1e-9): return []
    thr = LVN_FRAC * poc_v
    thin = hist <= thr; thin[:LEG_BUF_T] = False; thin[n - LEG_BUF_T:] = False
    bands, j = [], LEG_BUF_T
    while j < n - LEG_BUF_T:
        if thin[j]:
            k = j
            while k + 1 < n - LEG_BUF_T and thin[k + 1]: k += 1
            if 2 <= (k - j + 1) <= 0.45 * n:
                tr = int(hist[j:k + 1].argmin()) + j
                bands.append((base + j, base + k, float(hist[tr] / poc_v)))
            j = k + 1
        else:
            j += 1
    return bands


def forward(bh, bl, bc, r, entry, blo_px):
    """Forward outcome from retrace bar r: (mfe_t, mae_t, R, hit2R)."""
    risk = max(entry - (blo_px - 2 * TICK), MIN_RISK_PTS)
    stop = entry - risk
    tgt = entry + 2 * risk
    seg_hi, seg_lo = bh[r:], bl[r:]
    mfe = (seg_hi.max() - entry) / TICK
    mae = (entry - seg_lo.min()) / TICK
    R, hit2 = None, 0
    for j in range(r, len(bh)):
        if bl[j] <= stop:            # conservative: stop wins ties within a bar
            R = -1.0; break
        if bh[j] >= tgt:
            R = 2.0; hit2 = 1; break
    if R is None:
        R = (bc[-1] - entry) / risk
    return mfe, mae, R, hit2


def day_rows(day):
    sym = tickmod.contract_for_cached('NQ', day)
    if not sym: return []
    rth = tickmod.cached_rth(sym, day)
    if rth is None or rth.empty: return []
    price = rth['price'].to_numpy('float64'); size = rth['size'].to_numpy('float64')
    ts_ns = pd.to_datetime(rth['ts_utc'], utc=True).astype('int64').to_numpy()
    bars = barmod.tick_bars(rth, TPB)
    if len(bars) < 20: return []
    bh, bl, bc = bars['high'].to_numpy(), bars['low'].to_numpy(), bars['close'].to_numpy()
    bsi, bei = bars['start_idx'].to_numpy(), bars['end_idx'].to_numpy()
    et = pd.to_datetime(bars['ts_utc'], utc=True).dt.tz_convert('America/New_York')
    tod = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    piv = zigzag(bh, bl, CONFIRM_PTS)

    cands = []
    for a in range(len(piv) - 1):
        k0, b0, p0, _ = piv[a]; k1, b1, p1, known = piv[a + 1]
        if k0 != 'L' or k1 != 'H' or (p1 - p0) < MIN_LEG_PTS: continue
        if b0 > 0 and p1 <= bh[:b0].max(): continue          # must break to a new high
        bands = [z for z in leg_lvn_bands(price, size, bsi[b0], bei[b1])
                 if z[1] * TICK <= p1 - CONFIRM_PTS]           # launchpad below the high
        if not bands: continue
        cands.append(dict(b0=b0, p0=p0, b1=b1, p1=p1, known=known, bands=bands))
    # non-overlapping dominant legs
    kept = []
    for L in sorted(cands, key=lambda z: -(z['p1'] - z['p0'])):
        if all(L['b1'] < K['b0'] or L['b0'] > K['b1'] for K in kept): kept.append(L)

    rows = []
    for L in sorted(kept, key=lambda z: z['b0']):
        real = max(L['bands'], key=lambda z: z[1] - z[0])
        w = (real[1] - real[0]) + 1
        leg_lo_i, leg_hi_i = int(round(L['p0'] / TICK)), int(round(L['p1'] / TICK))
        usable_hi_i = int(round((L['p1'] - CONFIRM_PTS) / TICK))

        def emit(blo_i, bhi_i, depth, is_null):
            bhi_px, blo_px = bhi_i * TICK, blo_i * TICK
            r = None
            for j in range(L['known'] + 1, len(bh)):
                if bl[j] <= bhi_px and bh[j] >= blo_px:
                    r = j; break
            if r is None: return None
            mfe, mae, R, hit2 = forward(bh, bl, bc, r, bhi_px, blo_px)
            # big-lot participation at re-entry: engine.py def — share of the trailing
            # 60s printed in records >= 10 lots (side-agnostic). NaN if no tape.
            i = int(bei[r]); w0 = int(np.searchsorted(ts_ns, ts_ns[i] - 60_000_000_000, 'left'))
            wsz = size[w0:i + 1]; tot = wsz.sum()
            part = float(wsz[wsz >= 10].sum() / tot) if tot > 0 else np.nan
            ctr_i = (blo_i + bhi_i) / 2                       # depth-in-leg of the band centre
            pos = (ctr_i - leg_lo_i) / max(leg_hi_i - leg_lo_i, 1)
            return dict(day=str(day), is_null=is_null, tod=int(tod[r]),
                        leg_pts=round(L['p1'] - L['p0'], 1), lvn_depth=round(depth, 3),
                        pos_frac=round(float(pos), 3), band_w_t=int(w), entry=round(bhi_px, 2),
                        lag_bars=r - L['known'], part=round(part, 4) if part == part else np.nan,
                        mfe_t=round(mfe, 1), mae_t=round(mae, 1), R=round(R, 3), hit2R=hit2)

        rr = emit(real[0], real[1], real[2], 0)
        if rr: rows.append(rr)
        for f in NULL_FRACS:
            c = leg_lo_i + f * (leg_hi_i - leg_lo_i)
            nb_hi_i = int(round(c + w / 2)); nb_lo_i = nb_hi_i - (w - 1)
            if nb_hi_i > usable_hi_i or nb_lo_i < leg_lo_i: continue
            if not (nb_hi_i < real[0] or nb_lo_i > real[1]): continue   # no overlap w/ real LVN
            nr = emit(nb_lo_i, nb_hi_i, np.nan, 1)
            if nr: rows.append(nr)
    return rows


# --- run --------------------------------------------------------------------
days = tickmod.session_dates(START, END)
all_rows, done = [], 0
for d in days:
    try:
        all_rows.extend(day_rows(d))
    except Exception as e:
        print(f'  ! {d}: {e}')
    done += 1
    if done % 40 == 0:
        print(f'  ...{done}/{len(days)} days, {len(all_rows)} rows')

df = pd.DataFrame(all_rows)
if df.empty:
    sys.exit('no rows')
df.to_parquet(OUT)


def summ(g, label):
    n = len(g)
    print(f'{label:22} n={n:4d}  MFE_med={g.mfe_t.median():5.0f}t  MAE_med={g.mae_t.median():5.0f}t  '
          f'P(hit2R)={g.hit2R.mean():.2f}  R_mean={g.R.mean():+.3f}  %close>0={(g.R>0).mean():.2f}')


print(f'\n=== LVN retrace forward outcomes — {START}..{END} — {df.day.nunique()} days ===')
real, null = df[df.is_null == 0], df[df.is_null == 1]
summ(real, 'REAL LVN retrace')
summ(null, 'NULL random pullback')
print('\n-- by time of day (REAL) --')
summ(real[real.tod < 690], '  morning <11:30')
summ(real[real.tod >= 690], '  afternoon >=11:30')
print('-- by time of day (NULL) --')
summ(null[null.tod < 690], '  morning <11:30')
summ(null[null.tod >= 690], '  afternoon >=11:30')
print(f'\nwrote {OUT}')
