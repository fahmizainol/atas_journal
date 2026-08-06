"""After a touch, how far & which way -- broken down BY LEVEL.

Reads stable_level_excursion.parquet. For each developing VP level, reports the
60-minute excursion shape: net direction split, median net, and the typical
up/down envelope (both raw and in the approach frame: continue-through vs
bounce-back).

Usage: .venv/bin/python data/research/market-structure/analyze_excursion.py
"""
import numpy as np
import pandas as pd

D = 'data/research/market-structure'
df = pd.read_parquet(f'{D}/stable_level_excursion.parquet')
r = df[df.is_rth].copy()


def block(sub, label):
    n = len(sub)
    if n < 20:
        print(f'{label:16s} n={n:5d}  (too few)'); return
    up = (sub.net_t > 0).mean()
    print(f'{label:16s} n={n:5d}  '
          f'up/down={up:4.0%}/{1-up:4.0%}  '
          f'net med={sub.net_t.median():+6.1f}t mean={sub.net_t.mean():+6.1f}t  '
          f'|  env  up={sub.up_t.median():5.1f}t  dn={sub.dn_t.median():5.1f}t  '
          f'|  thru={sub.thru_t.median():5.1f}t back={sub.back_t.median():5.1f}t')


print('=== excursion after a touch, RTH, BY LEVEL ===')
print('up/down = net@60m sign;  env = raw max up / max down reached (ticks);')
print('thru/back = approach-frame: continue-through vs bounce-back MFE (ticks)\n')
block(r, 'ALL')
print()
for lv in ['gx_poc', 'ny_poc', 'gx_vah', 'ny_vah', 'gx_val', 'ny_val']:
    block(r[r.level == lv], lv)

print('\n=== same, split by approach (support vs resistance test) ===')
for lv in ['gx_poc', 'ny_poc', 'gx_vah', 'ny_vah', 'gx_val', 'ny_val']:
    for tst in ('support', 'resistance'):
        block(r[(r.level == lv) & (r.test == tst)], f'{lv}/{tst[:3]}')
    print()

print('=== net@60m: is any level directionally biased? (t-stat vs 0) ===')
for lv in ['gx_poc', 'ny_poc', 'gx_vah', 'ny_vah', 'gx_val', 'ny_val']:
    x = r[r.level == lv].net_t.to_numpy()
    if len(x) < 20:
        continue
    tstat = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
    print(f'{lv:10s} mean net={x.mean():+6.2f}t  t={tstat:+5.2f}  n={len(x)}')

print('\n=== thru vs back at each level: does price continue or reverse more? ===')
print('(ratio >1 = travels further THROUGH than it bounces BACK)')
for lv in ['gx_poc', 'ny_poc', 'gx_vah', 'ny_vah', 'gx_val', 'ny_val']:
    sub = r[r.level == lv]
    if len(sub) < 20:
        continue
    ratio = sub.thru_t.median() / max(sub.back_t.median(), 1e-9)
    print(f'{lv:10s} thru={sub.thru_t.median():5.1f}t  back={sub.back_t.median():5.1f}t  '
          f'ratio={ratio:4.2f}')
