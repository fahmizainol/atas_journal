"""Market-structure / price-action extraction for winners-vs-losers study.

Feature families (all live-decidable at their anchor):
  ENTRY anchor (data <= entry ts):
    - swing structure: causal zigzag (20t / 40t reversal) on 1-min ON+RTH bars
      -> trend state (HH/HL vs LH/LL), entry vs last swing low/high, swing lows
         sitting above entry (already knifed through)
    - momentum: 1/5/15/30-min returns into entry (R units), approach-leg
      depth/duration/efficiency from last swing high
    - consolidation: 15m-vs-60m range compression, bar-overlap fraction,
      down-push count, minutes since session high
    - HTF: 5-min slope (30m window), position in overnight range, ON trend
    - bar character: last completed 1m bar wick/close location
  UNDERWATER anchors (first touch of -0.25R / -0.40R, data <= touch ts):
    - structure break: below last pre-entry swing low? by how much? how many
      pre-entry lows broken since entry?
    - path anatomy: efficiency (net/path on 15s closes), overlap, new-low
      cadence, max retrace before the touch
    - momentum at touch: 1/5-min return, 5-min HTF slope

Causality: a zigzag pivot exists at time T only if the reversal that confirms
it completed at or before T (confirm_idx). No future data leaks into features.

Conventions from prior studies: entry_idx/exit_idx positional into
ticks.cached_rth, NQ tick = 0.25, risk from avg_entry - stop_price.

Usage: .venv/bin/python extract_structure.py <run_dir_name>
  e.g. 20250201-20260630-v10-cdc07ca2
"""
import sys, time
sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks

RUN = sys.argv[1]
BASE = f'data/sims/vwap-upper-band-bounce/{RUN}'
OUT = f"data/research/market-structure/features_{RUN.split('-')[-1]}.parquet"
TICK = 0.25

trades = pd.read_parquet(f'{BASE}/trades.parquet').reset_index(drop=True)


def make_bars(ts, price, freq):
    """OHLC bars from tick arrays. Returns df indexed 0..n with bar end ts."""
    s = pd.Series(price, index=pd.DatetimeIndex(ts))
    o = s.resample(freq).first()
    h = s.resample(freq).max()
    l = s.resample(freq).min()
    c = s.resample(freq).last()
    df = pd.DataFrame({'o': o, 'h': h, 'l': l, 'c': c}).dropna()
    df['end'] = df.index + pd.tseries.frequencies.to_offset(freq)
    return df.reset_index(drop=True)


def causal_zigzag(high, low, thr):
    """Causal zigzag. Returns list of (pivot_idx, price, kind, confirm_idx),
    kind 'H'/'L'. A pivot is usable at bar t iff confirm_idx <= t."""
    n = len(high)
    piv = []
    direction = 0  # 0 unknown, +1 up-leg (tracking max), -1 down-leg
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


def swings_at(piv, t):
    """Pivots confirmed by bar t, in pivot order."""
    return [p for p in piv if p[3] <= t]


def swing_features(piv, t, px, prefix, rec, tickf=TICK):
    """Swing-sequence + level features at bar t, current price px."""
    sw = swings_at(piv, t)
    lows = [p for p in sw if p[2] == 'L']
    highs = [p for p in sw if p[2] == 'H']
    rec[f'{prefix}_nswings'] = len(sw)
    # trend state from last two highs + last two lows
    hh = highs[-1][1] > highs[-2][1] if len(highs) >= 2 else np.nan
    hl = lows[-1][1] > lows[-2][1] if len(lows) >= 2 else np.nan
    if not (hh is np.nan or hl is np.nan):
        rec[f'{prefix}_trend'] = (1 if (hh and hl) else -1 if (not hh and not hl) else 0)
    else:
        rec[f'{prefix}_trend'] = np.nan
    rec[f'{prefix}_hh'] = float(hh) if hh is not np.nan else np.nan
    rec[f'{prefix}_hl'] = float(hl) if hl is not np.nan else np.nan
    # up-trend purity over last 6 swings: fraction of swing-to-swing steps rising
    steps = []
    for kind, arr in (('H', highs[-3:]), ('L', lows[-3:])):
        for a, b in zip(arr, arr[1:]):
            steps.append(1.0 if b[1] > a[1] else 0.0)
    rec[f'{prefix}_uppurity'] = float(np.mean(steps)) if steps else np.nan
    rec[f'{prefix}_vs_lastlow_t'] = (px - lows[-1][1]) / tickf if lows else np.nan
    rec[f'{prefix}_vs_lasthigh_t'] = (px - highs[-1][1]) / tickf if highs else np.nan
    # how many recent swing lows sit ABOVE current price (already knifed through)
    rec[f'{prefix}_lows_above'] = float(sum(1 for p in lows[-5:] if p[1] > px))
    return lows, highs


