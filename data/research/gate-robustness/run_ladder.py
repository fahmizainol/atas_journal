"""A/B ladder for the gate-robustness scorecard (docs/research/gate-robustness.md).

Baseline is the pinned 20250201-20260630-v13-a348d176 (full stack: regime,
gx_poc_shape, gx_overhang, chop 0.65 + reenter_after_stop_only). Two variant
families per gate:

  off:<gate>    — the section deleted entirely (house convention: matches how
                  d2f44d4e was made, so chop-off reuses that existing run).
  <gate>:<val>  — parameter neighbors, for the plateau-vs-spike test.

Sequential on purpose: the runner parallelizes across sessions internally, and
two pools oversubscribe the box. The __main__ guard is load-bearing — the
runner's forkserver workers re-import this script as __main__.
"""
import sys, json, time
sys.path.insert(0, 'src')
from journal.sim import store, registry, runner

SLUG = 'vwap-upper-band-bounce'
BASELINE_RID = '20250201-20260630-v13-a348d176'

# (label, gate-section overrides; a None section deletes the gate)
LADDER = [
    ('off:regime',        {'regime': None}),
    ('off:gx_poc_shape',  {'gx_poc_shape': None}),
    ('off:gx_overhang',   {'gx_overhang': None}),
    ('off:chop',          {'chop': None}),                     # == d2f44d4e, skipped as existing
    ('regime:bbr0.30',    {'regime': {'enabled': True, 'bbr_max': 0.30, 'checkpoint': '10:30'}}),
    ('regime:bbr0.40',    {'regime': {'enabled': True, 'bbr_max': 0.40, 'checkpoint': '10:30'}}),
    ('overhang:40',       {'gx_overhang': {'enabled': True, 'max_ticks': 40}}),
    ('overhang:60',       {'gx_overhang': {'enabled': True, 'max_ticks': 60}}),
    ('pocshape:25-75',    {'gx_poc_shape': {'enabled': True, 'zone_min_ticks': 25, 'zone_max_ticks': 75, 'mode': 'veto'}}),
    ('pocshape:25-125',   {'gx_poc_shape': {'enabled': True, 'zone_min_ticks': 25, 'zone_max_ticks': 125, 'mode': 'veto'}}),
    ('chop:0.60',         {'chop': {'enabled': True, 'max_overlap': 0.60}}),   # exists: 4a1f81e9
    ('chop:0.70',         {'chop': {'enabled': True, 'max_overlap': 0.70}}),   # exists: 9951f7bf
]


def variant_config(base: dict, gates: dict) -> dict:
    d = json.loads(json.dumps(base))
    for name, sec in gates.items():
        if sec is None:
            d['confluences'].pop(name, None)
        else:
            d['confluences'][name] = sec
    return d


def main():
    base = json.load(open(f'data/sims/{SLUG}/{BASELINE_RID}/config.json'))
    strat = registry.get(SLUG)
    for label, gates in LADDER:
        cfg = store.config_from_json(variant_config(base, gates))
        rid = store.run_id(cfg, strat.version)
        if store.read_run(SLUG, rid) is not None:
            print(f'SKIP {label} {rid} (exists)', flush=True)
            continue
        store.delete_run(SLUG, rid)  # clear any partial dir from a crash
        t0 = time.time()
        print(f'RUN  {label} {rid} ...', flush=True)
        runner.execute(strat, cfg)
        m = json.load(open(f'data/sims/{SLUG}/{rid}/metrics.json'))
        print(f'DONE {label} {rid} {time.time()-t0:.0f}s '
              f'net={m.get("net_pnl", 0):,.0f} trades={m.get("trades")}', flush=True)
    print('ALL DONE', flush=True)


if __name__ == '__main__':
    main()
