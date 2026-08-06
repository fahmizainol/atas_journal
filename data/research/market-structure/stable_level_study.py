"""Does a STABLE (non-moving / "flat") developing VP level hold as support or
resistance better than a freshly-relocated one?

Contrast to the VAH-snap study: there the level had just leapt; here we ask
whether a level that has been *sitting still* acts as S/R when price arrives.

Design, all causal (levels_in_force = the reading a trader has at that tick):

  For each cached NQ session, on a 1-minute grid over the ON+RTH tick frame,
  for six developing levels -- {Globex, NY} x {POC, VAH, VAL} -- sampled at each
  minute's last tick:

  TOUCH event: price arrives within 6t of the level (prev minute was farther).
    approach_dir = sign(prev_price - level): +1 from above (support test),
    -1 from below (resistance test). 30-min dedup per (session, level).

  STABILITY at the touch (the independent variable):
    age_min   = minutes since the level last sat > 2t from its touch value
                (backward scan on the minute grid, capped 180 min)
    drift_30m = max |level move| over the prior 30 min (ticks) -- flatness

  OUTCOME, first-to-hit on ticks within 60 min of the touch:
    'break'  price traded 12t past the level in the travel direction first,
    'reject' price fell 15t back on the approach side first (level HELD),
    'none'   neither within 60 min (chop).
    fwd_60m  signed ticks in the travel/continue-through direction (+ = broke).

  Controls recorded to catch the obvious confound (stable levels may simply be
  farther from price): dist_prev_t (gap the minute before the touch),
  approach_vel (ticks/min over the prior 5 min), band_pos.

Usage: .venv/bin/python data/research/market-structure/stable_level_study.py
Writes stable_level_events.parquet next to this file.
"""
import os, sys, time
from datetime import date, timedelta

sys.path.insert(0, 'src')
import numpy as np
import pandas as pd
from journal.sim import ticks as tickmod
from journal.sim import bars as barmod
from journal.sim import profile as profmod
from journal.sim import vwap as vwapmod

TICK = 0.25
TPB = 500
START, END = date(2025, 2, 3), date(2026, 6, 30)
TOUCH_TOL = 6      # ticks: within this = a touch
# BREAK_B / REJECT_R are env-overridable so the sweep can be re-run at other
# thresholds (e.g. a symmetric 30/30t) without touching the canonical defaults.
BREAK_B = int(os.environ.get('SL_BREAK_B', 12))    # ticks past the level = broke
REJECT_R = int(os.environ.get('SL_REJECT_R', 15))  # ticks back = held
OUT_SUFFIX = os.environ.get('SL_OUT_SUFFIX', '')   # e.g. '_30t' -> separate file
AGE_TOL = 2        # ticks: level "moved" if it leaves +/- this of its touch value
WINDOW_S = 3600    # 60-min outcome window
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


