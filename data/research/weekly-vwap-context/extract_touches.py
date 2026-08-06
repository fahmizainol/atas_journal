"""Context-classified weekly-band touch extraction — every ±1σ touch, not just
the session's first, tagged with where price came from.

Motivated by hand observations on Feb 2025 (see docs/research/weekly-vwap-context.md):
  1. retest of +1σ from below AFTER price failed out of the upper band → rejects
  2. pullback onto +1σ from above after ACCEPTANCE in the upper band → bounces
  3. fresh traverse into +1σ from the mid / lower band → mixed
  4. mid-crossing count ~ rotational day; mid as S/R after band residence

The pooled weekly_vwap.py study scores only the first RTH touch approached from
the mid's side — it cannot see any of these cuts. This extractor works on full
Globex sessions (ON + RTH minute bars; the hand examples include overnight
touches) against the developing weekly bands, causally: each bar reads the band
value at its own last tick (end_idx), nothing forward.

Events are episode-ized with a re-arm rule (a new touch of a level only counts
after a full bar has traded clear of it by REARM_SIG weekly sigmas) so a choppy
hour hugging the band is one episode, not thirty.

Output: touches.parquet (one row per band-touch event, upper1/lower1/mid) and
sessions.parquet (one row per session: rotation + residence stats).

Usage: .venv/bin/python data/research/weekly-vwap-context/extract_touches.py
"""
import sys, time
sys.path.insert(0, 'src')
from datetime import date

import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod
from journal.sim import vwap as vwapmod
from journal.sim import weekly as weeklymod
from journal.sim.regime import minute_bars

SYMBOL = "NQ"
START, END = date(2025, 2, 1), date(2026, 7, 22)
OUT = 'data/research/weekly-vwap-context'

REARM_SIG = 0.25      # bar must clear the level by this many σ to re-arm it
RACE_SIG = 0.30       # reject/break race threshold, in σ at the touch
ORIGIN_LOOKBACK = 120  # minutes of history for the approach-origin read
WINDOWS = (30, 60, 120)


def _touch_events(lo, hi, lvl, std):
    """Bar indices of armed touches of a level (lo<=lvl<=hi), re-armed only
    after a full bar trades clear of the level by REARM_SIG sigmas."""
    events, armed = [], True
    for i in range(len(lo)):
        if lo[i] <= lvl[i] <= hi[i]:
            if armed:
                events.append(i)
                armed = False
        else:
            clear = lo[i] - lvl[i] if lo[i] > lvl[i] else lvl[i] - hi[i]
            if clear >= REARM_SIG * std[i]:
                armed = True
    return events


def _race(i, lo, hi, lvl_i, thr, window):
    """First-crossing race from bar i: does price trade thr below the level
    ('dn') or thr above it ('up') first inside the window? 'ambig' if a single
    bar spans both, 'none' if neither prints."""
    j = min(i + window, len(lo) - 1)
    for k in range(i, j + 1):
        d = lo[k] <= lvl_i - thr
        u = hi[k] >= lvl_i + thr
        if d and u:
            return 'ambig'
        if d:
            return 'dn'
        if u:
            return 'up'
    return 'none'


def _excursions(i, lo, hi, lvl_i, window):
    j = min(i + window, len(lo) - 1)
    return float(hi[i:j + 1].max() - lvl_i), float(lvl_i - lo[i:j + 1].min())


def _sigma_extreme(sig, i, kind):
    """min/max σ-position over the origin lookback before bar i (nan if i==0)."""
    a = sig[max(0, i - ORIGIN_LOOKBACK):i]
    if len(a) == 0:
        return np.nan
    return float(np.nanmin(a) if kind == 'min' else np.nanmax(a))


