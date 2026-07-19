"""A/B ladder for the market-structure gates (chop / structure_clarity).

Re-runs the adopted cdc07ca2 config on the current engine as the v13 baseline,
then each gate variant. Sequential on purpose: the runner parallelizes across
sessions internally. The __main__ guard is load-bearing — the runner's
forkserver workers re-import this script as __main__.
"""
import sys, json, time
sys.path.insert(0, 'src')
from journal.sim import store, registry, runner

LADDER = [
    ('baseline-v13', {}),
    ('chop-0.55', {'chop': {'enabled': True, 'max_overlap': 0.55}}),
    ('chop-0.60', {'chop': {'enabled': True, 'max_overlap': 0.60}}),
    ('chop-0.65', {'chop': {'enabled': True, 'max_overlap': 0.65}}),
    ('chop-0.70', {'chop': {'enabled': True, 'max_overlap': 0.70}}),
    ('clarity-40', {'structure_clarity': {'enabled': True, 'zz_ticks': 40}}),
]


def main():
    base = json.load(open(
        'data/sims/vwap-upper-band-bounce/20250201-20260630-v10-cdc07ca2/config.json'))
    strat = registry.get('vwap-upper-band-bounce')
    for label, gates in LADDER:
        d = json.loads(json.dumps(base))
        for name, sec in gates.items():
            d['confluences'][name] = sec
        cfg = store.config_from_json(d)
        rid = store.run_id(cfg, strat.version)
        existing = store.read_run(strat.slug, rid)
        if existing is not None:
            print(f'SKIP {label} {rid} (exists)', flush=True)
            continue
        store.delete_run(strat.slug, rid)  # clear any partial dir from a crash
        t0 = time.time()
        print(f'RUN  {label} {rid} ...', flush=True)
        runner.execute(strat, cfg)
        m = json.load(open(f'data/sims/{strat.slug}/{rid}/metrics.json'))
        print(f'DONE {label} {rid} {time.time()-t0:.0f}s '
              f'net={m.get("net_pnl", 0):,.0f} trades={m.get("trades")}', flush=True)
    print('ALL DONE')


if __name__ == '__main__':
    main()
