"""Event study: developing VAH snapping upward past price while price sits in
the Globex upper band (dev1-dev2).

Question (user hypothesis): when the NY/Globex VAH violently relocates to above
price while price is inside the upper band, does that VAH act as resistance and
mark the start of a downtrend?

Design, all causal (levels_in_force = the reading a trader has at that tick):

  For each cached NQ session, on a 1-minute grid over the ON+RTH tick frame:
    bands  = Globex-anchored VWAP dev bands (vwap.vwap_bands over the full frame
             -- the same bands the drift-fade engine trades)
    gx VAH = developing profile over the full ON+RTH frame (Globex VAH)
    ny VAH = developing profile over the RTH slice only (NY VAH), RTH minutes only

  EVENT (per VAH source): between minute j-1 and j the VAH crosses from below
  price to above price, while price at j is inside [upper1, upper2]. Violence is
  measured, not assumed: snap1_t = VAH_j - VAH_{j-1} in ticks, snap5_t over 5
  minutes -- analysis buckets by size instead of picking a threshold. Events
  within 30 min of a prior same-source event in the session are dropped.

  BASELINE: every 5th minute where price is inside the upper band (no VAH
  condition), skipping minutes within 30 min after an event. This is the
  unconditional "price is in the upper band" drift the event cohort must beat.

  OUTCOMES (signed ticks, + = up): fwd_15m/30m/60m, fwd_eod (RTH close),
  max_up_60m / max_dn_60m, broke_vah (traded >= event VAH + 2t within 60m),
  retest_reject (touched within 8t of VAH then closed a minute >= 20t below it,
  within 60m).

Usage: .venv/bin/python data/research/market-structure/vah_snap_study.py
Writes vah_snap_events.parquet + vah_snap_baseline.parquet next to this file.
"""
import sys, time
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
OUTDIR = 'data/research/market-structure'


def minute_grid(ts):
    """Tick indices at 1-minute boundaries (last tick at or before each minute)."""
    lo = pd.Timestamp(ts[0]).ceil('1min')
    hi = pd.Timestamp(ts[-1]).floor('1min')
    if hi <= lo:
        return np.array([], dtype='int64'), np.array([], dtype='datetime64[ns]')
    grid = pd.date_range(lo, hi, freq='1min').values.astype('datetime64[ns]')
    idx = np.searchsorted(ts, grid, side='right') - 1
    keep = idx >= 0
    return idx[keep], grid[keep]


def fwd_outcomes(px, ts, i, p0, vah0, eod_i):
    """Forward outcome dict from tick i / price p0, event VAH vah0."""
    out = {}
    t0 = ts[i]
    for mins, tag in ((15, '15m'), (30, '30m'), (60, '60m')):
        j = int(np.searchsorted(ts, t0 + np.timedelta64(mins * 60, 's'), side='right')) - 1
        j = min(j, eod_i)
        out[f'fwd_{tag}'] = (px[j] - p0) / TICK if j > i else np.nan
    out['fwd_eod'] = (px[eod_i] - p0) / TICK if eod_i > i else np.nan
    j60 = int(np.searchsorted(ts, t0 + np.timedelta64(3600, 's'), side='right')) - 1
    j60 = min(j60, eod_i)
    if j60 > i:
        w = px[i + 1:j60 + 1]
        out['max_up_60m'] = (w.max() - p0) / TICK
        out['max_dn_60m'] = (w.min() - p0) / TICK
        out['broke_vah'] = int(np.isfinite(vah0) and w.max() >= vah0 + 2 * TICK)
        # retest-reject: got within 8t of VAH, later printed >= 20t below VAH
        near = np.nonzero(w >= vah0 - 8 * TICK)[0]
        rej = 0
        if np.isfinite(vah0) and len(near):
            after = w[near[0]:]
            rej = int(after.min() <= vah0 - 20 * TICK and w.max() < vah0 + 2 * TICK)
        out['retest_reject'] = rej
    else:
        out.update(max_up_60m=np.nan, max_dn_60m=np.nan,
                   broke_vah=np.nan, retest_reject=np.nan)
    return out