def overlap_frac(h, l):
    """Mean bar-to-bar range overlap / avg range, over given bar arrays."""
    if len(h) < 2:
        return np.nan
    inter = np.minimum(h[1:], h[:-1]) - np.maximum(l[1:], l[:-1])
    rng = ((h[1:] - l[1:]) + (h[:-1] - l[:-1])) / 2.0
    ok = rng > 0
    return float(np.mean(np.clip(inter[ok] / rng[ok], 0, 1))) if ok.any() else np.nan


def down_pushes(l, min_run=2):
    """Count runs of >=min_run consecutive lower lows."""
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
    """Last price at or before t. nan if none."""
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
    h1, l1, c1, o1 = b1.h.values, b1.l.values, b1.c.values, b1.o.values
    zz20 = causal_zigzag(h1, l1, 20 * TICK)   # 5 pt
    zz40 = causal_zigzag(h1, l1, 40 * TICK)   # 10 pt
    # 15s closes over RTH for underwater path anatomy
    b15 = make_bars(rts, rpx, '15s')
    b15_end = b15['end'].values.astype('datetime64[ns]')
    c15 = b15.c.values

    sub = trades[trades.session == sess]
    for _, tr in sub.iterrows():
        ei, xi = int(tr.entry_idx), int(tr.exit_idx)
        ei = min(ei, len(rth) - 1)
        xi = min(xi, len(rth) - 1)
        et, xt = rts[ei], rts[xi]
        entry = float(tr.avg_entry)
        risk = float(tr.avg_entry - tr.stop_price)
        if risk <= 0:
            risk = np.nan
        rec = dict(idx=int(tr.name), session=sess, r=float(tr.r_multiple),
                   net=float(tr.net_pnl), exit_reason=tr.exit_reason,
                   mfe_r=float(tr.mfe_r), mae_r=float(tr.mae_r),
                   dur_s=float(tr.duration_s), risk_pts=risk,
                   entry_ts=str(et), band_w=float(tr.band_width_ticks))

        # bar index of last COMPLETED 1m/5m bar at entry
        e1 = int(np.searchsorted(b1_end, et, side='right')) - 1
        e5 = int(np.searchsorted(b5_end, et, side='right')) - 1

        # ---- momentum into entry ----
        for L, tag in ((60, '1m'), (300, '5m'), (900, '15m'), (1800, '30m')):
            p = px_at(allts, allpx, et - np.timedelta64(L, 's'))
            rec[f'mom_{tag}_r'] = (entry - p) / risk if np.isfinite(p) else np.nan
        rec['ret_rthopen_r'] = (entry - rth_open) / risk
        rec['gx_ret_r'] = (on_close - on_open) / risk if np.isfinite(on_open) else np.nan
        rec['pos_in_gxrange'] = ((entry - on_lo) / (on_hi - on_lo)
                                 if np.isfinite(on_hi) and on_hi > on_lo else np.nan)
        rec['above_gxhigh'] = float(entry > on_hi) if np.isfinite(on_hi) else np.nan

        # ---- swing structure at entry ----
        lows20, highs20 = swing_features(zz20, e1, entry, 'zz20', rec)
        swing_features(zz40, e1, entry, 'zz40', rec)

        # ---- approach leg: last confirmed zz20 swing high -> entry ----
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

        # ---- consolidation / range regime before entry ----
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
        # minutes since the running session (ON+RTH) high
        hi_run = np.maximum.accumulate(h1[:e1 + 1])
        last_new_hi = int(np.nonzero(h1[:e1 + 1] >= hi_run)[0][-1])
        rec['min_since_hi'] = float(e1 - last_new_hi)

        # ---- HTF trend at entry ----
        if e5 >= 6:
            rec['htf5_slope_t'] = float((b5.c.values[e5] - b5.c.values[e5 - 6]) / 6 / TICK)
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

        # pre-entry-confirmed zz20 lows (for underwater break tests)
        pre_lows = [p for p in zz20 if p[2] == 'L' and p[3] <= e1]
        pre_lastlow = pre_lows[-1][1] if pre_lows else np.nan
        n_prelow_above_entry = sum(1 for p in pre_lows[-8:] if p[1] > entry)

        # ---- underwater touch anchors ----
        hold_px = rpx[ei:xi + 1]
        depth_r = (entry - hold_px) / risk
        for thr_r, tag in ((0.25, 't25'), (0.40, 't40')):
            hit = np.nonzero(depth_r >= thr_r)[0]
            if len(hit) == 0:
                rec[f'{tag}_hit'] = 0
                continue
            ti = ei + int(hit[0])
            tt = rts[ti]
            tpx = float(rpx[ti])
            rec[f'{tag}_hit'] = 1
            rec[f'{tag}_secs'] = float((tt - et) / np.timedelta64(1, 's'))
            t1 = int(np.searchsorted(b1_end, tt, side='right')) - 1
            t5 = int(np.searchsorted(b5_end, tt, side='right')) - 1

            # structure break vs pre-entry swings
            rec[f'{tag}_below_prelow_t'] = ((pre_lastlow - tpx) / TICK
                                            if np.isfinite(pre_lastlow) else np.nan)
            n_now_above = sum(1 for p in pre_lows[-8:] if p[1] > tpx)
            rec[f'{tag}_lows_broken_uw'] = float(n_now_above - n_prelow_above_entry)
            # swing state including swings formed DURING the hold
            swing_features(zz20, t1, tpx, f'{tag}_zz20', rec)

            # path anatomy entry -> touch (15s closes)
            a = int(np.searchsorted(b15_end, et, side='right'))
            b = int(np.searchsorted(b15_end, tt, side='right'))
            seg = c15[a:b]
            if len(seg) > 1:
                path = float(np.abs(np.diff(seg)).sum())
                rec[f'{tag}_uw_eff'] = (entry - tpx) / path if path > 0 else np.nan
            else:
                rec[f'{tag}_uw_eff'] = np.nan
            s1 = int(np.searchsorted(b1_end, et, side='right'))
            rec[f'{tag}_uw_overlap'] = overlap_frac(h1[s1:t1 + 1], l1[s1:t1 + 1])
            rec[f'{tag}_uw_pushes'] = float(down_pushes(l1[s1:t1 + 1]))
            # max retrace (in R) between entry and the touch
            pfx = rpx[ei:ti + 1]
            runlow = np.minimum.accumulate(pfx)
            rec[f'{tag}_maxretr_r'] = float(((pfx - runlow).max()) / risk)
            # new-low cadence: median secs between new hold lows (30s clusters)
            newlow = np.nonzero(pfx <= runlow)[0]
            if len(newlow) > 2:
                nlts = rts[ei + newlow].astype('datetime64[s]').astype('int64')
                gaps = np.diff(nlts)
                gaps = gaps[gaps >= 30]
                rec[f'{tag}_lowcad_s'] = float(np.median(gaps)) if len(gaps) else 0.0
            else:
                rec[f'{tag}_lowcad_s'] = np.nan
            # momentum at the touch
            for L, mtag in ((60, '1m'), (300, '5m')):
                p = px_at(allts, allpx, tt - np.timedelta64(L, 's'))
                rec[f'{tag}_mom_{mtag}_r'] = (tpx - p) / risk if np.isfinite(p) else np.nan
            if t5 >= 6:
                rec[f'{tag}_htf5_slope_t'] = float(
                    (b5.c.values[t5] - b5.c.values[t5 - 6]) / 6 / TICK)
            else:
                rec[f'{tag}_htf5_slope_t'] = np.nan

        # descriptive: at the hold low, how far below the pre-entry last swing low
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
