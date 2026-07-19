"""Evaluate the market-structure gate A/B ladder against the v13 baseline."""
import sys, json
sys.path.insert(0, 'src')
import pandas as pd
from journal.sim import store, registry
from run_ab import LADDER

SLUG = 'vwap-upper-band-bounce'


def main():
    base = json.load(open(
        f'data/sims/{SLUG}/20250201-20260630-v10-cdc07ca2/config.json'))
    strat = registry.get(SLUG)
    rows = []
    for label, gates in LADDER:
        d = json.loads(json.dumps(base))
        for name, sec in gates.items():
            d['confluences'][name] = sec
        cfg = store.config_from_json(d)
        rid = store.run_id(cfg, strat.version)
        run_dir = f'data/sims/{SLUG}/{rid}'
        try:
            m = json.load(open(f'{run_dir}/metrics.json'))
        except FileNotFoundError:
            print(f'{label}: {rid} missing'); continue
        t = pd.read_parquet(f'{run_dir}/trades.parquet')
        yr = pd.to_datetime(t.session).dt.year
        r = dict(label=label, rid=rid.split('-')[-1], trades=m['trades'],
                 net=m['net_pnl'], pf=m['profit_factor'], win=m['win_rate'],
                 maxdd=m['max_drawdown'], sharpe=m['sharpe'],
                 sortino=m['sortino'], expect=m['expectancy'],
                 net25=t[yr == 2025].net_pnl.sum(),
                 net26=t[yr == 2026].net_pnl.sum(),
                 stops=int((t.exit_reason == 'stop').sum()))
        # ghost ledger: what THIS run's new gate alone filtered
        gate_name = next(iter(gates), None)
        if gate_name:
            v = store.read_vetoed(SLUG, rid)
            if len(v):
                mine = v[v.gate == gate_name]
                r['ghost_n'] = len(mine)
                r['ghost_net'] = mine.net_pnl.sum()
                r['ghost_stoprate'] = float((mine.exit_reason == 'stop').mean()) if len(mine) else float('nan')
        rows.append(r)
    df = pd.DataFrame(rows)
    pd.set_option('display.width', 250)
    fmt = {c: '{:,.0f}'.format for c in ('net', 'maxdd', 'expect', 'net25', 'net26', 'ghost_net')}
    fmt.update({c: '{:.2f}'.format for c in ('pf', 'sharpe', 'sortino')})
    fmt.update({'win': '{:.1f}'.format, 'ghost_stoprate': '{:.2f}'.format})
    print(df.to_string(index=False, formatters={k: v for k, v in fmt.items() if k in df}))


if __name__ == '__main__':
    main()
