"""Analysis for vah_snap_study.py output: does an upward VAH snap past price,
with price in the Globex upper band, predict resistance + downtrend?

Compares event cohorts (by VAH source and snap violence) against the
unconditional in-upper-band baseline on forward drift, VAH-break rate, and
retest-reject rate. Session-level clustering: means are also reported per
session to keep one wild day from carrying a cohort.

Usage: .venv/bin/python data/research/market-structure/analyze_vah_snap.py
"""
import numpy as np
import pandas as pd
rng = np.random.default_rng(7)


def perm_p(x, y, n_iter=2000):
    """Two-sided permutation p-value for difference in means."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    obs = abs(x.mean() - y.mean())
    pool = np.concatenate([x, y])
    cnt = 0
    for _ in range(n_iter):
        rng.shuffle(pool)
        if abs(pool[:len(x)].mean() - pool[len(x):].mean()) >= obs:
            cnt += 1
    return (cnt + 1) / (n_iter + 1)


def spearman(a, b):
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    r = float(np.corrcoef(ra, rb)[0, 1])
    n = len(ra)
    t = r * np.sqrt(max(n - 2, 1) / max(1e-12, 1 - r * r))
    from math import erf, sqrt
    pv = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))
    return r, pv

D = 'data/research/market-structure'
ev = pd.read_parquet(f'{D}/vah_snap_events.parquet')
bl = pd.read_parquet(f'{D}/vah_snap_baseline.parquet')

HORIZONS = ['fwd_15m', 'fwd_30m', 'fwd_60m', 'fwd_eod']


def describe(df, name, base=None):
    n = len(df)
    if n == 0:
        print(f'{name:38s}  n=0')
        return
    line = f'{name:38s}  n={n:5d}'
    for h in HORIZONS:
        x = df[h].dropna()
        m = x.mean()
        star = ''
        if base is not None and len(x) > 10:
            b = base[h].dropna()
            p = perm_p(x, b.sample(min(len(b), 3000), random_state=1))
            star = '**' if p < 0.01 else ('*' if p < 0.05 else '')
        line += f'  {h[4:]}:{m:+7.1f}{star:2s}'
    if 'broke_vah' in df:
        line += f'  brk:{df.broke_vah.mean():.2f}  rej:{df.retest_reject.mean():.2f}'
    print(line)


print('=== BASELINE: price in Globex upper band (1-min samples, every 5th) ===')
blr = bl[bl.is_rth]
describe(blr, 'RTH in-band baseline (all)')
describe(blr[blr.vah_above], '  gx VAH already above price')
describe(blr[~blr.vah_above], '  gx VAH below price')
describe(bl[~bl.is_rth], 'ON in-band baseline')

print('\n=== EVENTS: VAH relocates up past price, price in upper band ===')
for src, label in (('gx', 'Globex VAH'), ('ny', 'NY (RTH) VAH')):
    sub = ev[(ev.src == src) & ev.is_rth]
    print(f'\n-- {label}, RTH events --')
    describe(sub, 'all snaps', blr)
    for lo, hi, tag in ((1, 10, 'snap 1-10t'), (10, 25, 'snap 10-25t'),
                        (25, 50, 'snap 25-50t'), (50, 1e9, 'snap >=50t')):
        describe(sub[(sub.snap1_t >= lo) & (sub.snap1_t < hi)], f'  {tag}', blr)
    # violent by the 5-min measure
    describe(sub[sub.snap5_t >= 50], '  snap5m >=50t (violent)', blr)
    describe(sub[sub.vah_above_t >= 20], '  lands >=20t above price', blr)
    describe(sub[sub.vah_above_t < 20], '  lands <20t above price', blr)
    # time-of-day
    hm = sub.hm.str.slice(0, 2).astype(int)
    describe(sub[hm < 12], '  morning (<12:00)', blr)
    describe(sub[hm >= 12], '  afternoon (>=12:00)', blr)

gx_on = ev[(ev.src == 'gx') & ~ev.is_rth]
print('\n-- Globex VAH, overnight events --')
describe(gx_on, 'all snaps', bl[~bl.is_rth])

print('\n=== session-clustered check (per-session mean fwd_60m, gx RTH) ===')
for name, df, b in (('gx', ev[(ev.src == 'gx') & ev.is_rth], blr),
                    ('ny', ev[(ev.src == 'ny') & ev.is_rth], blr)):
    s_ev = df.groupby('session').fwd_60m.mean().dropna()
    s_bl = b.groupby('session').fwd_60m.mean().dropna()
    if len(s_ev) > 10:
        p = perm_p(s_ev, s_bl)
        print(f'{name}: {len(s_ev)} event-sessions mean {s_ev.mean():+.1f}t '
              f'vs {len(s_bl)} baseline-sessions {s_bl.mean():+.1f}t  p={p:.3f}')

print('\n=== violence vs outcome: rank corr (gx+ny RTH pooled) ===')
sub = ev[ev.is_rth].dropna(subset=['snap1_t', 'fwd_60m'])
for col in ('snap1_t', 'snap5_t', 'vah_above_t'):
    s = ev[ev.is_rth].dropna(subset=[col, 'fwd_60m'])
    r, p = spearman(s[col], s.fwd_60m)
    print(f'{col:12s} vs fwd_60m: rho={r:+.3f} p={p:.3f} (n={len(s)})')
