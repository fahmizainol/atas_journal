"""Does the tape I score against contain the fill I'm scoring? — preflight for proximity.py.

The negative control failed on the first run (stopped exits scored as 'level
attraction'), and the two ways that happens are both alignment, not method:

  contract   a journal row is labelled with the front month at EXPORT time, and
             the replay tapes live in two stores. Score a fill against the wrong
             contract's session and every distance is noise with a trend in it.

  instant    if the fill price does not match what was trading at the fill's own
             timestamp, the timestamps are off (tz, or a tape offset) and every
             'level' is being read at the wrong moment.

Both are answered by one number: |fill price - price on the tape at the fill's
timestamp|. Zero says aligned; anything structural says stop interpreting.

Usage: .venv/bin/python data/research/level-proximity/validate.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path[:0] = [os.path.join(ROOT, 'src'), ROOT,
                os.path.join(ROOT, 'data/research/level-proximity')]
import numpy as np
import pandas as pd

from journal.sim import ticks as tickmod
from proximity import load_trades  # noqa: E402


def main():
    tr = load_trades()
    rec = []
    for day, grp in tr.groupby('day'):
        sym = tickmod.contract_for_cached('NQ', day)
        rth = tickmod.cached_rth(sym, day) if sym else None
        if rth is None or rth.empty:
            rec.append({'day': day, 'sym': sym, 'n': len(grp), 'state': 'no ticks'})
            continue
        ns = pd.to_datetime(rth['ts_utc'], utc=True).astype('int64').to_numpy()
        px = rth['price'].to_numpy(dtype=float)
        lo, hi = float(px.min()), float(px.max())
        gaps = []
        for _, t in grp.iterrows():
            i = int(np.searchsorted(ns, pd.Timestamp(t['entry_ts']).value, 'right')) - 1
            if i < 0:
                gaps.append(np.nan)
                continue
            gaps.append(abs(float(t['open_price']) - float(px[i])))
        g = np.array(gaps, dtype=float)
        rec.append({
            'day': day, 'sym': sym, 'labelled': grp['instrument'].iloc[0],
            'n': len(grp), 'state': 'ok',
            'rng_lo': lo, 'rng_hi': hi,
            'out_of_range': float(((grp['open_price'] < lo) |
                                   (grp['open_price'] > hi)).mean()),
            'med_gap_pts': float(np.nanmedian(g)),
            'max_gap_pts': float(np.nanmax(g)) if np.isfinite(g).any() else np.nan,
        })
    df = pd.DataFrame(rec)
    ok = df[df['state'] == 'ok']
    print(df.to_string(index=False))
    print(f'\ndays: {len(df)}  scored: {len(ok)}  no-ticks: {(df["state"] != "ok").sum()}')
    if not ok.empty:
        print(f'trades on scorable days: {int(ok["n"].sum())} / {int(df["n"].sum())}')
        print(f'median |fill - tape| across days: {ok["med_gap_pts"].median():.2f} pts')
        bad = ok[ok['med_gap_pts'] > 2.0]
        print(f'days misaligned (median gap > 2 pts): {len(bad)} '
              f'covering {int(bad["n"].sum())} trades')
        print(f'fills outside the session range: '
              f'{(ok["out_of_range"] * ok["n"]).sum():.0f}')


if __name__ == '__main__':
    main()
