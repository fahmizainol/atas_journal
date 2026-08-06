"""Side-aware market-structure extraction for the drift-touch-fade runs.

Same feature families as extract_structure.py (flagship long-only study),
made two-sided by mirroring: for Short trades the price axis is negated so
highs<->lows swap and every feature reads in TRADE-RELATIVE terms:
  - mom_*_r / gx_ret_r / ret_rthopen_r : signed return in trade-favor R units
    (negative = market moved against the trade direction into entry, i.e. the
    counter-move being faded)
  - zz*_trend +1 : market trending in the trade's direction (HH/HL mirrored)
  - *_lows_above / lows_broken : adverse-side pivots already knifed through
  - leg_* : counter-trade approach leg from last favorable-side zz20 pivot
  - pos_in_gxrange 1.0 : entry at the trade-favor extreme of the ON range
  - bar_lowwick : wick on the adverse side of the last completed 1m bar
Range/overlap/compression features are sign-invariant and unchanged.

Causality identical to the parent script: zigzag pivots usable only after
their confirm bar; no future data in any feature.

INDEX-BASE WARNING (the bug that invalidated this study's first pass): the
drift engines run on ON+RTH ticks (session="globex"), so trades.parquet's
entry_idx/exit_idx index the COMBINED array — NOT ticks.cached_rth like the
flagship's. Treating them as RTH-only shifts every anchor ~2h into the future
and leaks the trade's outcome into "entry-time" features (AUC 0.83 of pure
lookahead). Anchors here are located by entry/exit TIMESTAMP instead, which is
immune to any index convention.

Usage: .venv/bin/python extract_structure_drift.py <strategy> <run_dir> <tag>
  e.g. extract_structure_drift.py drift-touch-fade 20250203-20260630-v1-03f4c56c dtf_03f4c56c
"""
import sys, time
sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks

STRAT, RUN, TAG = sys.argv[1], sys.argv[2], sys.argv[3]
BASE = f'data/sims/{STRAT}/{RUN}'
OUT = f'data/research/market-structure/features_{TAG}.parquet'
TICK = 0.25

trades = pd.read_parquet(f'{BASE}/trades.parquet').reset_index(drop=True)


def make_bars(ts, price, freq):
    s = pd.Series(price, index=pd.DatetimeIndex(ts))
    o = s.resample(freq).first()
    h = s.resample(freq).max()
    l = s.resample(freq).min()
    c = s.resample(freq).last()
    df = pd.DataFrame({'o': o, 'h': h, 'l': l, 'c': c}).dropna()
    df['end'] = df.index + pd.tseries.frequencies.to_offset(freq)
    return df.reset_index(drop=True)


def causal_zigzag(high, low, thr):
    n = len(high)
    piv = []
    direction = 0
    max_i, min_i = 0, 0
    for i in range(n):
        if high[i] >= high[max_i]:
            max_i = i
        if low[i] <= low[min_i]:
            min_i = i
        if direction >= 0 and high[max_i] - low[i] >= thr:
            piv.append((max_i, high[max_i], 'H', i))
            direction = -1
            min_i = i
        elif direction <= 0 and high[i] - low[min_i] >= thr:
            piv.append((min_i, low[min_i], 'L', i))
            direction = 1
            max_i = i
    return piv


def mirror_piv(piv):
    return [(i, -p, 'L' if k == 'H' else 'H', ci) for i, p, k, ci in piv]


def swing_features(piv, t, px, prefix, rec, tickf=TICK):
    sw = [p for p in piv if p[3] <= t]
    lows = [p for p in sw if p[2] == 'L']
    highs = [p for p in sw if p[2] == 'H']
    rec[f'{prefix}_nswings'] = len(sw)
    hh = highs[-1][1] > highs[-2][1] if len(highs) >= 2 else np.nan
    hl = lows[-1][1] > lows[-2][1] if len(lows) >= 2 else np.nan
    if not (hh is np.nan or hl is np.nan):
        rec[f'{prefix}_trend'] = (1 if (hh and hl) else -1 if (not hh and not hl) else 0)
    else:
        rec[f'{prefix}_trend'] = np.nan
    rec[f'{prefix}_hh'] = float(hh) if hh is not np.nan else np.nan
    rec[f'{prefix}_hl'] = float(hl) if hl is not np.nan else np.nan
    steps = []
    for kind, arr in (('H', highs[-3:]), ('L', lows[-3:])):
        for a, b in zip(arr, arr[1:]):
            steps.append(1.0 if b[1] > a[1] else 0.0)
    rec[f'{prefix}_uppurity'] = float(np.mean(steps)) if steps else np.nan
    rec[f'{prefix}_vs_lastlow_t'] = (px - lows[-1][1]) / tickf if lows else np.nan
    rec[f'{prefix}_vs_lasthigh_t'] = (px - highs[-1][1]) / tickf if highs else np.nan
    rec[f'{prefix}_lows_above'] = float(sum(1 for p in lows[-5:] if p[1] > px))
    return lows, highs