events, baseline = [], []
t0 = time.time()
day, nsess = START, 0
while day <= END:
    if day.weekday() >= 5:
        day += timedelta(days=1)
        continue
    sym = tickmod.contract_for_cached('NQ', day)
    if sym is None:
        day += timedelta(days=1)
        continue
    t = tickmod.get_day_ticks(sym, day, include_overnight=True)
    if t is None or t.empty:
        day += timedelta(days=1)
        continue
    n = len(t)
    ts = t['ts_utc'].values.astype('datetime64[ns]')
    px = t['price'].to_numpy(dtype='float64')
    b = barmod.tick_bars(t, TPB)
    bands = vwapmod.vwap_bands(t)
    up1 = bands['upper1'].to_numpy()
    up2 = bands['upper2'].to_numpy()
    rth0_ts, rth1_ts = tickmod.session_bounds_utc(day)
    rth_i0 = int(t['ts_utc'].searchsorted(rth0_ts, side='left'))
    eod_i = int(t['ts_utc'].searchsorted(rth1_ts, side='right')) - 1
    eod_i = min(max(eod_i, 0), n - 1)

    prof_gx = profmod.developing_profile(t, b, TICK)
    vah_gx = profmod.levels_in_force(prof_gx, b, n, edge='vah')

    vah_ny = np.full(n, np.nan)
    if rth_i0 < n - 10:
        t_r = t.iloc[rth_i0:].reset_index(drop=True)
        b_r = barmod.tick_bars(t_r, TPB)
        prof_ny = profmod.developing_profile(t_r, b_r, TICK)
        vah_ny[rth_i0:] = profmod.levels_in_force(prof_ny, b_r, n - rth_i0, edge='vah')

    gi, gts = minute_grid(ts)
    if len(gi) < 10:
        day += timedelta(days=1)
        continue
    in_band = (px[gi] >= up1[gi]) & (px[gi] <= up2[gi]) & np.isfinite(up1[gi])
    sess = str(day)

    for src, vah in (('gx', vah_gx), ('ny', vah_ny)):
        v = vah[gi]
        p = px[gi]
        last_ev_ts = None
        for k in range(5, len(gi)):
            if not in_band[k]:
                continue
            if not (np.isfinite(v[k]) and np.isfinite(v[k - 1])):
                continue
            # VAH relocates upward and crosses from below price to above price.
            # The v[k] > v[k-1] guard keeps out the other way this cross happens
            # (price dropping through a static VAH) — that's a pullback, not a snap.
            if not (v[k - 1] <= p[k - 1] and v[k] > p[k] and v[k] > v[k - 1] + TICK):
                continue
            if last_ev_ts is not None and (gts[k] - last_ev_ts) < np.timedelta64(30, 'm'):
                continue
            last_ev_ts = gts[k]
            i = int(gi[k])
            rec = dict(session=sess, src=src,
                       hm=pd.Timestamp(gts[k]).tz_localize('UTC').tz_convert(
                           'America/New_York').strftime('%H:%M'),
                       is_rth=bool(gts[k] >= rth0_ts.tz_localize(None).to_datetime64()
                                   and i <= eod_i),
                       price=p[k],
                       snap1_t=(v[k] - v[k - 1]) / TICK,
                       snap5_t=(v[k] - v[k - 5]) / TICK if np.isfinite(v[k - 5]) else np.nan,
                       vah_above_t=(v[k] - p[k]) / TICK,
                       band_pos=(p[k] - up1[i]) / max(up2[i] - up1[i], 1e-9))
            rec.update(fwd_outcomes(px, ts, i, p[k], v[k], eod_i))
            events.append(rec)

    # baseline: every 5th in-band minute, gx VAH context recorded
    for k in range(5, len(gi), 5):
        if not in_band[k]:
            continue
        i = int(gi[k])
        rec = dict(session=sess,
                   hm=pd.Timestamp(gts[k]).tz_localize('UTC').tz_convert(
                       'America/New_York').strftime('%H:%M'),
                   is_rth=bool(gts[k] >= rth0_ts.tz_localize(None).to_datetime64()
                               and i <= eod_i),
                   price=px[i],
                   vah_above=bool(np.isfinite(vah_gx[i]) and vah_gx[i] > px[i]),
                   band_pos=(px[i] - up1[i]) / max(up2[i] - up1[i], 1e-9))
        rec.update(fwd_outcomes(px, ts, i, px[i], vah_gx[i], eod_i))
        baseline.append(rec)

    nsess += 1
    if nsess % 20 == 0:
        print(f'{nsess} sessions  {len(events)} events  {time.time() - t0:.0f}s',
              flush=True)
    day += timedelta(days=1)

ev = pd.DataFrame(events)
bl = pd.DataFrame(baseline)
ev.to_parquet(f'{OUTDIR}/vah_snap_events.parquet')
bl.to_parquet(f'{OUTDIR}/vah_snap_baseline.parquet')
print('WROTE', ev.shape, bl.shape, f'{time.time() - t0:.0f}s')
