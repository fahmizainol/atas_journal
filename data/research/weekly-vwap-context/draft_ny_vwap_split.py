"""weekly-upper1-accepted-pullback-long: NY-session cut, split by where the
entry sits relative to the *NY* VWAP (a different anchor than the weekly +1σ
the event is defined on).

For every trade whose entry falls in RTH, sample the developing NY-anchored
VWAP mid at the entry instant and classify:
  upper half : entry price >= NY VWAP mid  (price trading above the day's NY VWAP)
  lower half : entry price <  NY VWAP mid

Then the draft's own outcome stats (to-target rate, avg/total R) per half.
A weekly +1σ touch can happen on either side of the NY VWAP, so this asks
whether the accepted-pullback buy behaves differently when it's above vs below
the intraday NY mean.

Usage: .venv/bin/python data/research/weekly-vwap-context/draft_ny_vwap_split.py [slug-substring]
       (default slug-substring: 'upper1-accepted'; e.g. pass 'lower1-deep' for the seed draft)
"""
import sys
sys.path.insert(0, 'src')
import glob
import json

import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod
from journal.sim import vwap as vwapmod

_MATCH = sys.argv[1] if len(sys.argv) > 1 else 'upper1-accepted'
SNAP = [p for p in glob.glob('data/cache/drafts/*.json') if _MATCH in p][0]

_cache = {}


def ny_at_entry(day_iso, entry_iso):
    """(NY vwap mid, NY std) at the entry instant, or None if pre-RTH / no data."""
    if day_iso not in _cache:
        day = pd.Timestamp(day_iso).date()
        contract = tickmod.contract_for_cached('NQ', day)
        rth = tickmod.cached_rth(contract, day) if contract else None
        if rth is None or rth.empty:
            _cache[day_iso] = None
        else:
            w = vwapmod.vwap_bands(rth)
            _cache[day_iso] = (rth['ts_utc'].astype('int64').to_numpy(),
                               w['mid'].to_numpy(), w['std'].to_numpy())
    c = _cache[day_iso]
    if c is None:
        return None
    ts_ns, mid, std = c
    e = pd.Timestamp(entry_iso).value
    if e < ts_ns[0]:
        return None                      # entry before the bell — not NY-anchored
    i = min(int(np.searchsorted(ts_ns, e, side='right')) - 1, len(mid) - 1)
    return float(mid[i]), float(std[i])


def stats(rows, label):
    if not rows:
        print(f"{label:22s} n=  0  (empty)")
        return
    r = np.array([x['r_multiple'] for x in rows])
    reasons = [x['exit_reason'] for x in rows]
    decided = sum(x in ('target', 'stop') for x in reasons)
    wr = reasons.count('target') / decided if decided else float('nan')
    print(f"{label:22s} n={len(rows):3d}  to-target={wr:5.3f}  "
          f"avgR={r.mean():+.3f}  totalR={r.sum():+6.2f}  "
          f"(tgt {reasons.count('target')}/stop {reasons.count('stop')}/"
          f"time {reasons.count('time')})")


def main():
    d = json.load(open(SNAP))
    print(f"draft {d['run_id']}  —  {len(d['trades'])} trades total\n")

    ny, dropped = [], 0
    for t in d['trades']:
        if not t['is_rth']:
            continue
        pos = ny_at_entry(t['day'], t['entry_ts_utc'])
        if pos is None:
            dropped += 1
            continue
        mid, std = pos
        t = {**t, 'ny_mid': mid, 'ny_std': std,
             'ny_sig': (t['avg_entry'] - mid) / std if std > 0 else 0.0}
        ny.append(t)

    print(f"NY-session trades: {len(ny)}  (dropped {dropped} with no NY anchor at entry)\n")
    stats(ny, 'NY session (all)')

    upper = [t for t in ny if t['avg_entry'] >= t['ny_mid']]
    lower = [t for t in ny if t['avg_entry'] < t['ny_mid']]
    print()
    stats(upper, 'NY upper half (>= mid)')
    stats(lower, 'NY lower half (< mid)')

    # How far above/below the NY mid, in NY-σ — sanity on the split.
    su = np.array([t['ny_sig'] for t in upper])
    sl = np.array([t['ny_sig'] for t in lower])
    if len(su):
        print(f"\nentry vs NY mid (σ): upper median +{np.median(su):.2f} "
              f"(max +{su.max():.2f})", end='')
    else:
        print("\nentry vs NY mid (σ): upper (none)", end='')
    if len(sl):
        print(f" | lower median {np.median(sl):.2f} (min {sl.min():.2f})")
    else:
        print(" | lower (none)")

    # Also a finer band cut, since 'upper half' lumps just-above with far-above.
    print("\nby NY-VWAP band zone (entry in σ from NY mid):")
    zones = [('below -1σ', -1e9, -1.0), ('-1σ..mid', -1.0, 0.0),
             ('mid..+1σ', 0.0, 1.0), ('above +1σ', 1.0, 1e9)]
    for name, lo, hi in zones:
        grp = [t for t in ny if lo <= t['ny_sig'] < hi]
        if grp:
            stats(grp, f'  {name}')

    # --- split-half robustness -------------------------------------------
    # The edge is post-hoc; split the NY trades at the median date and check the
    # two candidate cohorts (below NY mid, below NY -1σ) survive in BOTH halves.
    # A real effect holds its sign and rough size across halves; a mined one
    # concentrates in one half.
    ny_sorted = sorted(ny, key=lambda t: t['day'])
    mid_day = ny_sorted[len(ny_sorted) // 2]['day']
    half1 = [t for t in ny_sorted if t['day'] < mid_day]
    half2 = [t for t in ny_sorted if t['day'] >= mid_day]
    print(f"\n=== split-half at {mid_day} "
          f"(half1 {half1[0]['day']}..{half1[-1]['day']}, "
          f"half2 {half2[0]['day']}..{half2[-1]['day']}) ===")

    for label, cond in (('below NY mid', lambda t: t['ny_sig'] < 0.0),
                        ('below NY -1σ', lambda t: t['ny_sig'] < -1.0)):
        print(f"\n{label}:")
        stats([t for t in half1 if cond(t)], '  half1')
        stats([t for t in half2 if cond(t)], '  half2')

    # And the complement, to confirm the OTHER side stays dead in both halves.
    print("\nat/above NY mid (should stay negative both halves):")
    stats([t for t in half1 if t['ny_sig'] >= 0.0], '  half1')
    stats([t for t in half2 if t['ny_sig'] >= 0.0], '  half2')

    # Per-calendar-year, since the sample spans Feb-2025..Jun-2026.
    print("\n=== by calendar year (below NY -1σ) ===")
    for yr in ('2025', '2026'):
        stats([t for t in ny if t['ny_sig'] < -1.0 and t['day'].startswith(yr)],
              f'  {yr}')


if __name__ == '__main__':
    main()
