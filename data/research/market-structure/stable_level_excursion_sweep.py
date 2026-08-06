"""Full-sweep version of the excursion demo: for every touch of every developing
VP level across all sessions, record the 60-minute up/down/net excursion, so the
"how far & which way after a touch" question can be answered per level.

Same touch detection + level construction as stable_level_study.py. Writes
stable_level_excursion.parquet next to this file.

Usage: .venv/bin/python data/research/market-structure/stable_level_excursion_sweep.py
"""
import sys, time
from datetime import date, timedelta

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import profile as profmod

TICK = 0.25
TPB = 500
START, END = date(2025, 2, 3), date(2026, 6, 30)
TOUCH_TOL = 6
AGE_TOL = 2
WINDOW_S = 3600
DEDUP_M = 30
OUTDIR = 'data/research/market-structure'


def minute_grid(ts):
    lo = pd.Timestamp(ts[0]).ceil('1min')
    hi = pd.Timestamp(ts[-1]).floor('1min')
    if hi <= lo:
        return np.array([], 'int64'), np.array([], 'datetime64[ns]')
    grid = pd.date_range(lo, hi, freq='1min').values.astype('datetime64[ns]')
    idx = np.searchsorted(ts, grid, side='right') - 1
    keep = idx >= 0
    return idx[keep], grid[keep]


rows = []
t0 = time.time()
day, nsess = START, 0
while day <= END:
    if day.weekday() >= 5:
        day += timedelta(days=1); continue
    sym = tickmod.contract_for_cached('NQ', day)
    if sym is None:
        day += timedelta(days=1); continue
    t = tickmod.get_day_ticks(sym, day, include_overnight=True)
    if t is None or t.empty:
        day += timedelta(days=1); continue
    n = len(t)
    ts = t['ts_utc'].values.astype('datetime64[ns]')
    px = t['price'].to_numpy(dtype='float64')
    b = barmod.tick_bars(t, TPB)
    rth0_ts, rth1_ts = tickmod.session_bounds_utc(day)
    rth_i0 = int(t['ts_utc'].searchsorted(rth0_ts, side='left'))
    rth0 = rth0_ts.tz_localize(None).to_datetime64()
    rth1_i = int(np.searchsorted(ts, rth1_ts.tz_localize(None).to_datetime64(),
                                 side='right')) - 1

    prof_gx = profmod.developing_profile(t, b, TICK)
    levels = {}
    for edge in ('poc', 'vah', 'val'):
        levels[f'gx_{edge}'] = profmod.levels_in_force(prof_gx, b, n, edge=edge)
    if rth_i0 < n - 10:
        t_r = t.iloc[rth_i0:].reset_index(drop=True)
        b_r = barmod.tick_bars(t_r, TPB)
        prof_ny = profmod.developing_profile(t_r, b_r, TICK)
        for edge in ('poc', 'vah', 'val'):
            arr = np.full(n, np.nan)
            arr[rth_i0:] = profmod.levels_in_force(prof_ny, b_r, n - rth_i0, edge=edge)
            levels[f'ny_{edge}'] = arr
    else:
        for edge in ('poc', 'vah', 'val'):
            levels[f'ny_{edge}'] = np.full(n, np.nan)

    gi, gts = minute_grid(ts)
    if len(gi) < 15:
        day += timedelta(days=1); continue
    pg = px[gi]
    sess = str(day)

    for lname, Larr in levels.items():
        Lg = Larr[gi]
        dist = (pg - Lg) / TICK
        last_ev = None
        for k in range(6, len(gi)):
            if not (np.isfinite(Lg[k]) and np.isfinite(Lg[k - 1])):
                continue
            if abs(dist[k]) > TOUCH_TOL or abs(dist[k - 1]) <= TOUCH_TOL:
                continue
            if last_ev is not None and (gts[k] - last_ev) < np.timedelta64(DEDUP_M, 'm'):
                continue
            last_ev = gts[k]
            i = int(gi[k])
            lvl = float(Lg[k])
            adir = 1.0 if dist[k - 1] > 0 else -1.0

            j = k - 1
            lim = max(0, k - 180)
            while j >= lim and abs(Lg[j] - lvl) <= AGE_TOL * TICK:
                j -= 1
            age_min = float((gts[k] - gts[j + 1]) / np.timedelta64(60, 's'))

            ej = int(np.searchsorted(ts, ts[i] + np.timedelta64(WINDOW_S, 's'),
                                     side='right')) - 1
            ej = min(ej, n - 1)
            if ej <= i:
                continue
            win = px[i + 1:ej + 1]
            up_t = max(0.0, (win.max() - lvl) / TICK)     # furthest above level
            dn_t = max(0.0, (lvl - win.min()) / TICK)     # furthest below level
            net_t = (px[ej] - lvl) / TICK                 # net at +60m
            # in approach frame: continue-through vs bounce-back excursion
            thru_t = up_t if adir < 0 else dn_t           # travel-direction MFE
            back_t = dn_t if adir < 0 else up_t           # bounce-side MFE

            src, edge = lname.split('_')
            rows.append(dict(
                session=sess, src=src, edge=edge, level=lname,
                is_rth=bool(gts[k] >= rth0 and i <= rth1_i),
                test=('support' if adir > 0 else 'resistance'),
                age_min=age_min,
                up_t=up_t, dn_t=dn_t, net_t=net_t,
                thru_t=thru_t, back_t=back_t))
    nsess += 1
    if nsess % 40 == 0:
        print(f'{nsess} sessions  {len(rows)} touches  {time.time()-t0:.0f}s', flush=True)
    day += timedelta(days=1)

df = pd.DataFrame(rows)
df.to_parquet(f'{OUTDIR}/stable_level_excursion.parquet')
print('WROTE stable_level_excursion.parquet', df.shape, f'{time.time()-t0:.0f}s')
