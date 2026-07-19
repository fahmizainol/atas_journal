"""Analyze market-structure features: winners vs losers / stop vs recover.

Cohorts:
  entry_winloss : all trades, is_win label, entry-knowable features
  entry_stop    : all trades, is_stop label, entry-knowable features
  t25 / t40     : trades that touched the depth, stop vs recover,
                  entry features + that anchor's live features

Per feature: Mann-Whitney AUC, permutation p (1000 label shuffles within
session-preserving order), split-half stability (odd/even session rank).

Usage: .venv/bin/python analyze_structure.py <features.parquet>
"""
import sys
import numpy as np
import pandas as pd

PATH = sys.argv[1]
df = pd.read_parquet(PATH)
rng = np.random.default_rng(7)

META = {'idx', 'session', 'r', 'net', 'exit_reason', 'mfe_r', 'mae_r', 'dur_s',
        'risk_pts', 'entry_ts', 'is_stop', 'is_win', 'low_below_prelow_t',
        't25_hit', 't40_hit', 't25_secs', 't40_secs'}
ALL_FEATS = [c for c in df.columns if c not in META and df[c].dtype != object]
ENTRY_FEATS = [c for c in ALL_FEATS if not c.startswith(('t25_', 't40_'))]


def auc(x, y):
    """AUC of x separating y=1 from y=0 (rank-based, ties handled)."""
    m = np.isfinite(x)
    x, y = x[m], y[m]
    n1, n0 = int(y.sum()), int((1 - y).sum())
    if n1 < 5 or n0 < 5:
        return np.nan, n1, n0
    ranks = pd.Series(x).rank().values
    u = ranks[y == 1].sum() - n1 * (n1 + 1) / 2
    return u / (n1 * n0), n1, n0


def perm_p(x, y, obs, n=1000):
    m = np.isfinite(x)
    x, y = x[m], y[m].copy()
    if np.isnan(obs):
        return np.nan
    cnt = 0
    for _ in range(n):
        yy = rng.permutation(y)
        a, _, _ = auc(x, yy)
        if abs(a - 0.5) >= abs(obs - 0.5):
            cnt += 1
    return cnt / n


def split_half(sub, feat, label):
    sess = sorted(sub.session.unique())
    a_s = set(sess[::2])
    ha = sub[sub.session.isin(a_s)]
    hb = sub[~sub.session.isin(a_s)]
    ra, _, _ = auc(ha[feat].values.astype(float), ha[label].values.astype(float))
    rb, _, _ = auc(hb[feat].values.astype(float), hb[label].values.astype(float))
    return ra, rb


def run_cohort(name, sub, feats, label):
    out = []
    y = sub[label].values.astype(float)
    for f in feats:
        x = sub[f].values.astype(float)
        a, n1, n0 = auc(x, y)
        if np.isnan(a):
            continue
        p = perm_p(x, y, a)
        ha, hb = split_half(sub, f, label)
        mu1 = np.nanmedian(x[y == 1]) if n1 else np.nan
        mu0 = np.nanmedian(x[y == 0]) if n0 else np.nan
        out.append(dict(cohort=name, feat=f, auc=a, p=p, n1=n1, n0=n0,
                        half_a=ha, half_b=hb, med_pos=mu1, med_neg=mu0))
    r = pd.DataFrame(out)
    r['sep'] = (r.auc - 0.5).abs()
    return r.sort_values('sep', ascending=False)


res = []
res.append(run_cohort('entry_winloss', df, ENTRY_FEATS, 'is_win'))
res.append(run_cohort('entry_stop', df, ENTRY_FEATS, 'is_stop'))
for tag in ('t25', 't40'):
    sub = df[df[f'{tag}_hit'] == 1].copy()
    feats = ENTRY_FEATS + [c for c in ALL_FEATS if c.startswith(f'{tag}_')]
    res.append(run_cohort(tag, sub, feats, 'is_stop'))
res = pd.concat(res, ignore_index=True)

out_csv = PATH.replace('features_', 'aucs_').replace('.parquet', '.csv')
res.to_csv(out_csv, index=False)
print('WROTE', out_csv)

pd.set_option('display.width', 200)
for c in res.cohort.unique():
    sub = res[res.cohort == c]
    print(f'\n=== {c}  (n1={sub.n1.iloc[0]}, n0={sub.n0.iloc[0]}) — top 12 by separation ===')
    print(sub.head(12)[['feat', 'auc', 'p', 'half_a', 'half_b', 'med_pos', 'med_neg']]
          .to_string(index=False, float_format=lambda v: f'{v:.3f}'))