def overlap_frac(h, l):
    if len(h) < 2:
        return np.nan
    inter = np.minimum(h[1:], h[:-1]) - np.maximum(l[1:], l[:-1])
    rng = ((h[1:] - l[1:]) + (h[:-1] - l[:-1])) / 2.0
    ok = rng > 0
    return float(np.mean(np.clip(inter[ok] / rng[ok], 0, 1))) if ok.any() else np.nan


def down_pushes(l, min_run=2):
    if len(l) < min_run + 1:
        return 0
    lower = l[1:] < l[:-1]
    cnt, run = 0, 0
    for x in lower:
        if x:
            run += 1
        else:
            if run >= min_run:
                cnt += 1
            run = 0
    if run >= min_run:
        cnt += 1
    return cnt


def px_at(ts_arr, price_arr, t):
    i = np.searchsorted(ts_arr, t, side='right') - 1
    return float(price_arr[i]) if i >= 0 else np.nan


rows = []
sessions = sorted(trades.session.unique())
t0 = time.time()
for si, sess in enumerate(sessions):
    day = pd.Timestamp(sess).date()
    sym = ticks.contract_for('NQ', day)
    rth = ticks.cached_rth(sym, day)
    if rth is None or len(rth) == 0:
        print('NO TICKS', sess)
        continue
    on = ticks.cached_overnight(sym, day)
    if on is not None and len(on):
        allts = np.concatenate([on.ts_utc.values.astype('datetime64[ns]'),
                                rth.ts_utc.values.astype('datetime64[ns]')])
        allpx = np.concatenate([on.price.values.astype('float64'),
                                rth.price.values.astype('float64')])
        on_hi, on_lo = float(on.price.max()), float(on.price.min())
        on_open, on_close = float(on.price.iloc[0]), float(on.price.iloc[-1])
    else:
        allts = rth.ts_utc.values.astype('datetime64[ns]')
        allpx = rth.price.values.astype('float64')
        on_hi = on_lo = on_open = on_close = np.nan

    rts = rth.ts_utc.values.astype('datetime64[ns]')
    rpx = rth.price.values.astype('float64')
    rth_open = float(rpx[0])

    b1 = make_bars(allts, allpx, '1min')
    b5 = make_bars(allts, allpx, '5min')
    b1_end = b1['end'].values.astype('datetime64[ns]')
    b5_end = b5['end'].values.astype('datetime64[ns]')
    h1o, l1o, c1o, o1o = b1.h.values, b1.l.values, b1.c.values, b1.o.values
    b5c = b5.c.values
    zz20o = causal_zigzag(h1o, l1o, 20 * TICK)
    zz40o = causal_zigzag(h1o, l1o, 40 * TICK)
    zz20m, zz40m = mirror_piv(zz20o), mirror_piv(zz40o)
    b15 = make_bars(rts, rpx, '15s')
    b15_end = b15['end'].values.astype('datetime64[ns]')
    c15o = b15.c.values

    sub = trades[trades.session == sess]
    for _, tr in sub.iterrows():
        d = 1.0 if tr.direction == 'Long' else -1.0
        # mirrored (trade-relative) views: for shorts negate prices, swap h/l
        if d > 0:
            h1, l1, c1, o1 = h1o, l1o, c1o, o1o
            zz20, zz40 = zz20o, zz40o
            apx, hpx, c15v, b5cv = allpx, rpx, c15o, b5c
            m_on_hi, m_on_lo = on_hi, on_lo
            m_on_open, m_on_close, m_rth_open = on_open, on_close, rth_open
        else:
            h1, l1, c1, o1 = -l1o, -h1o, -c1o, -o1o
            zz20, zz40 = zz20m, zz40m
            apx, hpx, c15v, b5cv = -allpx, -rpx, -c15o, -b5c
            m_on_hi, m_on_lo = -on_lo, -on_hi
            m_on_open, m_on_close, m_rth_open = -on_open, -on_close, -rth_open

        # Anchor by timestamp, not index (see INDEX-BASE WARNING above).
        # .to_datetime64() keeps ns precision — np.datetime64(Timestamp)
        # truncates to us and misplaces anchors inside same-ts tick bursts.
        et = pd.Timestamp(tr.entry_ts_utc).tz_localize(None).to_datetime64()
        xt = pd.Timestamp(tr.exit_ts_utc).tz_localize(None).to_datetime64()
        ei = min(max(int(np.searchsorted(rts, et, side='right')) - 1, 0), len(rth) - 1)
        xi = min(max(int(np.searchsorted(rts, xt, side='right')) - 1, ei), len(rth) - 1)
        if abs(float(rpx[ei]) - float(tr.avg_entry)) > 50 * TICK:
            print(f'WARN {sess}: tick at entry ts {rpx[ei]} far from fill {tr.avg_entry}')
        entry = d * float(tr.avg_entry)
        risk = d * float(tr.avg_entry - tr.stop_price)
        if risk <= 0:
            risk = np.nan
        rec = dict(idx=int(tr.name), session=sess, direction=tr.direction,
                   entry_reason=tr.entry_reason, r=float(tr.r_multiple),
                   net=float(tr.net_pnl), exit_reason=tr.exit_reason,
                   mfe_r=float(tr.mfe_r), mae_r=float(tr.mae_r),
                   dur_s=float(tr.duration_s), risk_pts=risk,
                   entry_ts=str(et), band_w=float(tr.band_width_ticks))

        e1 = int(np.searchsorted(b1_end, et, side='right')) - 1
        e5 = int(np.searchsorted(b5_end, et, side='right')) - 1

        # ---- momentum into entry (trade-favor R units) ----
        for L, tag in ((60, '1m'), (300, '5m'), (900, '15m'), (1800, '30m')):
            p = px_at(allts, apx, et - np.timedelta64(L, 's'))
            rec[f'mom_{tag}_r'] = (entry - p) / risk if np.isfinite(p) else np.nan
        rec['ret_rthopen_r'] = (entry - m_rth_open) / risk
        rec['gx_ret_r'] = ((m_on_close - m_on_open) / risk
                           if np.isfinite(m_on_open) else np.nan)
        rec['pos_in_gxrange'] = ((entry - m_on_lo) / (m_on_hi - m_on_lo)
                                 if np.isfinite(m_on_hi) and m_on_hi > m_on_lo else np.nan)
        rec['above_gxhigh'] = float(entry > m_on_hi) if np.isfinite(m_on_hi) else np.nan

        # ---- swing structure at entry ----
        lows20, highs20 = swing_features(zz20, e1, entry, 'zz20', rec)
        swing_features(zz40, e1, entry, 'zz40', rec)

        # ---- approach leg: last favorable-side zz20 pivot -> entry ----
        if highs20:
            hi_i, hi_px = highs20[-1][0], highs20[-1][1]
            rec['leg_depth_t'] = (hi_px - entry) / TICK
            rec['leg_dur_min'] = float(e1 - hi_i)
            seg = c1[hi_i:e1 + 1]
            path = float(np.abs(np.diff(seg)).sum()) if len(seg) > 1 else np.nan
            rec['leg_eff'] = ((seg[0] - seg[-1]) / path
                              if path and path > 0 else np.nan)
        else:
            rec['leg_depth_t'] = rec['leg_dur_min'] = rec['leg_eff'] = np.nan

        # ---- consolidation / range regime before entry (sign-invariant) ----
        w15 = (e1 - 15, e1)
        w60 = (e1 - 75, e1 - 15)
        if w60[0] >= 0:
            r15 = h1[w15[0]:w15[1]].max() - l1[w15[0]:w15[1]].min()
            r60 = h1[w60[0]:w60[1]].max() - l1[w60[0]:w60[1]].min()
            rec['rng_compress'] = r15 / r60 if r60 > 0 else np.nan
        else:
            rec['rng_compress'] = np.nan
        s10 = slice(max(0, e1 - 10), e1)
        rec['overlap_10'] = overlap_frac(h1[s10], l1[s10])
        rec['pushes_30m'] = float(down_pushes(l1[max(0, e1 - 30):e1 + 1]))
        hi_run = np.maximum.accumulate(h1[:e1 + 1])
        last_new_hi = int(np.nonzero(h1[:e1 + 1] >= hi_run)[0][-1])
        rec['min_since_hi'] = float(e1 - last_new_hi)

        # ---- HTF trend at entry ----
        if e5 >= 6:
            rec['htf5_slope_t'] = float((b5cv[e5] - b5cv[e5 - 6]) / 6 / TICK)
        else:
            rec['htf5_slope_t'] = np.nan

        # ---- bar character: last completed 1m bar ----
        if e1 >= 0:
            rng = h1[e1] - l1[e1]
            if rng > 0:
                rec['bar_lowwick'] = (min(o1[e1], c1[e1]) - l1[e1]) / rng
                rec['bar_closeloc'] = (c1[e1] - l1[e1]) / rng
            else:
                rec['bar_lowwick'] = rec['bar_closeloc'] = np.nan

        pre_lows = [p for p in zz20 if p[2] == 'L' and p[3] <= e1]
        pre_lastlow = pre_lows[-1][1] if pre_lows else np.nan
        n_prelow_above_entry = sum(1 for p in pre_lows[-8:] if p[1] > entry)

        # ---- underwater touch anchors ----
        hold_px = hpx[ei:xi + 1]
        depth_r = (entry - hold_px) / risk
        for thr_r, tag in ((0.25, 't25'), (0.40, 't40')):
            hit = np.nonzero(depth_r >= thr_r)[0]
            if len(hit) == 0:
                rec[f'{tag}_hit'] = 0
                continue
            ti = ei + int(hit[0])
            tt = rts[ti]
            tpx = float(hpx[ti])
            rec[f'{tag}_hit'] = 1
            rec[f'{tag}_secs'] = float((tt - et) / np.timedelta64(1, 's'))
            t1 = int(np.searchsorted(b1_end, tt, side='right')) - 1
            t5 = int(np.searchsorted(b5_end, tt, side='right')) - 1

            rec[f'{tag}_below_prelow_t'] = ((pre_lastlow - tpx) / TICK
                                            if np.isfinite(pre_lastlow) else np.nan)
            n_now_above = sum(1 for p in pre_lows[-8:] if p[1] > tpx)
            rec[f'{tag}_lows_broken_uw'] = float(n_now_above - n_prelow_above_entry)
            swing_features(zz20, t1, tpx, f'{tag}_zz20', rec)

            a = int(np.searchsorted(b15_end, et, side='right'))
            b = int(np.searchsorted(b15_end, tt, side='right'))
            seg = c15v[a:b]
            if len(seg) > 1:
                path = float(np.abs(np.diff(seg)).sum())
                rec[f'{tag}_uw_eff'] = (entry - tpx) / path if path > 0 else np.nan
            else:
                rec[f'{tag}_uw_eff'] = np.nan
            s1 = int(np.searchsorted(b1_end, et, side='right'))
            rec[f'{tag}_uw_overlap'] = overlap_frac(h1[s1:t1 + 1], l1[s1:t1 + 1])
            rec[f'{tag}_uw_pushes'] = float(down_pushes(l1[s1:t1 + 1]))
            pfx = hpx[ei:ti + 1]
            runlow = np.minimum.accumulate(pfx)
            rec[f'{tag}_maxretr_r'] = float(((pfx - runlow).max()) / risk)
            newlow = np.nonzero(pfx <= runlow)[0]
            if len(newlow) > 2:
                nlts = rts[ei + newlow].astype('datetime64[s]').astype('int64')
                gaps = np.diff(nlts)
                gaps = gaps[gaps >= 30]
                rec[f'{tag}_lowcad_s'] = float(np.median(gaps)) if len(gaps) else 0.0
            else:
                rec[f'{tag}_lowcad_s'] = np.nan
            for L, mtag in ((60, '1m'), (300, '5m')):
                p = px_at(allts, apx, tt - np.timedelta64(L, 's'))
                rec[f'{tag}_mom_{mtag}_r'] = (tpx - p) / risk if np.isfinite(p) else np.nan
            if t5 >= 6:
                rec[f'{tag}_htf5_slope_t'] = float(
                    (b5cv[t5] - b5cv[t5 - 6]) / 6 / TICK)
            else:
                rec[f'{tag}_htf5_slope_t'] = np.nan

        rec['low_below_prelow_t'] = ((pre_lastlow - hold_px.min()) / TICK
                                     if np.isfinite(pre_lastlow) else np.nan)
        rows.append(rec)
    if si % 40 == 0:
        print(f'{si + 1}/{len(sessions)} sessions  {time.time() - t0:.0f}s  rows={len(rows)}',
              flush=True)

df = pd.DataFrame(rows)
df['is_stop'] = (df.exit_reason == 'stop').astype(int)
df['is_win'] = (df.r > 0).astype(int)
df.to_parquet(OUT)
print('WROTE', OUT, df.shape, f'{time.time() - t0:.0f}s')
