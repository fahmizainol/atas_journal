"""Full-range runs of the non-inverted globex bounce on the 1f435cba chassis.

The three existing full-range v14 runs are all invert-on (buy the pullback
into the LOWER band). The registered default — invert off, buy the upper-band
pullback after acceptance above dev1 — has never been run past 8 months. Two
arms: plain, and with the vwap_slope 09:45 gate that the invert-on A/B tested.
The __main__ guard is load-bearing (forkserver re-import).
"""
import sys, json, time
sys.path.insert(0, 'src')
from journal.sim import store, registry, runner

ARMS = [
    ('invert-off', {'invert': False}, {}),
    ('invert-off-slope-0945', {'invert': False},
     {'vwap_slope': {'enabled': True, 'slope_min': 0.0, 'checkpoint': '09:45'}}),
]


def main():
    base = json.load(open(
        'data/sims/vwap-globex-bounce/20240303-20260630-v14-1f435cba/config.json'))
    strat = registry.get('vwap-globex-bounce')
    for label, overrides, gates in ARMS:
        d = json.loads(json.dumps(base))
        d.update(overrides)
        for name, sec in gates.items():
            d['confluences'][name] = sec
        cfg = store.config_from_json(d, strat.config_cls)
        rid = store.run_id(cfg, strat.version)
        if store.read_run(strat.slug, rid) is not None:
            print(f'SKIP {label} {rid} (exists)', flush=True)
            continue
        store.delete_run(strat.slug, rid)
        t0 = time.time()
        print(f'RUN  {label} {rid} ...', flush=True)
        runner.execute(strat, cfg)
        m = json.load(open(f'data/sims/{strat.slug}/{rid}/metrics.json'))
        print(f'DONE {label} {rid} {time.time()-t0:.0f}s '
              f'net={m.get("net_pnl", 0):,.0f} trades={m.get("trades")}', flush=True)
    print('ALL DONE')


if __name__ == '__main__':
    main()