def first_to_hit(win, up_lvl, dn_lvl):
    """Index of first tick reaching up_lvl or dn_lvl; ('up'|'dn'|None, idx)."""
    up = np.nonzero(win >= up_lvl)[0]
    dn = np.nonzero(win <= dn_lvl)[0]
    iu = up[0] if len(up) else np.inf
    idn = dn[0] if len(dn) else np.inf
    if iu == np.inf and idn == np.inf:
        return None, -1
    return ('up', int(iu)) if iu < idn else ('dn', int(idn))


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
    bands = vwapmod.vwap_bands(t)
    up1 = bands['upper1'].to_numpy(); up2 = bands['upper2'].to_numpy()
    lo1 = bands['lower1'].to_numpy(); lo2 = bands['lower2'].to_numpy()
    rth0_ts, rth1_ts = tickmod.session_bounds_utc(day)
    rth_i0 = int(t['ts_utc'].searchsorted(rth0_ts, side='left'))
    rth0 = rth0_ts.tz_localize(None).to_datetime64()

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
        dist = (pg - Lg) / TICK          # signed ticks, + = price above level
        last_ev = None
        for k in range(6, len(gi)):
            if not (np.isfinite(Lg[k]) and np.isfinite(Lg[k - 1])):
                continue
            if abs(dist[k]) > TOUCH_TOL or abs(dist[k - 1]) <= TOUCH_TOL:
                continue  # not an arrival
            if last_ev is not None and (gts[k] - last_ev) < np.timedelta64(DEDUP_M, 'm'):
                continue
            last_ev = gts[k]
            i = int(gi[k])
            lvl = float(Lg[k])
            adir = 1.0 if dist[k - 1] > 0 else -1.0   # +from above, -from below

            # stability: age + 30-min flatness on the minute grid
            j = k - 1
            lim = max(0, k - 180)
            while j >= lim and abs(Lg[j] - lvl) <= AGE_TOL * TICK:
                j -= 1
            age_min = float((gts[k] - gts[j + 1]) / np.timedelta64(60, 's'))
            w30 = Lg[max(0, k - 30):k + 1]
            drift_30m = float(np.nanmax(np.abs(w30 - lvl)) / TICK) if len(w30) else np.nan
            vel = float((pg[k] - pg[max(0, k - 5)]) / TICK / 5.0)

            # outcome: first-to-hit on ticks
            ej = int(np.searchsorted(ts, ts[i] + np.timedelta64(WINDOW_S, 's'),
                                     side='right')) - 1
            ej = min(ej, n - 1)
            outcome, thit, fwd60 = 'none', np.nan, np.nan
            if ej > i:
                win = px[i + 1:ej + 1]
                if adir < 0:   # from below: break=up, reject=down
                    side, hidx = first_to_hit(win, lvl + BREAK_B * TICK, lvl - REJECT_R * TICK)
                    outcome = 'break' if side == 'up' else ('reject' if side == 'dn' else 'none')
                else:          # from above: break=down, reject=up
                    side, hidx = first_to_hit(win, lvl + REJECT_R * TICK, lvl - BREAK_B * TICK)
                    outcome = 'break' if side == 'dn' else ('reject' if side == 'up' else 'none')
                if hidx >= 0:
                    thit = float((ts[i + 1 + hidx] - ts[i]) / np.timedelta64(60, 's'))
                j60 = int(np.searchsorted(ts, ts[i] + np.timedelta64(3600, 's'),
                                          side='right')) - 1
                j60 = min(j60, n - 1)
                fwd60 = float(-adir * (px[j60] - lvl) / TICK)  # + = continued through

            src, edge = lname.split('_')
            bpos = np.nan
            if np.isfinite(up1[i]) and up2[i] > up1[i] and up1[i] <= px[i] <= up2[i]:
                bpos = (px[i] - up1[i]) / (up2[i] - up1[i])
            rows.append(dict(
                session=sess, src=src, edge=edge, level=lname,
                hm=pd.Timestamp(gts[k]).tz_localize('UTC').tz_convert(
                    'America/New_York').strftime('%H:%M'),
                is_rth=bool(gts[k] >= rth0 and i <= int(np.searchsorted(
                    ts, rth1_ts.tz_localize(None).to_datetime64(), side='right')) - 1),
                approach=('above' if adir > 0 else 'below'),
                test=('support' if adir > 0 else 'resistance'),
                age_min=age_min, drift_30m_t=drift_30m,
                dist_prev_t=float(abs(dist[k - 1])), approach_vel=vel,
                band_pos=float(bpos),
                outcome=outcome, t_to_outcome=thit, fwd_60m=fwd60))
    nsess += 1
    if nsess % 20 == 0:
        print(f'{nsess} sessions  {len(rows)} touches  {time.time()-t0:.0f}s', flush=True)
    day += timedelta(days=1)

df = pd.DataFrame(rows)
df['held'] = (df.outcome == 'reject').astype(int)
df['broke'] = (df.outcome == 'break').astype(int)
df['decisive'] = df.outcome.isin(['reject', 'break']).astype(int)
df.to_parquet(f'{OUTDIR}/stable_level_events{OUT_SUFFIX}.parquet')
print('WROTE', f'stable_level_events{OUT_SUFFIX}.parquet', df.shape,
      f'(break={BREAK_B}t reject={REJECT_R}t)', f'{time.time()-t0:.0f}s')
