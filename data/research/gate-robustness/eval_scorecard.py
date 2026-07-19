"""Gate-robustness scorecard: is each baseline confluence a real edge or luck?

Companion to docs/research/gate-robustness.md. Reads the pinned baseline
(a348d176) plus the run_ladder.py variants and scores every gate on a ladder of
tests chosen so that no single lens (least of all raw net P&L, which is
top-20-dominated) can pass a gate on its own:

  T1 marginal      — full-stack A/B: baseline vs the gate-off run (real engine,
                     re-arm chains intact). Δnet / ΔPF / ΔmaxDD / ΔSharpe.
  T2 months        — per-month sign test on the baseline-vs-off monthly nets
                     (ties dropped, two-sided binomial). Stability over windows.
  T3 bootstrap     — block bootstrap (by month) of the daily net delta: CI of
                     the gate's total contribution. Day-level resample shown too.
  T4 tail          — does Δnet survive removing each run's top 20 trades, and
                     winsorizing at the pooled p95 trade P&L?
  T5 selection     — ghost-frame permutation: universe = kept ∪ this gate's
                     unique ghosts; is the kept subset's win-rate/avg-R better
                     than 10k random same-size subsets? (Quality of *selection*,
                     immune to the ghost-net mirage — but ghosts are simulated
                     without capacity/re-arm effects; directional only.)
  T6 cohort        — kept vs unique-ghost outcome distributions: Mann-Whitney U
                     on R, AUC, stop rates.
  T7 neighborhood  — parameter neighbors: plateau (robust) vs spike (fit).
  T8 halves        — Δnet sign agreement 2025 vs 2026.

Multiple-comparisons context: ~11 gates have been tried on this strategy with 1
A/B pass; at per-test α=0.05 a scorecard this size *will* hand out flukes —
read the column of verdicts per gate, not any single cell.
"""
import sys, json, math
sys.path.insert(0, 'src')
import numpy as np
import pandas as pd

from run_ladder import LADDER, SLUG, BASELINE_RID, variant_config
from journal.sim import store, registry

RNG = np.random.default_rng(7)
N_PERM = 10_000
GATES = ['regime', 'gx_poc_shape', 'gx_overhang', 'chop']


def load_run(rid):
    d = f'data/sims/{SLUG}/{rid}'
    try:
        m = json.load(open(f'{d}/metrics.json'))
    except FileNotFoundError:
        return None
    t = pd.read_parquet(f'{d}/trades.parquet')
    t['session'] = pd.to_datetime(t['session'])
    return {'rid': rid, 'metrics': m, 'trades': t}


def ladder_rids():
    base = json.load(open(f'data/sims/{SLUG}/{BASELINE_RID}/config.json'))
    strat = registry.get(SLUG)
    out = {}
    for label, gates in LADDER:
        cfg = store.config_from_json(variant_config(base, gates))
        out[label] = store.run_id(cfg, strat.version)
    return out


def daily_net(t: pd.DataFrame) -> pd.Series:
    return t.groupby(t.session.dt.date).net_pnl.sum()


def monthly_net(t: pd.DataFrame) -> pd.Series:
    return t.groupby(t.session.dt.to_period('M')).net_pnl.sum()


def binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial p at p0=0.5 (sum of tails ≤ P(k))."""
    pk = math.comb(n, k) / 2 ** n
    return float(min(1.0, sum(math.comb(n, i) / 2 ** n
                              for i in range(n + 1)
                              if math.comb(n, i) / 2 ** n <= pk + 1e-12)))


def sign_test(base_m: pd.Series, off_m: pd.Series):
    idx = base_m.index.union(off_m.index)
    d = base_m.reindex(idx, fill_value=0) - off_m.reindex(idx, fill_value=0)
    d = d[d.round(2) != 0]
    if len(d) == 0:
        return {'months': 0, 'better': 0, 'p': 1.0}
    better = int((d > 0).sum())
    return {'months': int(len(d)), 'better': better,
            'p': binom_two_sided(better, int(len(d)))}


def block_bootstrap(base_t, off_t, by='M'):
    """CI of total(baseline) - total(off) resampling blocks with replacement."""
    key = (lambda t: t.session.dt.to_period('M')) if by == 'M' else (lambda t: t.session.dt.date)
    b = base_t.groupby(key(base_t)).net_pnl.sum()
    o = off_t.groupby(key(off_t)).net_pnl.sum()
    idx = b.index.union(o.index)
    d = (b.reindex(idx, fill_value=0) - o.reindex(idx, fill_value=0)).values
    n = len(d)
    totals = d[RNG.integers(0, n, size=(N_PERM, n))].sum(axis=1)
    lo, hi = np.percentile(totals, [2.5, 97.5])
    return {'delta': float(d.sum()), 'ci_lo': float(lo), 'ci_hi': float(hi),
            'p_sign': float(min((totals <= 0).mean(), (totals >= 0).mean()) * 2),
            'blocks': n}


def tail_tests(base_t, off_t):
    ex20 = lambda t: t.net_pnl.sum() - t.net_pnl.nlargest(20).sum()
    pooled_p95 = np.percentile(pd.concat([base_t.net_pnl, off_t.net_pnl]), 95)
    wins = lambda t: t.net_pnl.clip(upper=pooled_p95).sum()
    return {'d_net_ex_top20': float(ex20(base_t) - ex20(off_t)),
            'd_net_winsor_p95': float(wins(base_t) - wins(off_t)),
            'base_ex_top20': float(ex20(base_t)), 'off_ex_top20': float(ex20(off_t))}


def unique_ghosts(vetoed: pd.DataFrame, gate: str) -> pd.DataFrame:
    g = vetoed.gates.astype(str)
    return vetoed[g == gate]


def selection_null(kept_r: np.ndarray, ghost_r: np.ndarray):
    """Kept vs 10k random same-size subsets of kept∪ghost: win-rate & mean-R percentile."""
    uni = np.concatenate([kept_r, ghost_r])
    k = len(kept_r)
    picks = np.argsort(RNG.random((N_PERM, len(uni))), axis=1)[:, :k]
    samp = uni[picks]
    null_win, null_mr = (samp > 0).mean(axis=1), samp.mean(axis=1)
    act_win, act_mr = float((kept_r > 0).mean()), float(kept_r.mean())
    return {'n_universe': int(len(uni)), 'n_kept': k,
            'win_rate': act_win, 'win_pctile': float((null_win < act_win).mean() * 100),
            'mean_r': act_mr, 'mean_r_pctile': float((null_mr < act_mr).mean() * 100)}


def mannwhitney(x: np.ndarray, y: np.ndarray):
    """U statistic for x-vs-y plus a normal-approximation two-sided p
    (tie-corrected). n here is far past where the approximation is exact-ish."""
    nx, ny = len(x), len(y)
    allv = np.concatenate([x, y])
    order = allv.argsort(kind='mergesort')
    ranks = np.empty(len(allv))
    ranks[order] = np.arange(1, len(allv) + 1)
    # average ranks for ties
    sv = allv[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = ranks[order[i:j + 1]].mean()
        i = j + 1
    u = ranks[:nx].sum() - nx * (nx + 1) / 2
    mu = nx * ny / 2
    _, cnt = np.unique(allv, return_counts=True)
    tie = (cnt ** 3 - cnt).sum()
    n = nx + ny
    sigma = math.sqrt(nx * ny / 12 * ((n + 1) - tie / (n * (n - 1))))
    z = (u - mu) / sigma if sigma > 0 else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return u, p


def cohort_tests(kept_r: np.ndarray, ghost_r: np.ndarray):
    if len(ghost_r) < 5:
        return {'n_ghost': int(len(ghost_r)), 'note': 'too few unique ghosts'}
    u, p = mannwhitney(kept_r, ghost_r)
    auc = u / (len(kept_r) * len(ghost_r))  # P(kept R > ghost R)
    return {'n_ghost': int(len(ghost_r)),
            'ghost_net': float('nan'),  # filled by caller (needs net col)
            'auc_kept_gt_ghost': float(auc), 'mw_p': float(p),
            'kept_win': float((kept_r > 0).mean()), 'ghost_win': float((ghost_r > 0).mean()),
            'kept_stop': float('nan'), 'ghost_stop': float('nan')}


def main():
    rids = ladder_rids()
    base = load_run(BASELINE_RID)
    bt, bm = base['trades'], base['metrics']
    vetoed = store.read_vetoed(SLUG, BASELINE_RID)
    vetoed['session'] = pd.to_datetime(vetoed['session'])
    kept_r = bt.r_multiple.values

    card = {'baseline': {'rid': BASELINE_RID, 'trades': len(bt),
                         'net': bm['net_pnl'], 'pf': bm['profit_factor'],
                         'maxdd': bm['max_drawdown'], 'sharpe': bm['sharpe']},
            'gates': {}}

    neighbors = {
        'regime': ['regime:bbr0.30', 'regime:bbr0.40'],
        'gx_poc_shape': ['pocshape:25-75', 'pocshape:25-125'],
        'gx_overhang': ['overhang:40', 'overhang:60'],
        'chop': ['chop:0.60', 'chop:0.70'],
    }

    for gate in GATES:
        g = {}
        off = load_run(rids[f'off:{gate}'])
        if off:
            ot, om = off['trades'], off['metrics']
            g['T1_marginal'] = {
                'off_rid': off['rid'], 'off_trades': len(ot),
                'd_net': bm['net_pnl'] - om['net_pnl'],
                'd_pf': bm['profit_factor'] - om['profit_factor'],
                'd_maxdd': bm['max_drawdown'] - om['max_drawdown'],
                'd_sharpe': bm['sharpe'] - om['sharpe'],
                'off_net': om['net_pnl'], 'off_pf': om['profit_factor'],
                'off_maxdd': om['max_drawdown'], 'off_sharpe': om['sharpe']}
            g['T2_months'] = sign_test(monthly_net(bt), monthly_net(ot))
            g['T3_bootstrap_month'] = block_bootstrap(bt, ot, by='M')
            g['T3_bootstrap_day'] = block_bootstrap(bt, ot, by='D')
            g['T4_tail'] = tail_tests(bt, ot)
            y = lambda t, yr: t[t.session.dt.year == yr].net_pnl.sum()
            g['T8_halves'] = {'d_2025': float(y(bt, 2025) - y(ot, 2025)),
                              'd_2026': float(y(bt, 2026) - y(ot, 2026))}
        gh = unique_ghosts(vetoed, gate)
        ghost_r = gh.r_multiple.values
        g['T5_selection'] = selection_null(kept_r, ghost_r) if len(ghost_r) >= 5 else {'note': 'too few'}
        c = cohort_tests(kept_r, ghost_r)
        if 'auc_kept_gt_ghost' in c:
            c['ghost_net'] = float(gh.net_pnl.sum())
            c['kept_stop'] = float((bt.exit_reason == 'stop').mean())
            c['ghost_stop'] = float((gh.exit_reason == 'stop').mean())
        g['T6_cohort'] = c
        nb = {}
        for lab in neighbors[gate]:
            r = load_run(rids[lab])
            if r:
                m, t = r['metrics'], r['trades']
                nb[lab] = {'net': m['net_pnl'], 'pf': m['profit_factor'],
                           'maxdd': m['max_drawdown'], 'sharpe': m['sharpe'],
                           'trades': m['trades'],
                           'net_ex_top20': float(t.net_pnl.sum() - t.net_pnl.nlargest(20).sum())}
        g['T7_neighborhood'] = nb
        card['gates'][gate] = g

    json.dump(card, open('data/research/gate-robustness/scorecard.json', 'w'),
              indent=2, default=str)

    for gate, g in card['gates'].items():
        print(f'\n=== {gate} ===')
        for k, v in g.items():
            print(f'  {k}: {json.dumps(v, default=str)}')


if __name__ == '__main__':
    main()
