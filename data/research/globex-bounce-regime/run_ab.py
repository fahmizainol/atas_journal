"""A/B ladder for the vwap_slope gate on vwap-globex-bounce (invert-on long).

Baseline is the existing 20240303-20260630-v14-1f435cba run (stop 150 / RR4 /
trail 75 step 75 BE 4, invert on) — the flattest chassis of the family. The
post-hoc lead: the run's whole bleed sits on trend-down days, and the 09:45
ny_vwap_slope_ppm read is the one checkpoint KPI whose post-checkpoint veto
recovered it in the re-cut (bbr@09:45 and every 10:30 read did not — the
morning damage is already done by 10:30). Two arms: the untuned gate default
(slope_min 0.0, "any upward grade at all") and the mid-plateau fit (-1.0).
Sequential on purpose: the runner parallelizes across sessions internally.
The __main__ guard is load-bearing — the runner's forkserver workers
re-import this script as __main__.
"""
import sys, json, time
sys.path.insert(0, 'src')
from journal.sim import store, registry, runner

LADDER = [
    ('slope-0945-min0.0', {'vwap_slope': {'enabled': True, 'slope_min': 0.0,
                                          'checkpoint': '09:45'}}),
    ('slope-0945-min-1.0', {'vwap_slope': {'enabled': True, 'slope_min': -1.0,
                                           'checkpoint': '09:45'}}),
]


def main():
    base = json.load(open(
        'data/sims/vwap-globex-bounce/20240303-20260630-v14-1f435cba/config.json'))
    strat = registry.get('vwap-globex-bounce')
    for label, gates in LADDER:
        d = json.loads(json.dumps(base))
        for name, sec in gates.items():
            d['confluences'][name] = sec
        cfg = store.config_from_json(d, strat.config_cls)
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
