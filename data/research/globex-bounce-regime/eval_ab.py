"""Evaluate the vwap_slope gate A/B arms against the 1f435cba baseline.

Prints, per arm: headline metrics vs baseline, the ghost cohort's net (a
net-positive ghost ledger is the gate-robustness study's mirage signature),
split-half nets, and the regime-class breakdown of what remains.
"""
import sys, json, glob
sys.path.insert(0, 'src')
import pandas as pd

SLUG = 'vwap-globex-bounce'
BASE = '20240303-20260630-v14-1f435cba'


def daily_dd(t):
    d = t.groupby('session')['net_pnl'].sum()
    eq = d.cumsum()
    return (eq - eq.cummax()).min()


def halves(t):
    s = t['session'].astype(str)
    return (t.loc[s < '2025-05-01', 'net_pnl'].sum(),
            t.loc[s >= '2025-05-01', 'net_pnl'].sum())


def klass(dates_by_class, t):
    cls = t['session'].astype(str).map(dates_by_class)
    return t.groupby(cls)['net_pnl'].sum().round(0).to_dict()


def main():
    reg = json.load(open(f'data/sims/{SLUG}/{BASE}/regime_pnl.json'))
    by_class = {d: b['class'] for b in reg['class_buckets'] for d in b['dates']}

    bt = pd.read_parquet(f'data/sims/{SLUG}/{BASE}/trades.parquet')
    print(f'BASELINE {BASE}: n={len(bt)} net={bt.net_pnl.sum():,.0f} '
          f'dd={daily_dd(bt):,.0f} halves={tuple(round(x) for x in halves(bt))}')
    print(f'  by class: {klass(by_class, bt)}')

    for rid in sys.argv[1:]:
        t = pd.read_parquet(f'data/sims/{SLUG}/{rid}/trades.parquet')
        m = json.load(open(f'data/sims/{SLUG}/{rid}/metrics.json'))
        gw = t.loc[t.net_pnl > 0, 'net_pnl'].sum()
        gl = -t.loc[t.net_pnl < 0, 'net_pnl'].sum()
        print(f'\nARM {rid}: n={len(t)} net={t.net_pnl.sum():,.0f} '
              f'PF={gw / gl:.3f} dd={daily_dd(t):,.0f} '
              f'sharpe={m.get("sharpe", float("nan")):.2f} '
              f'halves={tuple(round(x) for x in halves(t))}')
        print(f'  by class: {klass(by_class, t)}')
        try:
            v = pd.read_parquet(f'data/sims/{SLUG}/{rid}/vetoed.parquet')
            print(f'  ghosts: n={len(v)} net={v.net_pnl.sum():,.0f} '
                  f'(net-positive ghosts = mirage signature)')
            if 'gate' in v.columns:
                print(f'  by gate: '
                      f'{v.groupby("gate").net_pnl.agg(["size", "sum"]).round(0).to_dict()}')
        except FileNotFoundError:
            print('  no vetoed.parquet')


if __name__ == '__main__':
    main()
