"""Causal globex-VWAP characteristics at each entry of run 74e6af45.

All features are computed from ticks[:entry_idx+1] only — nothing after the
fill is touched. Occupancy is time-weighted (tick timestamps are irregular).
"""
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod, vwap as vwapmod

RUN = 'data/sims/vwap-globex-bounce/20240303-20260630-v14-74e6af45'
OUT = '/tmp/claude-1000/-home-afahmi-repos-atas-journal/9cdb1620-da88-4123-a804-64672494c19f/scratchpad/gx_features.parquet'

trades = pd.read_parquet(f'{RUN}/trades.parquet')

def tw_frac(ts_ns, cond, i0, i1):
    """Time-weighted fraction of [i0, i1) where cond holds."""
    if i1 - i0 < 2:
        return np.nan
    dt = np.diff(ts_ns[i0:i1 + 1] if i1 < len(ts_ns) else ts_ns[i0:i1])
    c = cond[i0:i0 + len(dt)]
    tot = dt.sum()
    return float(dt[c].sum() / tot) if tot > 0 else np.nan

rows = []
for sess, sub in trades.groupby('session', sort=True):
    day = pd.Timestamp(sess).date()
    t = tickmod.get_day_ticks(tickmod.contract_for('NQ', day), day,
                              include_overnight=True)
    if t is None or t.empty:
        continue
    w = vwapmod.vwap_bands(t)
    ts = t['ts_utc']
    ts_ns = ts.astype('int64').to_numpy()
    px = t['price'].to_numpy()
    mid = w['mid'].to_numpy()
    std = w['std'].to_numpy()
    lo1 = w['lower1'].to_numpy()

    rth_open_ts = tickmod.session_bounds_utc(day)[0]
    rth_i0 = int(ts.searchsorted(rth_open_ts, side='left'))
    on_hi = px[:rth_i0].max() if rth_i0 > 0 else np.nan
    on_lo = px[:rth_i0].min() if rth_i0 > 0 else np.nan

    below_mid = px < mid
    below_lo1 = px < lo1

    for _, r in sub.iterrows():
        i = int(r['entry_idx'])
        ti = ts_ns[i]
        feat = {'trade_no': int(r['trade_no']), 'session': sess}

        # trailing-window anchors
        for label, mins in (('15m', 15), ('30m', 30), ('60m', 60)):
            j = int(np.searchsorted(ts_ns, ti - mins * 60_000_000_000, side='left'))
            dt_min = (ti - ts_ns[j]) / 60e9
            feat[f'slope_{label}'] = (mid[i] - mid[j]) / dt_min if dt_min > 1 else np.nan
            feat[f'occ_below_mid_{label}'] = tw_frac(ts_ns, below_mid, j, i)
            feat[f'occ_below_lo1_{label}'] = tw_frac(ts_ns, below_lo1, j, i)

        # since RTH open
        if i > rth_i0:
            dt_min = (ti - ts_ns[rth_i0]) / 60e9
            feat['slope_since_open'] = ((mid[i] - mid[rth_i0]) / dt_min
                                        if dt_min > 1 else np.nan)
            feat['occ_below_mid_sess'] = tw_frac(ts_ns, below_mid, rth_i0, i)
            feat['mins_since_open'] = dt_min
        else:
            feat['slope_since_open'] = np.nan
            feat['occ_below_mid_sess'] = np.nan
            feat['mins_since_open'] = np.nan

        feat['std_pts'] = std[i]
        # slope in sigma-units per hour: trend strength relative to channel width
        feat['slope30_sig_hr'] = (feat['slope_30m'] * 60 / std[i]
                                  if std[i] > 0 and not np.isnan(feat['slope_30m'])
                                  else np.nan)
        feat['mid_vs_rth_open'] = mid[i] - px[rth_i0] if rth_i0 < len(px) else np.nan
        feat['on_range_pos'] = ((px[i] - on_lo) / (on_hi - on_lo)
                                if on_hi > on_lo else np.nan)
        feat['px_vs_on_lo'] = px[i] - on_lo
        rows.append(feat)

feats = pd.DataFrame(rows)
out = trades.merge(feats, on=['trade_no', 'session'], how='left')
out.to_parquet(OUT)
print('wrote', OUT, out.shape)
print(out[['slope_30m', 'occ_below_mid_60m', 'slope30_sig_hr']].describe())