def session_events(bars, w, day, first_session, contract):
    """Classified touch events + the session summary row for one Globex day."""
    pos = bars['end_idx'].to_numpy()
    mid = w['mid'].to_numpy()[pos]
    std = w['std'].to_numpy()[pos]
    u1 = w['upper1'].to_numpy()[pos]
    u2 = w['upper2'].to_numpy()[pos]
    l1 = w['lower1'].to_numpy()[pos]
    l2 = w['lower2'].to_numpy()[pos]
    op = bars['open'].to_numpy()
    hi = bars['high'].to_numpy()
    lo = bars['low'].to_numpy()
    cl = bars['close'].to_numpy()
    ts = bars['ts_utc'].dt.tz_convert('America/New_York')
    is_rth = np.array([t.time() >= pd.Timestamp('09:30').time()
                       and t.time() < pd.Timestamp('16:00').time() for t in ts])

    with np.errstate(invalid='ignore', divide='ignore'):
        sig = (cl - mid) / std
    beyond_u1 = cl > u1          # closes resident beyond the band
    beyond_l1 = cl < l1
    touched_u2 = hi >= u2
    touched_l2 = lo <= l2

    rows = []
    for name, lvl, side in (('upper1', u1, 'upper'), ('lower1', l1, 'lower'),
                            ('mid', mid, 'mid')):
        for i in _touch_events(lo, hi, lvl, std):
            if not np.isfinite(lvl[i]) or not np.isfinite(std[i]) or std[i] <= 0:
                continue
            prev = cl[i - 1] if i > 0 else op[0]
            prev_lvl = lvl[i - 1] if i > 0 else lvl[0]
            approach = 'below' if prev < prev_lvl else 'above'
            thr = RACE_SIG * std[i]
            r = {
                'day': day.isoformat(), 'contract': contract,
                'month': day.isoformat()[:7],
                'first_session': first_session,
                'level': name, 'bar': i,
                'ts_et': ts.iloc[i].isoformat(),
                'min_after_gx_open': i, 'is_rth': bool(is_rth[i]),
                'approach': approach,
                'level_px': float(lvl[i]), 'std': float(std[i]),
                # session-so-far context (bars < i, causal)
                'res_beyond_u1_min': int(beyond_u1[:i].sum()),
                'res_beyond_l1_min': int(beyond_l1[:i].sum()),
                'touched_u2_before': bool(touched_u2[:i].any()),
                'touched_l2_before': bool(touched_l2[:i].any()),
                # σ-extremes over the 120-min approach lookback (origin depth)
                'max_sig_before': _sigma_extreme(sig, i, 'max'),
                'min_sig_before': _sigma_extreme(sig, i, 'min'),
                'race60': _race(i, lo, hi, lvl[i], thr, 60),
                # conservative: race from the NEXT bar — the touch bar's
                # approach-side extreme may predate the touch within the bar
                'race60_ex': _race(min(i + 1, len(lo) - 1), lo, hi, lvl[i],
                                   thr, 59),
                'race_thr_pts': thr,
            }
            for wdw in WINDOWS:
                up, dn = _excursions(i, lo, hi, lvl[i], wdw)
                r[f'up_pts_{wdw}'] = round(up, 2)
                r[f'dn_pts_{wdw}'] = round(dn, 2)
            # did the weekly mid print within 60m (band levels only)
            j = min(i + 60, len(lo) - 1)
            if side == 'upper':
                r['hit_mid_60'] = bool((lo[i:j + 1] <= mid[i:j + 1]).any())
            elif side == 'lower':
                r['hit_mid_60'] = bool((hi[i:j + 1] >= mid[i:j + 1]).any())
            else:
                r['hit_mid_60'] = True
            rows.append(r)

    dsig = np.sign(cl - mid)
    crosses = int(np.sum(dsig[1:] * dsig[:-1] < 0))
    srow = {
        'day': day.isoformat(), 'month': day.isoformat()[:7],
        'first_session': first_session, 'n_bars': len(bars),
        'mid_crosses': crosses,
        'pct_above_u1': float(beyond_u1.mean()),
        'pct_below_l1': float(beyond_l1.mean()),
        'range_pts': float(hi.max() - lo.min()),
        'gx_drift_pts': float(cl[-1] - op[0]),
        'rot': float(abs(cl[-1] - op[0]) / (hi.max() - lo.min()))
               if hi.max() > lo.min() else np.nan,
    }
    return rows, srow


def main():
    t0 = time.time()
    days = tickmod.session_dates(START, END)
    touches, sessions, skipped = [], [], []
    for n, day in enumerate(days):
        contract = tickmod.contract_for_cached(SYMBOL, day)
        rth = tickmod.cached_rth(contract, day) if contract else None
        on = tickmod.cached_overnight(contract, day) if contract else None
        if rth is None or rth.empty or on is None or on.empty:
            skipped.append((day.isoformat(), 'ticks'))
            continue
        seed = weeklymod.weekly_seed(SYMBOL, day)
        if seed is None:
            skipped.append((day.isoformat(), 'week hole'))
            continue
        full = pd.concat([on, rth], ignore_index=True)
        w = vwapmod.vwap_bands(full, seed=seed)
        bars = minute_bars(full)
        if bars.empty or len(bars) < 60:
            skipped.append((day.isoformat(), 'bars'))
            continue
        rows, srow = session_events(bars, w, day, seed == (0.0, 0.0, 0.0),
                                    contract)
        touches.extend(rows)
        sessions.append(srow)
        if n % 25 == 0:
            print(f'{n}/{len(days)} {day} events={len(touches)} '
                  f'({time.time()-t0:.0f}s)', flush=True)

    tdf = pd.DataFrame(touches)
    sdf = pd.DataFrame(sessions)
    tdf.to_parquet(f'{OUT}/touches.parquet')
    sdf.to_parquet(f'{OUT}/sessions.parquet')
    print(f'\nsessions={len(sdf)} skipped={len(skipped)} events={len(tdf)} '
          f'in {time.time()-t0:.0f}s')
    print(tdf.groupby(['level', 'approach']).size())
    from collections import Counter
    print('skips:', Counter(w for _, w in skipped))


if __name__ == '__main__':
    main()
