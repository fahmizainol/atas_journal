"""Does level stability raise the hold (S/R rejection) rate?

hold rate = rejects / (rejects + breaks), decisive touches only. Compared
across age buckets, level types, and against the approach-distance confound.

Usage: .venv/bin/python data/research/market-structure/analyze_stable_level.py
"""
import os
import numpy as np
import pandas as pd

D = 'data/research/market-structure'
SUFFIX = os.environ.get('SL_OUT_SUFFIX', '')   # '' = canonical 12/15t, '_30t' = the sweep
df = pd.read_parquet(f'{D}/stable_level_events{SUFFIX}.parquet')
print(f'--- analyzing stable_level_events{SUFFIX}.parquet ---')
rng = np.random.default_rng(11)


def perm_p_rate(a, b, n_iter=4000):
    """Two-sided permutation p for difference in mean of 0/1 arrays a vs b."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    obs = abs(a.mean() - b.mean())
    pool = np.concatenate([a, b]); na = len(a); c = 0
    for _ in range(n_iter):
        rng.shuffle(pool)
        if abs(pool[:na].mean() - pool[na:].mean()) >= obs:
            c += 1
    return (c + 1) / (n_iter + 1)


def hold_line(sub, label):
    dec = sub[sub.decisive == 1]
    if len(dec) < 15:
        print(f'{label:34s} n={len(sub):5d}  (too few decisive)'); return None
    hr = dec.held.mean()
    none = 1 - sub.decisive.mean()
    print(f'{label:34s} n={len(sub):5d}  decisive={len(dec):5d}  '
          f'HOLD={hr:5.1%}  break={1-hr:5.1%}  none={none:4.0%}  '
          f'fwd60={sub.fwd_60m.mean():+6.1f}')
    return dec


print('=== overall (RTH touches) ===')
r = df[df.is_rth]
base = hold_line(r, 'all RTH touches')
print('  hold=price rejected off the level; break=traded through; higher hold = better S/R\n')

print('=== by level age bucket (stability -> does it hold more?) ===')
bins = [(0, 5, 'fresh <5m'), (5, 15, '5-15m'), (15, 30, '15-30m'),
        (30, 60, '30-60m'), (60, 120, '60-120m'), (120, 1e9, 'entrenched >120m')]
buckets = {}
for lo, hi, tag in bins:
    sub = r[(r.age_min >= lo) & (r.age_min < hi)]
    buckets[tag] = hold_line(sub, tag)
fresh = r[r.age_min < 15]; stab = r[r.age_min >= 60]
fd, sd = fresh[fresh.decisive == 1], stab[stab.decisive == 1]
p = perm_p_rate(fd.held, sd.held)
print(f'\nfresh(<15m) hold={fd.held.mean():.1%} (n={len(fd)}) vs '
      f'stable(>=60m) hold={sd.held.mean():.1%} (n={len(sd)})  perm p={p:.4f}')

print('\n=== flatness (drift_30m) — independent of the age clock ===')
for lo, hi, tag in [(0, 2, 'flat <2t/30m'), (2, 8, '2-8t'), (8, 20, '8-20t'),
                    (20, 1e9, 'active >20t')]:
    hold_line(r[(r.drift_30m_t >= lo) & (r.drift_30m_t < hi)], tag)

print('\n=== confound check: is it stability or just distance? ===')
print('hold rate in age x approach-distance cells (decisive only):')
r2 = r[r.decisive == 1].copy()
r2['agec'] = pd.cut(r2.age_min, [0, 15, 60, 1e9], labels=['fresh', 'mid', 'stable'])
r2['distc'] = pd.cut(r2.dist_prev_t, [0, 12, 25, 1e9], labels=['near', 'mid', 'far'])
piv = r2.pivot_table(index='agec', columns='distc', values='held',
                     aggfunc='mean', observed=True)
cnt = r2.pivot_table(index='agec', columns='distc', values='held',
                     aggfunc='size', observed=True)
print((piv * 100).round(1).to_string())
print('cell counts:'); print(cnt.to_string())

print('\n=== by level type (POC vs edges), RTH, stable >=60m ===')
for lv in ['gx_poc', 'ny_poc', 'gx_vah', 'ny_vah', 'gx_val', 'ny_val']:
    hold_line(r[(r.level == lv) & (r.age_min >= 60)], f'{lv} stable')

print('\n=== support vs resistance, stable >=60m ===')
for tst in ('support', 'resistance'):
    hold_line(r[(r.test == tst) & (r.age_min >= 60)], f'{tst} stable')

print('\n=== split-half (stable >=60m hold rate) ===')
sd = sd.assign(dt=pd.to_datetime(sd.session))
h1 = sd[sd.dt < '2025-11-01']; h2 = sd[sd.dt >= '2025-11-01']
print(f'H1 hold={h1.held.mean():.1%} (n={len(h1)})  H2 hold={h2.held.mean():.1%} (n={len(h2)})')

print('\n=== dose-response: spearman(age, held) among decisive RTH ===')
dec = r[r.decisive == 1]
ra = dec.age_min.rank().to_numpy(); rh = dec.held.rank().to_numpy()
rho = np.corrcoef(ra, rh)[0, 1]
print(f'rho={rho:+.3f}  (n={len(dec)})')
